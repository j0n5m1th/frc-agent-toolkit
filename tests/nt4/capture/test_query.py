"""Direct tests for capture/query: snapshotting a Capture and building replies from hand-made snapshots."""


import pytest
from capture_helpers import (
    add_gap,
    append_event,
    append_sample,
    event_state,
    read_events,
    read_gaps,
    seed_samples,
    seed_types,
    set_last_gap,
    trigger_state,
)

from frc_mcp_nt4.capture import Capture, CaptureSpec
from frc_mcp_nt4.capture import query as capture_query
from frc_mcp_nt4.capture.buffer import BufferView
from frc_mcp_nt4.capture.query import QuerySpec, Snapshot
from frc_mcp_nt4.capture.rules import EventRule, Trigger
from frc_mcp_nt4.capture.types import LossWindow, TimestampBarrier
from frc_mcp_nt4.errors import NT4Error
from frc_mcp_wpilib.reduce.types import Gap


def make_snapshot(**overrides):
    buffer_fields = dict(
        samples={}, types={}, dropped=0, coalesced=0, ignored_topics=0, dropped_by_topic={}, evicted_frontiers={},
        drop_barriers={}, topic_barriers={}, timestamp_barriers={}, coalesced_by_topic={}, last_update_time={})
    for name in list(overrides):
        if name in buffer_fields:
            buffer_fields[name] = overrides.pop(name)
    fields = dict(
        info={"id": "c", "active": True, "elapsed_s": 1.0, "topics": ["/a"], "rate_hz": 10.0},
        buffer=BufferView(**buffer_fields),
        rate_hz=10.0, all_changes=False, log_path=None, topic_limit=200, trigger_ms=None, trigger_value=None,
        trigger_type="", units={}, gaps=[], omitted=None,
        log_bytes=0, log_truncated=False, log_error=None, event_sequence=0,
        event_revision=0, events=[], event_rules=[], event_coverage=[], end_ms=1000.0)
    fields.update(overrides)
    return Snapshot(**fields)


def spec(**overrides):
    base = dict(topics=None, reduce="latest", fields=None, since_ms=None, until_ms=None, max_samples=20, precision=4)
    base.update(overrides)
    return QuerySpec(**base)


def new_capture(**kwargs):
    return Capture("c", CaptureSpec(topics=["/a", "/b"], prefix=False, rate_hz=10, all_changes=True, duration_s=10, **kwargs), None)


def test_trigger_status_variants_in_info_fields():
    cap = new_capture(trigger=Trigger(topic="/a", op="==", value=1))
    assert cap.info()["trigger"] == "armed"
    trigger_state(cap).after_gap = True
    assert cap.info()["trigger"] == {"state": "waiting_for_post_gap_value"}
    trigger_state(cap).rearming_order = True
    assert cap.info()["trigger"] == {"state": "waiting_for_ordered_value"}
    trigger_state(cap).waiting = "later"
    assert cap.info()["trigger"] == {"waiting": "later"}
    trigger_state(cap).error = "bad"
    assert cap.info()["trigger"] == {"error": "bad"}
    trigger_state(cap).error = trigger_state(cap).waiting = None
    trigger_state(cap).out_of_order, trigger_state(cap).history_uncertain = 2, True
    info = cap.info()
    assert info["trigger_out_of_order_updates"] == 2 and info["trigger_history_uncertain"]
    trigger_state(cap).fired_ms = 12.34
    info = cap.info()
    assert info["trigger"] == {"fired_ms": 12.3, "timing_uncertain": True}
    assert "trigger_history_uncertain" not in info


def test_info_fields_without_trigger_has_no_trigger_keys():
    info = new_capture().info()
    assert info["id"] == "c" and info["topics"] == ["/a", "/b"] and "trigger" not in info


def test_resolve_window_errors_and_offsets():
    cap = new_capture()
    assert capture_query.resolve_window(cap, 1.0, 2.0, False) == (1.0, 2.0)
    with pytest.raises(NT4Error, match="needs a capture started"):
        capture_query.resolve_window(cap, 1.0, 2.0, True)
    cap = new_capture(trigger=Trigger(topic="/a", op="==", value=1))
    with pytest.raises(NT4Error, match="has not fired"):
        capture_query.resolve_window(cap, 1.0, 2.0, True)
    trigger_state(cap).fired_ms = 100.0
    assert capture_query.resolve_window(cap, -10.0, None, True) == (90.0, None)
    assert capture_query.resolve_window(cap, None, 5.0, True) == (None, 105.0)
    trigger_state(cap).history_uncertain = True
    with pytest.raises(NT4Error, match="uncertain"):
        capture_query.resolve_window(cap, 1.0, 2.0, True)


def test_snapshot_copies_state_and_filters_topics():
    cap = new_capture(event_rules=[EventRule(topic="/a", op="changed")])
    seed_samples(cap, "/a", [(1.0, 5, True)])
    seed_samples(cap, "/b", [(2.0, 6, True)])
    seed_types(cap, {"/a": "double", "/b": "double"})
    add_gap(cap, Gap(3.0, None))
    append_event(cap, {"seq": 1, "rule": 0, "t_ms": 1.0})
    snap = cap.snapshot(["/a"])
    assert list(snap.buffer.samples) == ["/a"] and snap.buffer.types == {"/a": "double", "/b": "double"}
    assert snap.gaps == [Gap(3.0, None)] and snap.event_rules[0]["topic"] == "/a"
    append_sample(cap, "/a", (9.0, 9, True))
    set_last_gap(cap, Gap(read_gaps(cap)[0].start, 4.0))
    read_events(cap)[0]["seq"] = 99
    assert len(snap.buffer.samples["/a"]) == 1 and snap.gaps == [Gap(3.0, None)] and snap.events[0]["seq"] == 1
    assert len(cap.snapshot().buffer.samples) == 2


def test_rule_summary_flags_only_pending_conditions():
    cap = new_capture(event_rules=[EventRule(topic="/a", op="changed")])
    state = event_state(cap, 0)
    assert capture_query.rule_summary(state) == {"topic": "/a", "op": "changed", "observed": 0}
    state.after_gap, state.out_of_order, state.error = True, 2, "bad"
    summary = capture_query.rule_summary(state)
    assert summary["waiting_for_post_gap_value"] and summary["out_of_order_updates"] == 2 and summary["error"] == "bad"


def test_topic_result_latest_ignores_unreliable_samples_and_adds_unit():
    snap = make_snapshot(samples={"/a": [(1.0, 1.0, True), (5.0, 9.0, False)]}, types={"/a": "double"},
                         units={"/a": "m"}, timestamp_barriers={"/a": TimestampBarrier(0, 0.0, 1)})
    result = capture_query.topic_result(snap, "/a", spec(), False)
    assert result["v"] == 1.0 and result["unit"] == "m"
    assert result["timestamp_affected"] and result["unplaceable_updates"] == 1


def test_topic_result_windows_rows_and_reports_loss_notes():
    rows = [(10.0, 1.0, True), (20.0, 2.0, True), (30.0, 3.0, True)]
    snap = make_snapshot(samples={"/a": rows}, types={"/a": "double"}, dropped_by_topic={"/a": 4})
    result = capture_query.topic_result(snap, "/a", spec(reduce="samples", since_ms=15.0, until_ms=25.0), False)
    assert result["evicted_oldest"] == 4
    assert result["v"] == [2.0] and result["t_ms"] == [20.0]

    barrier = {"/a": [LossWindow(0, 25.0, 25.0)]}
    snap = make_snapshot(samples={"/a": rows}, types={"/a": "double"}, drop_barriers=barrier, topic_barriers=barrier)
    result = capture_query.topic_result(snap, "/a", spec(reduce="latest"), False)
    assert result["drop_affected"] and result["topic_affected"]
    assert "older than the dropped update" in result["drop_note"] and "predate the unpublish" in result["topic_note"]
    assert "evicted_oldest" not in result


def test_visible_history_excludes_rows_across_loss_windows_for_timing_reductions():
    rows = [(10.0, 1, True), (30.0, 2, True), (5.0, 3, True), (40.0, 4, False)]
    snap = make_snapshot(samples={"/a": rows}, drop_barriers={"/a": [LossWindow(0, 20.0, 35.0)]})
    history, windows, unplaceable = capture_query._visible_history(snap, "/a", "changes")
    assert history == [(10.0, 1), (5.0, 3)] and windows == ((20.0, 35.0),) and unplaceable == 0
    history, windows, _ = capture_query._visible_history(snap, "/a", "samples")
    assert [h[1] for h in history] == [1, 2, 3] and windows == ()
