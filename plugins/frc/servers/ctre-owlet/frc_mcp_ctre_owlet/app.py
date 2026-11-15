"""The Owlet server's FastMCP instance (made from `SPEC`), annotations and shared parameter types."""

from typing import Annotated

from pydantic import Field

from frc_mcp_core.server_support import (
    READ_ONLY_LOCAL as READ,  # reads only: no network, no change
)
from frc_mcp_core.server_support import tool_errors

from .spec import SPEC

# Writes one new .wpilog beside the hoot file inside OWLET_DIR; never overwrites, deletes nothing that existed.
CONVERT = {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False}

mcp, _ = SPEC.create()

File = Annotated[str, Field(description="Hoot file (see owlet_list)")]
NameContains = Annotated[str | None, Field(description="Case-insensitive substring of the name")]

__all__ = ["CONVERT", "READ", "File", "NameContains", "mcp", "tool_errors"]
