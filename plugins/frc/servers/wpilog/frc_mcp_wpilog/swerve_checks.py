"""The log side of the swerve check: find the arrays in an open log and shape the reply.

The comparison itself (and the naming conventions) is in `frc_mcp_wpilib.swerve`, which the live server shares.
"""

from typing import Any

from frc_mcp_wpilib import swerve as core
from frc_mcp_wpilib.swerve import Pair
from frc_mcp_wpilib.tracking import NothingToCompare

from .checks import no_match, require_topics, scope_for
from .logfile import serialized
from .loglike import LogLike
from .windows import Range


def log_pair(log: LogLike, measured: str | None = None, setpoint: str | None = None) -> Pair:
    """The swerve arrays of a log (see `core.pick_pair`); hidden topics are left out."""
    return core.pick_pair({t.name: t.type_str for t in log.topics.values() if not t.hidden}, measured, setpoint)


@serialized
def swerve(log: LogLike, measured: str | None, setpoint: str | None, window: Range) -> dict[str, Any]:
    """The `wpilog_swerve` answer: how each module followed its command over the window (default: while enabled), and which topics were used.

    Raises WPILogError for a named topic that is missing or is not a `SwerveModuleState[]` array."""
    require_topics(log, (measured, setpoint), (core.STATE_TYPE,), f"a swerve array is {core.STATE_TYPE}")
    pair = log_pair(log, measured, setpoint)
    if pair.measured is None or pair.setpoint is None:
        why = "pass both measured= and setpoint= as SwerveModuleState[] topics" if measured or setpoint else "no measured and commanded SwerveModuleState[] pair follows a known name; pass measured= and setpoint="
        return no_match({"measured": pair.measured, "setpoint": pair.setpoint, **({"candidates": pair.others} if pair.others else {})}, why)
    scope = scope_for(log, window)
    rows = log.rows([pair.measured, pair.setpoint])
    try:
        analysis = core.analyze(rows[pair.measured], rows[pair.setpoint], scope.windows, scope.origin)
    except NothingToCompare as e:  # nothing to compare is not "no module lags"
        return no_match({"measured": pair.measured, "setpoint": pair.setpoint}, str(e))
    out = {"measured": pair.measured, "setpoint": pair.setpoint, "chosen_by": pair.how, "t0": scope.label, "scope": scope.name, **analysis,
           "flags": core.lagging(analysis["modules"]) or ["no module lags the others"]}
    if pair.others:
        out["others"] = pair.others  # more SwerveModuleState[] topics exist; confirm this pair is the one you mean
    return out
