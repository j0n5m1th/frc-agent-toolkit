"""The CTRE tool manager's FastMCP instance (made from `SPEC`), annotations."""

from typing import Annotated

from pydantic import Field

from frc_mcp_core.server_support import tool_errors

from .spec import SPEC

# Reads CTRE's public index (and saves a copy of it in the tools folder); changes nothing else.
LOOKUP = {"destructiveHint": False, "idempotentHint": True}
# Downloads one program into the tools folder; refused unless CTRE_ALLOW_INSTALL=1. Never replaces or deletes a file.
INSTALL = {"destructiveHint": False, "idempotentHint": True}

mcp, _ = SPEC.create()

Tool = Annotated[str, Field(description="e.g. owlet, PhoenixDiagnosticsProgram")]

__all__ = ["INSTALL", "LOOKUP", "Tool", "mcp", "tool_errors"]
