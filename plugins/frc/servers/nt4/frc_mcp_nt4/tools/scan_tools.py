"""The live scan: record the robot's own topics for a few seconds and run the same usual-trouble checks `wpilog_scan` runs on a log."""

from typing import Annotated

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply
from frc_mcp_wpilib import scan as core
from frc_mcp_wpilib.scan import ScanContext
from frc_mcp_wpilib.scan_roles import ScanError, ScanTopic

from ..app import LOCAL_STATE, mcp, tool_errors
from ..capture import CaptureSpec
from ..errors import NT4Error
from ..session import session

RATE_HZ = 100  # enough to take every update of a 50 Hz publisher
HIDDEN_PREFIXES = ("$", "/.schema/")  # meta-topics and struct schemas are not the robot's signals


@mcp.tool(annotations={**LOCAL_STATE, "title": "Scan the robot for trouble (live)"}, output_schema=None)
@tool_errors
async def nt4_scan(
    duration_s: Annotated[float, Field(gt=0, le=30, description="Seconds to record")] = 5.0,
    battery: Annotated[str | None, Field(description="Battery voltage topic, if its name is not a usual one")] = None,
) -> ToolResult:
    """Usual trouble, live: record the robot's loop time, battery, brownouts, link and CAN numbers for a few seconds and flag problems, as wpilog_scan does for a log. Says which topics it used."""
    async with session.operation():
        known = {i.name: ScanTopic(i.name, i.type_str, 0, session.topic_unit(i.name)) for i in session.inst.getTopicInfo("") if not i.name.startswith(HIDDEN_PREFIXES)}
        try:
            plan = core.plan(list(known.values()), [], known, battery, "nt4_list_topics finds names")
        except ScanError as e:
            raise NT4Error(str(e)) from e
        samples = await session.record_samples(CaptureSpec(topics=plan.names, prefix=False, rate_hz=RATE_HZ, duration_s=duration_s, all_changes=True)) if plan.names else {}
    rows = {name: [(round(t_ms * 1000), value) for t_ms, value, _ in samples.get(name, ())] for name in plan.names}
    first = min((r[0][0] for r in rows.values() if r), default=0)
    ctx = ScanContext(
        end_us=first + round(duration_s * 1_000_000), duration_s=duration_s, origin_us=first, label="recording start", enabled_known=False,
        enabled_note="a live scan cannot tell when the robot was enabled, so the numbers cover the whole recording", check_stopped=False,
    )
    return reply(core.run(plan, rows, ctx))
