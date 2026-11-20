"""The log side of the mechanism check: read the signals the caller names from an open log and shape the reply.

The check itself is in `frc_mcp_wpilib.mechanism`, which the live server shares.
"""

from typing import Any

from frc_mcp_core.reply import no_match
from frc_mcp_wpilib import mechanism as core
from frc_mcp_wpilib.scan_roles import NUMBER_TYPES
from frc_mcp_wpilib.tracking import NothingToCompare

from .checks import require_topics, scope_for
from .logfile import serialized
from .loglike import LogLike
from .windows import Range


@serialized
def mechanism(log: LogLike, roles: dict[str, str | None], window: Range) -> dict[str, Any]:
    """The `wpilog_mechanism` answer for the signals named in `roles` (`setpoint`, `measurement`, and optionally `velocity`, `current`, `temperature`),
    over the window (default: while enabled). Raises WPILogError for a named signal that is missing or is not a number."""
    named = {role: name for role, name in roles.items() if name}
    require_topics(log, list(named.values()), NUMBER_TYPES, "it must be a number (double, float or int)")
    scope = scope_for(log, window)
    rows = log.rows(list(dict.fromkeys(named.values())))
    try:
        out = core.analyze_mechanism(rows[named["setpoint"]], rows[named["measurement"]], scope.windows, scope.origin,
                                     **{role: rows[name] for role, name in named.items() if role not in ("setpoint", "measurement")})
    except NothingToCompare as e:
        return no_match({"roles": named}, str(e))
    return {"roles": named, "t0": scope.label, "scope": scope.name, **out}
