"""The log side of the pose check: find the robot's pose in an open log and shape the reply (the check is `frc_mcp_wpilib.pose`)."""

from typing import Any

from frc_mcp_wpilib import pose as core
from frc_mcp_wpilib.tracking import NothingToCompare

from .checks import no_match, require_topics, scope_for
from .logfile import serialized
from .loglike import LogLike
from .windows import Range


@serialized
def check_pose(log: LogLike, pose: str | None, reference: str | None, window: Range) -> dict[str, Any]:
    """The `wpilog_pose` answer: the pose's jumps over the window (default: while enabled) and, with a reference pose, how far apart the two were.
    Raises WPILogError for a named topic that is missing or is not a `Pose2d`."""
    require_topics(log, (pose, reference), (core.POSE_TYPE,), f"a pose is {core.POSE_TYPE}")
    pick = core.pick_pose({t.name: t.type_str for t in log.topics.values() if not t.hidden}, pose)
    if pick.pose is None:
        why = f"{pose!r} is not a Pose2d topic" if pose else "no pose topic follows a known name; pass pose="
        return no_match({"candidates": pick.others} if pick.others else {}, why)
    scope = scope_for(log, window)
    rows = log.rows([pick.pose, *([reference] if reference else [])])
    try:
        out = core.analyze_pose(rows[pick.pose], scope.windows, scope.origin, rows[reference] if reference else None)
    except NothingToCompare as e:
        return no_match({"pose": pick.pose}, str(e))
    reply: dict[str, Any] = {"pose": pick.pose, "chosen_by": pick.how, **({"reference": reference} if reference else {}), "t0": scope.label, "scope": scope.name, **out}
    if pick.others:
        reply["others"] = pick.others
    return reply
