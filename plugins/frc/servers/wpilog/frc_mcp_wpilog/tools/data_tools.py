"""Data tools: wpilog_get_values, wpilog_query, wpilog_when, wpilog_text and wpilog_diff."""

import asyncio
from typing import Annotated, Any

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply
from frc_mcp_wpilib.conditions import CompareOp
from frc_mcp_wpilib.values import DEFAULT_PRECISION

from .. import queries
from ..app import (
    FULL_ONLY_TAG,
    READ_ONLY_LOCAL,
    Align,
    Also,
    Fields,
    Limit,
    LogId,
    MaxRows,
    MaxSamples,
    MaxTopics,
    NameContains,
    Prefix,
    ReduceParam,
    SinceMs,
    StructField,
    Threshold,
    Topics,
    UntilMs,
    mcp,
    tool_errors,
)
from ..store import logs

Names = Annotated[list[str], Field(min_length=1, max_length=50, description="Topic names; a trailing * means every topic under that prefix, e.g. ['/Robot/Shooter/*']")]


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "Values at a time"}, output_schema=None)
@tool_errors
async def wpilog_get_values(
    topics: Topics,
    at_ms: Annotated[float | None, Field(description="Time (ms after time zero); default: the end of the log")] = None,
    align: Align = "start",
    fields: Fields = None,
    log: LogId = None,
) -> ToolResult:
    """Log values at a moment: each topic's last update at or before it, and when it happened. Structs decode to dotted fields."""
    opened = logs.get(log)
    out = await asyncio.to_thread(queries.values_at, opened, topics, at_ms, fields, align)
    return reply(out, topics=len(out))


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "Query a log"}, output_schema=None)
@tool_errors
async def wpilog_query(
    topics: Names,
    reduce: ReduceParam = "summary",
    fields: Fields = None,
    since_ms: SinceMs = None,
    until_ms: UntilMs = None,
    align: Align = "start",
    threshold: Threshold = None,
    max_samples: MaxSamples = 20,
    max_topics: MaxTopics = 40,
    log: LogId = None,
    compare_with: Annotated[list[str] | None, Field(max_length=3, description="More open log ids: ask each the same question, side by side per topic (before/after a change); each log aligns on its own")] = None,
) -> ToolResult:
    """Log data over a window, reduced; times are ms after time zero (`align`). Shows what was logged (updates the robot never recorded are invisible). A topic that cannot be reduced gets its own err. Unchanging topics are under `constants`; `all` holds fields every topic shares."""
    spec = queries.QuerySpec(reduce=reduce, fields=fields, threshold=threshold, max_samples=max_samples,
                             max_topics=max_topics, precision=DEFAULT_PRECISION)
    window = queries.Range(align, since_ms, until_ms)
    if compare_with:
        picked = logs.pick([logs.resolve(log), *compare_with])
        out = await asyncio.to_thread(queries.compare, picked, topics, spec, window)
        return reply(out, ("data",))
    out = await asyncio.to_thread(queries.query, logs.get(log), topics, spec, window)
    return reply(out, ("data", "constants"))


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "When a condition held"}, output_schema=None)
@tool_errors
async def wpilog_when(
    topic: Annotated[str, Field(description="Exact topic name")],
    op: Annotated[CompareOp, Field(description="== != > >= < <= | changed (times the value changed)")],
    value: Annotated[Any, Field(description="Comparison value (not for changed)")] = None,
    field: StructField = None,
    since_ms: SinceMs = None,
    until_ms: UntilMs = None,
    align: Align = "start",
    max_intervals: Limit = 20,
    log: LogId = None,
    also: Also = None,
) -> ToolResult:
    """When a condition was true, e.g. current > 40: count, total and longest ms, and [start,end] ms intervals. A value holds until the next update. `also` narrows it to when other conditions held too."""
    opened = logs.get(log)
    out = await asyncio.to_thread(queries.when, opened, topic, op, value, field, queries.Range(align, since_ms, until_ms),
                                  max_intervals, also or ())
    return reply(out)


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "Compare settings of two logs"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def wpilog_diff(
    other: Annotated[str, Field(description="Id of the second open log to compare with (open both first)")],
    prefix: Prefix = "",
    name_contains: NameContains = None,
    limit: MaxRows = 50,
    log: LogId = None,
) -> ToolResult:
    """Compare two logs: settings (tunables, constants) that differ: `changed` value, `only_in` one log, `varied_in` (changed during one log). Busy signals are skipped. Answers 'what changed between these matches?'"""
    picked = logs.pick([logs.resolve(log), other])
    out = await asyncio.to_thread(queries.diff, picked, prefix, name_contains, limit)
    return reply(out, ("changed",))


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "Search log text"}, output_schema=None)
@tool_errors
async def wpilog_text(
    name_contains: NameContains = None,
    topics: Annotated[list[str] | None, Field(max_length=20, description="Text topics to search (default: every string topic)")] = None,
    since_ms: SinceMs = None,
    until_ms: UntilMs = None,
    align: Align = "start",
    limit: Limit = 20,
    log: LogId = None,
) -> ToolResult:
    """Log messages, console output, errors and faults, oldest first. Use it to answer 'what errors or warnings happened?'"""
    opened = logs.get(log)
    out = await asyncio.to_thread(queries.search_text, opened, topics, name_contains, queries.Range(align, since_ms, until_ms), limit)
    return reply(out)
