"""The live checks that need only a few seconds of recording: power, mechanism tracking, correlation and pose.

Each is the live half of a log tool (`wpilog_power`, `wpilog_mechanism`, `wpilog_correlate`, `wpilog_pose`) and runs the same shared check from
`frc_mcp_wpilib` on a recording instead of a log. A live recording cannot tell when the robot was enabled, so it covers the whole recording.
"""

from typing import Annotated, Any

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply, with_status
from frc_mcp_wpilib import correlate as correlate_core
from frc_mcp_wpilib import mechanism as mechanism_core
from frc_mcp_wpilib import pose as pose_core
from frc_mcp_wpilib import power as power_core
from frc_mcp_wpilib.scan_roles import NUMBER_TYPES, ScanError, ScanTopic
from frc_mcp_wpilib.tracking import NothingToCompare

from ..app import FULL_ONLY_TAG, LOCAL_STATE, mcp, tool_errors
from ..errors import NT4Error
from ..session import session
from .live import first_time, record_rows, topic_types

Seconds = Annotated[float, Field(gt=0, le=30, description="Seconds to record")]


def no_match(facts: dict[str, Any], reason: str) -> ToolResult:
    """A reply that says nothing could be checked (`status: no_match`) and why."""
    return reply(with_status(facts, (), reason=reason))


@mcp.tool(annotations={**LOCAL_STATE, "title": "Which device drew the current (live)"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def nt4_power(
    duration_s: Seconds = 5.0,
    battery: Annotated[str | None, Field(description="Battery voltage topic, if its name is not a usual one")] = None,
    currents: Annotated[list[str] | None, Field(max_length=40, description="Current topics (a number or an array of numbers), if not the usual ones")] = None,
) -> ToolResult:
    """Power, live: record the battery and the device currents and say which devices drew the most, and what each drew when the battery was lowest. Channels get names from the robot's PdhChannels map."""
    async with session.operation():
        known = {name: ScanTopic(name, type_str, 0, session.topic_unit(name)) for name, type_str in topic_types().items()}
        try:
            plan = power_core.plan_power(list(known.values()), known, battery, currents or (), "nt4_list_topics finds names")
        except ScanError as e:
            raise NT4Error(str(e)) from e
        rows = await record_rows(plan.names, duration_s)
    try:
        out = power_core.run_power(plan, rows, [], first_time(rows))
    except NothingToCompare as e:
        return no_match(plan.facts(), f"{e}; name the signals with battery= and currents=")
    return reply({"seconds": duration_s, **out})


@mcp.tool(annotations={**LOCAL_STATE, "title": "Check a mechanism's tracking (live)"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def nt4_mechanism(
    setpoint: Annotated[str, Field(description="The mechanism's commanded value (a number topic)")],
    measurement: Annotated[str, Field(description="Its measured value, in the same units")],
    duration_s: Seconds = 5.0,
    velocity: Annotated[str | None, Field(description="Its velocity, for stalls")] = None,
    current: Annotated[str | None, Field(description="Its motor current in amps, for stalls")] = None,
    temperature: Annotated[str | None, Field(description="Its motor temperature")] = None,
) -> ToolResult:
    """One mechanism, live: record it and report following error against its setpoint, step overshoot and settling, stalls, temperature. You name the signals; setpoint and measurement must share units. Someone must be moving the mechanism."""
    roles = {"setpoint": setpoint, "measurement": measurement, "velocity": velocity, "current": current, "temperature": temperature}
    named = {role: name for role, name in roles.items() if name}
    async with session.operation():
        types = topic_types()
        for name in named.values():
            if name not in types:
                raise NT4Error(f"{name!r} is not among the topics (nt4_list_topics finds names).")
            if types[name] not in NUMBER_TYPES:
                raise NT4Error(f"{name!r} is type {types[name]}; it must be a number (double, float or int).")
        rows = await record_rows(list(dict.fromkeys(named.values())), duration_s)
    try:
        out = mechanism_core.analyze_mechanism(rows[setpoint], rows[measurement], [], first_time(rows),
                                               **{role: rows[name] for role, name in named.items() if role not in ("setpoint", "measurement")})
    except NothingToCompare as e:
        return no_match({"roles": named}, f"{e}; was the mechanism moving, and are the signals being published?")
    return reply({"roles": named, "seconds": duration_s, **out})


@mcp.tool(annotations={**LOCAL_STATE, "title": "Do two signals move together (live)"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def nt4_correlate(
    a: Annotated[str, Field(description="First signal (a number topic)")],
    b: Annotated[str, Field(description="Second signal (a number topic)")],
    duration_s: Seconds = 5.0,
    max_lag_s: Annotated[float, Field(ge=0, le=10, description="Also look for b following a by up to this many seconds")] = 1.0,
) -> ToolResult:
    """Do two signals move together, live? Records both; Pearson's r, how strong, and the lag at which they match best. Correlation is not cause. Someone must be driving the robot."""
    async with session.operation():
        types = topic_types()
        for name in (a, b):
            if name not in types:
                raise NT4Error(f"{name!r} is not among the topics (nt4_list_topics finds names).")
            if types[name] not in NUMBER_TYPES:
                raise NT4Error(f"{name!r} is type {types[name]}; it must be a number (double, float or int).")
        rows = await record_rows([a, b], duration_s)
    try:
        out = correlate_core.analyze_correlation(rows[a], rows[b], [], max_lag_s)
    except NothingToCompare as e:
        return no_match({"a": a, "b": b}, f"{e}; was the robot moving?")
    return reply({"a": a, "b": b, "seconds": duration_s, **out})


@mcp.tool(annotations={**LOCAL_STATE, "title": "Check the robot pose (live)"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def nt4_pose(
    duration_s: Seconds = 5.0,
    pose: Annotated[str | None, Field(description="Robot pose (a Pose2d topic), if its name is not a usual one")] = None,
    reference: Annotated[str | None, Field(description="A second Pose2d (vision, or odometry alone) to compare it with")] = None,
) -> ToolResult:
    """Robot pose, live: jumps (moves faster than a robot can), and with a reference pose how far apart the two were in position and heading. Someone must be driving the robot."""
    async with session.operation():
        types = topic_types()
        for name in (reference, pose):
            if name and types.get(name) != pose_core.POSE_TYPE:
                raise NT4Error(f"{name!r} is not a {pose_core.POSE_TYPE} topic (nt4_list_topics finds names).")
        pick = pose_core.pick_pose(types, pose)
        if pick.pose is None:
            return no_match({"candidates": pick.others} if pick.others else {}, "no pose topic follows a known name; pass pose=")
        rows = await record_rows([pick.pose, *([reference] if reference else [])], duration_s)
    try:
        out = pose_core.analyze_pose(rows[pick.pose], [], first_time(rows), rows[reference] if reference else None)
    except NothingToCompare as e:
        return no_match({"pose": pick.pose}, f"{e}; was the robot moving?")
    reply_: dict[str, Any] = {"pose": pick.pose, "chosen_by": pick.how, **({"reference": reference} if reference else {}), "seconds": duration_s, **out}
    if pick.others:
        reply_["others"] = pick.others
    return reply(reply_)
