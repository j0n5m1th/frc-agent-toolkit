"""Direct tests for capture/rules: rule models, per-condition state and the admission/unpublish hooks."""

import math
from collections import deque

import pytest
from pydantic import ValidationError

from frc_mcp_nt4.capture.rules import (
    UNPLACEABLE_WAITING,
    UNPUBLISHED_WAITING,
    EventComparison,
    EventRule,
    EventState,
    RuleState,
    Trigger,
    TriggerState,
)
from frc_mcp_wpilib.conditions import UNSET
from frc_mcp_wpilib.reduce.models import MAX_COMPARISON_DELAY_MS
from frc_mcp_wpilib.reduce.segments import Window
from frc_mcp_wpilib.reduce.types import Gap


class FakeCap:
    """The few Capture members the state hooks call."""

    def __init__(self, arrival_ms=100.0, rearm=5_000):
        self.arrival, self.rearm = arrival_ms, rearm
        self.invalidated = []

    def invalidate_coverage(self, index, since, until=None):
        self.invalidated.append((index, since, until))

    def local_clock_now_ms(self):
        return self.arrival

    def rearm_point(self):
        return self.rearm


def event_state():
    return EventState(EventRule(topic="/t", op="changed"), 3)


def trigger_state():
    return TriggerState(Trigger(topic="/t", op="==", value=1), "trigger")


def test_models_validate_their_bounds():
    assert Trigger(topic="/t", op="==", value=1).after_s == 2.0
    with pytest.raises(ValidationError):
        Trigger(topic="/t", op="==", after_s=301)
    with pytest.raises(ValidationError):
        EventRule(topic="", op="==")
    with pytest.raises(ValidationError):
        EventRule(topic="/t", op="~")
    with pytest.raises(ValidationError):
        EventComparison(start_rule=-1, end_rule=0)
    with pytest.raises(ValidationError):
        EventComparison(start_rule=0, end_rule=1, max_delay_ms=MAX_COMPARISON_DELAY_MS + 1)
    assert EventComparison(start_rule=0, end_rule=1).max_pairs == 20


def test_rule_state_mark_gap_started_forgets_history_and_recover_clears_pending():
    state = event_state()
    state.previous, state.last_timestamp_us, state.rearming_order = True, 9, True
    state.mark_gap_started()
    assert state.previous is UNSET and state.after_gap and state.last_timestamp_us is None and not state.rearming_order
    state.error, state.waiting = "boom", "later"
    state.recover()
    assert state.error is None and state.waiting is None and not state.after_gap


def test_fail_records_error_or_waiting_and_marks_history_uncertain():
    state = event_state()
    state.fail(ValueError("bad"), indeterminate=False)
    assert state.error == "bad" and state.waiting is None and state.history_uncertain
    state = event_state()
    state.fail(ValueError("pending"), indeterminate=True)
    assert state.waiting == "pending" and state.error is None and state.previous is UNSET


def test_event_state_index_and_coverage():
    state = event_state()
    assert state.index == 3
    state.end_coverage(10)  # nothing open: no-op
    state.coverage_ms.append(Gap(5.0, None))
    state.end_coverage(2.0)  # never ends before it began
    assert state.coverage_ms[-1] == Gap(5.0, 5.0)
    state.coverage_ms.append(Gap(8.0, None))
    state.end_coverage(20.0)
    assert state.coverage_ms[-1] == Gap(8.0, 20.0)
    state.end_coverage(30.0)  # already closed
    assert state.coverage_ms[-1] == Gap(8.0, 20.0)


def test_event_state_rejection_hooks():
    cap, state = FakeCap(), event_state()
    state.previous = True
    state.reject_unplaceable(cap)
    assert cap.invalidated == [(3, 0, None)]
    assert state.waiting == UNPLACEABLE_WAITING
    assert state.previous is UNSET and state.rearm_after_us == 5_000

    cap, state = FakeCap(), event_state()
    state.reject_before_gap(cap, (10.0, 20.0))
    assert cap.invalidated == [(3, 10.0, 20.0)] and state.out_of_order == 1

    cap, state = FakeCap(), event_state()
    state.reject_out_of_order(cap, 42.0)
    assert cap.invalidated == [(3, 42.0, None)] and state.out_of_order == 1 and state.rearming_order


def test_event_state_unpublish_closes_coverage_or_invalidates_without_a_clock():
    cap, state = FakeCap(arrival_ms=50.0), event_state()
    state.coverage_ms.append(Gap(10.0, None))
    state.on_unpublish(cap)
    assert state.coverage_ms[-1] == Gap(10.0, 50.0) and cap.invalidated == []
    assert state.waiting == UNPUBLISHED_WAITING and state.history_uncertain and state.rearm_after_us == 5_000

    cap, state = FakeCap(arrival_ms=math.inf), event_state()
    state.on_unpublish(cap)
    assert len(cap.invalidated) == 1 and cap.invalidated[0][:2] == (3, 0)


def test_trigger_state_lose_history_also_drops_baseline():
    state = trigger_state()
    state.previous = state.baseline = 7
    state.lose_history()
    assert state.previous is UNSET and state.baseline is UNSET


def test_trigger_unplaceable_only_rearms_before_it_fires():
    cap, state = FakeCap(), trigger_state()
    state.reject_unplaceable(cap)
    assert state.history_uncertain and state.waiting == UNPLACEABLE_WAITING and state.rearm_after_us == 5_000
    fired = trigger_state()
    fired.fired_ms = 10.0
    fired.reject_unplaceable(cap)
    assert fired.history_uncertain and fired.waiting is None and fired.rearm_after_us is None


def test_trigger_before_gap_and_out_of_order_after_firing():
    cap = FakeCap()
    state = trigger_state()
    state.reject_before_gap(cap, (5.0, 6.0))
    assert state.out_of_order == 1 and state.history_uncertain

    fired = trigger_state()
    fired.fired_ms = 10.0
    fired.reject_before_gap(cap, (20.0, 30.0))  # after the firing: its time is unaffected
    assert not fired.history_uncertain
    fired.reject_before_gap(cap, (10.0, 30.0))
    assert fired.history_uncertain

    fired = trigger_state()
    fired.fired_ms = 10.0
    fired.reject_out_of_order(cap, 11.0)
    assert not fired.history_uncertain and not fired.rearming_order
    fired.reject_out_of_order(cap, 9.0)
    assert fired.history_uncertain and fired.out_of_order == 2 and not fired.rearming_order

    unfired = trigger_state()
    unfired.reject_out_of_order(cap, 1.0)
    assert unfired.history_uncertain and unfired.rearming_order and unfired.previous is UNSET


def test_trigger_unpublish_is_ignored_once_fired():
    cap = FakeCap()
    fired = trigger_state()
    fired.fired_ms = 1.0
    fired.on_unpublish(cap)
    assert fired.waiting is None and not fired.history_uncertain
    armed = trigger_state()
    armed.on_unpublish(cap)
    assert armed.waiting == UNPUBLISHED_WAITING and armed.history_uncertain and armed.rearm_after_us == 5_000


def test_base_state_hooks_must_be_overridden():
    # the base is an ABC: only its concrete subclasses are instantiated, and each hook must override it
    assert RuleState.__abstractmethods__ == frozenset(
        {"_evaluate", "reject_unplaceable", "reject_before_gap", "reject_out_of_order", "on_unpublish"})
    with pytest.raises(TypeError):
        RuleState(None, "k")
    state = event_state()
    for call in (lambda: RuleState.reject_unplaceable(state, None),
                 lambda: RuleState.reject_before_gap(state, None, (0, 0)),
                 lambda: RuleState.reject_out_of_order(state, None, 0.0),
                 lambda: RuleState.on_unpublish(state, None)):
        with pytest.raises(NotImplementedError):
            call()


def test_window_normalizes_gap_sequences_and_keeps_live_gap_containers():
    window = Window(gaps=[[1, 2], [3, None]])
    assert window.gaps == (Gap(1, 2), Gap(3, None))
    live = deque([Gap(1, 2)])
    assert Window(gaps=live).gaps is live
