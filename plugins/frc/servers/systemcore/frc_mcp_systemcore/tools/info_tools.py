"""systemcore_info: the controller's health summary, or one area in depth."""

from typing import Annotated, Literal

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply

from .. import info, state
from ..app import READ_ONLY, Host, mcp, tool_errors
from ..target import resolve


@mcp.tool(annotations={**READ_ONLY, "title": "Systemcore information"}, output_schema=None)
@tool_errors
async def systemcore_info(
    area: Annotated[Literal["summary", "network", "hardware", "services", "dmesg"],
                    Field(description="summary, or one area in depth")] = "summary",
    host: Host = None,
) -> ToolResult:
    """Health summary, `warnings` first: OS, uptime, CPU, disk, eMMC, clock, and with SSH memory, temperatures, failed services."""
    target = await resolve(host)
    out = await (info.summary(target) if area == "summary" else info.detail(target, area))
    if area == "summary":
        state.remember_device(target, team=out.get("team"), os_hash=(out.get("os") or {}).get("hash"))
        if "ssh" in out.get("errors", {}):
            out["hint"] = "Shell readings are missing; systemcore_ssh_status says why."
    return reply(out)
