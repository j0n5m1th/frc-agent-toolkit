"""Where this server may connect: never port 1740, and only robot-network addresses (a private, loopback or link-local IP, or a name that resolves to one)."""

import asyncio
import ipaddress
import re
import socket

from frc_mcp_core.network import is_robot_address

from . import config
from .errors import NT4Error

_HOSTNAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
DRIVER_STATION_PORT = 1740  # the Driver Station's only slot: a second client there takes the robot's comms
RESOLVE_WAIT_S = 3.0  # an mDNS name that does not answer must not hold the connection up for long
_ALLOWED = "Allowed: '127.0.0.1' (sim), '10.TE.AM.2', 'roborio-TEAM-frc.local' or a robot-network IP."


def _literal(host: str) -> str | None:
    """`host` as an IP address (IPv6 may be in brackets), or None for a name."""
    inner = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        ipaddress.ip_address(inner.split("%", 1)[0])
    except ValueError:
        return None
    return inner


def check_endpoint(host: str, port: int) -> None:
    """Refuse port 1740 and a host that is not a plain name or a robot-network IP address (never a URL, never a public IP). No lookup: see `check_resolved`."""
    if port == DRIVER_STATION_PORT:
        raise NT4Error("Port 1740 is the Driver Station's connection and is never used. NetworkTables 4 is on port 5810.")
    config.check_target(host, "client")  # a URL, a port in the host, an empty host: its messages say how to fix each
    host = host.strip()
    literal = _literal(host)
    if literal is None and not _HOSTNAME.match(host):
        raise NT4Error(f"{host!r} is not a host name or IP address. {_ALLOWED}")
    if literal is not None and not is_robot_address(literal):
        raise NT4Error(f"{host} is not a robot-network address. Allowed: '127.0.0.1' (sim), '10.TE.AM.2' or another private address.")


def _lookup(host: str) -> list[str]:
    try:
        return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, None, socket.AF_UNSPEC)})
    except OSError:
        return []


async def check_resolved(host: str) -> None:
    """For a host NAME: when it resolves, every address must be a robot-network one. A name that does not resolve here (an mDNS `roborio-TEAM-frc.local`
    is found by the connection itself) is left to the connection attempt. An IP address needs no lookup.
    """
    host = host.strip()
    if _literal(host) is not None:
        return
    try:
        async with asyncio.timeout(RESOLVE_WAIT_S):
            found = await asyncio.to_thread(_lookup, host)
    except TimeoutError:
        return
    if found and not all(is_robot_address(a) for a in found):
        raise NT4Error(f"{host} resolves to a public address ({next(a for a in found if not is_robot_address(a))}), which is not a robot-network address. "
                       "Use '127.0.0.1' (sim), '10.TE.AM.2' or another private address.")


async def check_connection(host: str, port: int) -> None:
    """Everything `session.connect` requires of a target before it opens a connection."""
    check_endpoint(host, port)
    await check_resolved(host)
