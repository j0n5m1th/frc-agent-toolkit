"""Read-only, explicit connection diagnostics for students and mentors."""

import sys

from . import config
from .errors import NT4Error
from .session import session


def _check_configuration(host: str | None, port: int | None) -> tuple[str, int, str | None]:
    """(host, port, write restriction) to use, or NT4Error saying why the configuration is unusable."""
    host = (host or config.default_host() or "").strip()
    if not host:
        raise NT4Error("No robot address configured. Pass --host, or set NT4_HOST. Fake robot: --host 127.0.0.1 --port 5820.")
    effective_port = config.checked_port(port) if port is not None else config.default_port()
    config.idle_seconds()  # validate only
    try:
        restriction = config.write_restriction()
    except NT4Error as e:
        raise NT4Error(f"Invalid write safety configuration: {e}") from e
    return host, effective_port, restriction


def _write_policy_line(restriction: str | None) -> str:
    if restriction:
        return f"Normal server write policy: {restriction}"
    return "Normal server write policy: writes are enabled for configured prefixes."


async def diagnose(host: str | None, port: int | None, timeout_s: float) -> int:
    try:
        host, effective_port, restriction = _check_configuration(host, port)
    except NT4Error as e:
        print(e, file=sys.stderr)
        return 1
    print(f"Checking NT4 at {host}:{effective_port} (read-only; no values published).")
    try:
        await session.connect(host, effective_port, "mcp-nt4-diagnostic", timeout_s)
        status = session.status()
        if not status["connected"]:
            print("Connection dropped while checking NT4; retry and check the robot and network.", file=sys.stderr)
            return 1
        print(f"Connected. Discovered {status['topic_count']} topics.")
        if not session.clock_synchronized():
            print("NT4 clock synchronization is not available yet; update ages may be unknown.")
        print(_write_policy_line(restriction))
        return 0
    except NT4Error as e:
        print(f"Connection failed: {e}", file=sys.stderr)
        return 1
    finally:
        await session.disconnect_safely(wait=True)
