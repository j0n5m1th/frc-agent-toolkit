"""The VS Code command server's FastMCP instance (made from `SPEC`) and annotations; what every server shares
comes from `frc_mcp_core.server_support`."""

from frc_mcp_core.server_support import (
    FULL_ONLY_TAG,
    READ_ONLY_LOCAL,
    Project,
    tool_errors,
)
from frc_mcp_wpilib.tooling.topic_params import NameContains

from .spec import SPEC

# Running a build runs the project's own build scripts (code on this machine); nothing it does is a delete we could not
# redo. Both tools leave this machine too: Gradle resolves from its remote repositories unless `offline` is set, and
# the tool windows Glass/SysId/OutlineViewer open talk to the robot over NT4 - hence openWorldHint.
RUN = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}
# Deploy replaces the program on a robot; vendordep install/remove rewrites files in the project.
DEPLOY = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}
EDIT = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False}


mcp, apply_toolset = SPEC.create()  # the tool modules in `SPEC.tool_modules` register on this instance


__all__ = ["DEPLOY", "EDIT", "FULL_ONLY_TAG", "Project", "READ_ONLY_LOCAL", "RUN", "NameContains", "apply_toolset", "mcp", "tool_errors"]
