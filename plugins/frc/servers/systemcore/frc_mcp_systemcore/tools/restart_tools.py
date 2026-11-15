"""systemcore_restart_robot_code and systemcore_reboot (the rules are in restart.py)."""

from typing import Annotated

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply
from frc_mcp_core.toolset import gated_by

from .. import config, restart
from ..app import FULL_ONLY_TAG, RESTART, Host, mcp, tool_errors
from ..target import resolve


@mcp.tool(annotations={**RESTART, "title": "Restart robot code"}, output_schema=None, tags={FULL_ONLY_TAG, *gated_by(config.restart)})
@tool_errors
async def systemcore_restart_robot_code(
    wait_s: Annotated[float, Field(ge=5, le=120)] = 30,
    force: Annotated[bool, Field(description="Only if the robot's state cannot be read and you are sure it is disabled.")] = False,
    host: Host = None,
) -> ToolResult:
    """Restart the robot program. Refused while the robot is enabled or on a field (never forced), or if its state is unreadable (force overrides only that). Ask first."""
    config.restart.require()
    return reply(await restart.restart_robot_code(await resolve(host), wait_s, force))


@mcp.tool(annotations={**RESTART, "title": "Reboot the Systemcore"}, output_schema=None, tags={FULL_ONLY_TAG, *gated_by(config.restart)})
@tool_errors
async def systemcore_reboot(
    wait_s: Annotated[float, Field(ge=10, le=300)] = 120,
    force: Annotated[bool, Field(description="Only if the robot's state cannot be read and you are sure it is disabled.")] = False,
    host: Host = None,
) -> ToolResult:
    """Reboot the Systemcore (robot program, Wi-Fi and Driver Station drop for about a minute). Refused while the robot is enabled or on a field (never forced), or if its state is unreadable (force overrides only that). Ask first."""
    config.restart.require()
    return reply(await restart.reboot(await resolve(host), wait_s, force))
