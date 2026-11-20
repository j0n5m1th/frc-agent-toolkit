"""The live scan: record the robot's own topics for a few seconds and run the same usual-trouble checks `wpilog_scan` runs on a log."""

from typing import Annotated

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply
from frc_mcp_wpilib import scan as core
from frc_mcp_wpilib.scan import ScanContext
from frc_mcp_wpilib.scan_roles import ScanError, ScanTopic

from ..app import LOCAL_STATE, mcp, tool_errors
from ..errors import NT4Error
from ..session import session
from .live import first_time, record_rows, topic_types


@mcp.tool(annotations={**LOCAL_STATE, "title": "Scan the robot for trouble (live)"}, output_schema=None)
@tool_errors
async def nt4_scan(
    duration_s: Annotated[float, Field(gt=0, le=30, description="Seconds to record")] = 5.0,
    battery: Annotated[str | None, Field(description="Battery voltage topic, if its name is not a usual one")] = None,
) -> ToolResult:
    """Usual trouble, live: record the robot's loop time, battery, brownouts, link and CAN numbers for a few seconds and flag problems, as wpilog_scan does for a log. Says which topics it used."""
    async with session.operation():
        known = {name: ScanTopic(name, type_str, 0, session.topic_unit(name)) for name, type_str in topic_types().items()}
        try:
            plan = core.plan(list(known.values()), [], known, battery, "nt4_list_topics finds names")
        except ScanError as e:
            raise NT4Error(str(e)) from e
        rows = await record_rows(plan.names, duration_s)
    first = first_time(rows)
    ctx = ScanContext(
        end_us=first + round(duration_s * 1_000_000), duration_s=duration_s, origin_us=first, label="recording start", enabled_known=False,
        enabled_note="a live scan cannot tell when the robot was enabled, so the numbers cover the whole recording", check_stopped=False,
    )
    return reply(core.run(plan, rows, ctx))
