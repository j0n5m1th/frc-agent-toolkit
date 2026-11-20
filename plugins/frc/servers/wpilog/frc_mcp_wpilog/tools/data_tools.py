"""Data tools: wpilog_get_values, wpilog_query, wpilog_when, wpilog_text, wpilog_diff and wpilog_scan."""

import asyncio
from typing import Annotated, Any

from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply, with_status
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
from ..correlate_checks import correlate
from ..mechanism_checks import mechanism
from ..pose_checks import check_pose
from ..power_checks import power
from ..scan_queries import scan
from ..store import logs
from ..swerve_checks import swerve

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
    return reply(with_status(out), topics=len(out))


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
    by_mode: Annotated[bool, Field(description="Answer once per enabled period (auto, teleop, ...); times are ms from log start")] = False,
) -> ToolResult:
    """Log data over a window, reduced; times are ms after time zero (`align`). Shows what was logged (updates the robot never recorded are invisible). A topic that cannot be reduced gets its own err. Unchanging topics are under `constants`; `all` holds fields every topic shares."""
    spec = queries.QuerySpec(reduce=reduce, fields=fields, threshold=threshold, max_samples=max_samples,
                             max_topics=max_topics, precision=DEFAULT_PRECISION)
    window = queries.Range(align, since_ms, until_ms)
    if by_mode:
        if compare_with:
            raise ToolError("by_mode and compare_with cannot be combined; run them one at a time.")
        if since_ms is not None or until_ms is not None or align != "start":
            raise ToolError("by_mode uses each enabled period as the window; leave since_ms, until_ms and align unset (times are ms from log start).")
        out = await asyncio.to_thread(queries.by_mode, logs.get(log), topics, spec)
        return reply(out, topics=len(topics) * len(out["modes"]))
    if compare_with:
        picked = logs.pick([logs.resolve(log), *compare_with])
        out = await asyncio.to_thread(queries.compare, picked, topics, spec, window)
        return reply(out, ("data",))
    out = await asyncio.to_thread(queries.query, logs.get(log), topics, spec, window)
    return reply(with_status(out, out["data"].values(), ok=len(out.get("constants", ()))), ("data", "constants"))  # a topic that never changed is an answer too


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
    sample: Annotated[list[str] | None, Field(max_length=5, description="Topics to read at the start of each interval or change; `sampled` lists line up with them")] = None,
) -> ToolResult:
    """When a condition was true, e.g. current > 40: count, total and longest ms, and [start,end] ms intervals. A value holds until the next update. `also` narrows it to when other conditions held too."""
    opened = logs.get(log)
    out = await asyncio.to_thread(queries.when, opened, topic, op, value, field, queries.Range(align, since_ms, until_ms),
                                  max_intervals, also or (), sample or ())
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
    if not out["searched"]:  # no text topic to look in: that is not "no errors"
        reason = "none of the named topics are in this log" if out.get("not_in_log") else "this log has no text topic to search"
        out = with_status(out, (), reason=reason)
    return reply(out)


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "Check the swerve modules"}, output_schema=None)
@tool_errors
async def wpilog_swerve(
    measured: Annotated[str | None, Field(description="Measured SwerveModuleState[] topic, if its name is not a usual one")] = None,
    setpoint: Annotated[str | None, Field(description="Commanded SwerveModuleState[] topic")] = None,
    align: Align = "start",
    since_ms: SinceMs = None,
    until_ms: UntilMs = None,
    log: LogId = None,
) -> ToolResult:
    """Swerve: how closely each module followed its command (speed and angle error, top speed) and which module lags the others. Default window: while enabled. Says which topics it used."""
    out = await asyncio.to_thread(swerve, logs.get(log), measured, setpoint, queries.Range(align, since_ms, until_ms))
    return reply(out)


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "Do two signals move together"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def wpilog_correlate(
    a: Annotated[str, Field(description="First signal (a number topic)")],
    b: Annotated[str, Field(description="Second signal (a number topic)")],
    max_lag_s: Annotated[float, Field(ge=0, le=10, description="Also look for b following a by up to this many seconds")] = 1.0,
    align: Align = "start",
    since_ms: SinceMs = None,
    until_ms: UntilMs = None,
    log: LogId = None,
) -> ToolResult:
    """Do two signals move together? Pearson's r, how strong, and the lag at which they match best. Correlation is not cause. Default window: while enabled."""
    return reply(await asyncio.to_thread(correlate, logs.get(log), a, b, max_lag_s, queries.Range(align, since_ms, until_ms)))


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "Check the robot pose"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def wpilog_pose(
    pose: Annotated[str | None, Field(description="Robot pose (a Pose2d topic), if its name is not a usual one")] = None,
    reference: Annotated[str | None, Field(description="A second Pose2d (vision, or odometry alone) to compare it with")] = None,
    align: Align = "start",
    since_ms: SinceMs = None,
    until_ms: UntilMs = None,
    log: LogId = None,
) -> ToolResult:
    """Robot pose: jumps (moves faster than a robot can), and with a reference pose how far apart the two were in position and heading. Default window: while enabled."""
    return reply(await asyncio.to_thread(check_pose, logs.get(log), pose, reference, queries.Range(align, since_ms, until_ms)))


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "Check a mechanism's tracking"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def wpilog_mechanism(
    setpoint: Annotated[str, Field(description="The mechanism's commanded value (a number topic)")],
    measurement: Annotated[str, Field(description="Its measured value, in the same units")],
    velocity: Annotated[str | None, Field(description="Its velocity, for stalls")] = None,
    current: Annotated[str | None, Field(description="Its motor current in amps, for stalls")] = None,
    temperature: Annotated[str | None, Field(description="Its motor temperature")] = None,
    align: Align = "start",
    since_ms: SinceMs = None,
    until_ms: UntilMs = None,
    log: LogId = None,
) -> ToolResult:
    """One mechanism (arm, elevator, flywheel): following error against its setpoint, step overshoot and settling, stalls (stopped with high current), temperature. You name the signals; the units of setpoint and measurement must match. Default window: while enabled."""
    roles = {"setpoint": setpoint, "measurement": measurement, "velocity": velocity, "current": current, "temperature": temperature}
    return reply(await asyncio.to_thread(mechanism, logs.get(log), roles, queries.Range(align, since_ms, until_ms)))


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "Which device drew the current"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def wpilog_power(
    battery: Annotated[str | None, Field(description="Battery voltage topic, if its name is not a usual one")] = None,
    currents: Annotated[list[str] | None, Field(max_length=40, description="Current topics (a number or an array of numbers), if not the usual ones")] = None,
    align: Align = "start",
    since_ms: SinceMs = None,
    until_ms: UntilMs = None,
    log: LogId = None,
) -> ToolResult:
    """Power: the battery low point and brownouts, the highest-current devices with when, and what each drew when the battery was lowest. Default window: while enabled. Channels get names from the robot's PdhChannels map."""
    out = await asyncio.to_thread(power, logs.get(log), battery, currents or (), queries.Range(align, since_ms, until_ms))
    return reply(out)


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "Scan a log for trouble"}, output_schema=None)
@tool_errors
async def wpilog_scan(log: LogId = None, battery: Annotated[str | None, Field(description="Battery voltage topic, if its name is not a usual one")] = None) -> ToolResult:
    """Usual trouble in one call: errors, loop time and overruns, battery sag, brownouts, link numbers, CAN use and errors, stopped signals. `flags` rank high to low; says which topics it used; times are s after first auto."""
    opened = logs.get(log)
    return reply(await asyncio.to_thread(scan, opened, battery))
