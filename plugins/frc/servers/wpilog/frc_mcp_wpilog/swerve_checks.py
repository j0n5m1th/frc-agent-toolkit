"""The log side of the swerve checks: find the arrays in an open log, pick the time windows, and shape the replies.

The comparison itself (and the naming conventions) is in `frc_mcp_wpilib.swerve`, which the live server shares.
"""

from collections.abc import Sequence
from typing import Any

from frc_mcp_core.reply import with_status
from frc_mcp_wpilib import swerve as core
from frc_mcp_wpilib.reduce.stats import US_PER_MS
from frc_mcp_wpilib.swerve import NothingToCompare, Pair

from .errors import WPILogError
from .logfile import serialized
from .loglike import LogLike
from .timeline import Phase, origin_us, phases
from .windows import FIND_NAMES_HINT, NOT_IN_LOG, Range


def log_pair(log: LogLike, measured: str | None = None, setpoint: str | None = None) -> Pair:
    """The swerve arrays of a log (see `core.pick_pair`); hidden topics are left out."""
    return core.pick_pair({t.name: t.type_str for t in log.topics.values() if not t.hidden}, measured, setpoint)


def analysis_windows(found: Sequence[Phase], start_us: int, end_us: int, lo_us: int | None, hi_us: int | None) -> list[core.Window]:
    """The stretches to look at: the one the caller gave, else each enabled period, else the whole log."""
    if lo_us is not None or hi_us is not None:
        return [(lo_us if lo_us is not None else start_us, hi_us if hi_us is not None else end_us)]
    return [(p.start_us, p.end_us) for p in found] or [(start_us, end_us)]


@serialized
def swerve(log: LogLike, measured: str | None, setpoint: str | None, window: Range) -> dict[str, Any]:
    """The `wpilog_swerve` answer: how each module followed its command over the window (default: while enabled), and which topics were used.

    Raises WPILogError for a named topic that is missing or is not a `SwerveModuleState[]` array."""
    for name in (measured, setpoint):
        if name and name not in log.topics:
            raise WPILogError(f"{name!r} is {NOT_IN_LOG} ({FIND_NAMES_HINT}).")
        if name and log.topics[name].type_str != core.STATE_TYPE:
            raise WPILogError(f"{name!r} is type {log.topics[name].type_str}; a swerve array is {core.STATE_TYPE}.")
    pair = log_pair(log, measured, setpoint)
    if pair.measured is None or pair.setpoint is None:
        why = "pass both measured= and setpoint= as SwerveModuleState[] topics" if measured or setpoint else "no measured and commanded SwerveModuleState[] pair follows a known name; pass measured= and setpoint="
        return with_status({"measured": pair.measured, "setpoint": pair.setpoint, **({"candidates": pair.others} if pair.others else {})}, (), reason=why)
    origin, label = origin_us(log, window.align)
    lo = origin + int(window.since_ms * US_PER_MS) if window.since_ms is not None else None
    hi = origin + int(window.until_ms * US_PER_MS) if window.until_ms is not None else None
    explicit = lo is not None or hi is not None  # `align` only moves time zero; it does not turn the enabled default into the whole log
    found = [] if explicit else phases(log)
    rows = log.rows([pair.measured, pair.setpoint])
    try:
        analysis = core.analyze(rows[pair.measured], rows[pair.setpoint], analysis_windows(found, log.start_us, log.end_us, lo, hi), origin)
    except NothingToCompare as e:  # nothing to compare is not "no module lags"
        return with_status({"measured": pair.measured, "setpoint": pair.setpoint}, (), reason=str(e))
    scope = "window" if explicit else "enabled" if found else "all"
    out = {"measured": pair.measured, "setpoint": pair.setpoint, "chosen_by": pair.how, "t0": label, "scope": scope, **analysis,
           "flags": core.lagging(analysis["modules"]) or ["no module lags the others"]}
    if pair.others:
        out["others"] = pair.others  # more SwerveModuleState[] topics exist; confirm this pair is the one you mean
    return out
