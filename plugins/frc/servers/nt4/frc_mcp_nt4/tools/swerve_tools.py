"""The live swerve check: record the module states for a few seconds and say which module lags its command."""

from typing import Annotated, Any

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import no_match, reply
from frc_mcp_wpilib import swerve as core
from frc_mcp_wpilib.tracking import NothingToCompare

from ..app import LOCAL_STATE, mcp, tool_errors
from ..session import session
from .live import first_time, record_rows, topic_types


@mcp.tool(annotations={**LOCAL_STATE, "title": "Check the swerve modules (live)"}, output_schema=None)
@tool_errors
async def nt4_swerve(
    duration_s: Annotated[float, Field(gt=0, le=30, description="Seconds to record while the drivers move the robot")] = 3.0,
    measured: Annotated[str | None, Field(description="Measured SwerveModuleState[] topic, if its name is not a usual one")] = None,
    setpoint: Annotated[str | None, Field(description="Commanded SwerveModuleState[] topic")] = None,
) -> ToolResult:
    """Swerve, live: record the measured and commanded module states and say how closely each module followed its command and which one lags. Reads only; someone must be driving the robot."""
    async with session.operation():
        pair = core.pick_pair(topic_types(), measured, setpoint)
        if pair.measured is None or pair.setpoint is None:
            why = "pass both measured= and setpoint= as SwerveModuleState[] topics" if measured or setpoint else "no measured and commanded SwerveModuleState[] pair follows a known name; pass measured= and setpoint="
            return reply(no_match({"measured": pair.measured, "setpoint": pair.setpoint, **({"candidates": pair.others} if pair.others else {})}, why))
        rows = await record_rows([pair.measured, pair.setpoint], duration_s)
    first = first_time(rows)
    try:
        analysis = core.analyze(rows[pair.measured], rows[pair.setpoint], [], first)
    except NothingToCompare as e:
        return reply(no_match({"measured": pair.measured, "setpoint": pair.setpoint}, f"{e}; was the robot moving, and are both arrays being published?"))
    out: dict[str, Any] = {"measured": pair.measured, "setpoint": pair.setpoint, "chosen_by": pair.how, "seconds": duration_s, **analysis,
                           "flags": core.lagging(analysis["modules"]) or ["no module lags the others"]}
    if pair.others:
        out["others"] = pair.others
    return reply(out)
