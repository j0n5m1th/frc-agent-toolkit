"""The Phoenix Diagnostic Server server's FastMCP instance (made from `SPEC`), annotations and shared parameter types."""

from typing import Annotated

from pydantic import Field

from frc_mcp_core.server_support import FULL_ONLY_TAG, tool_errors
from frc_mcp_core.server_support import (
    READ_ONLY_LOCAL as READ,  # reads from the controller over the robot network (not `openWorld`: the network is the robot's); changes nothing
)

from .spec import SPEC

# Blinks one device's LED; changes nothing that lasts.
BLINK = {"destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

mcp, apply_toolset = SPEC.create()  # the tool modules in `SPEC.tool_modules` register on this instance


Device = Annotated[str, Field(description="Name from diag_status, or its id")]
Host = Annotated[str | None, Field(description="Default DIAG_HOST")]

__all__ = ["BLINK", "FULL_ONLY_TAG", "READ", "Device", "Host", "apply_toolset", "mcp", "tool_errors"]
