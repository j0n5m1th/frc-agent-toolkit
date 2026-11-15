"""The robot-network check resolves a name once, needs every answer to be private, and connections go to the checked address."""

import ipaddress
import socket

import pytest
from envelope_helpers import j
from fastmcp.exceptions import ToolError

from frc_mcp_systemcore import config, net, ssh
from frc_mcp_systemcore.errors import SystemcoreError
from frc_mcp_systemcore_common import hosts

REAL_GETADDRINFO = socket.getaddrinfo


def answers(*addresses):
    """A getaddrinfo that answers the given addresses for any name (and records the family asked for)."""
    asked = []

    def fake(host, port, family=0, *args, **kwargs):
        try:
            ipaddress.ip_address(host)
            return REAL_GETADDRINFO(host, port, family, *args, **kwargs)  # a literal (what the checked connection uses) is answered for real
        except ValueError:
            pass
        asked.append(family)
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0)) for a in addresses]

    fake.asked = asked
    return fake


def test_a_name_with_any_public_address_is_refused_even_when_another_is_private(monkeypatch):
    fake = answers("172.30.0.1", "2001:4860:4860::8888")  # an AAAA record that points at the internet
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with pytest.raises(SystemcoreError, match="not a private address"):
        net.check_host_sync("robot.example")
    with pytest.raises(SystemcoreError, match="not a private address"):
        net.connect_address("robot.example")
    assert socket.AF_INET not in fake.asked and fake.asked  # IPv4 and IPv6 are both asked for (AF_UNSPEC)
    monkeypatch.setattr(socket, "getaddrinfo", answers("172.30.0.1", "8.8.4.4"))
    with pytest.raises(SystemcoreError, match="not a private address"):
        net.check_host_sync("robot.example")


def test_a_name_that_resolves_only_to_private_addresses_is_used_by_its_literal(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", answers("fe80::1", "172.30.0.1"))
    assert net.check_host_sync("robot.example") == "robot.example"  # the name stays the key of pins and settings
    assert net.connect_address("robot.example") == "172.30.0.1"  # IPv4 first
    monkeypatch.setattr(socket, "getaddrinfo", answers("fd00::5"))
    assert net.connect_address("robot.example") == "fd00::5" and hosts.url_host("fd00::5") == "[fd00::5]"
    assert hosts.url_host("fe80::1%eth0") == "[fe80::1%25eth0]" and hosts.url_host("10.0.0.2") == "10.0.0.2"


def test_the_owner_can_allow_any_host_and_a_literal_never_touches_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", answers("8.8.8.8"))
    monkeypatch.setenv("SYSTEMCORE_ALLOW_ANY_HOST", "1")
    assert net.connect_address("far.example") == "8.8.8.8"
    monkeypatch.delenv("SYSTEMCORE_ALLOW_ANY_HOST")

    def boom(*args, **kwargs):
        raise AssertionError("no lookup for an address")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert net.connect_address("172.30.0.1") == "172.30.0.1"


async def test_requests_connect_to_the_checked_address_not_the_name(client, device, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", answers("127.0.0.1"))  # `robot.example` exists only in this fake: a request by name would fail
    out = await net.request_json("robot.example", "update", "/api/oshash")
    assert out["release"].startswith("limelightosr")
    monkeypatch.setattr(socket, "getaddrinfo", answers("127.0.0.1", "2606:4700:4700::1111"))
    with pytest.raises(SystemcoreError, match="not a private address"):
        await net.request_json("robot.example", "update", "/api/oshash")
    assert len(device.requests) == 1  # the refused one never connected


def test_ssh_probe_connects_with_a_timeout_to_the_literal(monkeypatch):
    seen = {}

    def create(address, timeout=None):
        seen["address"], seen["timeout"] = address, timeout
        raise OSError("refused")

    monkeypatch.setattr(socket, "getaddrinfo", answers("127.0.0.1"))
    monkeypatch.setattr(ssh.socket, "create_connection", create)
    with pytest.raises(SystemcoreError, match="No SSH server answered"):
        ssh.probe_host_key("robot.example", timeout=3)
    assert seen == {"address": ("127.0.0.1", 22), "timeout": 3}


def test_ssh_login_connects_to_the_literal_and_the_pin_stays_keyed_by_the_name(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", answers("172.30.0.1"))
    calls = []

    class Client:
        def connect(self, hostname, **kwargs):
            calls.append(hostname)

    ssh._login(Client(), "robot.example", 5, pkey=None)
    assert calls == ["172.30.0.1"]
    assert ssh._PinPolicy("robot.example", None).host == "robot.example"  # state.pin/pinned are keyed by this name, as before


async def test_a_public_name_gives_no_ssh_probe(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", answers("8.8.8.8"))
    with pytest.raises(SystemcoreError, match="not a private address"):
        ssh.probe_host_key("robot.example")


async def test_a_bundle_over_the_size_limit_stops_and_leaves_no_file(client, device, monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEMCORE_MAX_DOWNLOAD_MB", "1")
    device.routes[("GET", "/api/support/download/job1")] = b"x" * (1024 * 1024 + 4096)
    with pytest.raises(ToolError, match="limit"):
        await client.call_tool("systemcore_bundle", {})
    assert list((tmp_path / "downloads").glob("*")) == []  # no zip and no .part


async def test_a_bundle_within_the_limit_is_saved(client, device, monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_MAX_DOWNLOAD_MB", "1")
    out = j(await client.call_tool("systemcore_bundle", {}))
    assert out["member_count"] == 2 and config.max_download_bytes() == 1024 * 1024


def test_the_download_limit_has_a_safe_default_and_rejects_nonsense(monkeypatch):
    assert config.max_download_bytes() == 200 * 1024 * 1024
    monkeypatch.setenv("SYSTEMCORE_MAX_DOWNLOAD_MB", "0")
    with pytest.raises(SystemcoreError, match="SYSTEMCORE_MAX_DOWNLOAD_MB"):
        config.max_download_bytes()


# ---- redirects are never followed ----

class _Redirector:
    """A local server that answers every GET with a redirect, and a second one that records whether anyone followed it."""

    def __init__(self, location):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.hits = []
        hits = self.hits

        class Target(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                hits.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        self.target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
        target_port = self.target.server_address[1]

        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(302)
                self.send_header("Location", location.format(port=target_port))
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.redirect = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        self.servers = [self.target, self.redirect]
        for server in self.servers:
            threading.Thread(target=lambda s=server: s.serve_forever(poll_interval=0.01), daemon=True).start()

    @property
    def port(self):
        return self.redirect.server_address[1]

    def close(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()


@pytest.fixture(params=["http://127.0.0.1:{port}/api/wifi", "http://localhost:{port}/elsewhere"])
def redirector(request):
    r = _Redirector(request.param)
    yield r
    r.close()


async def test_a_redirect_from_a_controller_service_is_an_error_and_never_followed(redirector, monkeypatch, tmp_path):
    monkeypatch.setitem(net.PORTS, "update", redirector.port)
    monkeypatch.setenv("SYSTEMCORE_HOST", "127.0.0.1")
    with pytest.raises(SystemcoreError, match="redirect.*not followed"):
        await net.request_json("127.0.0.1", "update", "/api/oshash")
    with pytest.raises(SystemcoreError, match="redirect.*not followed"):
        await net.download("127.0.0.1", "update", "/file", tmp_path / "f.bin")
    assert redirector.hits == [] and not (tmp_path / "f.bin").exists()  # the target was never contacted


@pytest.mark.parametrize("literal", ["::1", "[::1]", "fe80::1", "fe80::1%usb0", "fe80::1%12", "fd00::5", "fc00::1", "[fe80::1%eth0]"])
def test_ipv6_loopback_unique_local_and_link_local_literals_are_robot_network_addresses(monkeypatch, literal):
    def boom(*args, **kwargs):
        raise AssertionError("no lookup for an address")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert net.check_host_sync(literal) == literal
    assert net.connect_address(literal) == literal.strip("[]")  # the literal itself, never a name again


@pytest.mark.parametrize("literal", ["2001:4860:4860::8888", "[2606:4700:4700::1111]", "2002:808:808::", "2001:db8::1", "::ffff:8.8.8.8",
                                     "64:ff9b::808:808", "ff02::1", "fec0::1", "::"])
def test_other_ipv6_literals_are_refused_unless_the_owner_allows_any_host(monkeypatch, literal):
    with pytest.raises(SystemcoreError, match="not a private address"):
        net.check_host_sync(literal)
    with pytest.raises(SystemcoreError, match="not a private address"):
        net.connect_address(literal)
    monkeypatch.setenv("SYSTEMCORE_ALLOW_ANY_HOST", "1")
    assert net.connect_address(literal) == literal.strip("[]")


@pytest.mark.parametrize("bad", ["fe80::1%", "fe80::1%a b", "fe80::1%%25", "fe80::1/64", "[fe80::1", "fe80::1]", "::g", "1:2:3:4:5:6:7:8:9", ":::", "fe80::1%eth0%eth1",
                                 "[fe80::1]:80"])
def test_a_malformed_ipv6_literal_is_not_a_host(bad):
    with pytest.raises(SystemcoreError, match="not a host name or IP address"):
        net.check_host_sync(bad)


def test_every_resolved_ipv6_address_must_be_loopback_unique_local_or_link_local(monkeypatch):
    for odd in ("2002:808:808::", "2001:db8::1", "ff02::1"):
        monkeypatch.setattr(socket, "getaddrinfo", answers("fe80::1", odd))
        with pytest.raises(SystemcoreError, match="not a private address"):
            net.connect_address("robot.example")


def test_the_bracketed_url_form_holds_the_literal_and_an_escaped_zone():
    assert hosts.url_host("::1") == "[::1]" and hosts.url_host(net.connect_address("[fe80::1%usb0]")) == "[fe80::1%25usb0]"
    import urllib.request

    assert urllib.request.Request(f"http://{hosts.url_host('fe80::1%usb0')}:5807/api/x").host == "[fe80::1%usb0]:5807"  # urllib undoes the escape


def test_a_request_to_an_ipv6_loopback_literal_connects_there_and_never_follows_a_redirect(monkeypatch):
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/")
                self.end_headers()
                return
            body = b'{"release": "ipv6-ok"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    class Server6(http.server.ThreadingHTTPServer):
        address_family = socket.AF_INET6

    try:
        server = Server6(("::1", 0), Handler)
    except OSError:
        pytest.skip("IPv6 loopback is not available here")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setitem(net.PORTS, "update", server.server_address[1])
        assert net._request_sync("[::1]", "update", "/api/oshash", "GET", None, 5) == {"release": "ipv6-ok"}
        assert net._request_sync("::1", "update", "/api/oshash", "GET", None, 5) == {"release": "ipv6-ok"}
        with pytest.raises(SystemcoreError, match="redirect"):
            net._request_sync("::1", "update", "/redirect", "GET", None, 5)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
