"""What every check tool on a log shares: the time scope (the enabled periods unless a window was asked for) and the topics a caller names.

The checks themselves (swerve, power, mechanism, correlation, pose) are shared with the live server and live in `frc_mcp_wpilib`; a tool here only
finds its topics in an open log, picks the scope and shapes the reply.
"""

from collections.abc import Collection
from typing import NamedTuple

from frc_mcp_wpilib.reduce.stats import US_PER_MS
from frc_mcp_wpilib.scan_roles import ScanTopic
from frc_mcp_wpilib.tracking import Window

from .errors import WPILogError
from .loglike import LogLike
from .timeline import origin_us, phases
from .windows import FIND_NAMES_HINT, NOT_IN_LOG, Range


class Scope(NamedTuple):
    """The stretches of the log a check looks at, and what time zero is."""

    windows: list[Window]
    origin: int  # time zero, in the rows' clock
    label: str  # what time zero is, shown as `t0`
    name: str  # enabled (the enabled periods), window (the one asked for) or all (the whole log)


def scope_for(log: LogLike, window: Range) -> Scope:
    """The enabled periods by default, the whole log when it records none, or exactly the window asked for (`since_ms`, `until_ms`). `align` only
    moves time zero: it does not turn the enabled default into the whole log."""
    origin, label = origin_us(log, window.align)
    lo = origin + int(window.since_ms * US_PER_MS) if window.since_ms is not None else None
    hi = origin + int(window.until_ms * US_PER_MS) if window.until_ms is not None else None
    if lo is not None or hi is not None:
        return Scope([(lo if lo is not None else log.start_us, hi if hi is not None else log.end_us)], origin, label, "window")
    found = phases(log)
    return Scope([(p.start_us, p.end_us) for p in found] or [(log.start_us, log.end_us)], origin, label, "enabled" if found else "all")


def require_topics(log: LogLike, names: Collection[str | None], types: Collection[str] | None = None, what: str = "") -> None:
    """Raise WPILogError for a named topic that is missing, or (with `types`) is not one of those types. `None` names are skipped."""
    for name in names:
        if name and name not in log.topics:
            raise WPILogError(f"{name!r} is {NOT_IN_LOG} ({FIND_NAMES_HINT}).")
        if name and types is not None and log.topics[name].type_str not in types:
            raise WPILogError(f"{name!r} is type {log.topics[name].type_str}; {what or 'it must be one of ' + ', '.join(sorted(types))}.")


def scan_topics(log: LogLike) -> tuple[list[ScanTopic], dict[str, ScanTopic]]:
    """`(visible, everything)`: the topics a check may look at, and every topic by name (a caller may name a hidden one)."""
    everything = {t.name: ScanTopic(t.name, t.type_str, t.count, t.unit, t.last_us) for t in log.topics.values()}
    return [everything[t.name] for t in log.topics.values() if not t.hidden], everything
