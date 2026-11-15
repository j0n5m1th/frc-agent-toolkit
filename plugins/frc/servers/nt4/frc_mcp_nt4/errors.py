"""The live NT4 server's error type (`NT4Error`), for failures that only exist when talking to a robot."""

from frc_mcp_wpilib.errors import WPILibMCPError


class NT4Error(WPILibMCPError):
    """Not connected, a write refused, or a topic the server has not announced."""
