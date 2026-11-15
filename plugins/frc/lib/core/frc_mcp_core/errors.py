"""The one error type the shared code raises when it cannot know which server is running."""


class FrcMcpError(Exception):
    """A user-facing failure: the message says what to do next.

    Code in `frc_mcp_core` (a bad size setting for replies, say) raises this, because it knows no server. Each library and server
    has its own subclass (`WPILibMCPError`, `JfrError`, ...), and a tool wrapper turns this base, so every subclass, into a tool error the model can read.
    """
