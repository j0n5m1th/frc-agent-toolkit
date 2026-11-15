"""wpilib_info: the project information the VS Code extension shows (team, versions, vendordeps, whether deploy is allowed)."""

import asyncio

from fastmcp.tools import ToolResult

from frc_mcp_core.reply import reply

from .. import config
from .. import project as files
from ..app import READ_ONLY_LOCAL, Project, mcp, tool_errors


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "Project information"}, output_schema=None)
@tool_errors
async def wpilib_info(project: Project = None) -> ToolResult:
    """Team number, WPILib year, GradleRIO and Java versions, deploy targets, installed vendordeps (and offline ones available), deploy allowed."""
    folder = await config.source.resolve_async(project)
    out = {**await asyncio.to_thread(files.info, folder), "deploy_allowed": config.deploy.enabled()}
    if notice := config.source.notice():
        out["hint"] = notice
    return reply(out)
