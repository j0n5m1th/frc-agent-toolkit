"""The log server's FastMCP instance (made from `SPEC`) and its own parameter types. Shared annotations, parameter types, error
conversion and lean schemas come from `frc_mcp_core.server_support` and `frc_mcp_wpilib.tooling.topic_params`.
"""

from typing import Annotated, Literal

from pydantic import Field

from frc_mcp_core.server_support import FULL_ONLY_TAG, READ_ONLY_LOCAL, tool_errors
from frc_mcp_wpilib.tooling.topic_params import (
           Also,
           Depth,
           Fields,
           IncludeHidden,
           Limit,
           MaxRows,
           MaxSamples,
           MaxTopics,
           PageLimit,
           PageOffset,
           Prefix,
           ReduceParam,
           SinceMs,
           StructField,
           StructFields,
           Threshold,
           Topics,
           UntilMs,
)
from frc_mcp_wpilib.tooling.topic_params import (
           AnyName as NameContains,
)

from .spec import SPEC, server_instructions

mcp, apply_toolset = SPEC.create()  # the tool modules in `SPEC.tool_modules` register on this instance



LogId = Annotated[str | None, Field(description="Log id from wpilog_open (default: the newest)")]
Align = Annotated[Literal["start", "enable", "auto", "teleop", "test", "utility"], Field(description="Time zero: start (log start) | enable | auto | teleop | test | utility (first such period; utility is 2027's renamed test)")]

__all__ = ["FULL_ONLY_TAG", "READ_ONLY_LOCAL", "Align", "Also", "Depth", "Fields", "IncludeHidden", "Limit", "LogId", "MaxRows", "MaxSamples", "MaxTopics",
           "NameContains", "PageLimit", "PageOffset", "Prefix", "ReduceParam", "SinceMs", "StructField", "StructFields", "Threshold", "Topics", "UntilMs",
           "apply_toolset", "mcp", "server_instructions", "tool_errors"]
