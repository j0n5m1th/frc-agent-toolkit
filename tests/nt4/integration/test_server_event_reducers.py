"""Integration tests for the event inbox, the reducers and watch, against the real nt4 server."""


import asyncio

import ntcore
import pytest
from capture_helpers import (
    attach_log,
    event_sequence,
    event_states,
    feed_rule,
    trigger_ms,
    trigger_waiting,
)
from fastmcp.exceptions import ToolError
from nt_helpers import (
    drive,
    j,
    publish,
    read_and_discard,
    wait_for_capture_samples,
    wait_for_capture_tail,
    wait_for_client_sync,
    wait_until,
)

from frc_mcp_nt4.capture import CaptureSpec
from frc_mcp_nt4.session import session

pytestmark = pytest.mark.usefixtures("isolate_capture_logs", "allow_test_writes")


async def test_event_inbox_pages_without_losing_events_on_discard(client, nt_server):
    pub = publish(nt_server, "/inbox/current", "double", ntcore.Value.makeDouble(0))
    await wait_for_client_sync(nt_server)
    started = j(await client.call_tool("nt4_capture_start", {
        "topics": ["/inbox/other"], "duration_s": 30,
        "event_rules": [{"topic": "/inbox/current", "op": ">", "value": 40}],
    }))
    cap = session.get_capture(started["id"])
    assert await wait_until(lambda: event_states(cap)[0].previous is False)
    pub.set(ntcore.Value.makeDouble(45))
    assert await wait_until(lambda: event_sequence(cap) == 1)
    pub.set(ntcore.Value.makeDouble(0))
    assert await wait_until(lambda: event_states(cap)[0].previous is False)
    pub.set(ntcore.Value.makeDouble(46))
    assert await wait_until(lambda: event_sequence(cap) == 2)
    q = j(await client.call_tool("nt4_capture_query", {
        "capture_id": started["id"], "events_after": 0, "max_events": 1,
    }))
    assert q["events"]["observed"] == 2 and q["events"]["items"][0]["v"] == 45
    assert q["events"]["remaining"] == 1
    last = j(await read_and_discard(client, {
        "capture_id": started["id"], "events_after": q["events"]["next_after"],
    }))
    assert last["events"]["items"][0]["v"] == 46 and last["events"]["remaining"] == 0
    del pub


async def test_capture_rearms_after_topic_is_unpublished_and_republished(client, nt_server):
    pub = publish(nt_server, "/lifecycle/value", "double", ntcore.Value.makeDouble(0))
    await wait_for_client_sync(nt_server)
    started = j(await client.call_tool("nt4_capture_start", {
        "topics": ["/lifecycle/value"], "all_changes": True, "duration_s": 30,
        "trigger": {"topic": "/lifecycle/value", "op": ">", "value": 5, "after_s": 10},
        "event_rules": [{"topic": "/lifecycle/value", "op": ">", "value": 5}],
    }))
    cap = session.get_capture(started["id"])
    assert await wait_until(lambda: event_states(cap)[0].previous is False and cap.trigger_state.previous is False, timeout=3)
    pub = None
    assert await wait_until(lambda: "/lifecycle/value" in cap.buffer.topic_barriers, timeout=5)
    assert await wait_until(lambda: event_states(cap)[0].waiting and trigger_waiting(cap), timeout=3)

    pub = publish(nt_server, "/lifecycle/value", "double", ntcore.Value.makeDouble(10))
    assert await wait_until(lambda: event_states(cap)[0].waiting is None and trigger_waiting(cap) is None, timeout=5)
    after = j(await client.call_tool("nt4_capture_query", {
        "capture_id": cap.id, "topics": ["/lifecycle/value"], "reduce": "edges", "threshold": 5,
    }))
    assert after["events"]["observed"] == 0 and trigger_ms(cap) is None
    assert after["events"]["rules"][0]["history_uncertain"]
    assert after["data"]["/lifecycle/value"].get("rising_ms", []) == []
    assert after["data"]["/lifecycle/value"]["topic_affected"]

    await wait_for_capture_tail(cap.id, "/lifecycle/value", [10.0])  # the republished value is recorded first
    pub.set(ntcore.Value.makeDouble(0))
    await wait_for_capture_tail(cap.id, "/lifecycle/value", [0.0])  # the 0 is recorded before the rising edge
    pub.set(ntcore.Value.makeDouble(10))
    assert await wait_until(lambda: event_sequence(cap) == 1 and trigger_ms(cap) is not None, timeout=3)
    result = j(await read_and_discard(client, {"capture_id": cap.id, "events_after": 0}))
    assert result["trigger"]["timing_uncertain"] and result["events"]["items"][0]["v"] == 10
    del pub


async def test_capture_stop_reports_last_events_and_log_close_errors(client, nt_server, tmp_path):
    pub = publish(nt_server, "/finish/value", "double", ntcore.Value.makeDouble(0))
    await wait_for_client_sync(nt_server)
    started = j(await client.call_tool("nt4_capture_start", {
        "topics": ["/finish/value"], "duration_s": 20,
        "event_rules": [{"topic": "/finish/value", "op": ">", "value": 5}],
    }))
    cap = session.get_capture(started["id"])
    assert await wait_until(lambda: event_states(cap)[0].previous is False, timeout=3)

    class BrokenLog:
        def close(self):
            raise OSError("log close failed")

    cap.log_path = tmp_path / "capture.wpilog"
    attach_log(cap, BrokenLog())
    feed_rule(cap, 0, 10)
    await client.call_tool("nt4_capture_stop", {"capture_id": cap.id})
    result = j(await client.call_tool("nt4_capture_query", {"capture_id": cap.id}))
    assert result["active"] is False
    assert result["events"]["observed"] == 1
    assert result["log_error"] == "log close failed"
    assert cap.id in session.capture_registry._captures
    del pub


async def test_invalid_query_does_not_stop_recording(client, nt_server):
    pub = publish(nt_server, "/finish/invalid", "double", ntcore.Value.makeDouble(1))
    await wait_for_client_sync(nt_server)
    started = j(await client.call_tool("nt4_capture_start", {"topics": ["/finish/invalid"], "duration_s": 20}))
    cap = session.get_capture(started["id"])
    assert await wait_until(lambda: bool(cap.buffer.samples.get("/finish/invalid")), timeout=3)
    with pytest.raises(ToolError, match="needs `threshold`"):
        await client.call_tool("nt4_capture_query", {"capture_id": cap.id, "reduce": "edges"})
    assert cap.active and cap.id in session.capture_registry._captures
    await client.call_tool("nt4_capture_stop", {"capture_id": cap.id, "discard": True})
    assert cap.id not in session.capture_registry._captures
    del pub


async def test_watch_snapshots_only_after_stopping(client, nt_server, monkeypatch):
    from frc_mcp_nt4.capture import Capture

    pub = publish(nt_server, "/finish/watch", "double", ntcore.Value.makeDouble(1))
    original_run = Capture.execute_query

    def run_after_stop(cap, *args, **kwargs):
        assert not cap.active
        return original_run(cap, *args, **kwargs)

    monkeypatch.setattr(Capture, "execute_query", run_after_stop)
    await client.call_tool("nt4_watch", {"topics": ["/finish/watch"], "duration_s": 0.3})
    del pub


async def test_large_integer_conditions_remain_exact_through_nt4(client, nt_server):
    base = 2**53
    pub = publish(nt_server, "/precise/count", "int", ntcore.Value.makeInteger(base))
    await wait_for_client_sync(nt_server)
    unmatched = j(await client.call_tool("nt4_wait_for", {
        "topic": "/precise/count", "op": "==", "value": base + 1, "wait_s": 0.3,
    }))
    assert not unmatched["met"] and unmatched["v"] == base
    started = j(await client.call_tool("nt4_capture_start", {
        "topics": ["/precise/count"], "duration_s": 10, "all_changes": True,
        "event_rules": [{"topic": "/precise/count", "op": "==", "value": base + 1, "immediate": True}],
    }))
    cap = session.get_capture(started["id"])
    await wait_for_capture_samples(started["id"], "/precise/count")
    initial = j(await client.call_tool("nt4_capture_query", {"capture_id": started["id"]}))
    assert initial["events"]["observed"] == 0
    pub.set(ntcore.Value.makeInteger(base + 1))
    assert await wait_until(lambda: event_sequence(cap) == 1)
    result = j(await read_and_discard(client, {
        "capture_id": started["id"], "events_after": 0,
    }))
    assert result["events"]["observed"] == 1 and result["events"]["items"][0]["v"] == base + 1
    del pub


async def test_repeated_nan_does_not_flood_the_event_inbox(client, nt_server):
    pub = publish(nt_server, "/inbox/nan", "double", ntcore.Value.makeDouble(float("nan")))
    await wait_for_client_sync(nt_server)
    started = j(await client.call_tool("nt4_capture_start", {
        "topics": ["/inbox/nan"], "duration_s": 10,
        "event_rules": [{"topic": "/inbox/nan", "op": "changed"}],
    }))
    cap = session.get_capture(started["id"])
    assert await wait_until(lambda: event_states(cap)[0].previous is not None)
    for _ in range(4):
        pub.set(ntcore.Value.makeDouble(float("nan")))
        await asyncio.sleep(0.15)  # deliberate quiet window: repeated NaNs must not produce events
    result = j(await client.call_tool("nt4_capture_query", {"capture_id": started["id"]}))
    assert result["events"]["observed"] == 0
    pub.set(ntcore.Value.makeDouble(1))
    assert await wait_until(lambda: event_sequence(cap) == 1)
    pub.set(ntcore.Value.makeDouble(float("nan")))
    assert await wait_until(lambda: event_sequence(cap) == 2)
    result = j(await read_and_discard(client, {
        "capture_id": started["id"], "events_after": 0,
    }))
    assert result["events"]["observed"] == 2
    assert [item["v"] for item in result["events"]["items"]] == [1, "nan"]
    del pub


async def test_capture_compares_observed_events_and_calculates_time_weighted_mean(client, nt_server):
    arm = publish(nt_server, "/analysis/arm", "double", ntcore.Value.makeDouble(0))
    current = publish(nt_server, "/analysis/current", "double", ntcore.Value.makeDouble(0))
    await wait_for_client_sync(nt_server)
    started = j(await client.call_tool("nt4_capture_start", {
        "topics": ["/analysis/arm"], "all_changes": True,
        "event_rules": [
            {"topic": "/analysis/arm", "op": ">=", "value": 45},
            {"topic": "/analysis/current", "op": ">", "value": 40},
        ],
    }))
    cap = session.get_capture(started["id"])
    assert await wait_until(lambda: all(state.previous is False for state in event_states(cap)))
    arm.set(ntcore.Value.makeDouble(50))
    await asyncio.sleep(0.2)  # deliberate minimum gap between the two sets: the delay between the two events must be at least 100 ms
    current.set(ntcore.Value.makeDouble(45))
    assert await wait_until(lambda: event_sequence(cap) == 2)
    result = j(await read_and_discard(client, {
        "capture_id": started["id"], "reduce": "time_weighted",
        "event_comparison": {"start_rule": 0, "end_rule": 1},
    }))
    assert result["event_comparison"]["matched"] == 1
    pair = result["event_comparison"]["pairs"][0]
    assert pair["start_ms"] < pair["end_ms"]  # ordering: the arm event precedes the current event
    assert pair["delay_ms"] >= 100  # the 0.2 s sleep is a minimum gap; scheduling can only widen it
    assert result["data"]["/analysis/arm"]["method"] == "previous_value_hold"
    del arm, current


async def test_sample_times_use_value_timestamps_not_batched_arrival(client, nt_server):
    started = j(await client.call_tool("nt4_capture_start", {"topics": ["/ts/p"], "all_changes": True, "rate_hz": 10}))
    pub = nt_server.getTopic("/ts/p").genericPublish("double")
    pub.set(ntcore.Value.makeDouble(0.0))
    await wait_for_capture_tail(started["id"], "/ts/p", [0.0])
    pub.set(ntcore.Value.makeDouble(1.0))
    await asyncio.sleep(0.15)  # deliberate: the pulse width under test
    pub.set(ntcore.Value.makeDouble(0.0))
    await wait_for_capture_tail(started["id"], "/ts/p", [0.0, 1.0, 0.0])
    q = j(await read_and_discard(client, {"capture_id": started["id"], "reduce": "changes"}))
    col = q["data"]["/ts/p"]
    assert col["v"] == [0.0, 1.0, 0.0]
    assert 120 <= col["t_ms"][2] - col["t_ms"][1] <= 180, col["t_ms"]


async def test_edges_reducer_interpolates_crossings(client, nt_server):
    started = j(await client.call_tool("nt4_capture_start", {"topics": ["/e/v"], "all_changes": True}))
    values = [0.0, 2.0, 6.0, 8.0, 3.0, 1.0, 7.0, 9.0, 0.0]
    await drive(nt_server, "/e/v", values, 0.1)
    await wait_for_capture_tail(started["id"], "/e/v", values)
    q = j(await client.call_tool("nt4_capture_query", {"capture_id": started["id"], "reduce": "edges", "threshold": 5}))
    d = q["data"]["/e/v"]
    assert d["threshold"] == 5 and len(d["rising_ms"]) == 2 and len(d["falling_ms"]) == 2
    r0, f0, r1, f1 = d["rising_ms"][0], d["falling_ms"][0], d["rising_ms"][1], d["falling_ms"][1]
    assert r0 < f0 < r1 < f1  # alternating, in time order
    assert 250 < r1 - r0 < 600  # the crossing updates are 4 samples (0.4 s) apart
    # needs a threshold for numeric topics
    with pytest.raises(ToolError, match="needs `threshold`"):
        await client.call_tool("nt4_capture_query", {"capture_id": started["id"], "reduce": "edges"})
    await read_and_discard(client, {"capture_id": started["id"]})


async def test_edges_boolean_default_and_non_numeric_error(client, nt_server):
    keep = publish(nt_server, "/e/s", "string", ntcore.Value.makeString("x"))
    pub = nt_server.getTopic("/e/b").genericPublish("boolean")
    pub.set(ntcore.Value.makeBoolean(False))
    started = j(await client.call_tool("nt4_capture_start", {"topics": ["/e/b", "/e/s"], "all_changes": True}))
    seen = [False]
    await wait_for_capture_tail(started["id"], "/e/b", seen)  # the initial False must be recorded before any change
    for v in (True, False, True):
        pub.set(ntcore.Value.makeBoolean(v))
        seen.append(v)
        await wait_for_capture_tail(started["id"], "/e/b", seen)  # each change is recorded before the next is sent
    q = j(await client.call_tool("nt4_capture_query", {"capture_id": started["id"], "reduce": "edges", "topics": ["/e/b"]}))
    d = q["data"]["/e/b"]
    assert len(d["rising_ms"]) == 2 and len(d["falling_ms"]) == 1 and d["threshold"] == 0.5
    with pytest.raises(ToolError, match="numeric or boolean"):
        await client.call_tool("nt4_capture_query", {"capture_id": started["id"], "reduce": "edges", "topics": ["/e/s"]})
    del keep


async def test_durations_reducer_counts_only_complete_runs(client, nt_server):
    started = j(await client.call_tool("nt4_capture_start", {"topics": ["/du/m"], "all_changes": True}))
    pub = nt_server.getTopic("/du/m").genericPublish("string")
    names = []
    for name, secs in (("a", 0.1), ("b", 0.3), ("a", 0.2), ("b", 0.3), ("a", 0.1)):
        pub.set(ntcore.Value.makeString(name))
        names.append(name)
        await asyncio.sleep(secs)  # deliberate: each run's length is what the reducer measures
    await wait_for_capture_tail(started["id"], "/du/m", names)
    q = j(await client.call_tool("nt4_capture_query", {"capture_id": started["id"], "reduce": "durations"}))
    d = q["data"]["/du/m"]
    assert d["runs_seen"] == 5 and d["complete_runs"] == 3  # first and last runs are partial
    assert d["states"]["b"]["runs"] == 2 and 220 <= d["states"]["b"]["mean_ms"] <= 380
    assert d["states"]["a"]["runs"] == 1 and 120 <= d["states"]["a"]["mean_ms"] <= 280
    short = j(await client.call_tool("nt4_watch", {"topics": ["/du/m"], "duration_s": 0.3, "reduce": "durations"}))
    assert "need at least one full run" in short["data"]["/du/m"]["note"]


async def test_wide_capture_is_capped_with_valid_json(client, nt_server):
    keep = [publish(nt_server, f"/wide/t{i:02d}", "double", ntcore.Value.makeDouble(float(i))) for i in range(30)]
    await wait_for_client_sync(nt_server, timeout=0.5)  # the client only reads values for the first ~24 topics until asked, so waiting for all of them would time out (5 s)
    s = j(await client.call_tool("nt4_capture_start", {"topics": ["/wide/"], "prefix": True, "duration_s": 20}))
    await wait_for_capture_samples(s["id"], *[f"/wide/t{i:02d}" for i in range(30)], timeout=8)
    q = j(await client.call_tool("nt4_capture_query", {"capture_id": s["id"], "max_topics": 10}))
    assert len(q["constants"]) == 10 and q["omitted_topics"] == 20  # valid JSON, partial, and says what was left out
    q = j(await client.call_tool("nt4_capture_query", {"capture_id": s["id"], "topics": ["/wide/t29"]}))
    assert q["constants"] == {"/wide/t29": 29.0} and "omitted_topics" not in q
    del keep


def test_failed_capture_start_removes_its_empty_log(tmp_path, monkeypatch):
    monkeypatch.setattr("frc_mcp_nt4.session.new_log_path", lambda cid: tmp_path / f"{cid}.wpilog")

    async def run():
        inst = ntcore.NetworkTableInstance.create()
        monkeypatch.setattr(type(session), "inst", property(lambda self: inst))
        monkeypatch.setattr(ntcore.NetworkTableInstance, "addConnectionListener",
                            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")), raising=False)
        with pytest.raises(RuntimeError, match="boom"):
            session.capture_start(CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, duration_s=5, all_changes=True), log=True)

    asyncio.run(run())
    assert list(tmp_path.iterdir()) == []


async def test_percentiles_and_rate_reducers_work_through_nt4_watch(client, nt_server):
    task = asyncio.create_task(drive(nt_server, "/pr/v", [0, 5, 10, 5, 0, 5, 10], 0.1))
    await asyncio.sleep(0.05)
    spread = j(await client.call_tool("nt4_watch", {"topics": ["/pr/v"], "duration_s": 1.0, "all_changes": True, "reduce": "percentiles"}))
    await task
    stats = spread["data"]["/pr/v"]
    assert stats["min"] <= stats["p50"] <= stats["p95"] <= stats["p99"] <= stats["max"]
    task = asyncio.create_task(drive(nt_server, "/pr/w", [0, 5, 10, 5, 0, 5, 10], 0.1))
    await asyncio.sleep(0.05)
    speed = j(await client.call_tool("nt4_watch", {"topics": ["/pr/w"], "duration_s": 1.0, "all_changes": True, "reduce": "rate"}))
    await task
    assert speed["data"]["/pr/w"]["max_rise_per_s"] > 0 and speed["data"]["/pr/w"]["max_fall_per_s"] < 0
