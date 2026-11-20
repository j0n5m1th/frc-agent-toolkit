"""What the live checks share: the topics the robot has, and a few seconds of their updates as rows the shared checks read."""

from frc_mcp_wpilib.scan_rows import Row

from ..capture import CaptureSpec
from ..session import session

RATE_HZ = 100  # enough to take every update of a 50 Hz publisher
HIDDEN_PREFIXES = ("$", "/.schema/")  # meta-topics and struct schemas are not the robot's signals


def topic_types() -> dict[str, str]:
    """Every topic the robot has announced, `name -> type`, without meta-topics and schemas. Call inside `session.operation()`."""
    return {i.name: i.type_str for i in session.inst.getTopicInfo("") if not i.name.startswith(HIDDEN_PREFIXES)}


async def record_rows(names: list[str], duration_s: float) -> dict[str, list[Row]]:
    """Record `names` for `duration_s` seconds, taking every update, and return each topic's rows as `(microseconds, value)`. Call inside `session.operation()`."""
    if not names:
        return {}
    samples = await session.record_samples(CaptureSpec(topics=names, prefix=False, rate_hz=RATE_HZ, duration_s=duration_s, all_changes=True))
    return {name: [(round(t_ms * 1000), value) for t_ms, value, _ in samples.get(name, ())] for name in names}


def first_time(rows: dict[str, list[Row]]) -> int:
    """The time of the earliest update in any topic (0 when there is none): time zero for a live reply."""
    return min((r[0][0] for r in rows.values() if r), default=0)
