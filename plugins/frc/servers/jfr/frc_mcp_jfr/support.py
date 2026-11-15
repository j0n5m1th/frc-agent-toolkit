"""What every tool needs: error conversion, annotations and one size-bounded JSON reply (all but the annotations are `frc_mcp_core`'s; this server imports nothing else of the repo)."""

from typing import Any

from fastmcp.tools import ToolResult

from frc_mcp_core.reply import reply_halving
from frc_mcp_core.server_support import tool_errors

from . import config

# Every reading tool: `jfr_list` (both kinds) and `jfr_live` (both `what` values) only print what jcmd already knows -
# the thread dump, and the class histograms whose GC jcmd forces itself - so nothing beyond the printed rows changes.
LOCAL_ONLY = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
# `jfr_record` alone: starting a recording puts a profiler inside another process and writes one .jfr file, it deletes
# nothing, and a second call makes a second recording - so not read-only, and not idempotent either.
RECORD = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}

__all__ = ["LOCAL_ONLY", "RECORD", "reply", "tool_errors"]  # `tool_errors` (apply under `@mcp.tool`): a JfrError becomes a ToolError, whose message reaches the model as written


def reply(data: dict[str, Any]) -> ToolResult:
    """One compact JSON block within `JFR_MAX_CHARS` (default 6000, allowed 500-50000): over it the longest list is halved until it fits (`frc_mcp_core.reply.reply_halving`)."""
    return reply_halving(data, config.max_chars())
