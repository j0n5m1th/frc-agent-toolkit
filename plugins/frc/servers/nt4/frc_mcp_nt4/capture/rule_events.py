"""Trigger and event-rule handling (second layer of `Capture`, see capture/state.py): admit an update, evaluate it, and
react. Runs on ntcore listener threads; each method takes the capture lock itself or is called with it held.
"""

import json
from collections.abc import Callable
from typing import Any, NamedTuple

import ntcore

from frc_mcp_core.reply import to_jsonable
from frc_mcp_wpilib.conditions import UNSET
from frc_mcp_wpilib.reduce.segments import inside_outage
from frc_mcp_wpilib.reduce.types import EventRecord
from frc_mcp_wpilib.values import shape

from .. import clock as nt_clock
from .rules import (
    MAX_EVENT_VALUE_BYTES,
    Admission,
    Admitted,
    Evaluation,
    EventState,
    RuleState,
    TriggerState,
)
from .state import _CaptureState

EVENT_VALUE_PRECISION = 4  # decimals kept for a recorded event's value


class _Observation(NamedTuple):
    """An admitted, successfully evaluated update of a trigger or event rule."""

    admitted: Admitted
    type_str: str
    raw: Any
    evaluation: Evaluation


class _RuleEvents(_CaptureState):
    def invalidate_coverage(self, index: int, since: float, until: float | None = None) -> None:
        """Mark rule `index`'s coverage and already-recorded events in [since, until) as untrustworthy."""
        self._event_states[index].invalidate_coverage(since, until)
        for event in self._events:
            if event["rule"] == index and event["t_ms"] >= since and (until is None or event["t_ms"] < until):
                event["timing_uncertain"] = True
        self._event_revision += 1

    # -- trigger and event rules ----------------------------------------------
    def _admit(self, state: RuleState, value: Any, raw: Any) -> Admitted | None:
        """Timestamp admission shared by the trigger and every event rule.

        None means the update must not be evaluated (the state's reject_* hook has recorded why, where relevant).
        """
        now_us = nt_clock.local_now_us()
        if not self._clock.can_place_update(self._inst, value, now_us):
            state.reject_unplaceable(self)
            return None
        if not self._admission.after_latest_gap(value, now_us):
            before_gap = self._admission.before_gap(value, now_us)
            if before_gap is not None:
                state.reject_before_gap(self, before_gap)
            return None
        t_ms = self._sample_time_ms(value)
        if inside_outage(t_ms, self._admission.continuity()):
            return None
        timestamp_us = value.time()
        prior_us = state.last_timestamp_us
        outcome = state.admit_timestamp(timestamp_us)
        if outcome is Admission.PENDING_REARM:
            return None
        if outcome is Admission.OUT_OF_ORDER:
            state.reject_out_of_order(self, t_ms)
            return None
        return Admitted(t_ms, timestamp_us, prior_us)

    def _observe(self, state: RuleState, data: Any) -> _Observation | None:
        """Admit then evaluate one update of the trigger or an event rule (caller holds the lock).
        None means nothing to act on: rejected, finished, or not evaluable (the state recorded why).
        """
        raw = data.value.value()
        type_str = self._known_type(state.key, data.topic)
        admitted = self._admit(state, data.value, raw)
        if admitted is None or state.finished:
            return None
        evaluation = state.evaluate(admitted, type_str, raw, self._clock)
        return None if evaluation is None else _Observation(admitted, type_str, raw, evaluation)

    def _update_rule(self, state: RuleState, ev: ntcore.Event, act: Callable[..., None]) -> _Observation | None:
        """The one skeleton every rule update follows: lock, active check, observe, then let `act` react (the
        `act` callback runs while the lock is held; None means there was nothing to act on).
        """
        with self._lock:
            if not self.active:
                return None
            seen = self._observe(state, ev.data)
            if seen is None:
                return None
            act(state, seen)
            return seen

    def _on_trigger(self, ev: ntcore.Event) -> None:
        trig = self.trigger
        if trig is None:
            return
        seen = self._update_rule(self._trig, ev, self._apply_trigger)
        if seen is not None and seen.evaluation.matched:
            self._schedule_stop(trig.after_s)

    @staticmethod
    def _apply_trigger(state: TriggerState, seen: _Observation) -> None:
        if seen.evaluation.matched:
            state.fire(seen.admitted.t_ms, seen.raw, seen.type_str)

    def _schedule_stop(self, after_s: float) -> None:
        """Hand the post-trigger stop to the loop `start` was given (we are on an ntcore thread here)."""
        loop = self._loop  # set by `start`, which requires the running loop
        if loop is not None:
            loop.call_soon_threadsafe(loop.call_later, after_s, self.stop)

    def _on_rule(self, index: int, ev: ntcore.Event) -> None:
        self._update_rule(self._event_states[index], ev, self._apply_event)

    def _apply_event(self, state: EventState, seen: _Observation) -> None:
        t_ms, evaluation = seen.admitted.t_ms, seen.evaluation
        if evaluation.previous is UNSET:
            state.begin_coverage(t_ms)
        if evaluation.matched:
            self._record_event(state, t_ms, evaluation.current, evaluation.previous, seen.type_str)

    def _record_event(self, state: EventState, t_ms: float, current: Any, previous: Any, type_str: str) -> None:
        self._event_sequence += 1
        state.count += 1
        value = to_jsonable(shape(current, type_str if state.rule.field is None else "", None, EVENT_VALUE_PRECISION))
        item: EventRecord = {"seq": self._event_sequence, "rule": state.index, "t_ms": t_ms}
        if previous is UNSET:
            item["initial"] = True
        if len(json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")) <= MAX_EVENT_VALUE_BYTES:
            item["v"] = value
        else:
            item["value_omitted"] = "value exceeds event size limit"
        self._events.append(item)
