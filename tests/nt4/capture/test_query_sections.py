"""Reply-section tests of `capture.query`: health notes, the event page, evidence and the condition check."""

import copy
from pathlib import Path

import pytest
from test_query import make_snapshot, spec

from frc_mcp_nt4.capture import query as capture_query
from frc_mcp_nt4.capture.rules import EventComparison
from frc_mcp_nt4.capture.types import LossWindow, OmittedGaps, TimestampBarrier
from frc_mcp_nt4.errors import NT4Error
from frc_mcp_wpilib.reduce.stats import Shaper
from frc_mcp_wpilib.reduce.types import Gap


def test_health_fields_lists_each_loss_and_gap():
    assert capture_query.health_fields(make_snapshot(), 3) == {}
    snap = make_snapshot(trigger_ms=1.0, trigger_value=1.23456, trigger_type="double", dropped=2, coalesced=3,
                         ignored_topics=4, topic_limit=7, gaps=[[1.04, 2.06], [5.0, None]], omitted=OmittedGaps(1, 9.99))
    out = capture_query.health_fields(snap, 2)
    assert out["trigger_v"] == 1.23 and out["dropped"] == 2 and out["coalesced"] == 3
    assert out["ignored_updates"] == 4 and out["ignored_topics"].startswith("7 topic limit")
    assert out["connection_gaps_ms"] == [[1.0, 2.1], [5.0, None]]
    assert out["omitted_gaps"] == 1 and out["gap_history_unknown_until_ms"] == 10.0


def test_event_section_pages_and_reports_missed_events():
    events = [{"seq": s, "t_ms": s + 0.04, "rule": 0} for s in (3, 4, 5)]
    events[1]["timing_uncertain"] = True
    snap = make_snapshot(events=events, event_sequence=5, event_revision=2, event_rules=[{"topic": "/a"}])
    summary = capture_query.event_section(snap, None, 20)
    assert summary["observed"] == 5 and summary["overwritten"] == 2 and summary["revision"] == 2
    assert "items" not in summary and summary["timing_uncertain_buffered"] == 1
    page = capture_query.event_section(snap, 0, 2)
    assert page["buffered"] == 3 and page["overwritten"] == 2 and page["revision"] == 2 and "note" in page
    assert page["rules"] == [{"topic": "/a"}]  # the cursor form keeps the full rule rows
    assert [e["seq"] for e in page["items"]] == [3, 4] and page["next_after"] == 4
    assert page["remaining"] == 1 and page["missed"] == 2 and page["items"][0]["t_ms"] == 3.0
    done = capture_query.event_section(snap, 5, 2)
    assert done["items"] == [] and done["next_after"] == 5 and done["remaining"] == 0 and done["missed"] == 0
    empty = capture_query.event_section(make_snapshot(event_sequence=2), 1, 5)
    assert empty["missed"] == 1 and empty["next_after"] == 1


def test_counts_only_event_section_is_compact():
    rules = [{"topic": "/a", "op": ">", "observed": 2}, {"topic": "/a", "op": "changed", "observed": 0, "history_uncertain": True}]
    snap = make_snapshot(events=[{"seq": 1, "t_ms": 1.0, "rule": 0}], event_sequence=1, event_rules=rules)
    assert capture_query.event_section(snap, None, 20) == {
        "observed": 1, "rules": [{"observed": 2}, {"observed": 0, "history_uncertain": True}]}


def test_evidence_section_reports_retention_and_update_age():
    snap = make_snapshot(samples={"/a": [(5.0, 1, True), (9.0, 2, True), (1.0, 3, False)], "/b": []},
                         dropped_by_topic={"/a": 2}, coalesced_by_topic={"/a": 1},
                         last_update_time={"/a": (1_000_000, 500)})
    evidence = capture_query.evidence_section(snap, ["/a", "/b"], True, 1_050_000)
    assert evidence["/a"]["retained_ms"] == [5.0, 9.0] and evidence["/a"]["retained_samples"] == 3
    assert evidence["/a"]["unplaceable_updates"] == 1 and evidence["/a"]["dropped"] == 2
    assert evidence["/a"]["last_update"] == {"age_ms": 50.0}
    assert evidence["/b"]["retained_ms"] is None and "unplaceable_updates" not in evidence["/b"]
    assert evidence["/b"]["last_update"]["age_ms"] is None


def test_check_events_after_validates_against_rules_and_sequence():
    with pytest.raises(NT4Error, match="no event rules"):
        capture_query._check_events_after(make_snapshot(), 0)
    snap = make_snapshot(event_rules=[{}], event_sequence=3)
    capture_query._check_events_after(snap, None)
    capture_query._check_events_after(snap, 3)
    for bad in (-1, 4):
        with pytest.raises(NT4Error, match="between 0 and 3"):
            capture_query._check_events_after(snap, bad)


def test_window_bounds_clamp_and_detect_overlapping_outages():
    snap = make_snapshot(gaps=[[100.0, 200.0]], end_ms=500.0)
    assert capture_query._window_bounds(snap, spec()) == (0.0, 500.0, True)
    assert capture_query._window_bounds(snap, spec(since_ms=250.0, until_ms=900.0)) == (250.0, 500.0, False)
    assert capture_query._window_bounds(make_snapshot(gaps=[[100.0, None]], end_ms=500.0), spec(since_ms=300.0))[2]
    assert capture_query._window_bounds(make_snapshot(omitted=OmittedGaps(1, 50.0)), spec(since_ms=40.0))[2]
    assert not capture_query._window_bounds(make_snapshot(gaps=[[1.0, 2.0]]), spec(since_ms=600.0, until_ms=700.0))[2]


def test_log_fields_only_when_logging():
    assert capture_query._log_fields(make_snapshot()) == {}
    out = capture_query._log_fields(make_snapshot(log_path=Path("x.wpilog"), log_bytes=5, log_truncated=True,
                                                  log_error="disk"))
    assert out == {"log_bytes": 5, "log_truncated": True, "log_error": "disk"}  # the path is not repeated on every query
    dropped = capture_query._log_fields(make_snapshot(log_path=Path("x.wpilog"), log_dropped_nonfinite_time=3))
    assert dropped["dropped_nonfinite_time"] == 3
    assert "dropped_nonfinite_time" not in capture_query._log_fields(make_snapshot(log_path=Path("x.wpilog")))


def test_unaffected_constants_skip_caveated_results():
    out = {"/a": {"const": 1}, "/b": {"const": 2, "gap_affected": True}, "/c": {"v": 3},
           "/d": {"const": 4, "order_affected": True}}
    assert capture_query._unaffected_constants(out) == {"/a": 1}
    # each flag in the shared tuple, as spelled by its setter, suppresses the constant
    flagged = {f"/{i}": {"const": i, flag: True} for i, flag in enumerate(capture_query.CAVEAT_FLAGS)}
    assert capture_query._unaffected_constants({**flagged, "/a": {"const": 1}}) == {"/a": 1}


def test_caveat_flags_are_exactly_the_keys_the_setters_emit():
    """The tuple `_unaffected_constants` reads must hold every `*_affected` key the setters write."""
    emitted: set[str] = set()
    rows = [(0.0, 1.0), (1.0, 2.0), (0.5, 3.0)]
    for window in (capture_query.Window(gaps=[Gap(0.4, 0.6)]), capture_query.Window(evicted_frontier=1.0)):
        emitted |= {k for k in capture_query.reduce_rows(rows, Shaper("double", None, 4), "summary", 20, window=window)
                    if k.endswith("_affected")}
    snap = make_snapshot(timestamp_barriers={"/a": TimestampBarrier(0, 0.0, 1)},
                         drop_barriers={"/a": [LossWindow(0, 20.0, 25.0)]},
                         topic_barriers={"/a": [LossWindow(0, 30.0, 35.0)]})
    notes = {"const": 1}
    capture_query._loss_notes(snap, "/a", "summary", 1, notes)
    emitted |= {k for k in notes if k.endswith("_affected")}
    assert set(capture_query.CAVEAT_FLAGS) == emitted == {
        "gap_affected", "order_affected", "timestamp_affected", "drop_affected", "topic_affected"}


def test_build_reply_assembles_sections_and_collapses_summary_constants():
    snap = make_snapshot(
        samples={"/a": [(1.0, 5.0, True)], "/b": [(1.0, 1.0, True), (2.0, 2.0, True)]},
        types={"/a": "double", "/b": "double"}, dropped=1)
    reply = capture_query.build_reply(snap, spec(reduce="summary", include_evidence=True, clock_synchronized=True,
                                                 now_us=1))
    assert reply["sampling"]["mode"] == "sampled" and reply["dropped"] == 1
    assert "topics" not in reply and "rate_hz" not in reply
    assert reply["constants"] == {"/a": 5.0} and list(reply["data"]) == ["/b"]
    assert set(reply["evidence"]) == {"/a", "/b"}
    assert "events" not in reply and "omitted_topics" not in reply


def test_build_reply_caps_topics_and_pages_events_and_compares():
    names = [f"/t{i}" for i in range(5)]
    rule = {"topic": "/t0", "op": "changed", "observed": 2}
    events = [{"seq": 1, "rule": 0, "t_ms": 1.0}, {"seq": 2, "rule": 0, "t_ms": 2.0}]
    snap = make_snapshot(samples={n: [(1.0, 1.0, True)] for n in names}, types={n: "double" for n in names},
                         event_rules=[rule, {**rule}], events=events, event_sequence=2, all_changes=True,
                         event_coverage=[[[0.0, None]], [[0.0, None]]])
    reply = capture_query.build_reply(snap, spec(max_topics=2, events_after=0, max_events=1,
                                                 event_comparison=EventComparison(start_rule=0, end_rule=1)))
    assert list(reply["data"]) == ["/t0", "/t1"] and reply["omitted_topics"] == 3
    assert reply["sampling"]["mode"] == "all_changes"
    assert reply["events"]["next_after"] == 1 and "event_comparison" in reply


def test_build_reply_rejects_invalid_events_after():
    with pytest.raises(NT4Error):
        capture_query.build_reply(make_snapshot(), spec(events_after=1))


def test_build_reply_leaves_snapshot_info_unchanged():
    snap = make_snapshot(samples={"/a": [(1.0, 1.0, True)]}, types={"/a": "double"})
    before = copy.deepcopy(snap.info)
    reply = capture_query.build_reply(snap, spec())
    assert snap.info == before
    assert set(reply) == {"id", "active", "elapsed_s", "sampling", "data"}
