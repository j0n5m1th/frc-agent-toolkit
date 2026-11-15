"""Run tools: wpilib_run (build, test, clean, tasks, install_tools), wpilib_deploy and wpilib_tool."""

from typing import Annotated, Any, Literal

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply
from frc_mcp_core.toolset import gated_by

from .. import config, runner
from .. import project as files
from ..app import DEPLOY, FULL_ONLY_TAG, RUN, NameContains, Project, mcp, tool_errors

GRADLE_TASKS = {"build": ["build"], "test": ["test"], "clean": ["clean"], "tasks": ["tasks", "--all"], "install_tools": ["InstallAllTools"]}


@mcp.tool(annotations={**RUN, "title": "Run Gradle command"}, output_schema=None)
@tool_errors
async def wpilib_run(
    command: Annotated[Literal["build", "test", "clean", "tasks", "install_tools"], Field(description="WPILib: Build / Test / Clean, list Gradle tasks, Install tools")],
    name_contains: NameContains = None,
    offline: Annotated[bool, Field(description="Gradle --offline: use the local caches only")] = False,
    project: Project = None,
) -> ToolResult:
    """Build or test: run the Gradle command to the end. Returns ok, seconds, the BUILD line, test lines and, on failure, the error lines."""
    folder = await config.source.resolve_async(project)
    run = await runner.run_to_end(folder, GRADLE_TASKS[command], offline)
    out = runner.outcome(run)
    if command == "tasks" and out["ok"]:
        out["tasks"] = [line.text for line in run.console.select(contains=name_contains) if " - " in line.text][:80]
    return reply(out)


@mcp.tool(annotations={**DEPLOY, "title": "Deploy robot code"}, output_schema=None, tags=gated_by(config.deploy))
@tool_errors
async def wpilib_deploy(
    debug: Annotated[bool, Field(description="WPILib: Debug Robot Code (-PdebugMode); only deploys, the user attaches the debugger in VS Code")] = False,
    skip_tests: Annotated[bool, Field(description="-xcheck")] = False,
    offline: Annotated[bool, Field(description="Gradle --offline: use the local caches only")] = False,
    team: Annotated[int | None, Field(description="Default: teamNumber in the project's preferences")] = None,
    project: Project = None,
) -> ToolResult:
    """Build and deploy to the robot (replaces its code). Needs VSCODE_ALLOW_DEPLOY=1 and the robot reachable."""
    config.deploy.require()
    folder = await config.source.resolve_async(project)
    number = files.check_team(team) if team is not None else files.team_number(folder)
    args = ["deploy", f"-PteamNumber={number}", *(["-PdebugMode"] if debug else []), *(["-xcheck"] if skip_tests else [])]
    run = await runner.run_to_end(folder, args, offline)
    return reply(runner.outcome(run, {"team": number, "debug": debug}))


@mcp.tool(annotations={**RUN, "title": "Start WPILib tool"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def wpilib_tool(
    name: Annotated[Literal["Glass", "SysId", "OutlineViewer", "DataLogTool", "wpical"], Field(description="WPILib: Start Tool")],
    project: Project = None,
) -> ToolResult:
    """Open a WPILib tool window (it keeps running until the user closes it). Run wpilib_run install_tools first if it is missing."""
    folder = await config.source.resolve_async(project)
    run, started = await runner.start_tool(folder, name)  # one owner for the already-running check: the runner's lock
    # One key set on every branch: the shared Gradle base (`ok`, `running`, `seconds`, ...) plus this tool's own keys.
    extra: dict[str, Any] = {"pid": run.pid}
    if not started:
        extra["hint"] = f"{name} is already running."
    return reply(runner.outcome(run, extra))
