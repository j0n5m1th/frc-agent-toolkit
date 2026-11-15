"""Finding a Systemcore: the UDP "LLPhoneHome" responder on port 5808.
Send `LLPhoneHome` to a controller (or a subnet's directed broadcast address) and it answers
`v2:h:<hostname>:interfaces:wlan0=<ip>/<mask>/<broadcast>;usb0=...;` (seen on image alpha14). The limited broadcast address
255.255.255.255 got no answer, so each local subnet gets its own directed broadcast.
"""

import asyncio
import ipaddress
import re
import socket
import time

from . import net, state

QUERY = b"LLPhoneHome"
# Where a Systemcore usually is, tried even when no local interface is on that subnet (WiFi AP, the two USB links).
KNOWN_BROADCASTS = ("172.30.0.255", "172.27.0.255", "172.26.0.255")
_ENTRY = re.compile(r"([A-Za-z0-9_.-]+)=(\d+\.\d+\.\d+\.\d+)/(\d+\.\d+\.\d+\.\d+)/(\d+\.\d+\.\d+\.\d+)")


def parse_reply(text: str) -> dict[str, object] | None:
    """`{"hostname": ..., "interfaces": {name: {"ip", "mask", "broadcast"}}}` from a reply, or None when it is not one."""
    if not text.startswith("v2:h:"):
        return None
    hostname, _, rest = text[5:].partition(":interfaces:")
    if not hostname or not rest:
        return None
    interfaces = {m[1]: {"ip": m[2], "mask": m[3], "broadcast": m[4]} for m in _ENTRY.finditer(rest)}
    return {"hostname": hostname, "interfaces": interfaces} if interfaces else None


def local_broadcasts() -> list[str]:
    """Directed broadcast (/24 assumed) of each IPv4 address this machine has, plus the usual Systemcore subnets."""
    found: list[str] = []
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        addresses = set()
    for address in sorted(addresses):
        ip = ipaddress.ip_address(address)
        if ip.is_loopback or ip.is_link_local:
            continue
        found.append(str(ipaddress.ip_network(f"{address}/24", strict=False).broadcast_address))
    return [*dict.fromkeys([*found, *KNOWN_BROADCASTS])]


def _probe_sync(targets: list[str], timeout_s: float, port: int) -> list[dict[str, object]]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    found: dict[str, dict[str, object]] = {}
    try:
        for target in targets:
            try:
                sock.sendto(QUERY, (target, port))
            except OSError:
                continue  # a subnet this machine has no route to
        deadline = time.monotonic() + timeout_s
        while (left := deadline - time.monotonic()) > 0:
            sock.settimeout(left)
            try:
                data, (source, _) = sock.recvfrom(2048)
            except (TimeoutError, OSError):
                break
            if (parsed := parse_reply(data.decode("utf-8", "replace"))) is not None and source not in found:
                found[source] = {"host": source, **parsed}
    finally:
        sock.close()
    return list(found.values())


def brief(found: list[dict[str, object]]) -> list[dict[str, object]]:
    """Just the address and name of each device found."""
    return [{"host": d["host"], "hostname": d.get("hostname")} for d in found]


def remember_single(found: list[dict[str, object]]) -> None:
    """A single result becomes the default controller; with none or several there is no obvious choice."""
    if len(found) == 1:
        state.remember_device(str(found[0]["host"]), hostname=found[0].get("hostname"))


async def discover(timeout_s: float = 2.0, hosts: list[str] | None = None, port: int | None = None) -> list[dict[str, object]]:
    """Controllers that answered: `[{"host", "hostname", "interfaces"}]`. `hosts` (unicast) are asked as well as the broadcasts."""
    targets = [*(hosts or []), *local_broadcasts()]
    return await asyncio.to_thread(_probe_sync, targets, timeout_s, port or net.port("discovery"))
