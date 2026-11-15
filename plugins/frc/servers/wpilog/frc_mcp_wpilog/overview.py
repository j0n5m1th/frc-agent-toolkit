"""The summary `wpilog_open` returns: what the log is, how long, when the robot was enabled, and what to look at."""

from typing import Any

from . import timeline
from .logfile import serialized
from .loglike import LogLike


@serialized
def overview(log: LogLike, log_id: str) -> dict[str, Any]:
    """Facts a student wants first. Times are seconds since the start of the log."""
    out: dict[str, Any] = {
        "log": log_id,
        "file": log.path.name,
        "size_mb": round(log.size_bytes / 2**20, 1),
        "duration_s": round(log.duration_s, 1),
        "topics": sum(1 for t in log.topics.values() if not t.hidden),
        "updates": log.records,
    }
    found = timeline.phases(log)
    out["enabled"] = [{"mode": p.mode, **({"opmode": p.opmode} if p.opmode else {}),
                       **({"already_enabled": True} if timeline.already_enabled(log, p) else {}),
                       "from_s": round((p.start_us - log.start_us) / 1e6, 1),
                       "to_s": round((p.end_us - log.start_us) / 1e6, 1),
                       "for_s": round((p.end_us - p.start_us) / 1e6, 1)} for p in found]
    match = timeline.match_info(log)
    if match:
        out["match"] = match
    out["busiest"] = [[name, n] for name, n in log.busiest()]
    warnings: list[str] = []
    if not found:
        warnings.append("No Driver Station data found, so align can only be 'start'.")
    if log.cut_short:
        warnings.append("The file ends in the middle of a record (a log cut short, for example by a power loss); anything after that point is missing.")
    if log.skipped_records:
        causes = ", ".join(f"{count} {reason}" for reason, count in log.skipped_reasons.items() if count)
        warnings.append(f"{log.skipped_records} records could not be read and were skipped"
                        + (f" ({causes})" if causes else "") + ".")
    if warnings:
        out["warnings"] = warnings
    return out
