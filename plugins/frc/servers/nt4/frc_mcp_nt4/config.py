"""NT4 settings (NT4_*), read at call time. The list is in docs/SETTINGS.md.

With NT4_HOST set, tools connect lazily on first use. Writes need NT4_ALLOW_WRITES and NT4_WRITE_PREFIXES; NT4_READ_ONLY
overrides them. NT4_TOOLSET=core hides the capture and write tools (about half the schema tokens); `nt4_watch` stays.
"""

from pathlib import Path

from frc_mcp_core.settings import Capability, env_int, env_number, env_str
from frc_mcp_core.toolset import Toolset

from .errors import NT4Error

DEFAULT_PORT = 5810


TOOLSET = Toolset("NT4_TOOLSET", NT4Error)  # read it with TOOLSET.current(); `create_server` makes the server's apply_toolset from it


def default_host() -> str | None:
    return env_str("NT4_HOST")


SECURE_PORT = 5811  # NT4's wss port; ntcore (and so this server) only speaks plain ws
CONTROLLER_PORT = 6810  # the Systemcore's private NT4 server (alerts, /sys, /canbus): read-only here, whatever the write settings say
MAX_IDENTITY = 64
MAX_PORT = 65535
MAX_IDLE_S = 86400  # one day: the largest idle timeout worth waiting for


def default_port() -> int:
    return env_int("NT4_PORT", DEFAULT_PORT, 1, MAX_PORT, NT4Error)


# Naming convention for these helpers: `check_*` raise NT4Error and return None; `checked_*` also return the value.
def checked_port(port: int) -> int:
    """`port` if it is a usable TCP port, else NT4Error."""
    if not 1 <= port <= MAX_PORT:
        raise NT4Error(f"Port must be between 1 and {MAX_PORT}.")
    return port


def check_target(host: str, identity: str) -> None:
    """Reject host and client-name values ntcore would accept but that can only mean a mistake."""
    if not host.strip():
        raise NT4Error("host is empty; give an IP or name such as '127.0.0.1' or '10.TE.AM.2'.")
    if "://" in host:
        scheme = host.split("://", 1)[0].lower()
        if scheme in ("wss", "https"):
            raise NT4Error(f"Secure NT4 (wss, port {SECURE_PORT}) is not supported: ntcore only speaks plain ws. "
                           "Pass a plain host name or IP with the default port 5810.")
        raise NT4Error("host must be a name or IP, not a URL; drop the 'ws://' (the port goes in `port`).")
    if any(c in host for c in "/?# \t") or (host.count(":") == 1 and not host.startswith("[")):
        raise NT4Error(f"host {host!r} must be only a name or IP; put the port in `port` (default 5810).")
    if not identity.strip() or len(identity) > MAX_IDENTITY or any(ord(c) < 32 or ord(c) == 127 for c in identity):
        raise NT4Error(f"The client name must be 1 to {MAX_IDENTITY} printable characters. "
                       "The server appends '@<number>' to it, so a name may itself contain '@'.")


def log_dir() -> Path:
    """Where capture logs go: NT4_LOG_DIR, else `logs/` in the checkout this server runs from (the nearest folder above this
    file with `plugins/frc/pyproject.toml`), else `logs/` in the working directory when installed as a package.
    """
    raw = env_str("NT4_LOG_DIR")
    if raw:
        return Path(raw).expanduser()
    project = next((p for p in Path(__file__).resolve().parents if (p / "plugins" / "frc" / "pyproject.toml").is_file()), Path.cwd())
    return project / "logs"


def default_identity() -> str:
    return env_str("NT4_IDENTITY", "mcp-nt4") or "mcp-nt4"


def idle_seconds() -> float:
    """NT4_IDLE_SECONDS as a finite, nonnegative number of seconds (0 = never disconnect for idleness)."""
    return env_number("NT4_IDLE_SECONDS", 600, 0, MAX_IDLE_S, NT4Error)


def write_prefixes() -> list[str]:
    prefixes = [p.strip().rstrip("/") for p in (env_str("NT4_WRITE_PREFIXES") or "").split(",") if p.strip()]
    if any(not p or not p.startswith("/") or "//" in p for p in prefixes):
        raise NT4Error("NT4_WRITE_PREFIXES must contain non-root absolute topic paths; writes are disabled.")
    return prefixes


# The write gate as two shared Capabilities (as the commands server's `deploy`). `NT4_READ_ONLY` is read with `enabled()` only:
# it is the override, so being *set* refuses, the reverse of what `require()` states.
allow_writes = Capability("NT4_ALLOW_WRITES", "Writing values", NT4Error, why="the robot reads them as input")
read_only = Capability("NT4_READ_ONLY", "Read-only mode", NT4Error)


def writes_possible() -> bool:
    """False when every write would be refused (read-only on, writes not opted in, or no allowed prefix): serving then hides the two write tools.

    A bad `NT4_READ_ONLY` or `NT4_ALLOW_WRITES` value raises NT4Error, so the server stops with the message; a bad prefix list only hides the tools.
    """
    only_read = read_only.enabled()
    opted_in = allow_writes.enabled()
    try:
        has_prefixes = bool(write_prefixes())
    except NT4Error:
        has_prefixes = False
    return opted_in and has_prefixes and not only_read


def write_restriction(prefixes: list[str] | None = None, port: int | None = None) -> str | None:
    """Why writes are disabled (None if enabled), for status reports. Raises NT4Error for an invalid safety setting.

    `prefixes` may carry an already-parsed `write_prefixes()` result, so a caller that also matches a topic against it
    (see `check_writable`) reads the environment once instead of twice."""
    if port == CONTROLLER_PORT:  # first: the settings below cannot lift it, so do not send the user to them
        return f"Port {CONTROLLER_PORT} is the controller's own server: read-only here, whatever the write settings say. Use port 5810 for the robot program's topics."
    # All three settings are read before anything is decided, so a malformed one is reported even when an earlier one
    # already refuses (the way the two flags and the prefix list are validated together).
    only_read = read_only.enabled()
    refusal = allow_writes.refusal()
    if prefixes is None:
        prefixes = write_prefixes()
    if only_read:
        return "Writes are disabled (NT4_READ_ONLY is set on the MCP server)."
    if refusal:
        return refusal
    if not prefixes:
        return "Writes are disabled; NT4_WRITE_PREFIXES must list the allowed topic paths."
    return None


def check_writable(topic: str, port: int | None = None) -> None:
    """Raise NT4Error, saying why and how to change it, unless writing `topic` is allowed."""
    prefixes = write_prefixes()  # the environment read once for the whole check
    if restriction := write_restriction(prefixes, port):
        raise NT4Error(restriction)
    if not any(topic == p or topic.startswith(p + "/") for p in prefixes):
        raise NT4Error(f"Topic {topic!r} is outside the writable prefixes {prefixes} (NT4_WRITE_PREFIXES).")
