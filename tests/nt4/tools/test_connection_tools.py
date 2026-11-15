"""Connection tools against a fake session: host/port defaulting, no-op reconnects and error conversion."""

import json

import pytest
from fastmcp.exceptions import ToolError

from frc_mcp_nt4.errors import NT4Error
from frc_mcp_nt4.tools import connection_tools as tc


class FakeSession:
    def __init__(self):
        self.connected = False
        self.host = None
        self.port = None
        self.identity = None
        self.connects = []
        self.touched = 0
        self.disconnects = 0
        self.fail_with = None

    def status(self):
        return {"connected": self.connected, "host": self.host, "port": self.port}

    def note_activity(self):
        self.touched += 1

    async def connect(self, host, port, identity, timeout_s):
        if self.fail_with:
            raise self.fail_with
        self.connects.append((host, port, identity, timeout_s))
        self.connected, self.host, self.port, self.identity = True, host, port, identity
        self.note_activity()  # as the real session does: the transition re-arms the idle timer itself

    async def disconnect_safely(self, wait=False):
        if self.fail_with:
            raise self.fail_with
        self.disconnects += 1
        self.connected = False
        self.note_activity()


@pytest.fixture
def fake(monkeypatch):
    fake = FakeSession()
    monkeypatch.setattr(tc, "session", fake)
    monkeypatch.delenv("NT4_HOST", raising=False)
    monkeypatch.delenv("NT4_PORT", raising=False)
    return fake


def body(result):
    return json.loads(result.content[0].text)


async def test_connect_without_any_host_returns_a_hint(fake):
    out = body(await tc.nt4_connection())
    assert "hint" in out and fake.connects == []


async def test_connect_uses_explicit_host_and_default_port(fake):
    out = body(await tc.nt4_connection(host="10.1.2.2", wait_s=3))
    assert fake.connects == [("10.1.2.2", 5810, tc.config.default_identity(), 3)]
    assert out["connected"] is True and fake.touched == 1


async def test_connect_falls_back_to_env_host(fake, monkeypatch):
    monkeypatch.setenv("NT4_HOST", "robot.local")
    monkeypatch.setenv("NT4_PORT", "5811")
    await tc.nt4_connection()
    assert fake.connects[0][:2] == ("robot.local", 5811)


async def test_connect_again_to_same_endpoint_is_a_noop(fake):
    await tc.nt4_connection(host="127.0.0.1", port=5810)
    await tc.nt4_connection(host="127.0.0.1", port=5810)
    await tc.nt4_connection()  # no args while connected: stay on the current host
    assert len(fake.connects) == 1


async def test_connect_with_same_identity_is_a_noop(fake):
    await tc.nt4_connection(host="127.0.0.1", port=5810, identity="me")
    await tc.nt4_connection(host="127.0.0.1", port=5810, identity="me")
    assert len(fake.connects) == 1


async def test_connect_with_different_identity_asks_to_disconnect_first(fake):
    await tc.nt4_connection(host="127.0.0.1", port=5810, identity="me")
    with pytest.raises(ToolError, match="action='disconnect' first"):
        await tc.nt4_connection(identity="other")
    assert len(fake.connects) == 1


async def test_connect_to_other_port_with_new_identity_reconnects(fake):
    await tc.nt4_connection(host="127.0.0.1", port=5810, identity="me")
    await tc.nt4_connection(host="127.0.0.1", port=5811, identity="other")
    assert [c[2] for c in fake.connects] == ["me", "other"]


async def test_connect_to_other_port_reconnects(fake):
    await tc.nt4_connection(host="127.0.0.1", port=5810)
    await tc.nt4_connection(host="127.0.0.1", port=5811)
    assert [c[1] for c in fake.connects] == [5810, 5811]


async def test_connect_failure_becomes_tool_error(fake):
    fake.fail_with = NT4Error("Timed out connecting.")
    with pytest.raises(ToolError, match="Timed out"):
        await tc.nt4_connection(host="10.0.0.1")


async def test_status_touches_session_and_reports(fake):
    assert body(await tc.nt4_status()) == {"connected": False, "host": None, "port": None}
    assert fake.touched == 1


async def test_disconnect_reports_final_status_and_converts_errors(fake):
    fake.connected = True
    assert body(await tc.nt4_connection(action="disconnect"))["connected"] is False
    fake.fail_with = NT4Error("busy")
    with pytest.raises(ToolError, match="busy"):
        await tc.nt4_connection(action="disconnect")


def test_connection_tool_annotations():
    import asyncio

    from frc_mcp_nt4.app import mcp

    tools = {n: asyncio.run(mcp.get_tool(n)).annotations for n in ("nt4_connection", "nt4_status")}
    assert tools["nt4_status"].read_only_hint is True
    assert tools["nt4_connection"].destructive_hint is True  # it can also disconnect, which drops our publishers


@pytest.mark.parametrize(("host", "port", "match"), [
    ("172.30.0.1", 1740, "1740"),
    ("127.0.0.1", 1740, "Driver Station"),
    ("8.8.8.8", 5810, "not a robot-network address"),
    ("http://10.1.2.2", 5810, "not a URL"),
    ("10.1.2.2/x", 5810, "put the port in `port`"),
    ("a b", 5810, "put the port in `port`"),
])
async def test_a_bad_host_or_the_driver_station_port_is_refused_before_connecting(fake, host, port, match):
    with pytest.raises(ToolError, match=match):
        await tc.nt4_connection(host=host, port=port)
    assert fake.connects == []


async def test_port_1740_from_the_env_default_is_refused_too(fake, monkeypatch):
    monkeypatch.setenv("NT4_HOST", "172.30.0.1")
    monkeypatch.setenv("NT4_PORT", "1740")
    with pytest.raises(ToolError, match="1740"):
        await tc.nt4_connection()
    assert fake.connects == []


@pytest.mark.parametrize("host", ["127.0.0.1", "10.99.99.2", "172.30.0.1", "roborio-9999-frc.local", "localhost", "::1", "fe80::1"])
async def test_the_usual_hosts_are_allowed(fake, host):
    await tc.nt4_connection(host=host)
    assert fake.connects[0][0] == host
