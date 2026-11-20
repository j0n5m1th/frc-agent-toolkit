"""The live swerve check: record the module states for a few seconds and say which module lags its command."""

from typing import Annotated, Any

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply, with_status
from frc_mcp_wpilib import swerve as core
from frc_mcp_wpilib.swerve import NothingToCompare

from ..app import LOCAL_STATE, mcp, tool_errors
from ..capture import CaptureSpec
from ..session import session

RATE_HZ = 100  # enough to take every update of a 50 Hz publisher
FAR_FUTURE_US = 2**62


@mcp.tool(annotations={**LOCAL_STATE, "title": "Check the swerve modules (live)"}, output_schema=None)
@tool_errors
async def nt4_swerve(
    duration_s: Annotated[float, Field(gt=0, le=30, description="Seconds to record while the drivers move the robot")] = 3.0,
    measured: Annotated[str | None, Field(description="Measured SwerveModuleState[] topic, if its name is not a usual one")] = None,
    setpoint: Annotated[str | None, Field(description="Commanded SwerveModuleState[] topic")] = None,
) -> ToolResult:
    """Swerve, live: record the measured and commanded module states and say how closely each module followed its command and which one lags. Reads only; someone must be driving the robot."""
    async with session.operation():
        pair = core.pick_pair({t.name: t.type_str for t in session.inst.getTopicInfo("")}, measured, setpoint)
        if pair.measured is None or pair.setpoint is None:
            why = "pass both measured= and setpoint= as SwerveModuleState[] topics" if measured or setpoint else "no measured and commanded SwerveModuleState[] pair follows a known name; pass measured= and setpoint="
            return reply(with_status({"measured": pair.measured, "setpoint": pair.setpoint, **({"candidates": pair.others} if pair.others else {})}, (), reason=why))
        spec = CaptureSpec(topics=[pair.measured, pair.setpoint], prefix=False, rate_hz=RATE_HZ, duration_s=duration_s, all_changes=True)
        samples = await session.record_samples(spec)
    rows = {name: [(round(t_ms * 1000), value) for t_ms, value, _ in samples.get(name, ())] for name in (pair.measured, pair.setpoint)}
    first = min((r[0][0] for r in rows.values() if r), default=0)
    try:
        analysis = core.analyze(rows[pair.measured], rows[pair.setpoint], [(0, FAR_FUTURE_US)], first)
    except NothingToCompare as e:
        return reply(with_status({"measured": pair.measured, "setpoint": pair.setpoint}, (), reason=f"{e}; was the robot moving, and are both arrays being published?"))
    out: dict[str, Any] = {"measured": pair.measured, "setpoint": pair.setpoint, "chosen_by": pair.how, "seconds": duration_s, **analysis,
                           "flags": core.lagging(analysis["modules"]) or ["no module lags the others"]}
    if pair.others:
        out["others"] = pair.others
    return reply(out)
