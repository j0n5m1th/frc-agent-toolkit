"""Read tools: jfr_summary, jfr_hotspots, jfr_memory, jfr_around and jfr_compare (all read-only, over one or two recordings)."""

import asyncio
from pathlib import Path
from typing import Annotated, Any, Literal

from fastmcp.tools import ToolResult
from pydantic import Field

from .. import analysis, config, jdk, recorder
from ..app import LOCAL_ONLY, By, Event, File, Top, mcp, tool_errors
from ..errors import JfrError
from ..support import reply


async def _open(file: str) -> tuple[Path, jdk.Summary]:
    """The recording a name refers to (checked, off the event loop) and its `jfr summary`; a recording still being made says how long to wait."""
    recorder.check_ready(file)
    path = await asyncio.to_thread(config.resolve_recording, file)
    return path, await jdk.summary(path)


async def _events(path: Path, event: str, depth: int, s: jdk.Summary) -> list[dict[str, Any]]:
    """The events of one type: a JfrError says which types the recording does have, or that the answer would be too big to read."""
    jdk.refuse_secret_events([event])
    if not s.count([event]):
        raise JfrError(f"{path.name} has no {event} events. Most common types in it: {', '.join(sorted(s.events, key=lambda k: -s.events[k])[:8])}.")
    jdk.check_output_size(s, [event], depth)
    return await jdk.events(path, [event], depth)


@mcp.tool(annotations={**LOCAL_ONLY, "title": "Recording summary"}, output_schema=None)
@tool_errors
async def jfr_summary(file: File, top: Top = 15) -> ToolResult:
    """Recording summary: length, start time, common event types. Start here."""
    path, s = await _open(file)
    ranked = sorted(s.events.items(), key=lambda kv: kv[1], reverse=True)
    return reply({"file": path.name, "seconds": s.seconds, "start_utc": s.start.isoformat() if s.start else None,
                  "event_types": len(ranked), "events": [{"type": k, "n": n} for k, n in ranked[:top]]})


@mcp.tool(annotations={**LOCAL_ONLY, "title": "Ranked hot spots"}, output_schema=None)
@tool_errors
async def jfr_hotspots(
    file: File,
    event: Event,
    *,
    by: By,
    top: Top = 12,
    sort: Annotated[analysis.Sort, Field(description="Rank by")] = "total",
    match: Annotated[str | None, Field(description="Keep events containing this; by=stack then shows callers")] = None,
) -> ToolResult:
    """Hotspots: rank groups of one event type: n, total_ms, avg_us, max_us, max_at_ms (for jfr_around). Samples: by count. by=stack is the call path; cumulative counts every frame."""
    path, s = await _open(file)
    wanted = analysis.stack_depth(by, match)
    depth = jdk.fit_depth(s.count([event]), wanted, floor=min(wanted, 4)) if wanted > 1 else wanted  # deep stacks are big: keep as many frames as fit
    found = await _events(path, event, depth, s)
    return reply({"file": path.name, **analysis.hotspots(found, by, s.start, top, sort, match), **({"stack_depth": depth} if depth < wanted else {})})


@mcp.tool(annotations={**LOCAL_ONLY, "title": "Memory, GC and allocation"}, output_schema=None)
@tool_errors
async def jfr_memory(
    file: File,
    *,
    view: Annotated[Literal["gc", "alloc", "leaks"], Field(description="gc pauses and heap; alloc by class or site; leaks candidates")],
    group_by: Annotated[Literal["class", "site"], Field(description="alloc: group by")] = "class",
    match: Annotated[str | None, Field(description="alloc: class substring; adds real call stacks")] = None,
    top: Top = 10,
) -> ToolResult:
    """GC pauses and heap trend, allocation by class or site, or leak candidates (needs 90+ s). Read the real stacks (match=) before blaming a method."""
    path, s = await _open(file)
    if view == "alloc":
        depth = analysis.stack_depth("frame" if group_by == "site" else "class")
        if match:  # real call stacks are the costly part: keep as many frames as the size limit allows (at least 2)
            depth = jdk.fit_depth(s.count(["jdk.ObjectAllocationSample"]), 8, floor=2)
        samples = await _events(path, "jdk.ObjectAllocationSample", depth, s)
        return reply({"file": path.name, **analysis.allocation_report(samples, group_by, match, top, s.duration_s), **({"stack_depth": depth} if match else {})})
    types = ["jdk.GarbageCollection", "jdk.GCHeapSummary", *(["jdk.OldObjectSample"] if view == "leaks" else [])]
    jdk.check_output_size(s, types, 0)
    by_type: dict[str, list[dict[str, Any]]] = {t: [] for t in types}
    for e in await jdk.events(path, types):
        by_type[e["type"]].append(e)
    out: dict[str, Any] = {"file": path.name, "seconds": s.seconds, **analysis.gc_report(by_type[types[0]], by_type[types[1]], s.start, top)}
    if view == "leaks":
        out["old_objects"] = analysis.leak_report(by_type["jdk.OldObjectSample"], top)
    return reply(out)


@mcp.tool(annotations={**LOCAL_ONLY, "title": "What happened near a moment"}, output_schema=None)
@tool_errors
async def jfr_around(
    file: File,
    at_ms: Annotated[float, Field(description="ms from recording start, e.g. max_at_ms")],
    window_ms: Annotated[float, Field(ge=1, description="Either side")] = 100,
) -> ToolResult:
    """GC, JIT compilation and deoptimization near a moment: check these before blaming the code for one slow call."""
    path, s = await _open(file)
    if s.start is None:
        raise JfrError("This recording's start time could not be read, so moments cannot be placed.")
    jdk.check_output_size(s, list(analysis.NEAR_EVENTS), 0)
    found = await jdk.events(path, list(analysis.NEAR_EVENTS))
    return reply({"file": path.name, "at_ms": at_ms, "events": analysis.around(found, s.start, at_ms, window_ms)})


@mcp.tool(annotations={**LOCAL_ONLY, "title": "Compare two recordings"}, output_schema=None)
@tool_errors
async def jfr_compare(
    event: Event,
    *,
    by: By,
    before: Annotated[str | None, Field(description="Recording; default: the second-newest")] = None,
    after: Annotated[str | None, Field(description="Recording; default: the newest")] = None,
    top: Top = 12,
) -> ToolResult:
    """Compare recordings: per-group before/after and the NET change over all groups: time often moves to another label, so trust net_ms. Without before/after, compares the two newest recordings."""
    if (before is None) != (after is None):
        raise JfrError("Give both before and after, or neither (the two newest recordings are compared).")
    if before is None or after is None:
        newest = await asyncio.to_thread(config.find_recordings)
        if len(newest) < 2:
            raise JfrError("Comparing needs two recordings; jfr_list shows what there is.")
        before, after = newest[1].name, newest[0].name
    opened = await asyncio.gather(_open(before), _open(after))
    runs = await asyncio.gather(*(_events(path, event, analysis.stack_depth(by), s) for path, s in opened))
    return reply({"before": opened[0][0].name, "after": opened[1][0].name, **analysis.compare(runs[0], runs[1], by, top)})
