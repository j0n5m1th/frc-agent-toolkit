"""Run tools: sim_start (launch and wait for startup) and sim_stop."""

import os
from typing import Annotated, Any

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply
from frc_mcp_wpilib.gradle.console import fold

from .. import config
from ..app import START, STOP, Project, mcp, tool_errors
from ..run import SimRun
from ..session import session, wait_port_closed

TAIL_LINES = 15


def _evidence(run: SimRun) -> list[str]:
    """What explains a failed or slow start: the last error lines, else the last lines of any kind."""
    lines = run.console.select(level="error") or run.console.select()
    return fold(lines[-TAIL_LINES:])


@mcp.tool(annotations={**START, "title": "Start simulation"}, output_schema=None)
@tool_errors
async def sim_start(
    props: Annotated[dict[str, str] | None, Field(description="-Pname=value pairs; '' = flag, e.g. {'mode': 'teleop'}")] = None,
    task: Annotated[str | None, Field(description="simulate* Gradle task (default SIM_TASK)")] = None,
    wait_s: Annotated[float, Field(ge=1, le=600, description="Seconds to wait for startup")] = 90,
    run_s: Annotated[float | None, Field(ge=1, description="Auto-stop after this many seconds")] = None,
    project: Project = None,
) -> ToolResult:
    """Start the sim and wait for 'startup complete': state=ready, or ended/still starting with the error lines. One at a time."""
    run = await session.start(project, task, props, run_s)
    await run.wait_until_ready_or_over(wait_s)
    out: dict[str, Any] = await session.status()
    if run.active and not run.console.ready:
        out["hint"] = f"Not ready after {wait_s:g} s (still {run.phase}); poll sim_status or read sim_output."
        out["tail"] = _evidence(run)
    elif not run.active:
        out["hint"] = "The sim ended before reporting startup complete (or right after); the lines below say why."
        out["tail"] = _evidence(run)
    return reply(out)


@mcp.tool(annotations={**STOP, "title": "Stop simulation"}, output_schema=None)
@tool_errors
async def sim_stop() -> ToolResult:
    """Stop the sim and its child processes. The console stays readable."""
    run = await session.stop()
    if run is not None:
        await wait_port_closed(config.nt_port())  # the port closes a moment after the kill; do not report that gap as a survivor
    out: dict[str, Any] = await session.status()
    if run is None:
        out["hint"] = "No sim has run in this session."
    elif out["nt_port_open"]:
        port = config.nt_port()
        find = f"netstat -ano | findstr :{port}" if os.name == "nt" else f"lsof -i :{port}"
        out["hint"] = (f"Port {port} still answers 5 s after the stop: a leftover sim, or another program, holds it. "
                       f"Find its owner with `{find}`; do not kill by name (that ends every Java program).")
    return reply(out)
