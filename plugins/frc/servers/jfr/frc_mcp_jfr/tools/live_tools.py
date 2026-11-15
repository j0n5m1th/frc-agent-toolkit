"""jfr_live: look at a running JVM right now (class growth, threads, deadlocks) without a recording."""

import asyncio
from typing import Annotated, Any, Literal

from fastmcp.tools import ToolResult
from pydantic import Field

from .. import jdk, live
from ..app import HIDDEN_IN_CORE, LOCAL_ONLY, Top, mcp, tool_errors
from ..errors import JfrError
from ..support import reply


@mcp.tool(annotations={**LOCAL_ONLY, "title": "Look at a running JVM"}, output_schema=None, tags={HIDDEN_IN_CORE})
@tool_errors
async def jfr_live(
    pid: Annotated[int, Field(description="From jfr_list jvms")],
    what: Annotated[Literal["threads", "heap_growth"], Field(description="threads (lock waits, deadlock) or heap_growth")] = "threads",
    seconds: Annotated[int, Field(ge=1, le=60, description="heap_growth: gap between the two snapshots")] = 5,
    top: Top = 12,
) -> ToolResult:
    """Live JVM check, no recording. heap_growth: two class histograms `seconds` apart (each forces a GC): a quick leak check."""
    own = await jdk.jcmd_for(pid)
    if what == "threads":
        return reply({"pid": pid, **live.thread_report(await jdk.run("jcmd", [str(pid), "Thread.print"], tool_path=own), top)})
    before = live.parse_histogram(await jdk.run("jcmd", [str(pid), "GC.class_histogram"], tool_path=own))
    if not before:
        raise JfrError("jcmd printed no class histogram for this JVM.")
    await asyncio.sleep(seconds)
    after = live.parse_histogram(await jdk.run("jcmd", [str(pid), "GC.class_histogram"], tool_path=own))
    if not after:
        raise JfrError("jcmd printed no class histogram for this JVM on the second snapshot.")
    out: dict[str, Any] = {"pid": pid, "seconds": seconds, **live.histogram_growth(before, after, top)}
    return reply(out)
