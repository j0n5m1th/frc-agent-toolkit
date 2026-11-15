"""The Systemcore server's error type, for failures that only exist when talking to the controller."""

from frc_mcp_wpilib.errors import WPILibMCPError


class SystemcoreError(WPILibMCPError):
    """Unreachable controller, refused host, missing login, changed host key, or an action that is not allowed."""


class NeedsProvisioning(SystemcoreError):
    """SSH is reachable but this server's key is not installed (a new image, or never set up)."""


class HostKeyChanged(SystemcoreError):
    """The controller's SSH host key differs from the pinned one (usually an OS update; possibly something worse)."""
