"""Which IP addresses are robot-network addresses: loopback, private, link-local (IPv4 and IPv6) and IPv6 unique-local; nothing else.

Python's `is_private` also says yes to ranges that can reach the internet or only exist in documents (6to4 `2002::/16`, Teredo `2001::/32`,
documentation, benchmarking `198.18.0.0/15`, IETF protocol `192.0.0.0/24`), so those are listed here as refused. The Systemcore address check
(`frc_mcp_systemcore_common.hosts`) may not import this package, so it holds the same rule; `tests/common/test_network.py` keeps them equal.
"""

import ipaddress

_UNIQUE_LOCAL = ipaddress.ip_network("fc00::/7")
_NOT_ROBOT_V4 = tuple(ipaddress.ip_network(n) for n in (
    "192.0.0.0/24", "192.0.2.0/24", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24"))


def is_robot_address(address: str) -> bool:
    """True for a loopback, private or link-local address (a zone id such as `%usb0` is ignored). A text that is not an address raises ValueError."""
    ip = ipaddress.ip_address(address.split("%", 1)[0].strip("[]"))
    if isinstance(ip, ipaddress.IPv6Address):
        return ip.is_loopback or ip.is_link_local or ip in _UNIQUE_LOCAL
    if any(ip in net for net in _NOT_ROBOT_V4):
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local
