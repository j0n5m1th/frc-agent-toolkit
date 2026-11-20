"""Robot pose checks, shared by the log server and the live server: pose jumps, and how far one pose estimate is from another.

A pose is a WPILib `Pose2d` (x and y in meters, heading in radians; 24 bytes). A jump is a pose that moves faster than a robot can: a vision correction, or
an odometry reset. Comparing the robot's pose with a second pose (the vision estimate, or odometry alone) shows how far apart the two were. The caller
names the second pose; a robot pose is found only by a logging convention, never by a suggestive name.
"""

import math
import struct
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from .reduce.stats import US_PER_S, percentile
from .scan_roles import leaf_and_parents
from .scan_rows import Row, Window, everywhere, within
from .tracking import (
    MIN_COMPARED,
    NothingToCompare,
    Samples,
    held_pairs,
    require_enough,
    seconds_since,
    turn_deg,
)

POSE_TYPE = "struct:Pose2d"
POSE_BYTES = 24  # three doubles: x, y, heading
# (folder, leaf) for the robot's pose, then leaves that name it wherever they are; all lower case
CONVENTIONS = (("drivestate", "pose"), ("odometry", "robot"), ("drive", "pose"), (None, "estimatedpose"), (None, "robotpose"))
JUMP_MPS = 8.0  # a pose that moves faster than this between two updates is a jump (an FRC robot is slower)
JUMP_MIN_M = 0.1  # ...and by at least this far, so a tiny step between two close updates is not one
MAX_JUMPS_LISTED = 5
MAX_OTHERS = 3


class Pose(NamedTuple):
    x: float
    y: float
    theta: float  # radians


class PosePick(NamedTuple):
    pose: str | None
    how: str  # explicit, convention or none
    others: list[str]  # other Pose2d topics, not used


def pick_pose(types: Mapping[str, str], pose: str | None = None) -> PosePick:
    """The robot's pose topic: the one named, else a convention above (the first that matches), else none. Other `Pose2d` topics are listed."""
    poses = sorted(name for name, type_str in types.items() if type_str == POSE_TYPE)

    def pick(name: str | None, how: str) -> PosePick:
        return PosePick(name, how, [n for n in poses if n != name][:MAX_OTHERS])

    if pose:
        return pick(pose if pose in poses else None, "explicit")
    for folder, leaf in CONVENTIONS:
        for name in poses:
            found_leaf, parents = leaf_and_parents(name)
            if found_leaf == leaf and (folder is None or folder in parents):
                return pick(name, "convention")
    return pick(None, "none")


def decode_pose(raw: Any) -> Pose:
    """One update as a pose. Raises NothingToCompare for bytes that are not a `Pose2d`."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != POSE_BYTES:
        raise NothingToCompare(f"a pose update must be {POSE_BYTES} bytes (a Pose2d: x, y, heading in radians)")
    return Pose(*struct.unpack("<ddd", bytes(raw)))


def _jumps(rows: Sequence[Row], origin_us: int) -> dict[str, Any]:
    found: list[tuple[float, int, float]] = []  # meters, time, degrees
    previous = decode_pose(rows[0][1]) if rows else None
    for (t0, _), (t1, raw) in zip(rows, rows[1:], strict=False):
        assert previous is not None
        now = decode_pose(raw)
        meters = math.hypot(now.x - previous.x, now.y - previous.y)
        if meters >= JUMP_MIN_M and t1 > t0 and meters / ((t1 - t0) / US_PER_S) > JUMP_MPS:
            found.append((meters, t1, turn_deg(now.theta - previous.theta)))
        previous = now
    found.sort(reverse=True)
    out: dict[str, Any] = {"count": len(found)}
    if found:
        out["largest"] = [{"at_s": seconds_since(t, origin_us), "meters": round(m, 2), "degrees": round(d, 1)} for m, t, d in found[:MAX_JUMPS_LISTED]]
    return out


def _stats(values: list[float], digits: int) -> dict[str, float]:
    return {"mean": round(sum(values) / len(values), digits), "p95": round(percentile(sorted(values), 95), digits), "max": round(max(values), digits)}


def analyze_pose(pose: Samples, windows: Sequence[Window], origin_us: int, reference: Samples | None = None) -> dict[str, Any]:
    """The pose's jumps over the windows, and (with a `reference` pose) how far apart the two were in position and heading.
    Raises NothingToCompare when there are too few updates or the values are not poses."""
    windows = everywhere(windows)
    inside = within(pose, windows)
    if len(inside) < MIN_COMPARED:
        raise NothingToCompare(f"fewer than {MIN_COMPARED} pose updates in the time checked")
    out: dict[str, Any] = {"jumps": _jumps(inside, origin_us)}
    if reference is not None:
        meters: list[float] = []
        degrees: list[float] = []
        worst: tuple[float, int] | None = None
        for t, raw, _, ref_raw in held_pairs(pose, reference, windows):
            p, q = decode_pose(raw), decode_pose(ref_raw)
            d = math.hypot(p.x - q.x, p.y - q.y)
            meters.append(d)
            degrees.append(turn_deg(p.theta - q.theta))
            if worst is None or d > worst[0]:
                worst = (d, t)
        require_enough(len(meters))
        assert worst is not None
        out["vs_reference"] = {"n": len(meters), "meters": _stats(meters, 3), "degrees": _stats(degrees, 1), "worst_at_s": seconds_since(worst[1], origin_us)}
    return out
