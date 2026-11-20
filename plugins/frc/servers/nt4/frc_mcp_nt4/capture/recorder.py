"""Lifecycle and queries of a `Capture`: it records chosen topics into a bounded buffer (optionally a .wpilog file), and the
model later asks for a reduced view (latest, summary, changes, samples). Why it works this way: docs/ARCHITECTURE_SERVERS.md.
"""

import asyncio
import time
from dataclasses import replace
from typing import Any

import ntcore

from frc_mcp_wpilib.reduce.stats import MS_PER_S

from .. import clock as nt_clock
from .buffer import MAX_TOPICS
from .listeners import _Listeners
from .query import QuerySpec, Snapshot, build_reply, resolve_window, rule_summary
from .state import CaptureSpec
from .types import CaptureInfo

__all__ = ["Capture", "CaptureSpec"]  # the two records this module exposes; the rest are its collaborators'


class Capture(_Listeners):
    # -- lifecycle ----------------------------------------------------------
    def start(self, inst: ntcore.NetworkTableInstance, loop: asyncio.AbstractEventLoop) -> None:
        """Subscribe and start recording; `loop` is the running event loop that owns this capture's timers."""
        self._inst = inst
        self._clock.start(nt_clock.local_now_us())
        self.started = time.monotonic()
        self._loop = loop  # event loop that runs the post-trigger stop timer
        self._logfile.open()
        self._connection_handle = inst.addConnectionListener(False, self._on_connection)
        with self._lock:
            self._clock.synchronized = inst.getServerTimeOffset() is not None
            if not inst.isConnected():
                self._start_gap()
        self._subscribe_samples(inst)
        self._subscribe_rules(inst)

    def stop(self) -> None:
        with self._lock:
            if not self.active:
                return
            self._clock.stop(nt_clock.local_now_us())
            self.stopped_at = time.monotonic()
        self._release_subscriptions()
        with self._lock:
            self._logfile.close()

    # -- query --------------------------------------------------------------
    def _info_fields(self) -> CaptureInfo:
        """The capture's identity, elapsed time and trigger status (the caller holds the lock)."""
        out: CaptureInfo = {"id": self.id, "active": self.active,
                               "elapsed_s": round(self._run_time_ms() / MS_PER_S, 1),
                               "topics": self.topics, "rate_hz": self.rate_hz}
        state = self._trig
        if self.trigger is not None:
            out["trigger"] = state.status()
            if state.out_of_order:
                out["trigger_out_of_order_updates"] = state.out_of_order
            if state.fired_ms is None and state.history_uncertain:
                out["trigger_history_uncertain"] = True
        return out

    def info(self) -> CaptureInfo:
        with self._lock:
            return self._info_fields()

    def snapshot(self, topics: list[str] | None = None) -> Snapshot:
        """A copy of everything a query reads, taken under the lock; the reply is built from it without the lock."""
        with self._lock:
            return self._snapshot_locked(topics)

    def _snapshot_locked(self, topics: list[str] | None) -> Snapshot:
        buf, log, state = self._buffer, self._logfile, self._trig
        view = buf.view(topics)
        return Snapshot(
            info=self._info_fields(),
            buffer=view,
            rate_hz=self.rate_hz, all_changes=self.all_changes, log_path=self.log_path, topic_limit=MAX_TOPICS,
            trigger_ms=state.fired_ms, trigger_value=state.fired_value, trigger_type=state.fired_type,
            units={n: u for n in view.samples if (u := self._unit(n))},
            gaps=list(self._admission.gaps),
            omitted=self._admission.omitted,
            log_bytes=log.bytes, log_truncated=log.truncated, log_error=log.error, log_dropped_nonfinite_time=log.dropped_nonfinite_time,
            event_sequence=self._event_sequence,
            event_revision=self._event_revision,
            events=[event.copy() for event in self._events],
            event_rules=[rule_summary(rule) for rule in self._event_states],
            event_coverage=[list(rule.coverage_ms) for rule in self._event_states],
            end_ms=self._run_time_ms(),
        )

    def execute_query(self, spec: QuerySpec) -> dict[str, Any]:
        """Answer `spec` (which carries `rel_trigger`). The clock fields are filled in here."""
        with self._lock:
            since_ms, until_ms = resolve_window(self, spec.since_ms, spec.until_ms, spec.rel_trigger)
            snap = self._snapshot_locked(spec.topics)
        synchronized = spec.include_evidence and self._inst is not None and self._inst.getServerTimeOffset() is not None
        resolved = replace(spec, since_ms=since_ms, until_ms=until_ms, clock_synchronized=synchronized,
                           now_us=nt_clock.local_now_us() if spec.include_evidence else None)
        return build_reply(snap, resolved)
