"""Swerve drivetrain checks, shared by the log server and the live server: how closely each module followed its command.

A swerve robot publishes its module states (speed in m/s and angle in radians) twice: what the modules measured and what the code commanded. This
compares the two per module and says which module lags the others. It works on raw `SwerveModuleState[]` values, so it does not care whether they
came from a log or from a live capture, and it knows nothing about either server.

The two arrays are found only by a logging convention, never by a suggestive name: AdvantageKit's `SwerveStates/Measured` with `SetpointsOptimized`
(or `Setpoints`), and CTRE's `DriveState/ModuleStates` with `ModuleTargets`. Anything else is listed as an "other" array, or named by the caller.
The layout read is WPILib's `SwerveModuleState`: a double speed, then a double angle in radians, 16 bytes per module.
"""

import math
import struct
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from .errors import WPILibMCPError
from .reduce.stats import US_PER_S, percentile
from .scan_roles import leaf_and_parents

STATE_TYPE = "struct:SwerveModuleState[]"
# (parent folder, measured leaf, setpoint leaves in order of preference), all lower case
CONVENTIONS = (("swervestates", "measured", ("setpointsoptimized", "setpoints")), ("drivestate", "modulestates", ("moduletargets",)))
MODULE_BYTES = 16  # a double speed and a double angle
MAX_SAMPLES = 3000  # measured updates read per module (evenly spread), so a long log costs no more than a short one
MAX_OTHERS = 3
MIN_COMPARED = 10  # fewer measured updates than this say nothing about how a module followed its command
MOVING_MPS = 0.1  # below this commanded speed a module's angle is not being asked to do anything
LAG_FACTOR = 2.0  # a module whose p95 error is this many times the median of all modules' is lagging
LAG_SPEED_MPS, LAG_ANGLE_DEG = 0.3, 10.0  # ...and its error is at least this big, so a tiny difference is never a flag

Samples = Sequence[tuple[int, Any]]  # (time in microseconds, raw value), oldest first
Window = tuple[int, int]  # (start, end) in microseconds, inclusive


class NothingToCompare(WPILibMCPError):
    """The two arrays cannot be compared: one has no updates, none has a commanded update before it, or the values are not swerve states."""


class ModuleState(NamedTuple):
    speed: float  # m/s
    angle: float  # radians


class Pair(NamedTuple):
    measured: str | None
    setpoint: str | None
    how: str  # explicit, convention or none
    others: list[str]  # other swerve arrays that were not used


def pick_pair(types: Mapping[str, str], measured: str | None = None, setpoint: str | None = None) -> Pair:
    """The measured and commanded arrays among the topics (`name -> type`): the ones named by the caller, else a convention above, else none.
    The other `SwerveModuleState[]` topics come back as `others`: a log with CTRE's and AdvantageKit's arrays, or a replay's, has more than one pair."""
    states = sorted(name for name, type_str in types.items() if type_str == STATE_TYPE)

    def pair(m: str | None, s: str | None, how: str) -> Pair:
        return Pair(m, s, how, [n for n in states if n not in (m, s)][:MAX_OTHERS])

    if measured or setpoint:
        return pair(measured if measured in states else None, setpoint if setpoint in states else None, "explicit")
    for parent, leaf, setpoint_leaves in CONVENTIONS:
        for m in states:
            m_leaf, parents = leaf_and_parents(m)
            if m_leaf == leaf and parent in parents:
                base = m.rsplit("/", 1)[0]
                wanted = [n for name in setpoint_leaves for n in states if n.rsplit("/", 1)[0] == base and leaf_and_parents(n)[0] == name]
                return pair(m, wanted[0] if wanted else None, "convention")
    return pair(None, None, "none")


def decode_states(raw: Any) -> list[ModuleState]:
    """One update as a state per module. Raises NothingToCompare for bytes that are not whole `SwerveModuleState` values."""
    if not isinstance(raw, (bytes, bytearray)) or not raw or len(raw) % MODULE_BYTES:
        raise NothingToCompare(f"a swerve array update must be a whole number of {MODULE_BYTES}-byte SwerveModuleState values (speed, angle in radians)")
    return [ModuleState(speed, angle) for speed, angle in struct.iter_unpack("<dd", bytes(raw))]


def _wrap_deg(radians: float) -> float:
    """The size, in degrees, of an angle difference, taking the short way round."""
    return abs((math.degrees(radians) + 180) % 360 - 180)


def _sampled(times: Sequence[int], windows: Sequence[Window]) -> list[int]:
    """Row numbers inside the windows, thinned to at most `MAX_SAMPLES` evenly spread. Rows are in time order, so each window is a slice."""
    spans = [range(bisect_left(times, lo), bisect_right(times, hi)) for lo, hi in windows]
    step = max(1, sum(len(r) for r in spans) // MAX_SAMPLES)
    return [i for r in spans for i in r[::step]]


def analyze(measured: Samples, setpoint: Samples, windows: Sequence[Window], origin_us: int) -> dict[str, Any]:
    """Per module: how far its speed and angle were from the command, over the windows. The command is the last setpoint at or before each measured
    update. Angle is compared only while the module was commanded to move. Raises NothingToCompare when there is nothing to compare."""
    if not measured or not setpoint:
        raise NothingToCompare("the measured or the commanded array has no updates")
    s_times = [t for t, _ in setpoint]
    speed_err: defaultdict[int, list[float]] = defaultdict(list)
    angle_err: defaultdict[int, list[float]] = defaultdict(list)
    top_speed: defaultdict[int, float] = defaultdict(float)
    worst: tuple[float, int, int] | None = None  # speed error, module, time
    last_j, last_command = -1, []
    for i in _sampled([t for t, _ in measured], windows):
        t, raw = measured[i]
        if (j := bisect_right(s_times, t) - 1) < 0:
            continue
        if j != last_j:
            last_j, last_command = j, decode_states(setpoint[j][1])
        for k, (m, s) in enumerate(zip(decode_states(raw), last_command, strict=False)):
            err = abs(m.speed - s.speed)
            speed_err[k].append(err)
            top_speed[k] = max(top_speed[k], abs(m.speed))
            if abs(s.speed) >= MOVING_MPS:
                angle_err[k].append(_wrap_deg(m.angle - s.angle))
            if worst is None or err > worst[0]:
                worst = (err, k, t)
    if worst is None:
        raise NothingToCompare("no measured update has a commanded one before it")
    if (most := max(len(errors) for errors in speed_err.values())) < MIN_COMPARED:
        raise NothingToCompare(f"only {most} measured update(s) could be compared (at least {MIN_COMPARED} are needed)")
    modules = []
    for k in sorted(speed_err):
        row: dict[str, Any] = {"module": k, "n": len(speed_err[k]), "speed_err_mean_mps": round(sum(speed_err[k]) / len(speed_err[k]), 3),
                               "speed_err_p95_mps": round(percentile(sorted(speed_err[k]), 95), 3), "top_speed_mps": round(top_speed[k], 2)}
        if angle_err[k]:
            row |= {"angle_err_mean_deg": round(sum(angle_err[k]) / len(angle_err[k]), 1), "angle_err_p95_deg": round(percentile(sorted(angle_err[k]), 95), 1)}
        modules.append(row)
    return {"modules": modules, "worst_speed_err": {"module": worst[1], "mps": round(worst[0], 2), "at_s": round((worst[2] - origin_us) / US_PER_S, 1)}}


def lagging(modules: Sequence[dict[str, Any]]) -> list[str]:
    """Flags for a module whose error is far above the others'. Needs at least three modules, so there is a typical one to compare with."""
    flags: list[str] = []
    for key, floor, unit, label in (("speed_err_p95_mps", LAG_SPEED_MPS, "m/s", "speed"), ("angle_err_p95_deg", LAG_ANGLE_DEG, "deg", "angle")):
        values = [(m[key], m["module"]) for m in modules if key in m]
        if len(values) < 3:
            continue
        typical = percentile(sorted(v for v, _ in values), 50)
        flags += [f"med: swerve module {k} lags the others in {label} (p95 error {v} {unit}, typical {round(typical, 3)})" for v, k in values if v >= floor and v >= LAG_FACTOR * typical]
    return flags
