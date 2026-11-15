"""The simulation server's error type (`SimError`), for failures that only exist when launching a sim."""

from frc_mcp_wpilib.errors import WPILibMCPError


class SimError(WPILibMCPError):
    """No robot project found, a refused task or property, a sim already running, or a port already taken."""
