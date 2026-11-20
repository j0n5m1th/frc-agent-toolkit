"""The log side of the power check: find the battery, brownout flags and current signals in an open log and shape the reply.

The check itself (and the naming conventions) is in `frc_mcp_wpilib.power`, which the live server shares.
"""

from collections.abc import Sequence
from typing import Any

from frc_mcp_wpilib import power as core
from frc_mcp_wpilib.scan_roles import ScanError
from frc_mcp_wpilib.tracking import NothingToCompare

from .checks import no_match, scan_topics, scope_for
from .errors import WPILogError
from .logfile import serialized
from .loglike import LogLike
from .windows import FIND_NAMES_HINT, Range


@serialized
def power(log: LogLike, battery: str | None, currents: Sequence[str], window: Range, brownout_v: float | None = None) -> dict[str, Any]:
    """The `wpilog_power` answer: the battery low point and brownouts, the devices with the highest current, and what each was drawing when the battery
    was lowest, over the window (default: while enabled). Raises WPILogError for a named battery or current that is missing or not a number."""
    visible, everything = scan_topics(log)
    try:
        plan = core.plan_power(visible, everything, battery, currents, FIND_NAMES_HINT)
    except ScanError as e:
        raise WPILogError(str(e)) from e
    scope = scope_for(log, window)
    rows = log.rows(plan.names)
    try:
        out = core.run_power(plan, rows, scope.windows, scope.origin, brownout_v)
    except NothingToCompare as e:
        return no_match(plan.facts(), f"{e}; name the signals with battery= and currents=")
    return {"t0": scope.label, "scope": scope.name, **out}
