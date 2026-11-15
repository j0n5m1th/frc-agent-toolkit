"""systemcore_os_latest and systemcore_os_download: compare the controller's OS with the newest release, and fetch the update file."""

import asyncio
import time
from typing import Annotated

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply

from .. import config, net, releases
from ..app import FETCH, FULL_ONLY_TAG, LOOKUP, Host, mcp, tool_errors
from ..errors import SystemcoreError
from ..target import resolve


async def _device_release(host: str | None) -> tuple[str | None, dict]:
    """The controller's release name and its parse; `(None, {})` when no controller is reachable or none was asked for."""
    try:
        target = await resolve(host)
        name = (await net.os_info(target)).get("release")
    except SystemcoreError:
        return None, {}
    return name, releases.parse_release(name)


FRESH_FOR_S = 3600  # the saved release list is reused for an hour: GitHub allows only 60 anonymous requests an hour


async def _list(max_age_s: float) -> dict:
    """The saved release list, fetched again when there is none or it is older than `max_age_s` (0 = always)."""
    data = releases.cached()
    if not data or time.time() - float(data.get("fetched_at") or 0) > max_age_s:
        try:
            data = await releases.refresh()
        except SystemcoreError:
            if not data:
                raise
    return data


@mcp.tool(annotations={**LOOKUP, "title": "Newest Systemcore OS"}, output_schema=None)
@tool_errors
async def systemcore_os_latest(
    refresh: Annotated[bool, Field(description="Ask GitHub now (else a list saved within the hour is used)")] = False,
    host: Host = None,
) -> ToolResult:
    """Newest OS release for this controller (its alpha/beta line, or production) and whether it is behind. Needs internet."""
    (name, device), data = await asyncio.gather(_device_release(host), _list(0 if refresh else FRESH_FOR_S))
    newest = releases.newest_for(device, data["releases"]) if device else None
    out: dict = {"device": name, "checked_at": data.get("fetched_at")}
    if newest:
        out["newest"] = {"tag": newest["tag"], "published": newest["published"], "files": [a["name"] for a in newest["assets"]]}
        out["behind"] = releases.is_newer(newest["tag"], device)
    elif not device:
        out["hint"] = "The controller's release was not read (it is not reachable), so nothing was compared."
        out["releases"] = [r["tag"] for r in data["releases"][:6]]
    return reply(out)


@mcp.tool(annotations={**FETCH, "title": "Download Systemcore OS update"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def systemcore_os_download(
    tag: Annotated[str | None, Field(max_length=80, description="Release tag (default: newest)")] = None,
    usb_boot: Annotated[bool, Field(description="USB-boot file (rare); default eMMC")] = False,
    host: Host = None,
) -> ToolResult:
    """Save the `.llupdate` OS file (about 750 MB) and check its SHA-256. Installs nothing: the user updates at http://robot.local/configure."""
    name, device = await _device_release(host)
    data = await _list(float("inf"))  # an OS file is chosen from the saved list; GitHub is asked only when there is none
    if tag:
        release = next((r for r in data["releases"] if r["tag"] == tag), None)
        if release is None:
            raise SystemcoreError(f"{tag!r} is not in the saved release list; run systemcore_os_latest to refresh it.")
    else:
        if not device:
            raise SystemcoreError("The controller's release is unknown (not reachable): pass tag.")
        release = releases.newest_for(device, data["releases"])
        if release is None:
            raise SystemcoreError("No matching release in the saved list; run systemcore_os_latest to refresh it.")
    saved = await releases.download(releases.pick_asset(release, usb=usb_boot), config.download_dir() / "os-images")
    saved["release"] = release["tag"]
    saved["hint"] = ("Before updating: download the robot logs (systemcore_logs_download), because the update does not keep them by "
                     "default. Then open http://robot.local/configure and choose this file.")
    return reply(saved)
