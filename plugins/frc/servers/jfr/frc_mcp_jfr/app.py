"""The profiling server's FastMCP instance (made from `SPEC`) and its shared parameter types."""

from typing import Annotated

from pydantic import Field

from frc_mcp_core.toolset import FULL_ONLY_TAG

from .spec import SPEC
from .support import LOCAL_ONLY, RECORD, tool_errors

mcp, apply_toolset = SPEC.create()

HIDDEN_IN_CORE = FULL_ONLY_TAG  # hidden when JFR_TOOLSET=core

File = Annotated[str, Field(description="Recording (see jfr_list)")]
Event = Annotated[str, Field(description="Event type, e.g. jdk.ExecutionSample")]
By = Annotated[str | None, Field(description="Event field, or frame (default) | stack | cumulative")]
Top = Annotated[int, Field(ge=1, le=50, description="Max rows")]
Limit = Annotated[int, Field(ge=1, le=100, description="Max entries")]  # mirrors frc_mcp_wpilib.tooling.topic_params.Limit

__all__ = ["HIDDEN_IN_CORE", "LOCAL_ONLY", "RECORD", "By", "Event", "File", "Limit", "Top", "apply_toolset", "mcp", "tool_errors"]
