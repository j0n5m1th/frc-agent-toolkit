"""The ntcore side of `Capture` (third layer, see capture/state.py): subscriptions, listener callbacks and per-update
sample recording. `_on_event` holds the capture lock across the buffer update, the rule checks and the log write.
"""

import math
from collections import deque
from collections.abc import Sequence
from functools import partial
from typing import Any

import ntcore

from .. import clock as nt_clock
from . import state as capture_state
from .buffer import Sample
from .rule_events import _RuleEvents
from .types import sample_size

RULE_POLL_STORAGE = 1000  # queued updates for a trigger/rule's own sendAll subscription (bursts must not be lost)


def _log_us(t_ms: float) -> int | None:
    """A capture time as log microseconds (never negative), or None when it is not a number: with no clock to date an arrival
    by, the update has no place on the log's timeline, and 0 would date it to the capture's start."""
    return max(0, round(t_ms * 1000)) if math.isfinite(t_ms) else None


class _Listeners(_RuleEvents):
    def _listen(self, inst: ntcore.NetworkTableInstance, sub: Any, callback: Any) -> int:
        # kUnpublish needs its own listener: combined with kImmediate it duplicates the initial value event
        self._handles.append(inst.addListener(sub, ntcore.EventFlags.kUnpublish, callback))
        return inst.addListener(sub, ntcore.EventFlags.kValueAll | ntcore.EventFlags.kImmediate, callback)

    def _subscribe_samples(self, inst: ntcore.NetworkTableInstance) -> None:
        opts = ntcore.PubSubOptions(periodic=1.0 / self.rate_hz, sendAll=self.all_changes)
        if self.prefix:
            sub: Any = ntcore.MultiSubscriber(inst, self.topics, opts)
            self._subs.append(sub)
            self._handles.append(self._listen(inst, sub, self._on_sample_notification))
        else:  # exact names: a MultiSubscriber is always prefix-matching, so use one subscriber per topic
            for name in self.topics:
                self._subscribe_topic(inst, name, opts)
        trig = self.trigger
        if trig is not None:
            if self.prefix and not any(trig.topic.startswith(p) for p in self.topics):
                self._subscribe_topic(inst, trig.topic, opts)
            self._trig.sub = inst.getTopic(trig.topic).genericSubscribe(
                ntcore.PubSubOptions(sendAll=True, pollStorage=RULE_POLL_STORAGE))
            self._trig.handle = self._listen(inst, self._trig.sub, self._on_trigger_notification)

    def _subscribe_topic(self, inst: ntcore.NetworkTableInstance, name: str, opts: Any) -> None:
        sub = inst.getTopic(name).genericSubscribe(opts)
        self._subs.append(sub)
        self._handles.append(self._listen(inst, sub, self._on_sample_notification))

    def _subscribe_rules(self, inst: ntcore.NetworkTableInstance) -> None:
        for index, state in enumerate(self._event_states):
            sub = inst.getTopic(state.rule.topic).genericSubscribe(
                ntcore.PubSubOptions(sendAll=True, pollStorage=RULE_POLL_STORAGE))
            self._event_subs.append(sub)
            self._event_handles.append(self._listen(inst, sub, partial(self._on_rule_notification, index)))

    @staticmethod
    def _remove_listeners(inst: ntcore.NetworkTableInstance, handles: Sequence[int | None]) -> None:
        """Remove each listener handle in order; an absent (None) handle is simply skipped."""
        for h in handles:
            if h is not None:
                inst.removeListener(h)

    def _release_subscriptions(self) -> None:
        inst = self._inst
        if inst is not None:
            self._remove_listeners(inst, self._handles)
            self._remove_listeners(inst, [self._connection_handle])
            self._connection_handle = None
            self._remove_listeners(inst, self._event_handles)
        # MultiSubscriber has close(); a GenericSubscriber unsubscribes when its last
        # Python reference is dropped, so clearing the list releases both kinds.
        for s in self._subs:
            if hasattr(s, "close"):
                s.close()
        if inst is not None:
            self._remove_listeners(inst, [self._trig.handle])
        self._trig.handle = self._trig.sub = None
        self._handles.clear()
        self._subs.clear()
        self._event_handles.clear()
        self._event_subs.clear()

    def _start_gap(self) -> None:
        if self._admission.has_open_gap:
            return
        gap_start = self._run_time_ms()
        self._clock.synchronized = False
        self._trig.mark_gap_started()
        if self.trigger is not None and self._trig.fired_ms is None:
            self._trig.history_uncertain = True
        for state in self._event_states:
            state.end_coverage(gap_start)
            state.mark_gap_started()
        self._admission.start_gap(gap_start, capture_state.MAX_GAPS)

    def _on_connection(self, ev: ntcore.Event) -> None:
        with self._lock:
            if not self.active:
                return
            if ev.is_(ntcore.EventFlags.kDisconnected):
                self._start_gap()
            elif ev.is_(ntcore.EventFlags.kConnected) and self._admission.has_open_gap:
                self._admission.close_gap(self._run_time_ms())

    def _on_sample_notification(self, ev: ntcore.Event) -> None:
        if not ev.is_(ntcore.EventFlags.kUnpublish):
            self._on_event(ev)
            return
        name = ev.data.name
        with self._lock:
            if self.active and self._buffer.has_topic(name):
                self._buffer.note_unpublish(name, self.local_clock_now_ms())

    def _predates_capture(self, value: Any, buf: deque[Sample]) -> bool:
        """An initial-snapshot value older than the capture, arriving after one already clamped to 0."""
        return self._clock.predates_origin(value.time()) and bool(buf) and buf[-1][0] == 0

    def _on_event(self, ev: ntcore.Event) -> None:
        data = ev.data
        name = data.topic.getName()
        ts = data.value.server_time()  # may be ancient for the initial snapshot; only the update-age bookkeeping uses it
        raw = data.value.value()
        with self._lock:
            if not self.active:
                return
            t_ms = self._sample_time_ms(data.value)
            now_us = nt_clock.local_now_us()
            reliable = self._clock.can_place_update(self._inst, data.value, now_us)
            type_str = data.topic.getTypeString()
            buf = self._buffer.topic_buffer(name, type_str)
            if buf is None:
                return
            if reliable and self._predates_capture(data.value, buf):
                reliable = False
            if not reliable:
                arrival_ms = self.local_clock_now_ms()
                self._buffer.note_unplaceable(name, arrival_ms)
                t_ms = arrival_ms
            self._buffer.note_update_time(name, data.value.time(), ts)
            size = sample_size(raw)
            self._logfile.write(name, type_str, _log_us(t_ms), raw)  # the capture's own timeline, in microseconds
            self._buffer.record(name, (t_ms, raw, reliable), size, self._admission.continuity)

    def _on_trigger_notification(self, ev: ntcore.Event) -> None:
        if not ev.is_(ntcore.EventFlags.kUnpublish):
            self._on_trigger(ev)
            return
        with self._lock:
            if self.active and self.trigger is not None:
                self._trig.on_unpublish(self)

    def _on_rule_notification(self, index: int, ev: ntcore.Event) -> None:
        if not ev.is_(ntcore.EventFlags.kUnpublish):
            self._on_rule(index, ev)
            return
        with self._lock:
            if self.active:
                self._event_states[index].on_unpublish(self)
