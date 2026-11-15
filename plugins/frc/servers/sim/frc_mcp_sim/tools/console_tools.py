"""Console tools: sim_status (state and counts) and sim_output (the lines)."""

from typing import Annotated, Any

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import PAGE_NEXT_OFFSET, PAGE_OMITTED, reply
from frc_mcp_wpilib.gradle.console import fold

from ..app import FULL_ONLY_TAG, READ_ONLY_LOCAL, Level, NameContains, mcp, tool_errors
from ..errors import SimError
from ..session import session


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "Simulation status"}, output_schema=None)
@tool_errors
async def sim_status() -> ToolResult:
    """Simulation state (idle|building|starting|ready|exited|stopped|timed_out), counts, exit code, NT port, wpilog path, and the settings in force (nt_port, max_run_s, default_task)."""
    return reply(await session.status())


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "Simulation console"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def sim_output(
    since: Annotated[int | None, Field(ge=0, description="After this line number")] = None,
    tail: Annotated[int, Field(ge=1, le=300, description="Max lines, newest kept")] = 20,
    level: Level = "all",
    name_contains: NameContains = None,
) -> ToolResult:
    """Simulation console lines '<n> text'; repeats folded as (xN). Pass the reply's next_offset as since."""
    run = session.run
    if run is None:
        raise SimError("No sim has run yet; call sim_start.")
    lines = run.console.select(since=since, level=level, contains=name_contains)
    shown = fold(lines)
    out: dict[str, Any] = {"state": run.phase, PAGE_NEXT_OFFSET: run.console.total}
    if len(shown) > tail:
        out[PAGE_OMITTED] = len(shown) - tail
        shown = shown[-tail:]
    out["lines"] = shown
    return reply(out)
