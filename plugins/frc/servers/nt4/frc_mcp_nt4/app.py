"""The NT4 server's FastMCP instance (made from `SPEC`) and its own annotations and parameter types - declarations only, the boot steps
(importing the tool modules and applying the toolset) are in `spec.py`, as in every other server here; the ones the
NT4 and log servers share come from `frc_mcp_core.server_support` (plumbing) and
`frc_mcp_wpilib.tooling.topic_params` (topic parameters)."""

from typing import Annotated

from pydantic import Field

from frc_mcp_core.server_support import (
    FULL_ONLY_TAG,
    READ_ONLY,
    READ_ONLY_LOCAL,
    tool_errors,
)
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

from .spec import SPEC, WATCH_HINT, CoreAwareToolList, server_instructions, slim_tools

mcp, apply_toolset = SPEC.create()  # the tool modules in `SPEC.tool_modules` register on this instance


# Annotations (READ_ONLY is shared, see frc_mcp_core.server_support) are hints clients use for approval UX.
# Changes only this server's own state (connection, captures): no robot-visible effect, nothing destroyed on the robot.
LOCAL_STATE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}
# Can change what the robot sees: publishes, releases, or (disconnect) drops our publishers.
WRITE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}

# NT4-only parameter types (the ones the NT4 and log servers share come from `topic_params` and `server_support` above).
AsPrefixes = Annotated[bool, Field(description="Treat topics as prefixes")]
AllChanges = Annotated[bool, Field(description="Every update, not sampled (events); some may be lost upstream")]
RateHz = Annotated[float, Field(ge=0.1, le=250, description="Requested Hz")]
CaptureId = Annotated[str, Field(description="Capture id")]
ValueWaitS = Annotated[float, Field(ge=0, le=30, description="Seconds to wait for values")]
ConnectWaitS = Annotated[float, Field(gt=0, le=60, description="Seconds to wait")]
WaitConditionS = Annotated[float, Field(gt=0, le=120, description="Max seconds to wait")]
CaptureWaitS = Annotated[float, Field(ge=0, le=300, description="Seconds to wait for the capture to end first")]
ReadBackWaitS = Annotated[float, Field(ge=0, le=5, description="Max read-back wait")]
IncludeAge = Annotated[bool, Field(description="Include last-update age (needs synced clocks); old values can be legitimate")]

# The whole surface consumers import from this module: the shared re-exports, the instance and its toolset helpers,
# this server's own annotation dicts, and the NT4-only parameter types.
__all__ = [
    "FULL_ONLY_TAG", "LOCAL_STATE", "READ_ONLY", "READ_ONLY_LOCAL", "WATCH_HINT", "WRITE",
    "AllChanges", "Also", "AsPrefixes", "CaptureId", "CaptureWaitS", "ConnectWaitS", "CoreAwareToolList", "Depth", "Fields",
    "IncludeAge", "IncludeHidden", "Limit", "MaxRows", "MaxSamples", "MaxTopics", "NameContains", "PageLimit", "PageOffset", "RateHz",
    "ReadBackWaitS", "ReduceParam", "SinceMs", "StructField", "StructFields", "Threshold", "Topics", "UntilMs", "ValueWaitS", "WaitConditionS",
    "apply_toolset", "mcp", "server_instructions", "slim_tools", "tool_errors",
]
