"""When the robot was enabled, in which mode, and what match the log is from; plus the time alignment tools offer.

Aligning to the start of autonomous (or teleop) gives times that match what students saw on the field.

Sources, in order of preference (the set AdvantageScope reads, `src/shared/log/RobotState.ts`):
- WPILib `DataLogManager` booleans (`DS:enabled`, `DS:autonomous`, `DS:test`);
- AdvantageKit's (`/DriverStation/Enabled` with `RobotMode` from 2027, else `Autonomous` and `Test`), or mirrored over NT;
- the `ControlWord` struct (`DS:controlWord`, `/DriverStation/ControlWord`, `/FMSInfo/ControlWord`) or its separate fields;
- an integer control word: NT `/FMSInfo/FMSControlData` (bit 0 enabled, 1 autonomous, 2 test) or the FIRST Driver Station's
  `DS:/Dscomm/Control/Robot/ControlData/ControlWord` (bit 0 enabled, bits 1-2 mode: 1 autonomous, 2 teleop, 3 utility);
- Phoenix's `RobotEnable` and `RobotMode` (a converted `.hoot`).
WPILib 2027 renamed Test mode to Utility. The integer decodings follow AdvantageScope's and have not been checked against a
real log here. A 2027 robot's OpMode (`/DriverStation/OpMode` or `DS:opMode`) is attached to each period when logged.
"""

import math
from dataclasses import dataclass
from typing import Any

from frc_mcp_wpilib.values import shape

from .errors import WPILogError
from .loglike import LogLike

MODES = ("auto", "teleop", "test", "utility")
ALIGN_CHOICES = ("start", "enable", *MODES)
_CONTROL_WORD_NAMES = ("DS:controlWord", "/FMSInfo/ControlWord", "/DriverStation/ControlWord")
_MATCH_FIELDS = ("EventName", "MatchType", "MatchNumber", "ReplayNumber", "StationNumber", "IsRedAlliance")
_MATCH_TYPES = {0: "none", 1: "practice", 2: "qualification", 3: "elimination"}


@dataclass(frozen=True)
class Phase:
    """A stretch of time the robot was enabled in one mode."""

    mode: str  # auto | teleop | test | utility | unknown (enabled, but the log names no mode, e.g. RobotMode UNKNOWN)
    start_us: int
    end_us: int
    opmode: str | None = None  # the 2027 OpMode name, when the log records one


def _mode(flags: dict[str, Any]) -> str | None:
    """The mode of one control-word state, or None when disabled."""
    if not flags.get("enabled"):
        return None
    raw_mode = flags.get("robotMode", "")
    if isinstance(raw_mode, int) and not isinstance(raw_mode, bool):  # the 2027 enum as a number
        raw_mode = _ROBOT_MODES.get(raw_mode, "")
    robot_mode = str(raw_mode).lower()
    if flags.get("autonomous") or robot_mode.startswith("auto"):
        return "auto"
    if flags.get("test") or robot_mode == "test":
        return "test"
    if robot_mode == "utility":
        return "utility"
    if robot_mode == "unknown":
        return "unknown"
    return "teleop"


STATE_FROM = "state_from_us"  # `LogLike.derived` key: the first record of the enabled source the phases came from
_ROBOT_MODES = {0: "unknown", 1: "autonomous", 2: "teleoperated", 3: "utility"}  # WPILib 2027's RobotMode enum

# Per source: entry name -> the flag it sets. `robotMode` is a string or the 2027 enum number, the rest are booleans.
_DS_ENTRIES = {"DS:enabled": "enabled", "DS:autonomous": "autonomous", "DS:test": "test"}
_AKIT_ENTRIES = {"/DriverStation/Enabled": "enabled", "/DriverStation/Autonomous": "autonomous",
                 "/DriverStation/Test": "test", "/DriverStation/RobotMode": "robotMode"}
_FLAG_SOURCES = (  # in order of preference
    _DS_ENTRIES,
    {"DS:controlWord/enabled": "enabled", "DS:controlWord/robotMode": "robotMode"},
    _AKIT_ENTRIES,
    {"/AdvantageKit" + name: flag for name, flag in _AKIT_ENTRIES.items()},  # AdvantageKit mirrored over NT
    {"/DriverStation/ControlWord/enabled": "enabled", "/DriverStation/ControlWord/robotMode": "robotMode"},
)
_PHOENIX_ENTRIES = {"RobotEnable": "enabled", "RobotMode": "robotMode"}  # what Owlet names them in a converted .hoot


def _fms_control_data(word: int) -> dict[str, Any]:
    return {"enabled": bool(word & 1), "autonomous": bool(word >> 1 & 1), "test": bool(word >> 2 & 1)}


def _dscomm_control_word(word: int) -> dict[str, Any]:
    mode = word >> 1 & 3  # as AdvantageScope reads it: 1 autonomous, 3 utility, anything else teleop
    return {"enabled": bool(word & 1), "robotMode": {1: "autonomous", 3: "utility"}.get(mode, "teleoperated")}


_INT_CONTROL_WORDS = {"/FMSInfo/FMSControlData": _fms_control_data,
                      "DS:/Dscomm/Control/Robot/ControlData/ControlWord": _dscomm_control_word}
_OPMODE_NAMES = ("/DriverStation/OpMode", "DS:opMode")


def _as_flag(value: Any) -> bool | None:
    """A logged flag as a bool: numbers by truth, the strings "true" and "false" (any case) as they read, anything else None."""
    if isinstance(value, str):
        return {"true": True, "false": False}.get(value.strip().lower())
    if isinstance(value, (bool, int, float)):
        return bool(value)
    return None


def _from_flags(log: LogLike, entries: dict[str, str]) -> list[tuple[int, str | None]]:
    """(time, mode or None) at every change of one source's Driver Station entries."""
    names = {n: flag for n, flag in entries.items() if n in log.topics}
    if "enabled" not in names.values():
        return []
    changes: list[tuple[int, str, Any]] = []
    for name, rows in log.rows(list(names)).items():
        flag = names[name]
        for t, v in rows:
            value = v if flag == "robotMode" else _as_flag(v)
            if value is not None:  # a value that is not a flag (a retyped topic) says nothing
                changes.append((t, flag, value))
    state: dict[str, Any] = {}
    out: list[tuple[int, str | None]] = []
    for t, key, value in sorted(changes, key=lambda c: c[0]):
        state[key] = value
        if "enabled" in state:  # a mode flag logged before the first enabled record says nothing about enabling yet
            out.append((t, _mode(state)))
    return out


def _with_opmodes(log: LogLike, found: list[Phase]) -> list[Phase]:
    """Each period with the OpMode in effect when it began (or, failing that, the first one chosen during it)."""
    name = next((n for n in _OPMODE_NAMES if n in log.topics), None)
    if name is None or not found:
        return found
    rows = [(t, v) for t, v in log.rows([name])[name] if isinstance(v, str)]  # keep "" so a cleared OpMode clears
    out: list[Phase] = []
    for p in found:
        before = [v for t, v in rows if t <= p.start_us]
        during = [v for t, v in rows if p.start_us < t < p.end_us and v]
        opmode = (before[-1] if before else "") or (during[0] if during else None)
        out.append(Phase(p.mode, p.start_us, p.end_us, opmode))
    return out


def _from_control_word(log: LogLike) -> list[tuple[int, str | None]]:
    name = next((n for n in _CONTROL_WORD_NAMES if n in log.topics and log.topics[n].type_str.startswith("struct:")), None)
    if name is None:
        return []
    topic = log.topics[name]
    log.activate_schemas()
    out: list[tuple[int, str | None]] = []
    for t, raw in log.rows([name])[name]:
        shaped = shape(raw, topic.type_str, None, 3)
        if isinstance(shaped, dict):
            out.append((t, _mode(shaped)))
    return out


def phases(log: LogLike) -> list[Phase]:
    """Enabled periods in time order (adjacent stretches in the same mode are merged). Worked out once per log."""
    if "phases" not in log.derived:
        log.derived["phases"] = _find_phases(log)
    return log.derived["phases"]


def already_enabled(log: LogLike, phase: Phase) -> bool:
    """Whether `phase` began at the source's first record: the robot was enabled before the log saw it, so its start
    is when logging began, not a moment the robot was enabled."""
    phases(log)
    return phase.start_us == log.derived.get(STATE_FROM)


def _from_int_control_word(log: LogLike) -> list[tuple[int, str | None]]:
    # a number of any type: older NetworkTables published FMSControlData as a double
    name = next((n for n in _INT_CONTROL_WORDS if n in log.topics and log.topics[n].type_str in ("int", "double", "float")), None)
    if name is None:
        return []
    decode = _INT_CONTROL_WORDS[name]
    return [(t, _mode(decode(int(v)))) for t, v in log.rows([name])[name] if isinstance(v, (int, float)) and math.isfinite(v)]  # NaN/inf says nothing


def _transitions(log: LogLike) -> list[tuple[int, str | None]]:
    """(time, mode or None) from the most preferred source this log has."""
    for entries in _FLAG_SOURCES:
        if found := _from_flags(log, entries):
            return found
    return _from_control_word(log) or _from_int_control_word(log) or _from_flags(log, _PHOENIX_ENTRIES)


def _find_phases(log: LogLike) -> list[Phase]:
    changes: dict[int, str | None] = {}  # the last state at each instant (several flags can change in one instant)
    for t, mode in _transitions(log):
        changes[t] = mode
    log.derived[STATE_FROM] = min(changes, default=None)
    out: list[Phase] = []
    current: tuple[str, int] | None = None
    for t, mode in sorted(changes.items()):
        if current is not None and mode == current[0]:
            continue
        if current is not None:
            out.append(Phase(current[0], current[1], t))
        current = (mode, t) if mode else None
    if current is not None:
        out.append(Phase(current[0], current[1], max(log.end_us, current[1])))
    return _with_opmodes(log, out)


def match_info(log: LogLike) -> dict[str, Any]:
    """Event and match facts the Driver Station published, for whichever of them the log has (empty values skipped)."""
    found: dict[str, Any] = {}
    for prefix in ("/FMSInfo/", "/DriverStation/"):
        wanted = [prefix + f for f in _MATCH_FIELDS if prefix + f in log.topics]
        rows = log.rows(wanted)
        for name, values in rows.items():
            key = name.removeprefix(prefix)
            if key in found or not values:
                continue
            value = values[-1][1]
            if key == "MatchType" and isinstance(value, int):  # text, an array or a decimal stays as logged
                value = _MATCH_TYPES.get(value, value)
            if value not in ("", 0, None) or key == "IsRedAlliance":
                found[key] = value
    return found


def origin_us(log: LogLike, align: str) -> tuple[int, str]:
    """The log time (us) that `align` re-zeroes to, with a short label for replies.

    Raises WPILogError: for an unknown choice, or a mode the robot was never enabled in.
    """
    if align == "start":
        return log.start_us, "log start"
    if align not in ALIGN_CHOICES:
        raise WPILogError(f"align must be one of {', '.join(ALIGN_CHOICES)}, not {align!r}.")
    found = phases(log)
    wanted = [p for p in found if align == "enable" or p.mode == align]
    if not wanted:
        have = ", ".join(sorted({p.mode for p in found})) or "none (no Driver Station entries, or never enabled)"
        raise WPILogError(f"Cannot align to {align!r}: this log has no such period. Enabled modes found: {have}. Use align='start'.")
    return wanted[0].start_us, f"first {align}" if align != "enable" else "first enable"
