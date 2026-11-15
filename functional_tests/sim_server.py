"""Functional test of `mcp-wpilib-sim`: the installed command, over stdio, against the minimal robot project.

Gradle itself is not run here (no build in these checks): the idle status, the settings in force, and the two
cheap replies are what a first session sees, the two refusals that need no wrapper to execute, and the toolset switch.
"""

import shutil
from typing import Any

from harness import OUTPUT, Report, call, client, make_project

TOOLS = {"sim_output", "sim_start", "sim_status", "sim_stop"}
CORE_TOOLS = {"sim_start", "sim_status", "sim_stop"}
PROMPTS = {"run_scenario", "verify_startup"}
SETTINGS: dict[str, Any] = {"nt_port": 58997, "max_run_s": 60, "default_task": "simulateJava"}


async def run(keep: bool = False) -> bool:
    report = Report("sim server")
    folder = OUTPUT / "sim"
    shutil.rmtree(folder, ignore_errors=True)
    project = make_project(folder)
    env = {"SIM_PROJECT_DIR": str(project), "SIM_NT_PORT": "58997", "SIM_MAX_RUN_S": "60", "SIM_TASK": "simulateJava"}
    try:
        async with client("mcp-wpilib-sim", env) as c:
            report.check("offers exactly the 4 documented tools", {t.name for t in await c.list_tools()} == TOOLS)

            report.check("offers the two prompts", {p.name for p in await c.list_prompts()} == PROMPTS)

            status = await call(c, "sim_status")
            report.check("reports idle with the settings in force",
                         status.get("state") == "idle" and {k: status.get(k) for k in SETTINGS} == SETTINGS, status)
            report.check("the NT port is not answering", status.get("nt_port_open") is False, status)

            stopped = await call(c, "sim_stop")
            report.check("sim_stop with nothing run says so as a hint", "No sim has run" in str(stopped.get("hint", "")), stopped)

            empty = await call(c, "sim_output")
            report.check("sim_output before any run is an error, not a crash", "EXC" in empty, empty)

            refused = await call(c, "sim_start", {"task": "build"})
            report.check("only simulate* tasks are accepted", "EXC" in refused and "simulate" in str(refused), refused)

        async with client("mcp-wpilib-sim", {**env, "SIM_TOOLSET": "core"}) as c:
            report.check("SIM_TOOLSET=core keeps only the 3 always-on tools", {t.name for t in await c.list_tools()} == CORE_TOOLS)
    finally:
        if not keep:
            shutil.rmtree(folder, ignore_errors=True)
    return report.summary()
