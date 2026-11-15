"""Reaching the Systemcore from this computer: is it reachable, and if not, join its Wi-Fi (and later leave it).
The tools only gate and reply; the rules live here. A join is recorded in the state folder, so `disconnect` can go back to the
dropped network, but only within RECORD_TTL_S, so a network the user joined by hand is never mistaken for ours. Saved networks
are never deleted.
"""

import asyncio
import contextlib
import time
from typing import Any

from . import config, discovery, net, state, wifi
from .errors import SystemcoreError

RECORD_TTL_S = 12 * 3600  # a join older than this is not assumed to be ours any more
WIFI_RECORD = "wifi.json"  # per network this server joined: the network it left, so disconnect can go back to it


def _is_systemcore(name: str | None) -> bool:
    return bool(name) and name.upper().startswith("SYSTEMCORE")  # type: ignore[union-attr]


def _ssid(network: str, current: str | None) -> str:
    """The network to be on: the saved name; else (radio) FRC-<team>; else (systemcore) the one in range, or the one this computer is on."""
    saved, _ = config.wifi_credentials(network)
    if saved is not None:
        return saved
    if network == "radio":
        raise SystemcoreError(f"The robot radio's name is not known. Set SYSTEMCORE_TEAM (the name is FRC-<team>) or SYSTEMCORE_RADIO_SSID in {config.env_file()}.")
    if current and _is_systemcore(current):
        return current
    if _password_saved(network):
        raise SystemcoreError(
            f"A {network} Wi-Fi password is saved but its network name is not, so nothing is joined: any network called SYSTEMCORE could be an "
            f"impostor that captures the password. Add {config.wifi_prefix(network)}_SSID=<your controller's network name> to {config.env_file()} and restart the server.")
    return wifi.pick_systemcore(wifi.scan())


def _password_saved(network: str) -> bool:
    return config.wifi_credentials(network)[1] is not None


def _current_target(network: str, current: str | None) -> str | None:
    """The name of `network` if this computer is on it now, from settings or the current name: no scanning."""
    saved, _ = config.wifi_credentials(network)
    if current is None:
        return None
    if saved is not None:
        return saved if saved == current else None
    return current if network == "systemcore" and _is_systemcore(current) else None


def _password(network: str) -> str:
    password = config.wifi_credentials(network)[1]
    if password is None:
        raise SystemcoreError(f"No password is saved for the {network} Wi-Fi. Add {config.wifi_prefix(network)}_PASSWORD to {config.env_file()} and restart the "
                              "server. It is never taken from chat.")
    return password


def _remember(ssid: str, **facts: object) -> None:
    """Record a join (or, with no facts, drop its record). A failure to save is not worth failing the tool for."""
    with contextlib.suppress(SystemcoreError):
        record = state.load(WIFI_RECORD)
        if facts:
            record[ssid] = {**facts, "at": time.time()}
        else:
            record.pop(ssid, None)
        state.save(WIFI_RECORD, record)


async def reachable() -> list[dict[str, object]]:
    """Systemcores this computer can already reach (Wi-Fi, Ethernet or USB): UDP discovery, else the saved or last-used address."""
    async def probe(host: str) -> list[dict[str, object]]:
        try:
            await net.os_info(await net.check_host(host), timeout=2)
        except SystemcoreError:
            return []
        return [{"host": host, "hostname": None}]

    hosts = dict.fromkeys(h for h in (config.host(), state.last_host()) if h)
    found, *probed = await asyncio.gather(discovery.discover(2), *(probe(h) for h in hosts))  # together: the worst case is one timeout
    return found or next((p for p in probed if p), [])


async def connect(network: str, wait_s: float) -> dict[str, Any]:
    """Make a Systemcore reachable. Changes nothing when one already answers (the usual case: Wi-Fi, USB or Ethernet)."""
    if found := await reachable():
        discovery.remember_single(found)
        return {"network": network, "already_reachable": True, "devices": discovery.brief(found),
                "hint": "A Systemcore already answers (Wi-Fi, Ethernet or USB), so the Wi-Fi was not changed."}
    config.wifi.require()
    current = await asyncio.to_thread(wifi.current_ssid)
    ssid = _ssid(network, current)
    out: dict[str, Any] = {"network": network, "ssid": ssid}
    if current == ssid:
        out["already_connected"] = True  # no password is needed for a network this computer is already on
    else:
        saved = await asyncio.to_thread(wifi.has_profile, ssid)  # a network saved on this computer needs no password
        left = await asyncio.to_thread(wifi.join, ssid, None if saved else _password(network), wait_s)
        out["joined"] = True
        if left:
            out["left"] = left  # so the user can rejoin it afterwards
        _remember(ssid, left=left)
    found = await discovery.discover(3)
    if not found and network == "radio" and (radio_host := config.radio_host()) is not None:
        with contextlib.suppress(SystemcoreError):
            await net.os_info(radio_host)
            found = [{"host": radio_host, "hostname": "robot", "interfaces": {}}]
    discovery.remember_single(found)
    out["devices"] = discovery.brief(found)
    if not found:
        out["hint"] = "Joined, but no Systemcore answered yet. Wait a few seconds and run systemcore_discover."
    return out


async def disconnect(network: str, rejoin: bool) -> dict[str, Any]:
    """Leave a network this server joined recently and go back to the one it dropped; anything else is left alone."""
    config.wifi.require()
    current = await asyncio.to_thread(wifi.current_ssid)
    ssid = _current_target(network, current)
    if ssid is None:
        return {"network": network, "connected": False, "hint": "This computer is not on that network; nothing was changed."}
    record = state.load(WIFI_RECORD).get(ssid)
    if not isinstance(record, dict) or time.time() - float(record.get("at") or 0) > RECORD_TTL_S:
        return {"network": network, "ssid": ssid, "disconnected": False,
                "hint": "systemcore_connect did not join this network recently, so it was left alone. Disconnect it yourself if you want."}
    await asyncio.to_thread(wifi.leave, ssid)
    out: dict[str, Any] = {"network": network, "ssid": ssid, "disconnected": True}
    previous = record.get("left")
    if rejoin and isinstance(previous, str) and previous:
        reason = "it is not saved on this computer"
        if await asyncio.to_thread(wifi.has_profile, previous):
            try:
                await asyncio.to_thread(wifi.join, previous, None, 20)
                out["rejoined"] = previous
            except SystemcoreError as e:
                reason = str(e)
        if "rejoined" not in out:
            out["rejoin_failed"] = f"Could not go back to {previous}: {reason}. Join it from the Wi-Fi menu."
    elif rejoin:
        out["hint"] = "No earlier network was recorded, so nothing was rejoined. Join yours from the Wi-Fi menu."
    _remember(ssid)  # no facts: forget the record
    return out
