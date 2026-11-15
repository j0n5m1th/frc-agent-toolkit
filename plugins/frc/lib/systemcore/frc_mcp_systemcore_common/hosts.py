"""Which network addresses a server may contact: private, loopback or link-local ones, unless the owner allows any.
A tool argument, or text read from a device, must not point a server at the open internet, and a VPN or system proxy must not
carry robot traffic (the robot network is local). Each server passes its own error type and setting name.
"""

import ipaddress
import re
import socket
import urllib.request

_HOSTNAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_IPV6_LITERAL = re.compile(r"^[0-9A-Fa-f:.]{2,45}(%[A-Za-z0-9_.-]{1,32})?$")  # an address, then an optional zone id (`%usb0`, `%12`)
_UNIQUE_LOCAL = ipaddress.ip_network("fc00::/7")
_NOT_ROBOT_V4 = tuple(ipaddress.ip_network(n) for n in (  # private by Python's rules, but documents-only or special-purpose ranges
    "192.0.0.0/24", "192.0.2.0/24", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24"))


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the devices need none, and the target of one is not checked (a private-address, port or path rule bypass)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None  # urllib then raises HTTPError with the 3xx code


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects)  # no proxy, whatever the system says; no redirects


def redirect_message(code: int) -> str:
    """The short error text for a 3xx answer (redirects are refused)."""
    return f"answered with a redirect (HTTP {code}), which is not followed. A robot-network device does not redirect: this is not the expected service."


def _private(address: str) -> bool:  # keep equal to frc_mcp_core.network.is_robot_address (tests/common/test_network.py)
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):  # only loopback, unique-local and link-local: Python's "private" also covers 6to4, Teredo and documentation ranges
        return ip.is_loopback or ip.is_link_local or ip in _UNIQUE_LOCAL
    if any(ip in net for net in _NOT_ROBOT_V4):
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _ipv6_literal(host: str) -> str | None:
    """`host` as a bare IPv6 address (brackets and a zone id allowed), or None when it is not one."""
    inner = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    if ":" not in inner or not _IPV6_LITERAL.match(inner):
        return None
    try:
        ip = ipaddress.ip_address(inner)
    except ValueError:
        return None
    return inner if ip.version == 6 else None


def _addresses(host: str, error: type[Exception], resolve_hint: str) -> list[str]:
    """Every address `host` stands for, IPv4 first: itself for an address, else all answers of the resolver (IPv4 and IPv6)."""
    host = host.strip()
    literal = _ipv6_literal(host)
    if literal is not None:
        return [literal]  # an address needs no lookup
    if not host or not _HOSTNAME.match(host):
        raise error(f"{host!r} is not a host name or IP address. Give just the address, for example 172.30.0.1 (IPv6 such as fe80::1%usb0 is fine).")
    try:
        ipaddress.ip_address(host)
        return [host]  # an address needs no lookup (the Systemcore's own, 172.30.0.1, never touches DNS)
    except ValueError:
        pass
    try:
        found = {str(info[4][0]) for info in socket.getaddrinfo(host, None, socket.AF_UNSPEC)}
    except OSError as e:
        raise error(f"Cannot resolve {host}: {e}. {resolve_hint}") from e
    if not found:
        raise error(f"Cannot resolve {host}: no address. {resolve_hint}")
    return sorted(found, key=lambda a: (":" in a, a))


def checked_address(host: str, *, allow_any: bool, setting: str, error: type[Exception], resolve_hint: str = "Try the IP address.") -> str:
    """The IP address to connect to for `host`: resolved now, and (unless `allow_any`) EVERY address the name has must be a robot-network one.

    Connect to the returned literal, never to the name again: a second lookup could answer differently (DNS rebinding, an AAAA record).
    """
    addresses = _addresses(host, error, resolve_hint)
    if not allow_any and not all(_private(a) for a in addresses):
        raise error(f"{host.strip()} is not a private address, so it is not contacted. Set {setting}=1 to allow it.")
    return addresses[0]


def url_host(address: str) -> str:
    """`address` as the host part of a URL: IPv6 in brackets, a zone id (`%eth0`) escaped."""
    return f"[{address.replace('%', '%25')}]" if ":" in address else address


def check_host(host: str, *, allow_any: bool, setting: str, error: type[Exception], resolve_hint: str = "Try the IP address.") -> str:
    """`host` if it is a plain address or name whose addresses (all of them) are robot-network ones; else `error`, naming `setting` (the switch that allows any).

    Returns the name, so pins and settings stay keyed by it; connect through `checked_address`.
    """
    checked_address(host, allow_any=allow_any, setting=setting, error=error, resolve_hint=resolve_hint)
    return host.strip()
