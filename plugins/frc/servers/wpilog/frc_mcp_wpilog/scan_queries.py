"""`scan` for a log: the usual trouble in one log, checked the same way every time, as one small reply.

The checks are shared with the live server's `nt4_scan` (`frc_mcp_wpilib.scan`); this reads them from a whole log: the log text, the enabled periods and
a log that was cut short.
"""

from typing import Any

from frc_mcp_wpilib import scan as core
from frc_mcp_wpilib.scan import ScanContext
from frc_mcp_wpilib.scan_roles import ScanError, ScanTopic

from .errors import WPILogError
from .logfile import serialized
from .loglike import LogLike
from .text_queries import text_topics
from .timeline import origin_us, phases
from .windows import FIND_NAMES_HINT


@serialized
def scan(log: LogLike, battery_topic: str | None = None) -> dict[str, Any]:
    """The usual trouble in a log: `flags` ranked high to low, then the numbers behind them. Times are seconds after the first
    autonomous (else the first enable, else the log start; `t0` says which). `battery_topic` names the battery voltage topic;
    without it only a topic with a conventional name is used (see `frc_mcp_wpilib.scan_roles`)."""
    everything = {t.name: ScanTopic(t.name, t.type_str, t.count, t.unit, t.last_us) for t in log.topics.values()}
    visible = [everything[t.name] for t in log.topics.values() if not t.hidden]
    if battery_topic in log.topics and (why := log.topics[battery_topic].unusable_for("`wpilog_scan`")) is not None:
        raise WPILogError(why)
    try:
        plan = core.plan(visible, [n for n in text_topics(log) if n in log.topics], everything, battery_topic, FIND_NAMES_HINT)
    except ScanError as e:
        raise WPILogError(str(e)) from e
    rows = log.rows(plan.names)  # one pass over the file for every topic the checks read
    found = phases(log)
    align = "auto" if any(p.mode == "auto" for p in found) else "enable" if found else "start"
    origin, label = origin_us(log, align)
    ctx = ScanContext(
        end_us=log.end_us, duration_s=log.duration_s, origin_us=origin, label=label, windows=[(p.start_us, p.end_us) for p in found],
        cut_short_note="the log ends mid-record (cut short, e.g. by a power loss); later data is missing" if log.cut_short else None,
    )
    return core.run(plan, rows, ctx)
