"""The log side of the correlation check: read the two signals the caller names and shape the reply (the check is `frc_mcp_wpilib.correlate`)."""

from typing import Any

from frc_mcp_core.reply import no_match
from frc_mcp_wpilib import correlate as core
from frc_mcp_wpilib.scan_roles import NUMBER_TYPES
from frc_mcp_wpilib.tracking import NothingToCompare

from .checks import require_topics, scope_for
from .logfile import serialized
from .loglike import LogLike
from .windows import Range


@serialized
def correlate(log: LogLike, a: str, b: str, max_lag_s: float, window: Range) -> dict[str, Any]:
    """The `wpilog_correlate` answer for signals `a` and `b` over the window (default: while enabled). Raises WPILogError for a signal that is missing or not a number."""
    require_topics(log, (a, b), NUMBER_TYPES, "it must be a number (double, float or int)")
    scope = scope_for(log, window)
    rows = log.rows([a, b])
    try:
        out = core.analyze_correlation(rows[a], rows[b], scope.windows, max_lag_s)
    except NothingToCompare as e:
        return no_match({"a": a, "b": b}, str(e))
    return {"a": a, "b": b, "t0": scope.label, "scope": scope.name, **out}
