"""The CTRE packages' error type: a failure whose message says what to do next."""

from frc_mcp_wpilib.errors import WPILibMCPError


class CtreError(WPILibMCPError):
    """A bad setting, an index that cannot be read, a download that failed its hash check, a tool that is not installed, or an action that is not allowed."""
