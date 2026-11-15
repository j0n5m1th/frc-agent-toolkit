"""The profiling server's error type: a failure whose message says what to do next."""

from frc_mcp_core.errors import FrcMcpError


class JfrError(FrcMcpError):
    """A missing JDK tool, a file outside the allowed folders, a JVM that is not running, or a recording that cannot be read."""
