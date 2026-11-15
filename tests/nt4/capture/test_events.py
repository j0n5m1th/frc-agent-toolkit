"""Deterministic tests for capture event rules, the event inbox and comparisons."""

import ntcore
import pytest
from capture_helpers import (
    advance_clock,
    append_event,
    ask,
    close_last_gap,
    event_query,
    event_state,
    event_states,
    feed_rule,
    feed_update,
    lock,
    rule_update,
    set_clock_origin,
    set_event_sequence,
    set_loop,
    start_gap,
    trigger_ms,
)

from frc_mcp_nt4 import clock as nt_clock
from frc_mcp_nt4.capture import (
    Capture,
    CaptureSpec,
    EventComparison,
    EventRule,
    Trigger,
)
from frc_mcp_nt4.capture import state as capture_state
from frc_mcp_wpilib.errors import WPILibMCPError
from frc_mcp_wpilib.reduce.events import Continuity, compare_events
from frc_mcp_wpilib.reduce.types import Gap


def test_event_inbox_pages_counts_and_cursor_loss(monkeypatch):
    monkeypatch.setattr(capture_state, "MAX_EVENTS", 3)
    cap = Capture("c", CaptureSpec(topics=["/other"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, event_rules=[EventRule(topic="/current", op=">", value=40)]), None)
    for value in (0, 41, 45, 0, 42, 0, 43, 0, 44, 0, 45):
        rule_update(cap, 0, value)
    counts = event_query(cap)
    assert counts["observed"] == 5 and counts["overwritten"] == 2 and "items" not in counts
    first = event_query(cap, 0, 2)
    assert [item["seq"] for item in first["items"]] == [3, 4]
    assert first["missed"] == 2 and first["next_after"] == 4 and first["remaining"] == 1
    second = event_query(cap, first["next_after"], 2)
    assert [item["seq"] for item in second["items"]] == [5]
    assert second["missed"] == 0 and second["remaining"] == 0
    with pytest.raises(WPILibMCPError, match="between 0 and 5"):
        event_query(cap, 6)


def test_event_inbox_revises_previously_paged_uncertain_events(monkeypatch):
    monkeypatch.setattr(nt_clock, "local_now_us", lambda: 5_000_000)
    cap = Capture("c", CaptureSpec(topics=[], prefix=False, rate_hz=10, all_changes=True, duration_s=10, event_rules=[ EventRule(topic="/a", op="==", value=True), EventRule(topic="/b", op="==", value=True), ]), None)
    set_clock_origin(cap, 1_000_000)

    def update(index, value, ms):
        feed_rule(cap, index, value, "boolean", t_us=1_000_000 + ms * 1000)

    update(0, False, 100)
    update(0, True, 300)
    update(1, False, 100)
    update(1, True, 250)
    first = event_query(cap, 0)
    assert first["revision"] == 0 and all("timing_uncertain" not in item for item in first["items"])

    update(0, True, 200)
    after_cursor = event_query(cap, first["next_after"])
    assert after_cursor["revision"] == 1 and after_cursor["items"] == []
    assert after_cursor["rules"][0]["history_uncertain"]
    assert "history_uncertain" not in after_cursor["rules"][1]
    assert after_cursor["timing_uncertain_buffered"] == 1
    assert "timing_uncertain" not in first["items"][0]
    revised = event_query(cap, 0)
    assert revised["items"][0]["timing_uncertain"]
    assert "timing_uncertain" not in revised["items"][1]
    assert revised["observed"] == 2 and revised["rules"][0]["observed"] == 1

    update(0, False, 400)
    update(0, True, 500)
    recovered = event_query(cap, 0)
    assert "waiting_for_ordered_value" not in recovered["rules"][0]
    assert "timing_uncertain" not in recovered["items"][2]
    update(0, False, 450)
    revised_again = event_query(cap, 0)
    assert revised_again["revision"] == 2 and revised_again["timing_uncertain_buffered"] == 2
    assert revised_again["items"][2]["timing_uncertain"]


def test_event_uncertainty_survives_overwrite_and_unplaceable_updates(monkeypatch):
    monkeypatch.setattr(capture_state, "MAX_EVENTS", 1)
    monkeypatch.setattr(nt_clock, "local_now_us", lambda: 5_000_000)
    cap = Capture("c", CaptureSpec(topics=[], prefix=False, rate_hz=10, all_changes=True, duration_s=10, event_rules=[EventRule(topic="/a", op="==", value=True)]), None)
    set_clock_origin(cap, 1_000_000)

    def update(value, timestamp):
        feed_rule(cap, 0, value, "boolean", t_us=timestamp)

    for value, ms in [(False, 100), (True, 200), (False, 300), (True, 400)]:
        update(value, 1_000_000 + ms * 1000)
    update(False, 0)
    result = event_query(cap, 0)
    assert result["missed"] == 1 and result["observed"] == 2
    assert result["rules"][0]["history_uncertain"] and result["revision"] == 1
    assert result["items"][0]["timing_uncertain"] and result["timing_uncertain_buffered"] == 1


def test_out_of_order_update_marks_count_uncertain_even_without_affected_buffered_events(monkeypatch):
    monkeypatch.setattr(nt_clock, "local_now_us", lambda: 5_000_000)
    cap = Capture("c", CaptureSpec(topics=[], prefix=False, rate_hz=10, all_changes=True, duration_s=10, event_rules=[EventRule(topic="/a", op="==", value=True)]), None)
    set_clock_origin(cap, 1_000_000)
    for value, ms in [(False, 100), (True, 200), (False, 400), (True, 300)]:
        feed_rule(cap, 0, value, "boolean", t_us=1_000_000 + ms * 1000)
    result = event_query(cap, 0)
    assert result["rules"][0]["history_uncertain"] and result["revision"] == 1
    assert "timing_uncertain_buffered" not in result
    assert "timing_uncertain" not in result["items"][0]


def test_integer_event_rule_does_not_round_distinct_64_bit_values():
    cap = Capture("c", CaptureSpec(topics=["/count"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, event_rules=[ EventRule(topic="/count", op="==", value=2**53 + 1, immediate=True), ]), None)
    rule_update(cap, 0, 2**53, "int")
    assert event_query(cap)["observed"] == 0
    rule_update(cap, 0, 2**53 + 1, "int")
    events = event_query(cap, 0)
    assert events["observed"] == 1 and events["items"][0]["v"] == 2**53 + 1


@pytest.mark.parametrize(("type_str", "nan_value", "valid_value"), [
    ("double", float("nan"), 1.0),
    ("double[]", [float("nan"), 2.0], [1.0, 2.0]),
])
def test_change_rule_coalesces_repeated_nan_but_keeps_real_changes(type_str, nan_value, valid_value):
    cap = Capture("c", CaptureSpec(topics=["/value"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, event_rules=[ EventRule(topic="/value", op="changed"), ]), None)
    for _ in range(5):
        rule_update(cap, 0, nan_value, type_str)
    assert event_query(cap)["observed"] == 0
    rule_update(cap, 0, valid_value, type_str)
    rule_update(cap, 0, nan_value, type_str)
    assert event_query(cap)["observed"] == 2


@pytest.mark.parametrize("op", [">", "==", "!="])
def test_nan_does_not_rearm_trigger_or_event_comparison(op):
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, trigger=Trigger(topic="/a", op=op, value=40), event_rules=[EventRule(topic="/a", op=op, value=40)]), None)
    set_clock_origin(cap, ntcore._now())
    set_loop(cap)
    true_value, false_value = (40, 0) if op == "==" else (50, 0 if op == ">" else 40)

    def update(value):
        feed_update(cap, "/a", value, "double", sample=False, rule=0, trigger=True)

    update(true_value)
    update(float("nan"))
    assert "NaN" in cap.info()["trigger"]["waiting"]
    assert "NaN" in event_query(cap)["rules"][0]["waiting"]
    update(true_value)
    assert trigger_ms(cap) is None and event_query(cap)["observed"] == 0
    update(false_value)
    update(true_value)
    assert cap.info()["trigger"]["timing_uncertain"] and event_query(cap)["observed"] == 1
    assert event_query(cap)["rules"][0]["history_uncertain"]


def test_indeterminate_rule_count_stays_uncertain_after_recovery():
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, event_rules=[EventRule(topic="/a", op=">", value=1)]), None)
    rule_update(cap, 0, 0)
    rule_update(cap, 0, float("nan"))
    assert event_query(cap)["rules"][0]["history_uncertain"]
    rule_update(cap, 0, 2)
    recovered = event_query(cap)["rules"][0]
    assert recovered["observed"] == 0 and recovered["history_uncertain"]
    assert "waiting" not in recovered
    rule_update(cap, 0, 0)
    rule_update(cap, 0, 2)
    assert event_query(cap)["rules"][0]["observed"] == 1
    assert event_query(cap)["rules"][0]["history_uncertain"]


def test_event_changes_and_disconnect_rearm_do_not_invent_transitions():
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, event_rules=[ EventRule(topic="/a", op=">", value=5, immediate=True), EventRule(topic="/b", op="changed"), ]), None)
    set_clock_origin(cap, ntcore._now())
    rule_update(cap, 0, 7)
    for value in ("a", "b", "b", "c"):
        rule_update(cap, 1, value, "string")
    assert [rule["observed"] for rule in event_query(cap)["rules"]] == [1, 2]
    start_gap(cap)
    rule_update(cap, 0, 1)
    close_last_gap(cap)
    advance_clock()
    rule_update(cap, 0, 7)
    rule_update(cap, 1, "d", "string")
    assert event_query(cap)["observed"] == 3
    rule_update(cap, 0, 1)
    rule_update(cap, 0, 7)
    rule_update(cap, 1, "e", "string")
    assert [rule["observed"] for rule in event_query(cap)["rules"]] == [2, 3]


def test_event_comparison_marks_schema_unavailable_interval_unknown():
    events = [{"seq": 1, "rule": 0, "t_ms": 300}, {"seq": 2, "rule": 1, "t_ms": 500}]
    coverage = [[[0, None]], [[0, 200], [400, None]]]
    result = compare_events(events, 2, [{}, {}], EventComparison(start_rule=0, end_rule=1),
                             Continuity([], None, coverage), 0, 600)
    assert result["unknown"] == 1 and result["matched"] == 0


def test_event_rule_errors_are_reported_and_do_not_create_events():
    with pytest.raises(ValueError, match="needs a `value`"):  # pydantic's ValidationError: FastMCP shows its text
        Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, event_rules=[EventRule(topic="/a", op=">")]), None)
    cap = Capture("c", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, event_rules=[EventRule(topic="/a", op=">", value=1)]), None)
    rule_update(cap, 0, "x" * 10_000, "string")
    summary = event_query(cap, 0)
    assert summary["observed"] == 0 and "too large" in summary["rules"][0]["error"]
    with pytest.raises(WPILibMCPError, match="no event rules"):
        ask(Capture("c2", CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, all_changes=True, duration_s=10), None), None, "latest", events_after=0)


def test_event_comparison_pairs_next_observed_event_and_bounds_output():
    events = [
        {"seq": 1, "rule": 0, "t_ms": 100}, {"seq": 2, "rule": 0, "t_ms": 200},
        {"seq": 3, "rule": 1, "t_ms": 500}, {"seq": 4, "rule": 0, "t_ms": 600},
        {"seq": 5, "rule": 1, "t_ms": 700},
    ]
    rules = [{"topic": "/arm"}, {"topic": "/current"}]
    comparison = EventComparison(start_rule=0, end_rule=1, max_pairs=2)
    result = compare_events(events, 5, rules, comparison, Continuity([], None, [[[0, None]], [[0, None]]]), 0, 1000)
    assert result["matched"] == 3 and result["omitted_pairs"] == 1
    assert [pair["delay_ms"] for pair in result["pairs"]] == [400, 300]
    assert result["delay_ms"] == {"min": 100, "max": 400, "mean": pytest.approx(266.7)}
    assert "reused" in result["note"]


def test_event_comparison_window_uses_locked_event_snapshot(monkeypatch):
    cap = Capture("c", CaptureSpec(topics=[], prefix=False, rate_hz=10, all_changes=True, duration_s=10, event_rules=[ EventRule(topic="/start", op="changed"), EventRule(topic="/end", op="changed"), ]), None)
    append_event(cap, {"seq": 1, "rule": 0, "t_ms": 100})
    set_event_sequence(cap, 1)
    event_state(cap, 0).count = 1
    for state in event_states(cap):
        state.coverage_ms.append(Gap(0, None))
    clock = [200]
    unlocked_clock_reads = []

    def elapsed():
        if not lock(cap).locked():
            unlocked_clock_reads.append(True)
            with lock(cap):
                set_event_sequence(cap, 2)
                event_state(cap, 1).count = 1
                append_event(cap, {"seq": 2, "rule": 1, "t_ms": 300})
            clock[0] = 400
        return clock[0]

    monkeypatch.setattr(cap, "_run_time_ms", elapsed)
    comparison = EventComparison(start_rule=0, end_rule=1)
    first = ask(cap, None, "latest", event_comparison=comparison)
    assert not unlocked_clock_reads
    assert first["events"]["observed"] == 1
    assert first["event_comparison"]["no_following_event_in_window"] == 1

    with lock(cap):
        set_event_sequence(cap, 2)
        event_state(cap, 1).count = 1
        append_event(cap, {"seq": 2, "rule": 1, "t_ms": 300})
        clock[0] = 400
    second = ask(cap, None, "latest", event_comparison=comparison)
    assert second["events"]["observed"] == 2
    assert second["event_comparison"]["pairs"][0]["delay_ms"] == 200


def test_event_comparison_marks_gaps_and_simultaneous_events_unknown():
    events = [
        {"seq": 1, "rule": 0, "t_ms": 100}, {"seq": 2, "rule": 1, "t_ms": 500},
        {"seq": 3, "rule": 0, "t_ms": 600}, {"seq": 4, "rule": 1, "t_ms": 600},
        {"seq": 5, "rule": 0, "t_ms": 700},
    ]
    rules = [{"topic": "/arm"}, {"topic": "/current"}]
    comparison = EventComparison(start_rule=0, end_rule=1)
    result = compare_events(events, 5, rules, comparison, Continuity([[200, 300]], None, [[[0, 200], [400, None]], [[0, 200], [400, None]]]), 0, 1000)
    assert result["unknown"] == 2 and result["matched"] == 0
    assert result["no_following_event_in_window"] == 1
    with pytest.raises(WPILibMCPError, match="overwritten"):
        compare_events(events, 6, rules, comparison, Continuity([], None, [[[0, None]], [[0, None]]]), 0, 1000)
    with pytest.raises(WPILibMCPError, match="different rules"):
        compare_events(events, 5, rules, EventComparison(start_rule=0, end_rule=0), Continuity([], None, [[[0, None]], [[0, None]]]), 0, 1000)


def test_event_comparison_does_not_time_initial_snapshots_as_transitions():
    events = [
        {"seq": 1, "rule": 0, "t_ms": 100, "initial": True},
        {"seq": 2, "rule": 1, "t_ms": 200},
    ]
    result = compare_events(events, 2, [{}, {}], EventComparison(start_rule=0, end_rule=1), Continuity([], None, [[[0, None]], [[0, None]]]), 0, 300)
    assert result["unknown"] == 1 and not result["pairs"]


@pytest.mark.parametrize("uncertain_rule", [0, 1])
def test_event_comparison_rejects_uncertain_events_even_with_coverage(uncertain_rule):
    events = [
        {"seq": 1, "rule": 0, "t_ms": 100},
        {"seq": 2, "rule": 1, "t_ms": 200},
    ]
    events[uncertain_rule]["timing_uncertain"] = True
    result = compare_events(events, 2, [{}, {}], EventComparison(start_rule=0, end_rule=1),
                             Continuity([], None, [[[0, None]], [[0, None]]]), 0, 300)
    assert result["unknown"] == 1 and result["matched"] == 0


@pytest.mark.parametrize(("events", "end_baselines", "gaps", "expected"), [
    ([{"seq": 1, "rule": 0, "t_ms": 100}], [], [], "unknown"),
    ([{"seq": 1, "rule": 0, "t_ms": 100}, {"seq": 2, "rule": 1, "t_ms": 300}], [200], [], "unknown"),
    ([{"seq": 1, "rule": 0, "t_ms": 100}], [100], [], "unknown"),
    ([{"seq": 1, "rule": 0, "t_ms": 100}], [50], [], "no_following_event_in_window"),
    ([{"seq": 1, "rule": 0, "t_ms": 500}], [50], [[200, 300]], "unknown"),
    ([{"seq": 1, "rule": 0, "t_ms": 500}], [50, 350], [[200, 300]], "no_following_event_in_window"),
])
def test_event_comparison_requires_end_rule_baseline_before_each_start(events, end_baselines, gaps, expected):
    start_coverage = [[0, 200], [350, None]] if gaps else [[0, None]]
    result = compare_events(events, len(events), [{}, {}], EventComparison(start_rule=0, end_rule=1),
                             Continuity(gaps, None, [start_coverage, [[value, None] for value in end_baselines]]), 0, 1000)
    assert result[expected] == 1
    assert result["matched"] == 0


def test_query_does_not_claim_missing_end_rule_was_observed():
    cap = Capture("c", CaptureSpec(topics=["/start"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, event_rules=[ EventRule(topic="/start", op=">", value=5), EventRule(topic="/end", op=">", value=5), ]), None)
    rule_update(cap, 0, 0)
    rule_update(cap, 0, 10)
    result = ask(cap, None, "latest", event_comparison=EventComparison(start_rule=0, end_rule=1))
    assert result["event_comparison"]["start_events"] == 1
    assert result["event_comparison"]["unknown"] == 1
    assert result["event_comparison"]["no_following_event_in_window"] == 0


def test_event_near_capture_end_is_not_excluded_by_timestamp_rounding(monkeypatch):
    cap = Capture("c", CaptureSpec(topics=["/start"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, event_rules=[ EventRule(topic="/start", op=">", value=5), EventRule(topic="/end", op=">", value=5), ]), None)
    cap.started = 0
    monkeypatch.setattr(cap, "_sample_time_ms", lambda _value: 0.16)
    rule_update(cap, 0, 0)
    rule_update(cap, 0, 10)
    cap.stopped_at = 0.00017
    result = ask(cap, None, "latest", events_after=0, event_comparison=EventComparison(start_rule=0, end_rule=1))
    assert result["events"]["items"][0]["t_ms"] == 0.2
    assert result["event_comparison"]["start_events"] == 1
    assert result["event_comparison"]["unknown"] == 1
