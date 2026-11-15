"""Settings of the Systemcore server (SYSTEMCORE_*), read at call time. The list is in docs/SETTINGS.md.

A `.env` file in the state folder is read once at start (`frc-mcp systemcore init` writes a template). Real environment variables
win, and a blank one counts as unset. SYSTEMCORE_STATE_DIR and SYSTEMCORE_ENV_FILE are read only from the real environment.
Nothing from the file goes into os.environ, so programs this server starts never inherit a password. A password is never
returned by a tool and never asked for in chat.
"""

import contextlib
import functools
from pathlib import Path

from frc_mcp_core.settings import Capability, EnvFile, env_flag, env_number, env_str
from frc_mcp_core.toolset import Toolset

from .errors import SystemcoreError

DEFAULT_USER = "systemcore"  # the controller's documented default login
DEFAULT_PASSWORD = "systemcore"
_REAL_ENV_ONLY = ("SYSTEMCORE_STATE_DIR", "SYSTEMCORE_ENV_FILE")  # where the file is cannot be set by the file
_PORT_NAMES = ("diag", "hwmon", "update", "bundle", "discovery", "ssh", "nt")


def env_file() -> Path:
    return Path(raw).expanduser() if (raw := env_str("SYSTEMCORE_ENV_FILE")) else state_dir() / ".env"


# The one settings file of this server, layered behind the real environment (`frc_mcp_core.settings.EnvFile`); nothing from it is put into os.environ.
_file = EnvFile("SYSTEMCORE_", env_file, _REAL_ENV_ONLY)
_file_values = _file.values  # the settings file's values, read by load_env_file
get = _file.get  # a setting: the real environment first, then the settings file; a blank value counts as not set
parse_env_text = _file.parse
load_env_file = _file.load


wifi = Capability("SYSTEMCORE_ALLOW_WIFI_JOIN", "Joining a Wi-Fi network from this computer", SystemcoreError,
                  why="it changes this computer's Wi-Fi connection, and may cut its internet", getenv=get)
provision = Capability("SYSTEMCORE_ALLOW_PROVISION", "Installing this server's SSH key on the Systemcore", SystemcoreError,
                       why="it adds a key to the controller's authorized_keys", getenv=get)
restart = Capability("SYSTEMCORE_ALLOW_RESTART", "Restarting robot code or rebooting the Systemcore", SystemcoreError,
                     why="the robot program stops, and a reboot also drops Wi-Fi and the Driver Station", getenv=get)
prune = Capability("SYSTEMCORE_ALLOW_PRUNE", "Deleting logs on the Systemcore", SystemcoreError, why="it cannot be undone", getenv=get)


def host() -> str | None:
    return env_str("SYSTEMCORE_HOST", getenv=get)


def user() -> str:
    return env_str("SYSTEMCORE_USER", DEFAULT_USER, getenv=get) or DEFAULT_USER


def secret(name: str) -> str | None:
    """A password setting. Never put it in a reply, a log or an error message."""
    return get(name)


def password() -> str:
    """The controller's login password (default systemcore). Only the key installation reads it."""
    return secret("SYSTEMCORE_PASSWORD") or DEFAULT_PASSWORD


def custom_password() -> bool:
    """Has the user set their own login password (so it is worth protecting from an unconfirmed host)?"""
    return password() != DEFAULT_PASSWORD


def team() -> int | None:
    raw = env_str("SYSTEMCORE_TEAM", getenv=get)
    if not raw:
        return None
    if not raw.isdigit() or not 1 <= int(raw) <= 99999:
        raise SystemcoreError(f"SYSTEMCORE_TEAM must be a team number from 1 to 99999, not {raw!r}.")
    return int(raw)


def wifi_prefix(which: str) -> str:
    """The settings-name prefix of a network: SYSTEMCORE_WIFI (the controller's own) or SYSTEMCORE_RADIO."""
    return "SYSTEMCORE_WIFI" if which == "systemcore" else "SYSTEMCORE_RADIO"


def radio_host() -> str | None:
    """The controller's address through the robot radio, 10.TE.AM.2, when the team number is set."""
    number = team()
    return None if number is None else f"10.{number // 100}.{number % 100}.2"


def wifi_credentials(which: str) -> tuple[str | None, str | None]:
    """`(ssid, password)` for `which` = `systemcore` (the controller's own Wi-Fi) or `radio` (the robot radio); None where not set.

    The radio name defaults to FRC-<team> when SYSTEMCORE_TEAM is set.
    """
    prefix = wifi_prefix(which)
    ssid = env_str(f"{prefix}_SSID", getenv=get)
    if ssid is None and which == "radio" and (number := team()) is not None:
        ssid = f"FRC-{number}"
    return ssid, secret(f"{prefix}_PASSWORD")


def state_dir() -> Path:
    raw = env_str("SYSTEMCORE_STATE_DIR")  # the real environment only
    return Path(raw).expanduser() if raw else Path.home() / ".mcp-systemcore"


def download_dir() -> Path:
    raw = env_str("SYSTEMCORE_DOWNLOAD_DIR", getenv=get)
    return Path(raw).expanduser() if raw else state_dir() / "downloads"


def allow_any_host() -> bool:
    return env_flag("SYSTEMCORE_ALLOW_ANY_HOST", False, SystemcoreError, getenv=get)


def timeout_s() -> float:
    return env_number("SYSTEMCORE_TIMEOUT_S", 10, 1, 120, SystemcoreError, getenv=get)


def max_download_bytes() -> int:
    return int(env_number("SYSTEMCORE_MAX_DOWNLOAD_MB", 200, 1, 10_000, SystemcoreError, getenv=get) * 1024 * 1024)


def ports() -> dict[str, int]:
    """Port overrides from SYSTEMCORE_PORTS (`name=port,name=port`); a name or number that is not valid raises SystemcoreError."""
    return dict(_parse_ports(get("SYSTEMCORE_PORTS") or ""))


@functools.cache
def _parse_ports(raw: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for item in raw.split(","):
        if not item.strip():
            continue
        name, _, number = item.partition("=")
        if name.strip() not in _PORT_NAMES or not number.strip().isdigit() or not 1 <= int(number) <= 65535:
            raise SystemcoreError(f"SYSTEMCORE_PORTS has {item.strip()!r}: use name=port with a name from {', '.join(_PORT_NAMES)} and a port from 1 to 65535.")
        found[name.strip()] = int(number)
    return found


TOOLSET = Toolset("SYSTEMCORE_TOOLSET", SystemcoreError, getenv=get)  # read it with TOOLSET.current(); `create_server` makes the server's apply_toolset from it


# ---- the .env file (reading it is `_file`, above) ----

ENV_TEMPLATE = """# Settings for mcp-systemcore. Plain NAME=value lines; remove the # to use one. Restart the MCP server after editing.
# This file can hold passwords: keep it in your own user folder and never commit it.

# The controller's login (the documented defaults). The password is used only to install this server's SSH key.
# SYSTEMCORE_USER=systemcore
# SYSTEMCORE_PASSWORD=systemcore

# Your team number (the robot radio name defaults to FRC-<team>).
# SYSTEMCORE_TEAM=

# The controller's own Wi-Fi: SYSTEMCORE-<suffix>. The password is shown on its display.
# SYSTEMCORE_WIFI_SSID=
# SYSTEMCORE_WIFI_PASSWORD=

# The robot radio's Wi-Fi (default name FRC-<team>).
# SYSTEMCORE_RADIO_SSID=
# SYSTEMCORE_RADIO_PASSWORD=

# Allow systemcore_connect to join those networks from this computer (it changes this computer's Wi-Fi).
# SYSTEMCORE_ALLOW_WIFI_JOIN=1

# Allow systemcore_provision to install this server's SSH key on the controller (adds a key to its authorized_keys).
# SYSTEMCORE_ALLOW_PROVISION=1

# Allow systemcore_restart_robot_code and systemcore_reboot (never while the robot is enabled or on a field).
# SYSTEMCORE_ALLOW_RESTART=1
"""


def write_env_template() -> tuple[Path, bool]:
    """Create the file from the template unless it exists. Returns `(path, created)`; the file is made owner-only where the OS allows."""
    path = env_file()
    if path.exists():
        return path, False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ENV_TEMPLATE, encoding="utf-8")
    except OSError as e:
        raise SystemcoreError(f"Cannot write {path}: {e}. Check that file is writable (from a clone, you can set SYSTEMCORE_ENV_FILE).") from e
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return path, True


def settings_report() -> dict[str, str]:
    """What is set, with no secret values: for the `check` command."""
    def has(name: str) -> str:
        return "set" if secret(name) else "not set"

    def allowed(capability: Capability) -> str:
        return "allowed" if capability.enabled() else "off"

    wifi_ssid, radio_ssid = wifi_credentials("systemcore")[0], wifi_credentials("radio")[0]
    return {"env_file": f"{env_file()} ({'found' if env_file().exists() else 'missing: run init'})", "user": user(),
            "password": "set" if secret("SYSTEMCORE_PASSWORD") else "not set (the default is used)",
            "team": str(team() or "not set"), "wifi_ssid": wifi_ssid or "not set", "wifi_password": has("SYSTEMCORE_WIFI_PASSWORD"),
            "radio_ssid": radio_ssid or "not set", "radio_password": has("SYSTEMCORE_RADIO_PASSWORD"),
            "wifi_join": allowed(wifi), "provision": allowed(provision), "restart": allowed(restart)}
