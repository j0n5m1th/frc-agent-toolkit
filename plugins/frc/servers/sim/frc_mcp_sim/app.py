"""The simulation server's FastMCP instance (made from `SPEC`) and its own annotations and parameter types; the ones every
server uses come from `frc_mcp_core.server_support`; the instance and its core/full switch are made by the `SPEC` (`frc_mcp_core.spec`)."""

from typing import Annotated, Literal

from pydantic import Field

from frc_mcp_core.server_support import (
    FULL_ONLY_TAG,
    READ_ONLY_LOCAL,
    Project,
    tool_errors,
)
from frc_mcp_wpilib.tooling.topic_params import NameContains

from .spec import SPEC

# Starting runs the project's own build (code on this machine) and stopping ends only the process we started; neither deletes data.
START = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
STOP = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

mcp, apply_toolset = SPEC.create()  # the tool modules in `SPEC.tool_modules` register on this instance

Level = Annotated[Literal["all", "warn", "error"], Field(description="Lowest level shown: all | warn (also errors) | error")]


__all__ = ["FULL_ONLY_TAG", "Project", "READ_ONLY_LOCAL", "START", "STOP", "Level", "NameContains", "apply_toolset", "mcp", "tool_errors"]
