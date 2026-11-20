"""Connection lifecycle, configuration, tool listing and CLI tests against a real in-process ntcore server."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import ntcore
import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
from nt_helpers import (
    NT4_MAIN,
    PORT,
    j,
    publish,
    read_and_discard,
    toolset_listing,
    wait_for_client_sync,
    wait_for_tool,
    wait_until,
    watch_idle,
)

from frc_mcp_nt4.errors import NT4Error
from frc_mcp_nt4.session import session
from frc_mcp_nt4.spec import SPEC

mcp, _ = SPEC.load()

pytestmark = pytest.mark.usefixtures("isolate_capture_logs", "allow_test_writes")


async def test_connect_fails_with_actionable_error():
    async with Client(mcp) as c:
        with pytest.raises(ToolError, match="No NT4 server"):
            await c.call_tool("nt4_connection", {"host": "127.0.0.1", "port": 5898, "wait_s": 0.5})


async def test_stdio_client_can_use_a_read_only_robot(nt_server):
    root = Path(__file__).resolve().parents[3]
    pub = publish(nt_server, "/stdio/angle", "double", ntcore.Value.makeDouble(42))
    transport = StdioTransport(
        command=sys.executable,
        args=["-c", NT4_MAIN],
        cwd=str(root),
        env={
            "NT4_HOST": "127.0.0.1",
            "NT4_PORT": str(PORT),
            "NT4_READ_ONLY": "1",
            "NT4_ALLOW_WRITES": "0",
            "NT4_WRITE_PREFIXES": "",
        },
    )
    async with Client(transport) as stdio:
        topics = j(await stdio.call_tool("nt4_list_topics", {"prefix": "/stdio/"}))
        assert topics["topics"] == {"/stdio/angle": "double"}
        values = j(await stdio.call_tool("nt4_get_values", {"topics": ["/stdio/angle"]}))
        assert values["/stdio/angle"]["v"] == 42
        listed = {t.name for t in await stdio.list_tools()}
        assert not {"nt4_set_value", "nt4_release_publisher"} & listed  # read-only: the write tools are not even listed
        with pytest.raises(ToolError, match="Unknown tool"):
            await stdio.call_tool("nt4_set_value", {"topic": "/stdio/angle", "value": 0})
    del pub


async def test_student_prompts_discover_topics_and_remain_read_only(client):
    names = {prompt.name for prompt in await client.list_prompts()}
    assert {"diagnose_mechanism", "diagnose_connection", "compare_robot_signals"} <= names
    mechanism = await client.get_prompt("diagnose_mechanism", {"mechanism": 'arm "lift"'})
    text = mechanism.messages[0].content.text
    assert 'arm \\"lift\\"' in text
    assert "discover" in text.lower() and "Do not publish" in text
    comparison = await client.get_prompt("compare_robot_signals", {"first": "button", "second": "arm position"})
    assert "Discover actual topics" in comparison.messages[0].content.text
    assert "correlation does not prove causation" in comparison.messages[0].content.text
    connection = await client.get_prompt("diagnose_connection")
    assert "Do not scan" in connection.messages[0].content.text


async def test_cli_diagnoses_real_nt4_without_publishing(nt_server, monkeypatch):
    monkeypatch.setenv("NT4_HOST", "127.0.0.1")
    monkeypatch.setenv("NT4_PORT", str(PORT))
    before = {topic.name for topic in nt_server.getTopicInfo("") if not topic.name.startswith("/.")}
    result = subprocess.run(
        [sys.executable, "-c", NT4_MAIN, "check", "--timeout", "3"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "Connected. Discovered" in result.stdout
    assert "no values published" in result.stdout
    assert "writes are enabled" in result.stdout
    after = {topic.name for topic in nt_server.getTopicInfo("") if not topic.name.startswith("/.")}
    assert after == before


async def test_concurrent_connects_share_one_connection(nt_server):
    async with Client(mcp) as c:
        results = await asyncio.gather(*[
            c.call_tool("nt4_connection", {"host": "127.0.0.1", "port": PORT, "wait_s": 3})
            for _ in range(3)
        ])
        assert all(j(result)["connected"] for result in results)
        assert session.connected


async def test_concurrent_waits_do_not_block_each_other_or_allow_disconnect(client):
    waits = [asyncio.create_task(client.call_tool("nt4_wait_for", {
        "topic": f"/concurrent/{i}", "op": "exists", "wait_s": 1.2,
    })) for i in range(2)]
    assert await wait_until(lambda: session.active_operations == 2, timeout=1)
    with pytest.raises(ToolError, match="Cannot disconnect"):
        await client.call_tool("nt4_connection", {"action": "disconnect"})
    assert all(not j(result)["met"] for result in await asyncio.gather(*waits))
    assert not j(await client.call_tool("nt4_connection", {"action": "disconnect"}))["connected"]


async def test_requires_connection():
    async with Client(mcp) as c:
        with pytest.raises(ToolError, match="nt4_connection"):
            await c.call_tool("nt4_list_topics", {})


async def test_lazy_connect_from_env(nt_server, monkeypatch):
    monkeypatch.setenv("NT4_HOST", "127.0.0.1")
    monkeypatch.setenv("NT4_PORT", str(PORT))
    keep = publish(nt_server, "/lazy/x", "double", ntcore.Value.makeDouble(4.0))
    async with Client(mcp) as c:
        r = j(await c.call_tool("nt4_get_values", {"topics": ["/lazy/x"]}))  # no nt4_connection call
        assert r["/lazy/x"]["v"] == 4.0
        inst = session.inst
        assert j(await c.call_tool("nt4_status"))["connected"] is True  # status does not reconnect
        assert session.inst is inst
    del keep


async def test_tool_annotations_describe_state_changes(client):
    tools = {tool.name: tool for tool in await client.list_tools()}
    for name, read_only, destructive, idempotent in (
        ("nt4_connection", False, True, False), ("nt4_set_value", False, True, False),
        ("nt4_release_publisher", False, True, False),
        ("nt4_capture_start", False, False, False), ("nt4_watch", False, False, False),
        ("nt4_capture_query", True, False, None),  # a pure snapshot read: the shared READ_ONLY shape
    ):
        annotations = tools[name].annotations
        assert annotations.read_only_hint is read_only and annotations.destructive_hint is destructive
        assert annotations.idempotent_hint is idempotent and annotations.open_world_hint is True
    for name in ("nt4_get_values", "nt4_get_widgets"):
        assert tools[name].annotations.read_only_hint is True
    # nt4_status answers from our own state, so it also says no outside world is involved
    status = tools["nt4_status"].annotations
    assert status.read_only_hint is True and status.open_world_hint is False


async def test_connect_without_host_returns_hint():
    async with Client(mcp) as c:
        r = j(await c.call_tool("nt4_connection"))
        assert r["connected"] is False and "host" in r["hint"]


async def test_connect_same_server_is_idempotent_and_other_host_reconnects(client):
    inst = session.inst
    assert j(await client.call_tool("nt4_connection", {"host": "127.0.0.1", "port": PORT}))["connected"] is True
    assert session.inst is inst
    with pytest.raises(ToolError, match="action='disconnect' first"):
        await client.call_tool("nt4_connection", {"host": "127.0.0.1", "port": PORT, "identity": "other"})
    assert session.inst is inst  # same server: an identity change is refused, not silently ignored


async def test_idle_disconnect_only_when_nothing_would_be_lost(client, nt_server, monkeypatch):
    monkeypatch.setenv("NT4_IDLE_SECONDS", "0.3")
    fired = watch_idle(session)  # so a timer that never fires cannot pass this test either
    await client.call_tool("nt4_set_value", {"topic": "/idle/x", "value": 1, "type_str": "int"})
    # deliberate quiet window: the idle timeout (NT4_IDLE_SECONDS=0.3) must fire and be refused while we hold a publisher
    await asyncio.sleep(0.8)
    assert fired and session.connected  # it ran, and kept us connected because we hold a publisher
    await client.call_tool("nt4_release_publisher", {"topic": "/idle/x"})
    assert await wait_until(lambda: session._inst is None, timeout=8)  # idle and empty: freed


async def test_survives_server_restart(client, nt_server, monkeypatch, tmp_path):
    """Robot reboot/redeploy: ntcore reconnects; our publishers, captures and reads must recover."""
    monkeypatch.setenv("NT4_HOST", "127.0.0.1")
    monkeypatch.setenv("NT4_PORT", str(PORT))
    keep = publish(nt_server, "/rb/x", "double", ntcore.Value.makeDouble(1.0))
    await client.call_tool("nt4_set_value", {"topic": "/rb/ours", "value": 5, "type_str": "int"})
    started = j(await client.call_tool("nt4_capture_start", {
        "topics": ["/rb/x"], "all_changes": True, "duration_s": 60,
        "event_rules": [{"topic": "/rb/x", "op": ">", "value": 2}],
    }))
    await wait_for_client_sync(nt_server)
    keep.set(ntcore.Value.makeDouble(3))
    cap = session.get_capture(started["id"])
    assert await wait_until(lambda: bool(cap.snapshot().events))
    assert j(await client.call_tool("nt4_capture_query", {"capture_id": started["id"]}))["events"]["observed"] == 1
    # server goes away
    del keep
    ntcore.NetworkTableInstance.destroy(nt_server)
    assert await wait_until(lambda: not session.connected)
    status = j(await client.call_tool("nt4_status"))  # no args: status only
    assert status["connected"] is False
    assert any(cap["id"] == started["id"] for cap in status["captures"])
    assert j(await client.call_tool("nt4_capture_query", {"capture_id": started["id"]}))["events"]["observed"] == 1
    with monkeypatch.context() as patch:
        patch.setattr("frc_mcp_nt4.session.RECONNECT_WAIT_S", 0.3)
        with pytest.raises(ToolError, match=f"connection to 127.0.0.1:{PORT} was lost"):
            await client.call_tool("nt4_get_values", {"topics": ["/rb/x"]})  # a clear error, not a misleading "not announced"
    during = j(await client.call_tool("nt4_capture_query", {"capture_id": started["id"]}))
    assert during["connection_gaps_ms"][-1][1] is None
    # new server on the same port
    srv2 = ntcore.NetworkTableInstance.create()
    srv2.startServer(persist_filename=str(tmp_path / "nt2.json"), listen_address="127.0.0.1", port3=PORT + 1, port4=PORT)
    try:
        assert await wait_until(lambda: session.connected, timeout=15)
        pub2 = srv2.getTopic("/rb/x").genericPublish("double")
        pub2.set(ntcore.Value.makeDouble(42.0))
        # capture keeps working across the restart
        assert await wait_until(lambda: 42.0 in (session.get_capture(started["id"]).buffer.samples.get("/rb/x") and
                                            [v for _, v, _ in session.get_capture(started["id"]).buffer.samples["/rb/x"]] or []))
        after = j(await client.call_tool("nt4_capture_query", {"capture_id": started["id"]}))
        assert after["connection_gaps_ms"][-1][1] >= after["connection_gaps_ms"][-1][0]
        edges = j(await client.call_tool("nt4_capture_query", {
            "capture_id": started["id"], "topics": ["/rb/x"], "reduce": "edges", "threshold": 20,
        }))["data"]["/rb/x"]
        assert edges.get("rising_ms", []) == [] and edges["gap_affected"]
        if "rising_ms" not in edges:
            assert edges["n"] == 0 and edges["timestamp_affected"]
        # fresh reads work
        r = j(await client.call_tool("nt4_get_values", {"topics": ["/rb/x"]}))
        assert r["/rb/x"]["v"] == 42.0
        # the topic WE published is re-published to the new server by ntcore
        other = ntcore.NetworkTableInstance.create()
        other.startClient4("obs")
        other.setServer("127.0.0.1", PORT)
        sub = other.getTopic("/rb/ours").genericSubscribe()
        assert await wait_until(lambda: sub.get().isValid(), timeout=8)
        assert sub.get().value() == 5
        sub = None  # release the subscriber before destroying its instance
        ntcore.NetworkTableInstance.destroy(other)
        await read_and_discard(client, {"capture_id": started["id"]})
    finally:
        session.disconnect()
        ntcore.NetworkTableInstance.destroy(srv2)


async def test_advertised_schemas_omit_bounds_but_validation_still_enforces_them(client):
    tools = {t.name: t for t in await client.list_tools()}
    cap = tools["nt4_inspect"].input_schema["properties"]["max_rows_per_target"]
    assert "maximum" not in cap and "minimum" not in cap  # the advertised schema is lean...
    with pytest.raises(ToolError, match="max_rows_per_target"):  # ...but validation still enforces the bounds
        await client.call_tool("nt4_inspect", {"what": "clients", "max_rows_per_target": 9999})


def test_core_toolset_hides_capture_and_write_tools():
    core = toolset_listing("core")
    names, watch = core.stdout.strip().splitlines()[:2]
    assert "nt4_watch" in names and "nt4_get_values" in names
    assert not {"nt4_capture_start", "nt4_capture_query", "nt4_set_value", "nt4_release_publisher"} & set(names.split(","))
    assert "nt4_capture_start" not in watch
    full = toolset_listing("full").stdout.strip().splitlines()[0]
    assert {"nt4_capture_start", "nt4_capture_query", "nt4_set_value"} <= set(full.split(","))


def test_unknown_toolset_is_rejected():
    result = subprocess.run([sys.executable, "-c", NT4_MAIN], capture_output=True, text=True, timeout=30,
                            env={**os.environ, "NT4_TOOLSET": "everything"}, check=False)
    assert result.returncode != 0 and "NT4_TOOLSET" in result.stderr and "Traceback" not in result.stderr


async def test_inspect_prefix_connects_lazily(nt_server, monkeypatch):
    monkeypatch.setenv("NT4_HOST", "127.0.0.1")
    monkeypatch.setenv("NT4_PORT", str(PORT))
    keep = publish(nt_server, "/lz/a", "double", ntcore.Value.makeDouble(1))
    async with Client(mcp) as c:  # no nt4_connection first: the prefix expansion must trigger the lazy connect
        result = j(await c.call_tool("nt4_inspect", {"what": "publishers", "target": "/lz/*"}))
    assert list(result["targets"]) == ["/lz/a"]
    del keep


@pytest.mark.parametrize(("host", "message"), [
    ("ws://127.0.0.1", "not a URL"),
    ("wss://robot.local", "not supported"),
    ("https://robot", "not supported"),
    ("127.0.0.1:5810", "put the port in `port`"),
    ("10.0.0.2/nt", "put the port in `port`"),
])
async def test_connect_rejects_malformed_hosts(host, message):
    async with Client(mcp) as c:
        with pytest.raises(ToolError, match=message):
            await c.call_tool("nt4_connection", {"host": host, "wait_s": 0.5})


async def test_connect_explains_the_secure_port_and_bad_client_names():
    async with Client(mcp) as c:
        with pytest.raises(ToolError, match=r"secure \(wss\) port"):
            await c.call_tool("nt4_connection", {"host": "127.0.0.1", "port": 5811, "wait_s": 0.5})
        for name in ("   ", "x" * 65, "bad\nname"):
            with pytest.raises(ToolError, match="client name must be 1 to 64"):
                await c.call_tool("nt4_connection", {"host": "127.0.0.1", "port": 5898, "identity": name, "wait_s": 0.5})


async def test_clients_with_an_at_sign_in_their_name_are_still_labelled_self(nt_server):
    async with Client(mcp) as c:
        await c.call_tool("nt4_connection", {"host": "127.0.0.1", "port": PORT, "identity": "me@school"})
        clients = j(await c.call_tool("nt4_inspect", {"what": "clients"}))
        assert [r["id"] for r in clients["targets"][""]["rows"]].count("(self)") == 1
    other = ntcore.NetworkTableInstance.create()
    other.startClient4("me")  # shares the prefix but is a different client
    other.setServer("127.0.0.1", PORT)
    async with Client(mcp) as c:
        await c.call_tool("nt4_connection", {"host": "127.0.0.1", "port": PORT, "identity": "me@school"})
        entry = (await wait_for_tool(c, "nt4_inspect", {"what": "clients"},
                                     lambda r: any(row["id"].startswith("me@") and row["id"] != "(self)"
                                                   for row in r["targets"][""]["rows"])))["targets"][""]
        ids = [r["id"] for r in entry["rows"]]
        assert ids.count("(self)") == 1 and any(i.startswith("me@") and i != "(self)" for i in ids)
    ntcore.NetworkTableInstance.destroy(other)


def test_bad_nt4_port_is_a_clear_error(monkeypatch):
    from frc_mcp_nt4 import config as nt4_config

    for bad in ("abc", "0", "70000"):
        monkeypatch.setenv("NT4_PORT", bad)
        with pytest.raises(NT4Error, match="NT4_PORT"):
            nt4_config.default_port()
    monkeypatch.setenv("NT4_PORT", "5820")
    assert nt4_config.default_port() == 5820


async def test_a_read_of_topics_that_do_not_exist_says_no_match(client):
    out = j(await client.call_tool("nt4_get_values", {"topics": ["/no/such/topic"], "wait_s": 0.2}))
    assert out["status"] == "no_match" and "not proof" in out["hint"] and "err" in out["/no/such/topic"]


async def test_a_widget_read_of_an_empty_path_says_no_match(client):
    out = j(await client.call_tool("nt4_get_widgets", {"bases": ["/no/such/widget"], "wait_s": 0.2}))
    assert out["status"] == "no_match" and "err" in out["/no/such/widget"]
