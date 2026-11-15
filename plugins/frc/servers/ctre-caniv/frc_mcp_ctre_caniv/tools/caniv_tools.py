"""caniv_list, caniv_info, caniv_blink, caniv_firmware, caniv_configure and caniv_flash."""

import asyncio
import re
from typing import Annotated, Any

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply
from frc_mcp_core.toolset import gated_by
from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.firmware_index import device_releases, load_firmware_index

from .. import caniv, config
from ..app import BLINK, CONFIGURE, FLASH, FULL_ONLY_TAG, READ, Device, mcp, tool_errors

_FIRMWARE_NAME = re.compile(r"^CANivore-(?:Application-)?(?P<version>\d+(?:\.\d+){1,3})(?:-(?P<tag>.+?))?\.crf$", re.IGNORECASE)


@mcp.tool(annotations={**READ, "title": "List CANivores"}, output_schema=None)
@tool_errors
async def caniv_list() -> ToolResult:
    """The CANivores caniv can see: serial, name, status and `app` (the list's version column: the USB interface's; the firmware version is in caniv_info)."""
    async with caniv.session() as cv:
        rows, text = await cv.list_devices()
    out: dict[str, Any] = {"total": len(rows), "devices": rows}
    if not rows:
        extra = [line for line in caniv.lines(text) if not line.startswith("Serial Num")]  # caniv prints a header row even with no device; any other line that did not parse is shown as it came
        out["hint"] = "No CANivore found. Connect one by USB."
        if extra:
            out["raw"] = extra
    return reply(out)


@mcp.tool(annotations={**READ, "title": "CANivore info"}, output_schema=None)
@tool_errors
async def caniv_info(
    device: Annotated[str | None, Field(description="Serial or name; default: any one")] = None,
    full: Annotated[bool, Field(description="Also serial, name, dates, supply voltages, reset flags")] = False,
) -> ToolResult:
    """CANivore info as key: value (`firmware` = the version .crf files carry; the rarely needed keys only with full) and license lines. With several connected, name one."""
    async with caniv.session() as cv:
        return reply(await cv.info(device, full))


@mcp.tool(annotations={**BLINK, "title": "Blink a CANivore"}, output_schema=None)
@tool_errors
async def caniv_blink(device: Device) -> ToolResult:
    """Blink one CANivore's LED so you can tell which it is. The device must be listed."""
    async with caniv.session() as cv:
        serial = await cv.require_listed(device)
        result = await cv.run(["-d", serial, "--blink"], "blink")
    out: dict[str, Any] = {"device": serial, "blinked": True}
    if said := caniv.lines(result.output):  # caniv prints nothing for a blink (seen)
        out["caniv"] = said
    return reply(out)


@mcp.tool(annotations={**READ, "title": "CANivore firmware files"}, output_schema=None)
@tool_errors
async def caniv_firmware(limit: Annotated[int, Field(ge=1, le=100, description="Max files")] = 8) -> ToolResult:
    """CANivore .crf file names in CANIV_FIRMWARE_DIR (default: Tuner X's saved ones), highest version first; the name carries version and season. `latest` is CTRE's index per season."""
    found, index = await asyncio.gather(asyncio.to_thread(config.FIRMWARE.find, "CANivore"), asyncio.to_thread(load_firmware_index))  # two independent file reads
    names = sorted({p.name for p in found}, key=_firmware_key, reverse=True)  # a name once, even when two Tuner X apps saved it
    out: dict[str, Any] = {"total": len(names), "files": names[:limit]}
    if len(names) > limit:
        out["omitted"] = len(names) - limit
    if releases := device_releases(index, "CANivore"):
        out["latest"] = releases  # from Tuner X's saved firmware-index.json; may be out of date
    if not names and (notice := config.FIRMWARE.notice()):
        out["hint"] = notice
    return reply(out)


def _firmware_key(name: str) -> tuple[int, ...]:
    match = _FIRMWARE_NAME.match(name)
    return tuple(int(n) for n in match["version"].split(".")) if match else (0,)


@mcp.tool(annotations={**CONFIGURE, "title": "Rename or reset a CANivore"}, tags={FULL_ONLY_TAG, *gated_by(config.ALLOW_CONFIG)}, output_schema=None)
@tool_errors
async def caniv_configure(
    device: Device,
    name: Annotated[str | None, Field(description="New name; takes effect after a reset")] = None,
    reset: Annotated[bool, Field(description="Reset the device (it leaves the CAN bus for a moment)")] = False,
) -> ToolResult:
    """Rename and/or reset one CANivore. Needs CANIV_ALLOW_CONFIG=1; ask the user first, and never while a robot is enabled."""
    config.ALLOW_CONFIG.require()
    if name is None and not reset:
        raise CtreError("Give a new `name`, or `reset=true`, or both.")
    new_name = caniv.check_new_name(name) if name is not None else None
    async with caniv.session() as cv:  # the lookup and the changes are one unit: nothing else touches the CANivore between them
        target = await cv.require_listed(device)
        done: dict[str, Any] = {"device": target}
        if new_name is not None:
            await cv.run(["-d", target, "--name", new_name], "rename")
            done["renamed"] = new_name
        if reset:
            await cv.run(["-d", target, "--reset"], "reset")
            done["reset"] = True
        elif new_name is not None:
            done["note"] = "The new name takes effect after a reset."
    return reply(done)


@mcp.tool(annotations={**FLASH, "title": "Flash CANivore firmware"}, tags={FULL_ONLY_TAG, *gated_by(config.ALLOW_FLASH)}, output_schema=None)
@tool_errors
async def caniv_flash(
    device: Device,
    file: Annotated[str, Field(description="CANivore .crf file (see caniv_firmware)")],
) -> ToolResult:
    """Flash a CANivore .crf onto one CANivore. Needs CANIV_ALLOW_FLASH=1; ask the user first, keep power and USB connected, never during a match."""
    config.ALLOW_FLASH.require()
    path = await config.FIRMWARE.resolve_async(file)
    if not _FIRMWARE_NAME.match(path.name):
        raise CtreError(f"{path.name} is not a CANivore firmware file (they are named CANivore-<version>-<season>.crf). Use caniv_firmware to see them.")
    async with caniv.session() as cv:
        target = await cv.require_listed(device)
        result = await cv.run(["-d", target, "--flash", str(path)], "flash", config.flash_timeout_s())
    return reply({"device": target, "file": path.name, "caniv": caniv.lines(result.output), "next": "Run caniv_info to confirm the new version."})
