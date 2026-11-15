"""Capture, trigger, event-inbox and reducer tests."""

import asyncio
import struct
from pathlib import Path

import ntcore
import pytest
from capture_helpers import (
    event_sequence,
    event_states,
    trigger_ms,
    trigger_state,
    trigger_waiting,
)
from fastmcp.exceptions import ToolError
from nt_helpers import (
    drive,
    j,
    publish,
    publish_schemas,
    read_and_discard,
    wait_for_capture_samples,
    wait_for_capture_tail,
    wait_for_client_sync,
    wait_until,
    watch_idle,
)
from wpiutil.log import DataLogReader

from frc_mcp_nt4.capture import new_log_path
from frc_mcp_nt4.errors import NT4Error
from frc_mcp_nt4.session import session

pytestmark = pytest.mark.usefixtures("isolate_capture_logs", "allow_test_writes")


async def test_capture_summary_changes_samples(client, nt_server):
    started = j(await client.call_tool("nt4_capture_start", {"topics": ["/c/a"], "rate_hz": 50, "all_changes": True}))
    cid = started["id"]
    await drive(nt_server, "/c/a", [1, 1, 2, 3, 3, 4, 5], 0.05)
    await drive(nt_server, "/c/other", [9], 0.01)  # not subscribed: exact scope
    await wait_for_capture_tail(cid, "/c/a", [5])  # the last value is the last one recorded
    q = j(await client.call_tool("nt4_capture_query", {"capture_id": cid}))
    assert list(q["data"]) == ["/c/a"]  # exact scope: /c/other never captured
    s = q["data"]["/c/a"]
    assert s["first"] == 1 and s["last"] == 5 and s["min"] == 1 and s["max"] == 5
    q = j(await client.call_tool("nt4_capture_query", {"capture_id": cid, "reduce": "changes"}))
    assert q["data"]["/c/a"]["v"] == [1, 2, 3, 4, 5]
    q = j(await client.call_tool("nt4_capture_query", {"capture_id": cid, "reduce": "samples", "max_samples": 3}))
    col = q["data"]["/c/a"]
    assert len(col["v"]) == 3 and col["v"][0] == 1 and col["v"][-1] == 5 and col["n"] >= 5
    q = j(await client.call_tool("nt4_capture_query", {"capture_id": cid, "reduce": "latest"}))
    assert q["data"]["/c/a"]["v"] == 5
    stopped = j(await client.call_tool("nt4_capture_stop", {"capture_id": cid}))
    assert stopped["active"] is False and stopped["discarded"] is False
    # stopped captures stay queryable until discarded
    assert j(await client.call_tool("nt4_capture_query", {"capture_id": cid}))["active"] is False
    await read_and_discard(client, {"capture_id": cid})
    with pytest.raises(ToolError, match="No capture"):
        await client.call_tool("nt4_capture_query", {"capture_id": cid})


@pytest.mark.slow
async def test_default_rate_limits_updates(client, nt_server):
    task = asyncio.create_task(drive(nt_server, "/r/x", range(400), 0.005))
    r = j(await client.call_tool("nt4_watch", {"topics": ["/r/x"], "duration_s": 1.0}))
    await task
    assert r["data"]["/r/x"]["n"] <= 15
    assert r["sampling"]["mode"] == "sampled"
    assert "note" not in r["sampling"]


async def test_watch_can_request_all_changes_for_brief_events(client, nt_server):
    pub = nt_server.getTopic("/watch/pulse").genericPublish("boolean", ntcore.PubSubOptions(sendAll=True))
    pub.set(ntcore.Value.makeBoolean(False))
    nt_server.flush()
    watch = asyncio.create_task(client.call_tool("nt4_watch", {
        "topics": ["/watch/pulse"], "duration_s": 1.4, "rate_hz": 1,
        "all_changes": True, "reduce": "changes",
    }))
    # the watch subscribes asynchronously: wait until its capture has the initial value before pulsing
    assert await wait_until(lambda: any(c.buffer.samples.get("/watch/pulse") for c in session.capture_registry._captures.values()))
    pub.set(ntcore.Value.makeBoolean(True))
    nt_server.flush()
    await asyncio.sleep(0.12)  # deliberate: the True must be a brief pulse, not a steady level
    pub.set(ntcore.Value.makeBoolean(False))
    nt_server.flush()
    result = j(await watch)
    assert result["sampling"]["mode"] == "all_changes"
    assert result["data"]["/watch/pulse"]["v"] == [False, True, False]


async def test_constant_topic_summary_is_compact(client, nt_server):
    keep = publish(nt_server, "/k/const", "double", ntcore.Value.makeDouble(3.0))
    r = j(await client.call_tool("nt4_watch", {"topics": ["/k/const"], "duration_s": 0.5}))
    assert r["constants"] == {"/k/const": 3.0} and r["data"] == {}  # constants are listed compactly, not as full summaries
    del keep


async def test_a_struct_capture_log_opens_in_the_log_server_with_its_schemas(client, nt_server):
    from frc_mcp_wpilib.schema import state as schema_state
    from frc_mcp_wpilib.values import shape
    from frc_mcp_wpilog.logfile import LogFile

    keep = publish_schemas(nt_server)
    cw = nt_server.getTopic("/lg/cw").genericPublish("struct:ControlWord")
    cw.set(ntcore.Value.makeRaw((1 << 58).to_bytes(8, "little")))  # enabled
    await wait_for_client_sync(nt_server)
    await client.call_tool("nt4_connection", {"host": "127.0.0.1", "port": 5899})
    assert await wait_until(lambda: any("ControlWord" in name for name, _ in schema_state.entries()))
    started = j(await client.call_tool("nt4_capture_start", {"topics": ["/lg/cw"], "log": True, "all_changes": True}))
    await wait_for_capture_samples(started["id"], "/lg/cw")
    await client.call_tool("nt4_capture_stop", {"capture_id": started["id"]})
    log = LogFile(Path(started["log_file"]))
    log.activate_schemas()
    _, raw = log.rows(["/lg/cw"])["/lg/cw"][0]
    assert shape(raw, "struct:ControlWord", None, 4)["enabled"] is True
    del keep, cw


async def test_capture_log_file(client, nt_server, tmp_path):
    keep = publish(nt_server, "/l/a", "double", ntcore.Value.makeDouble(1))
    started = j(await client.call_tool("nt4_capture_start", {"topics": ["/l/a"], "log": True}))
    assert Path(started["log_file"]).parent == tmp_path / "capture-logs"
    await wait_for_capture_samples(started["id"], "/l/a")
    await read_and_discard(client, {"capture_id": started["id"]})
    reader = DataLogReader(started["log_file"])  # an ordinary WPILOG, as the log server reads
    assert reader.isValid()
    assert [rec.getStartData().name for rec in reader if rec.isStart()] == ["NT:/l/a"]
    del keep


def test_log_limit_preserves_existing_files(tmp_path):
    log_dir = tmp_path / "capture-logs"
    log_dir.mkdir()
    for i in range(20):
        (log_dir / f"nt4-capture-{i:02d}.wpilog").write_text("capture", encoding="utf-8")
    unrelated = log_dir / "other.wpilog"
    unrelated.write_text("keep", encoding="utf-8")

    with pytest.raises(NT4Error, match="log limit"):
        new_log_path("test")
    assert len(list(log_dir.glob("nt4-capture-*.wpilog"))) == 20
    assert unrelated.read_text(encoding="utf-8") == "keep"


async def test_stopped_event_capture_survives_idle_until_discarded(client, nt_server, monkeypatch):
    monkeypatch.setenv("NT4_IDLE_SECONDS", "0.3")
    pub = publish(nt_server, "/inbox/idle", "double", ntcore.Value.makeDouble(0))
    await wait_for_client_sync(nt_server)
    started = j(await client.call_tool("nt4_capture_start", {
        "topics": ["/inbox/idle"], "duration_s": 30,
        "event_rules": [{"topic": "/inbox/idle", "op": ">", "value": 40}],
    }))
    cap = session.get_capture(started["id"])
    fired = watch_idle(session)  # counts the real timer runs, so the two outcomes stay distinguishable
    assert await wait_until(lambda: event_states(cap)[0].previous is False)
    pub.set(ntcore.Value.makeDouble(45))
    assert await wait_until(lambda: event_sequence(cap) == 1)
    await client.call_tool("nt4_capture_stop", {"capture_id": started["id"]})
    stopped = j(await client.call_tool("nt4_capture_query", {"capture_id": started["id"], "events_after": 0}))
    assert not stopped["active"] and stopped["events"]["observed"] == 1
    await asyncio.sleep(0.8)  # deliberate quiet window: the idle timeout (0.3 s) passes; a stopped capture must keep us connected
    assert fired and session.connected  # the timer really ran, and the stopped capture held the connection
    saved = j(await read_and_discard(client, {
        "capture_id": started["id"], "events_after": 0,
    }))
    assert saved["events"]["items"][0]["v"] == 45
    assert await wait_until(lambda: session._inst is None, timeout=8)  # idle and nothing kept: freed
    del pub


async def test_capture_total_memory_cap(client, nt_server, monkeypatch):
    from frc_mcp_nt4.capture import buffer as capture_buffer

    monkeypatch.setattr(capture_buffer, "MAX_TOTAL", 10)
    started = j(await client.call_tool("nt4_capture_start", {"topics": ["/m/a"], "all_changes": True}))
    await drive(nt_server, "/m/a", range(40), 0.01)
    await wait_for_capture_tail(started["id"], "/m/a", [39])
    q = j(await client.call_tool("nt4_capture_query", {"capture_id": started["id"], "reduce": "samples", "max_samples": 500}))
    assert len(q["data"]["/m/a"]["v"]) <= 10 and q["dropped"] > 0
    assert q["data"]["/m/a"]["v"][-1] == 39  # newest kept, oldest evicted


async def test_capture_trigger_records_lead_up_and_aftermath(client, nt_server):
    started = j(await client.call_tool("nt4_capture_start", {
        "topics": ["/tr/v"], "all_changes": True, "duration_s": 30,
        "trigger": {"topic": "/tr/v", "op": ">", "value": 5, "after_s": 0.3}}))
    assert started["trigger"] == "armed"
    cid = started["id"]
    await drive(nt_server, "/tr/v", [1, 2, 3, 6, 7, 8, 9, 10, 11, 12], 0.08)  # fires at 6, stops ~0.3 s later
    q = j(await client.call_tool("nt4_capture_query", {
        "capture_id": cid, "wait_s": 5, "rel_trigger": True, "since_ms": -400, "until_ms": 1000, "reduce": "changes"}))
    assert q["active"] is False and q["trigger"]["fired_ms"] > 0
    assert q["trigger_v"] == 6
    vals = q["data"]["/tr/v"]["v"]
    assert vals[0] in (1, 2, 3) and vals.index(6) > 0  # lead-up kept: samples from before the trigger
    assert 6 in vals and 12 not in vals  # stopped shortly after the trigger, not at end of the drive
    # window is relative to the trigger: nothing before -400 ms
    q2 = j(await client.call_tool("nt4_capture_query", {
        "capture_id": cid, "rel_trigger": True, "since_ms": 100, "reduce": "changes"}))
    assert all(v >= 7 for v in q2["data"]["/tr/v"]["v"])


async def test_trigger_needs_a_crossing_unless_immediate(client, nt_server):
    keep = publish(nt_server, "/tc/v", "double", ntcore.Value.makeDouble(10.0))
    await wait_for_client_sync(nt_server)
    base = {"topics": ["/tc/v"], "all_changes": True, "duration_s": 20}
    a = j(await client.call_tool("nt4_capture_start", {**base, "trigger": {"topic": "/tc/v", "op": ">", "value": 5, "after_s": 0.2}}))
    b = j(await client.call_tool("nt4_capture_start", {**base, "trigger": {"topic": "/tc/v", "op": ">", "value": 5, "after_s": 0.2, "immediate": True}}))
    cap_a, cap_b = session.get_capture(a["id"]), session.get_capture(b["id"])
    # b fires at start; a has evaluated the already-true value (no crossing) and stays armed
    assert await wait_until(lambda: trigger_ms(cap_b) is not None and not cap_b.active and trigger_state(cap_a).previous is True)
    qa = j(await client.call_tool("nt4_capture_query", {"capture_id": a["id"]}))
    qb = j(await client.call_tool("nt4_capture_query", {"capture_id": b["id"]}))
    assert qa["active"] is True and qa["trigger"] == "armed"  # already true: no crossing yet
    assert qb["active"] is False and "fired_ms" in qb["trigger"]  # immediate=True fires at start
    await drive(nt_server, "/tc/v", [1.0, 10.0], 0.15)  # dips below then crosses
    qa = j(await client.call_tool("nt4_capture_query", {"capture_id": a["id"], "wait_s": 3}))
    assert qa["active"] is False and "fired_ms" in qa["trigger"]
    del keep


async def test_trigger_changed_and_struct_field(client, nt_server):
    keep = publish_schemas(nt_server)
    cw = nt_server.getTopic("/tf/cw").genericPublish("struct:ControlWord")
    cw.set(ntcore.Value.makeRaw(bytes(8)))
    await wait_for_client_sync(nt_server)
    s1 = j(await client.call_tool("nt4_capture_start", {"topics": ["/tf/cw"], "all_changes": True, "trigger": {
        "topic": "/tf/cw", "op": "==", "value": True, "field": "enabled", "after_s": 0.1}}))
    assert await wait_until(lambda: trigger_state(session.get_capture(s1["id"])).previous is False)  # evaluated: not enabled yet
    cw.set(ntcore.Value.makeRaw((1 << 58).to_bytes(8, "little")))  # enabled
    q = j(await client.call_tool("nt4_capture_query", {"capture_id": s1["id"], "wait_s": 3}))
    assert q["active"] is False and q["trigger_v"]["enabled"] is True
    n = nt_server.getTopic("/tf/n").genericPublish("double")
    n.set(ntcore.Value.makeDouble(1.0))
    await wait_for_client_sync(nt_server)  # the baseline value must be known before the trigger starts
    s2 = j(await client.call_tool("nt4_capture_start", {"topics": ["/tf/other"], "trigger": {
        "topic": "/tf/n", "op": "changed", "after_s": 0.1}}))
    await wait_for_capture_tail(s2["id"], "/tf/n", [1.0])  # the trigger topic is recorded: the baseline is armed
    n.set(ntcore.Value.makeDouble(2.0))
    # reduce=changes: the 2.0 sample is periodic (10 Hz) and may land after the trigger's 0.1 s stop, so only the baseline
    # can be there; `summary` would then fold the topic into `constants` and out of `data`
    q = j(await client.call_tool("nt4_capture_query", {"capture_id": s2["id"], "wait_s": 3, "reduce": "changes"}))
    assert q["active"] is False and q["trigger_v"] == 2.0
    assert "/tf/n" in q["data"]  # the trigger topic is auto-recorded in exact-name mode
    del keep


async def test_prefix_capture_records_trigger_outside_prefix(client, nt_server):
    trigger = publish(nt_server, "/trigger/outside", "boolean", ntcore.Value.makeBoolean(False))
    sample = publish(nt_server, "/drive/speed", "double", ntcore.Value.makeDouble(2))
    await wait_for_client_sync(nt_server)
    started = j(await client.call_tool("nt4_capture_start", {
        "topics": ["/drive/"], "prefix": True, "all_changes": True,
        "trigger": {"topic": "/trigger/outside", "op": "==", "value": True, "after_s": 0.3},
    }))
    cap = session.get_capture(started["id"])
    assert await wait_until(lambda: cap.trigger_state.previous is False)  # armed on the initial value
    trigger.set(ntcore.Value.makeBoolean(True))
    q = j(await client.call_tool("nt4_capture_query", {"capture_id": started["id"], "wait_s": 3, "reduce": "changes"}))
    assert q["trigger_v"] is True
    assert q["data"]["/trigger/outside"]["v"] == [False, True]
    assert "/drive/speed" in q["data"]
    del trigger, sample


async def test_trigger_validation_and_errors(client, nt_server):
    with pytest.raises(ToolError, match="needs a `value`"):
        await client.call_tool("nt4_capture_start", {"topics": ["/x"], "trigger": {"topic": "/x", "op": ">"}})
    with pytest.raises(ToolError, match="trigger.op\n.*Input should be"):
        await client.call_tool("nt4_capture_start", {"topics": ["/x"], "trigger": {"topic": "/x", "op": "~", "value": 1}})
    keep = publish(nt_server, "/te/s", "string", ntcore.Value.makeString("abc"))
    await wait_for_client_sync(nt_server)
    s = j(await client.call_tool("nt4_capture_start", {"topics": ["/te/s"], "trigger": {"topic": "/te/s", "op": ">", "value": 3}}))
    # the comparison error is recorded once the first sample is evaluated; the capture keeps recording
    assert await wait_until(lambda: "Cannot compare" in str(session.get_capture(s["id"]).info()), timeout=3)
    info = j(await client.call_tool("nt4_capture_query", {"capture_id": s["id"]}))
    assert "Cannot compare" in info["trigger"]["error"] and info["active"] is True  # keeps recording
    with pytest.raises(ToolError, match="has not fired"):
        c2 = j(await client.call_tool("nt4_capture_start", {"topics": ["/te/s"], "trigger": {"topic": "/te/never", "op": "==", "value": 1}}))
        await client.call_tool("nt4_capture_query", {"capture_id": c2["id"], "rel_trigger": True, "since_ms": -10})
    plain = j(await client.call_tool("nt4_capture_start", {"topics": ["/te/s"]}))
    with pytest.raises(ToolError, match="needs a capture started with a `trigger`"):
        await client.call_tool("nt4_capture_query", {"capture_id": plain["id"], "rel_trigger": True})
    del keep


async def test_struct_conditions_rearm_after_schema_arrives(client, nt_server):
    from frc_mcp_wpilib.schema.structs import REGISTRY

    def raw(value):
        return ntcore.Value.makeRaw(struct.pack("<d", value))

    topic = publish(nt_server, "/late/value", "struct:ReviewLate", raw(10))
    await wait_for_client_sync(nt_server)
    started = j(await client.call_tool("nt4_capture_start", {
        "topics": ["/late/value"], "duration_s": 20,
        "trigger": {"topic": "/late/value", "op": ">", "field": "x", "value": 5, "immediate": True, "after_s": 5},
        "event_rules": [{"topic": "/late/value", "op": ">", "field": "x", "value": 5, "immediate": True}],
    }))
    cap = session.get_capture(started["id"])
    assert await wait_until(lambda: trigger_waiting(cap) and event_states(cap)[0].waiting, timeout=3)
    pending = j(await client.call_tool("nt4_capture_query", {"capture_id": cap.id}))
    assert "Cannot decode" in pending["trigger"]["waiting"]
    assert "Cannot decode" in pending["events"]["rules"][0]["waiting"]
    schema = publish(nt_server, "/.schema/struct:ReviewLate", "structschema",
                     ntcore.Value.makeRaw(b"double x"))
    assert await wait_until(lambda: REGISTRY.decode("struct:ReviewLate", struct.pack("<d", 11)) is not None, timeout=3)
    topic.set(raw(11))
    assert await wait_until(lambda: trigger_waiting(cap) is None and event_states(cap)[0].waiting is None, timeout=3)
    assert trigger_ms(cap) is None and event_sequence(cap) == 0
    topic.set(raw(0))
    assert await wait_until(lambda: cap.trigger_state.previous is False and event_states(cap)[0].previous is False)
    topic.set(raw(10))
    assert await wait_until(lambda: trigger_ms(cap) is not None and event_sequence(cap) == 1, timeout=3)
    result = j(await read_and_discard(client, {
        "capture_id": cap.id, "events_after": 0,
    }))
    assert result["events"]["items"][0]["v"] == 10
    del topic, schema
