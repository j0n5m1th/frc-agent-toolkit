"""The generic error type shared code raises."""

from frc_mcp_core.errors import FrcMcpError


class WPILibMCPError(FrcMcpError):
    """User-facing failure; the message should say what to do next.
    Shared code raises this because it cannot know which server is running; servers subclass it, and `tool_errors` turns any of
    them into a tool error the model can read.
    """
