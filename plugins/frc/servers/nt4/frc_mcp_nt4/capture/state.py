"""The state every `Capture` part shares: options, clocks, buffers and accessors. `Capture` (recorder.py) is built in layers:
this one, then rule_events.py (triggers and event rules), then listeners.py (ntcore callbacks), then recorder.py.
"""

import asyncio
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ntcore

from frc_mcp_wpilib.reduce.stats import MS_PER_S
from frc_mcp_wpilib.reduce.types import EventRecord
from frc_mcp_wpilib.values import unit_of

from .. import clock as nt_clock
from .admission import CaptureAdmission
from .buffer import SampleBuffer
from .capture_clock import CaptureClock
from .log import CaptureLog
from .rules import EventRule, EventState, Trigger, TriggerState

_log = logging.getLogger(__name__)
Subscription = ntcore.MultiSubscriber | ntcore.GenericSubscriber
# Sentinel for a capture with no trigger: `TriggerState` always has a rule, and this one is never evaluated
# (`op="changed"` needs no `value`, so it satisfies the shared model validator).
_UNARMED = Trigger(topic="<unarmed>", op="changed", value=None, field=None, after_s=2.0, immediate=False)
MAX_GAPS = 20  # most recent observed connection gaps retained
MAX_EVENTS = 256  # bounded observed-event history per capture


@dataclass(frozen=True)
class CaptureSpec:
    """The options of one background capture. Built once where the request arrives, then passed by value through
    `CaptureRegistry.start` into `Capture.__init__`, so an option is declared here and read where it is used."""

    topics: list[str]
    prefix: bool
    rate_hz: float
    all_changes: bool
    duration_s: float  # the hard budget the session arms; stop() itself does not cancel it
    trigger: Trigger | None = None
    event_rules: list[EventRule] | None = None


class _CaptureState:
    # The timing fields a query reads between construction and `start()`; each is assigned exactly once,
    # by the lifecycle method that owns it.
    started: float = 0.0  # monotonic s, stamped by start() (0 until then)
    stopped_at: float | None = None  # monotonic s, stamped by stop(); `active` is exactly this being None

    def __init__(self, cid: str, spec: CaptureSpec, log_path: Path | None) -> None:
        trigger, event_rules = spec.trigger, spec.event_rules or []
        topics = self._recorded_topics(spec.topics, spec.prefix, trigger)
        self.id = cid
        self.topics = list(dict.fromkeys(topics))
        self.prefix = spec.prefix
        self.rate_hz = spec.rate_hz
        self.all_changes = spec.all_changes
        self.duration_s = spec.duration_s  # the hard budget the session arms; stop() itself does not cancel it
        self.log_path = log_path
        self.trigger = trigger
        self._trig = TriggerState(trigger or _UNARMED, "trigger")  # never consulted while `trigger` is None
        self._event_states = [EventState(rule, index, coverage_ms=deque(maxlen=MAX_GAPS + 1))
                              for index, rule in enumerate(event_rules)]
        self._events: deque[EventRecord] = deque(maxlen=MAX_EVENTS)
        self._event_sequence = 0
        self._event_revision = 0
        self._handler_types: dict[str | int, str] = {}
        self._event_subs: list[Subscription] = []
        self._event_handles: list[int] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()  # ntcore calls our listener from its own thread
        self._inst: ntcore.NetworkTableInstance | None = None
        self._handles: list[int] = []
        self._subs: list[Subscription] = []
        self._buffer = SampleBuffer(spec.rate_hz, spec.all_changes)
        self._logfile = CaptureLog(log_path)
        # connection state: the ntcore listener handle, then the clock and gap checks built on it
        self._connection_handle: int | None = None  # removed again by _release_subscriptions
        self._clock = CaptureClock()  # timestamp placement: the NT local-clock origin and its conversions
        self._admission = CaptureAdmission(self._clock)  # the connection gaps and the placement checks built on them

    def stop(self) -> None:
        """Implemented by `Capture` (capture/recorder.py); declared here because the rule handling schedules it."""
        raise NotImplementedError

    @staticmethod
    def _recorded_topics(topics: list[str], prefix: bool, trigger: Trigger | None) -> list[str]:
        """The topics to record: the trigger's topic is always included."""
        if trigger is not None and not prefix and trigger.topic not in topics:
            topics = [*topics, trigger.topic]
        return topics

    def _run_time_ms(self) -> float:
        """Capture run time: ms from start to now (frozen once stopped); falls back to the monotonic clock until the
        NT local clock has been read."""
        elapsed = self._clock.try_run_time_ms(self.active, nt_clock.local_now_us())
        if elapsed is not None:
            return elapsed
        end = self.stopped_at if self.stopped_at is not None else time.monotonic()
        return max(0.0, (end - self.started) * MS_PER_S)

    def local_clock_now_ms(self) -> float:
        """The NT local clock's now, in ms since capture start; infinite when unavailable. Unlike `_run_time_ms` it
        never falls back to the monotonic clock and does not freeze at stop: it dates when an update *arrived*."""
        return self._clock.arrival_ms(nt_clock.local_now_us())

    def _sample_time_ms(self, value: Any) -> float:
        """ms since capture start for a received value, from its own timestamp (falls back to arrival time, and never
        later than it)."""
        return self._clock.sample_time_ms(value, self._run_time_ms())  # value.time() is local-clock us

    # Accessors the session and the query path read (see capture/registry.py and capture/query.py). Those returning a
    # live object say so: do not mutate.
    @property
    def trigger_state(self) -> TriggerState:
        """The live trigger state, for inspection and tests; callers must not mutate it."""
        return self._trig

    @property
    def log_open(self) -> bool:
        return self._logfile.file is not None

    @property
    def active(self) -> bool:
        return self.stopped_at is None

    @property
    def has_event_rules(self) -> bool:
        return bool(self._event_states)

    @property
    def buffer(self) -> SampleBuffer:
        """The live sample buffer, for inspection and tests; the recorder owns it, so callers must not mutate it."""
        return self._buffer

    # -- connection gaps ------------------------------------------------------
    # The gap rows live in the admission object (capture/admission.py); this is the placement check over them.
    def after_latest_gap(self, value: Any) -> bool:
        """Whether `value`'s own timestamp places it after the newest closed outage (the shared rearm check)."""
        return self._admission.after_latest_gap(value, nt_clock.local_now_us())

    # -- timestamp admission --------------------------------------------------
    def rearm_point(self) -> int | float:
        """Updates stamped at or before this must not rearm a condition (infinite when the clock is unavailable)."""
        return self._clock.rearm_point(nt_clock.local_now_us())

    def _unit(self, name: str) -> str | None:
        if self._inst is None:
            return None
        try:
            return unit_of(self._inst.getTopic(name).getProperties())
        # Probed with ntcore: a missing topic gives {} and a destroyed instance None; the types below are what other pybind builds may raise.
        except (AttributeError, RuntimeError, ValueError):
            _log.debug("no unit for %s: topic or instance is gone", name, exc_info=True)
            return None

    def _known_type(self, key: str | int, topic: Any) -> str:
        """The topic's type, remembering it across values queued after an unpublish (which report "")."""
        type_str = topic.getTypeString()
        if type_str:
            self._handler_types[key] = type_str
            return type_str
        return self._handler_types.get(key, "")
