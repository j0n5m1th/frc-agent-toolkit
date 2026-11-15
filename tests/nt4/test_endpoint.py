"""Where the NT4 server may connect: the same rules on every path (the tool, the lazy connect of a read tool, the diagnostic command)."""

import socket

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from frc_mcp_nt4 import diagnostics, endpoint
from frc_mcp_nt4.errors import NT4Error
from frc_mcp_nt4.session import session
from frc_mcp_nt4.spec import SPEC

mcp, _ = SPEC.load()


@pytest.fixture
def no_connection(monkeypatch):
    """Fail the test if anything gets as far as opening a connection."""
    opened: list[tuple] = []

    async def fake(self, host, port, identity, timeout_s):
        opened.append((host, port))

    monkeypatch.setattr(type(session), "_connect", fake)
    session.disconnect()
    yield opened
    session.disconnect()


@pytest.mark.parametrize(("host", "port", "match"), [
    ("172.30.0.1", 1740, "1740"),
    ("8.8.8.8", 5810, "not a robot-network address"),
    ("2002:808:808::1", 5810, "not a robot-network address"),  # 6to4 of 8.8.8.8
    ("2001:db8::1", 5810, "not a robot-network address"),  # documentation
    ("2001:0:4136:e378:8000:63bf:3fff:fdd2", 5810, "not a robot-network address"),  # Teredo
    ("198.18.0.1", 5810, "not a robot-network address"),
    ("192.0.0.8", 5810, "not a robot-network address"),
])
async def test_auto_connect_from_the_env_refuses_what_the_tool_refuses(no_connection, monkeypatch, host, port, match):
    monkeypatch.setenv("NT4_HOST", host)
    monkeypatch.setenv("NT4_PORT", str(port))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=match):
            await client.call_tool("nt4_list_topics", {})  # a read tool: it connects by itself
    assert no_connection == []


async def test_the_diagnostic_command_refuses_port_1740_and_a_public_host(no_connection, capsys):
    assert await diagnostics.diagnose("127.0.0.1", 1740, 1.0) == 1
    assert await diagnostics.diagnose("8.8.8.8", 5810, 1.0) == 1
    err = capsys.readouterr().err
    assert "1740" in err and "not a robot-network address" in err
    assert no_connection == []


@pytest.mark.parametrize("host", ["127.0.0.1", "10.99.99.2", "172.30.0.1", "169.254.1.1", "::1", "fe80::1", "[fe80::1]", "fe80::1%usb0", "fd12::1"])
def test_robot_network_addresses_pass(host):
    endpoint.check_endpoint(host, 5810)


def patch_resolver(monkeypatch, answers):
    def getaddrinfo(host, port, family=0, *args):
        if host not in answers:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 0, "", (a, 0)) for a in answers[host]]

    monkeypatch.setattr(endpoint.socket, "getaddrinfo", getaddrinfo)


async def test_a_name_that_resolves_to_a_public_address_is_refused(no_connection, monkeypatch):
    patch_resolver(monkeypatch, {"8.8.8.8.nip.io": ["8.8.8.8"], "mixed.example": ["10.0.0.2", "8.8.8.8"], "6to4.example": ["2002:808:808::1"]})
    for name in ("8.8.8.8.nip.io", "mixed.example", "6to4.example"):
        with pytest.raises(NT4Error, match="resolves to a public address"):
            await session.connect(name, 5810, "x", 0.1)
    assert no_connection == []


async def test_a_name_that_resolves_to_private_addresses_or_not_at_all_goes_on_to_the_connection(no_connection, monkeypatch):
    patch_resolver(monkeypatch, {"robot.lan": ["10.99.99.2"], "both.lan": ["127.0.0.1", "::1"]})
    for name in ("robot.lan", "both.lan", "roborio-9999-frc.local"):  # the last one does not resolve here: mDNS is the connection's job
        await session.connect(name, 5810, "x", 0.1)
    assert [h for h, _ in no_connection] == ["robot.lan", "both.lan", "roborio-9999-frc.local"]


async def test_a_slow_lookup_does_not_block_the_connection(no_connection, monkeypatch):
    import time

    monkeypatch.setattr(endpoint, "RESOLVE_WAIT_S", 0.05)
    monkeypatch.setattr(endpoint, "_lookup", lambda host: time.sleep(0.3) or ["8.8.8.8"])
    await session.connect("slow.local", 5810, "x", 0.1)
    assert no_connection == [("slow.local", 5810)]
