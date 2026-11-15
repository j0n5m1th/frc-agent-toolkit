"""Functional test of `mcp-wpilib-vscode`: the installed command, over stdio, against the minimal robot project.

The Gradle commands themselves need a real build, so these checks cover what the server answers from the project
files alone: `wpilib_info`, the one edit tool that needs no build, the two refusals, and the toolset switch.
"""

import shutil
from typing import Any

from harness import (
    OUTPUT,
    Report,
    call,
    client,
    is_unknown_tool,
    make_project,
    tool_names,
)

GATED_TOOLS = {"wpilib_deploy"}  # hidden until VSCODE_ALLOW_DEPLOY=1
FULL_TOOLS = {"wpilib_info", "wpilib_run", "wpilib_set_team", "wpilib_tool", "wpilib_vendordep"}  # what is listed with the switch off
CORE_TOOLS = {"wpilib_info", "wpilib_run"}
FACTS: dict[str, Any] = {"team": 2559, "year": "2025", "language": "java", "gradlerio": "2025.3.2", "java": 17}


async def run(keep: bool = False) -> bool:
    report = Report("vscode server")
    folder = OUTPUT / "vscode"
    shutil.rmtree(folder, ignore_errors=True)
    project = make_project(folder)
    env = {"VSCODE_PROJECT_DIR": str(project)}
    try:
        async with client("mcp-wpilib-vscode", env) as c:
            report.check("offers the 5 tools that are always there, and hides wpilib_deploy", await tool_names(c) == FULL_TOOLS)

            report.check("offers the one prompt", {p.name for p in await c.list_prompts()} == {"check_build"})

            info = await call(c, "wpilib_info")
            report.check("reports the project facts it can read", {k: info.get(k) for k in FACTS} == FACTS, info)
            report.check("lists the installed vendordep", info.get("installed") == {"PathplannerLib": "2025.1.1"}, info)
            report.check("deploy is off without VSCODE_ALLOW_DEPLOY", info.get("deploy_allowed") is False, info)

            written = await call(c, "wpilib_set_team", {"team": 1234})
            report.check("sets the team number", written.get("team") == 1234 and written.get("was") == 2559, written)
            again = await call(c, "wpilib_info")
            report.check("the new team number reads back from disk", again.get("team") == 1234, again)

            hidden = await call(c, "wpilib_deploy")
            report.check("deploy is not a tool while VSCODE_ALLOW_DEPLOY is off", is_unknown_tool(hidden, "wpilib_deploy"), hidden)

            missing = await call(c, "wpilib_info", {"project": "no_such_folder"})
            report.check("a folder that is not there is an error, not a crash", "EXC" in missing, missing)

        async with client("mcp-wpilib-vscode", {**env, "VSCODE_ALLOW_DEPLOY": "1"}) as c:
            report.check("VSCODE_ALLOW_DEPLOY=1 lists wpilib_deploy too (6 tools)", await tool_names(c) == FULL_TOOLS | GATED_TOOLS)
            info = await call(c, "wpilib_info")
            report.check("deploy is reported as allowed", info.get("deploy_allowed") is True, info)
            bad_team = await call(c, "wpilib_deploy", {"team": 99999})  # rejected before any Gradle command runs
            report.check("deploy with a team number that cannot be right is an error, nothing runs",
                         "EXC" in bad_team and not is_unknown_tool(bad_team, "wpilib_deploy"), bad_team)

        async with client("mcp-wpilib-vscode", {**env, "VSCODE_TOOLSET": "core"}) as c:
            report.check("VSCODE_TOOLSET=core keeps only the 2 always-on tools (deploy still off)", await tool_names(c) == CORE_TOOLS)
        async with client("mcp-wpilib-vscode", {**env, "VSCODE_TOOLSET": "core", "VSCODE_ALLOW_DEPLOY": "1"}) as c:
            report.check("core with deploy allowed keeps wpilib_deploy", await tool_names(c) == CORE_TOOLS | GATED_TOOLS)
    finally:
        if not keep:
            shutil.rmtree(folder, ignore_errors=True)
    return report.summary()
