"""What every MCP server in this repository needs to declare tools: annotations, the project parameter type, error
conversion, lean advertised schemas (the settings readers are `frc_mcp_core.settings`)."""

from typing import Annotated

from pydantic import Field

from frc_mcp_core.tools import SlimToolList, ToolTuning, tool_errors_for
from frc_mcp_core.toolset import (
    FULL_ONLY_TAG,
    ToolsetName,
    env_toolset,
    parse_toolset,
    set_toolset_visibility,
)

from .errors import FrcMcpError

# Annotations are hints clients use for approval UX (e.g. auto-allow read-only tools).
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True}
# Reads local files only: no network, no effect on anything outside this machine.
READ_ONLY_LOCAL = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}

# The project-folder parameter every server that may know several projects uses; last in its tool's signature.
Project = Annotated[str | None, Field(description="Project folder, if several")]

# Toolsets (`full`, `core`, the `full-only` tag) and lean schemas are re-exported here, as every server imports them from this module.
tool_errors = tool_errors_for(FrcMcpError)  # apply under `@mcp.tool`: any of the repository's errors (every server's subclass of FrcMcpError) becomes a ToolError


__all__ = [
    "env_toolset",
    "FULL_ONLY_TAG",
    "ToolsetName",
    "parse_toolset",
    "set_toolset_visibility",
    "Project",
    "READ_ONLY",
    "READ_ONLY_LOCAL",
    "SlimToolList",
    "ToolTuning",
    "tool_errors",
]
