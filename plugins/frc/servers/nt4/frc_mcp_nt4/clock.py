"""NT4 local-clock helpers for reporting update age without guessing."""

import ntcore

from frc_mcp_wpilib.reduce.stats import US_PER_MS
from frc_mcp_wpilib.reduce.types import UpdateAge

__all__ = ["UpdateAge", "estimate_server_now_us", "local_now_us", "update_age"]


def local_now_us() -> int | None:
    """The NT4 local clock in microseconds since the epoch, or None when this ntcore has no `_now` to read it with."""
    now = getattr(ntcore, "_now", None)
    return now() if now is not None else None


def estimate_server_now_us(inst: ntcore.NetworkTableInstance) -> tuple[int, int] | None:
    """(estimated server time now, offset) in microseconds, or None until the clocks have synchronized."""
    offset = inst.getServerTimeOffset()
    now = local_now_us()
    return None if offset is None or now is None else (now + offset, offset)


def update_age(local_us: int, server_us: int, now_us: int | None, synchronized: bool) -> UpdateAge:
    if local_us <= 0 or server_us <= 0:
        return {"age_ms": None, "reason": "update timestamp unavailable"}
    if now_us is None:
        return {"age_ms": None, "reason": "local NT4 clock unavailable"}
    if not synchronized:
        return {"age_ms": None, "reason": "NT4 clock not synchronized"}
    if local_us > now_us:
        return {"age_ms": None, "reason": "update timestamp is in the future"}
    return {"age_ms": round((now_us - local_us) / US_PER_MS, 1)}
