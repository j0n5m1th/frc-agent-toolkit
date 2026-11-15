"""The Passerine server's FastMCP instance (made from `SPEC`), annotations and shared parameter types."""

from typing import Annotated

from pydantic import Field

from frc_mcp_core.server_support import (
    READ_ONLY_LOCAL as READ,  # reads only: no network, no change
)
from frc_mcp_core.server_support import tool_errors

from .spec import SPEC

# Writes one new .chrp beside the MIDI file inside PASSERINE_DIR; never overwrites, deletes nothing that existed.
CONVERT = {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False}

mcp, _ = SPEC.create()

File = Annotated[str, Field(description="MIDI file (see passerine_list)")]

__all__ = ["CONVERT", "READ", "File", "mcp", "tool_errors"]
