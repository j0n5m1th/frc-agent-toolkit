"""Settings of the Phoenix Diagnostic Server reader (DIAG_*), read at call time. The list is in docs/SETTINGS.md.
The default host 172.30.0.1 is the Systemcore on its own Wi-Fi (USB is 172.26.0.1 on Windows, 172.27.0.1 on Linux/macOS); the
port 1250 was seen on a Systemcore. Only private, loopback and link-local addresses are contacted unless DIAG_ALLOW_ANY_HOST=1.
Nothing here changes a device: only reads and an LED blink are sent (docs/CTRE.md lists the actions never sent).
"""

from frc_mcp_core.settings import env_flag, env_number, env_str
from frc_mcp_core.toolset import Toolset
from frc_mcp_ctre.errors import CtreError
from frc_mcp_systemcore_common.hosts import check_host, checked_address

DEFAULT_HOST = "172.30.0.1"


def host(given: str | None = None) -> str:
    """The controller to talk to: the tool's `host`, else DIAG_HOST, else the Systemcore's own Wi-Fi address; checked to be a robot-network address."""
    chosen = given or env_str("DIAG_HOST", DEFAULT_HOST) or DEFAULT_HOST
    return check_host(chosen, allow_any=env_flag("DIAG_ALLOW_ANY_HOST", False, CtreError), setting="DIAG_ALLOW_ANY_HOST", error=CtreError)


def address(host: str) -> str:
    """The IP address to connect to for `host`, resolved and checked now (every address of a name must be a robot-network one)."""
    return checked_address(host, allow_any=env_flag("DIAG_ALLOW_ANY_HOST", False, CtreError), setting="DIAG_ALLOW_ANY_HOST", error=CtreError)


def port() -> int:
    return int(env_number("DIAG_PORT", 1250, 1, 65535, CtreError))


def timeout_s() -> float:
    return env_number("DIAG_TIMEOUT_S", 5, 1, 60, CtreError)


TOOLSET = Toolset("DIAG_TOOLSET", CtreError)  # read it with TOOLSET.current(); `create_server` makes the server's apply_toolset from it
