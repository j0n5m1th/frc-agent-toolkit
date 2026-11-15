"""The log server's error type (`WPILogError`), for failures that only exist when reading logs."""

from frc_mcp_wpilib.errors import WPILibMCPError


class WPILogError(WPILibMCPError):
    """A file outside the allowed folders, an invalid log, no log open, or a mode never seen in it."""
