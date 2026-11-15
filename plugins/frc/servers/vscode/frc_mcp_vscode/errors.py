"""The VS Code command server's error type (`VSCodeError`), for failures that only exist when running these commands."""

from frc_mcp_wpilib.errors import WPILibMCPError


class VSCodeError(WPILibMCPError):
    """No robot project found, a deploy that is not allowed, a bad team number or vendordep name, or a missing file."""
