"""diag_status, diag_bus, diag_device, diag_read, diag_config and diag_blink."""

import asyncio
from typing import Annotated, Any

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply
from frc_mcp_ctre import choose
from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.fetch import load_index

from .. import client, diag
from ..app import BLINK, FULL_ONLY_TAG, READ, Device, Host, mcp, tool_errors

# The diagnostic server's platform name -> the Phoenix Diagnostics Program build CTRE lists for it. Only the Systemcore is supported (and seen).
BUILDS = {"Systemcore": "linuxsystemcore"}
COUNTERS = ("ArbitrationLostCnt", "BusErrorCnt", "BusOffCnt", "RestartCnt", "TxFullCnt")  # seen on a healthy bus: all 0
MAX_NAMES = 40  # signal names listed by one diag_read
MAX_CONFIGS = 60  # config rows returned by one diag_config
PASSIVE = 128  # CAN error-passive level for TEC and REC (seen: TEC 128 with the CAN low wire pulled, and on Systemcore buses with nothing on them)


async def _nothing() -> dict[str, Any]:
    return {}


def _listed(system: str, compliancy: Any) -> str | None:
    """The newest Phoenix Diagnostics Program CTRE lists for this compliancy and platform (from the saved or bundled index, no network), if any."""
    if not isinstance(compliancy, int) or system not in BUILDS:
        return None
    try:
        found = choose.for_compliancy(load_index(offline=True).index, "PhoenixDiagnosticsProgram", compliancy, BUILDS[system])
    except CtreError:
        return None
    return found.version if found else None


@mcp.tool(annotations={**READ, "title": "Diagnostic server status"}, output_schema=None)
@tool_errors
async def diag_status(host: Host = None) -> ToolResult:
    """The diagnostic server (version, compliancy, platform) and the CAN devices it sees, as rows of `cols`. `listed` = the newest Diagnostics Program CTRE lists for that compliancy."""
    where = await client.target(host)
    version, (reply_, found) = await asyncio.gather(client.call("getversion", where), diag.devices(where))
    system = str(version.get("System", ""))
    server = {"version": version.get("Version"), "compliancy": version.get("Compliancy"), "release": version.get("ReleaseInfo"), "system": system}
    if listed := _listed(system, version.get("Compliancy")):
        server["listed"] = listed
    out: dict[str, Any] = {"host": where, "server": server, "cols": ["name", "model", "id", "fw", "bus", "status"],
                           "devices": [[d.name, d.model, d.id, d.raw.get("CurrentVers"), d.label, d.raw.get("SoftStatus")] for d in found]}
    if isinstance(util := reply_.get("BusUtilPerc"), (int, float)) and util >= 0:
        out["bus_util_pct"] = round(util, 1)  # the server answers -1 when it does not know
    if not found:
        out["hint"] = "No CAN device answered. Check device power, the CAN wiring (both wires) and termination, and that the CANivore (if any) is plugged in."
        if seen := diag.known_buses(where):
            out["hint"] += f" Buses seen earlier: {', '.join(seen)}; diag_bus shows their errors."
    return reply(out)


@mcp.tool(annotations={**READ, "title": "CAN bus statistics"}, output_schema=None)
@tool_errors
async def diag_bus(
    bus: Annotated[str | None, Field(description="Bus name or CANivore serial (default: every bus a device was listed on)")] = None,
    host: Host = None,
) -> ToolResult:
    """CAN bus use and error counters per bus (only non-zero counters are listed; `passive` = TEC or REC at 128, the CAN error-passive level). Works for a bus whose devices all vanished: name it."""
    where = await client.target(host)
    _, found = await diag.devices(where)  # also remembers the buses, so one that lost every device is still asked about
    buses = diag.known_buses(where)
    if bus is not None:
        # A name no device was seen on is passed through: the server knows buses that never had a device (seen: can_s0 to can_s4 answer, can5 is an error).
        buses = {label: key for label, key in buses.items() if bus.lower() in (label.lower(), key.lower())} or {bus: bus}
    answers = await asyncio.gather(*(client.call("getcanbusstats", where, canbus=key) for key in buses.values()))
    occupied = {d.label for d in found}
    rows = []
    for label, stats in zip(buses, answers, strict=True):
        tec, rec = stats.get("TEC"), stats.get("REC")
        row: dict[str, Any] = {"bus": label, "util_pct": round(diag.as_float(stats.get("BusUtilPerc")) or 0, 1), "tec": tec, "rec": rec, "state": stats.get("CANState")}
        if any((diag.as_float(n) or 0) >= PASSIVE for n in (tec, rec)):
            row["passive"] = True
        if counters := {name: stats[name] for name in COUNTERS if stats.get(name)}:
            row["counters"] = counters
        if found and label not in occupied:
            row["no_device"] = True  # remembered from earlier (a device moved or lost): its counters may be an idle bus's
        rows.append(row)
    out: dict[str, Any] = {"host": where, "buses": rows}
    if not found:
        out["devices"] = 0  # none answered now: with a bus that had devices before, a pulled wire or lost power is the first thing to check
    if not rows:
        out["hint"] = "No bus is known: no device was listed in this session. Name one: Systemcore buses are can_s0 to can_s4; a CANivore is its name or serial."
    return reply(out)


@mcp.tool(annotations={**READ, "title": "Device details and faults"}, output_schema=None)
@tool_errors
async def diag_device(
    device: Device,
    details: Annotated[bool, Field(description="Also its detail fields")] = False,
    host: Host = None,
) -> ToolResult:
    """CTRE device details: model, firmware, status, license summary, and the faults set now (`faults`) and since power-up (`sticky`)."""
    where = await client.target(host)
    dev = await diag.find_device(where, device)
    raw = dev.raw
    (now, sticky, checked), info = await asyncio.gather(diag.active_faults(where, dev), client.call("deviceinformation", where, **dev.query()) if details else _nothing())
    out: dict[str, Any] = {"name": dev.name, "model": dev.model, "id": dev.id, "bus": dev.label, "fw": raw.get("CurrentVers"), "hw": raw.get("HardwareRev"),
                           "bootloader": raw.get("BootloaderRev"), "status": raw.get("SoftStatus"),
                           "license": {"pro": raw.get("IsPROLicensed"), "valid": raw.get("LicensesValid"), "n": len(raw.get("Licenses") or []), "capacity": raw.get("LicenseCapacity")},
                           "faults": now, "sticky": sticky, "faults_read": checked}
    if details:
        out["details"] = {k: str(v)[:60] for k, v in list((info.get("DeviceDetails") or {}).items())[:16]}
    return reply(out)


@mcp.tool(annotations={**READ, "title": "Device signals"}, output_schema=None)
@tool_errors
async def diag_read(
    device: Device,
    signals: Annotated[list[str] | None, Field(description="Names to read now (max 30)")] = None,
    name_contains: Annotated[str | None, Field(description="List names containing this")] = None,
    host: Host = None,
) -> ToolResult:
    """Live device signals (`signals`): values now. Without: the names (and units) of what the device can report, filtered by name_contains."""
    where = await client.target(host)
    dev = await diag.find_device(where, device)
    available = await diag.signals(where, dev)
    if not signals:
        needle = (name_contains or "").lower()
        names = [f"{s['Name']} ({s['Units']})" if s.get("Units") else str(s["Name"]) for s in available if needle in str(s["Name"]).lower()]
        out: dict[str, Any] = {"device": dev.name, "total": len(available), "matches": len(names), "signals": names[:MAX_NAMES]}
        if len(names) > MAX_NAMES:
            out["omitted"] = len(names) - MAX_NAMES
        return reply(out)
    if len(signals) > diag.MAX_SIGNALS:
        raise CtreError(f"Ask for at most {diag.MAX_SIGNALS} signals at a time.")
    by_name = {str(s["Name"]).lower(): s for s in available}
    unknown = [n for n in signals if n.lower() not in by_name]
    if unknown:
        close = [str(s["Name"]) for s in available if any(n.lower() in str(s["Name"]).lower() for n in unknown)][:6]
        raise CtreError(f"{dev.model} has no signal {', '.join(repr(n) for n in unknown)}." + (f" Similar: {', '.join(close)}." if close else " Use diag_read without `signals` to list them."))
    chosen = [by_name[n.lower()] for n in signals]
    read = await diag.values(where, dev, [s["Id"] for s in chosen])
    return reply({"device": dev.name, "values": {str(s["Name"]): diag.num(read.get(s["Id"])) for s in chosen}})


@mcp.tool(annotations={**READ, "title": "Device configs"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def diag_config(
    device: Device,
    name_contains: Annotated[str | None, Field(description="Only names containing this")] = None,
    all: Annotated[bool, Field(description="Every config, not only changed ones")] = False,  # noqa: A002
    host: Host = None,
) -> ToolResult:
    """Device configs that differ from their defaults (or all with `all`), as rows of `cols`. Read only: nothing is written to the device."""
    where = await client.target(host)
    dev = await diag.find_device(where, device)
    reply_ = await client.call("getconfigv2", where, **dev.query())
    needle = (name_contains or "").lower()
    rows, total, differing = [], 0, 0
    for group in reply_.get("Device") or []:
        for cfg in (group.get("Configs") or []) if isinstance(group, dict) else []:
            if not isinstance(cfg, dict):
                continue
            total += 1
            differs = cfg.get("Value") != cfg.get("DefaultValue")
            differing += differs
            if (all or differs) and needle in f"{group.get('Name', '')} {cfg.get('Name', '')}".lower():
                shown = bool if cfg.get("Type") == "bool" else diag.num
                rows.append([group.get("Name"), cfg.get("Name"), shown(cfg.get("Value")), shown(cfg.get("DefaultValue")), cfg.get("Units") or None])
    out: dict[str, Any] = {"device": dev.name, "total": total, "differ": differing, "cols": ["group", "name", "value", "default", "units"], "rows": rows[:MAX_CONFIGS]}
    if len(rows) > MAX_CONFIGS:
        out["omitted"] = len(rows) - MAX_CONFIGS
    return reply(out)


@mcp.tool(annotations={**BLINK, "title": "Blink a device"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def diag_blink(device: Device, host: Host = None) -> ToolResult:
    """Blink one device's LED so you can tell which it is. Changes nothing that lasts."""
    where = await client.target(host)
    dev = await diag.find_device(where, device)
    await client.call("blink", where, **dev.query())
    return reply({"device": dev.name, "blinked": True})
