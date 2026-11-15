"""Edit tools: wpilib_set_team and wpilib_vendordep (the file edits behind Set Team Number and Manage Vendor Libraries)."""

import asyncio
from typing import Annotated, Literal

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply

from .. import config
from .. import project as files
from ..app import EDIT, FULL_ONLY_TAG, Project, mcp, tool_errors


@mcp.tool(annotations={**EDIT, "title": "Set team number"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def wpilib_set_team(team: Annotated[int, Field(description="FRC team number")], project: Project = None) -> ToolResult:
    """Set team number in .wpilib/wpilib_preferences.json (WPILib: Set Team Number)."""
    folder = await config.source.resolve_async(project)
    old = await asyncio.to_thread(files.set_team, folder, team)
    return reply({"team": team, "was": old})


@mcp.tool(annotations={**EDIT, "title": "Vendor libraries"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def wpilib_vendordep(
    action: Annotated[Literal["install", "remove"], Field(description="install = copy from the WPILib install (replaces an older copy); remove = delete its file")],
    name: Annotated[str, Field(description="File name without .json, e.g. CommandsV2 (wpilib_info lists them)")],
    project: Project = None,
) -> ToolResult:
    """Manage vendor libraries (vendordeps/) offline. No downloads: online installs need the library's JSON file from you."""
    folder = await config.source.resolve_async(project)
    change = files.install_vendordep if action == "install" else files.remove_vendordep
    return reply(await asyncio.to_thread(change, folder, name))
