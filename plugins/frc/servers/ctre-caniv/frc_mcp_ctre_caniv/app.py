"""The CANivore server's FastMCP instance (made from `SPEC`), annotations, shared parameter types and toolset switch."""

from typing import Annotated

from pydantic import Field

from frc_mcp_core.server_support import FULL_ONLY_TAG, tool_errors
from frc_mcp_core.server_support import (
    READ_ONLY_LOCAL as READ,  # reads only: no network, no change
)

from .spec import SPEC

# Blinks the LED of one CANivore so it can be told apart; changes nothing that lasts.
BLINK = {"destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
# Renames or resets a CANivore; refused unless CANIV_ALLOW_CONFIG=1.
CONFIGURE = {"destructiveHint": True, "idempotentHint": True, "openWorldHint": False}
# Replaces a CANivore's firmware; refused unless CANIV_ALLOW_FLASH=1.
FLASH = {"destructiveHint": True, "idempotentHint": True, "openWorldHint": False}

mcp, apply_toolset = SPEC.create()  # the tool modules in `SPEC.tool_modules` register on this instance


Device = Annotated[str, Field(description="CANivore serial number or name (see caniv_list)")]

__all__ = ["BLINK", "CONFIGURE", "FLASH", "FULL_ONLY_TAG", "READ", "Device", "apply_toolset", "mcp", "tool_errors"]
