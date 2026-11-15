"""Connection tools: nt4_connection (connect or disconnect) and nt4_status."""

from typing import Annotated, Any, Literal

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply

from .. import config
from ..app import READ_ONLY_LOCAL, WRITE, ConnectWaitS, mcp, tool_errors
from ..endpoint import check_endpoint
from ..errors import NT4Error
from ..session import session


def _endpoint(host: str | None, port: int | None) -> tuple[str | None, int]:
    """The host and port to use: explicit arguments, else the live connection, else the NT4_HOST/NT4_PORT defaults."""
    host = host or (session.host if session.connected else None) or config.default_host()
    port = port or (session.port if session.connected and host == session.host else config.default_port())
    return host, port


def _brief_status() -> dict[str, Any]:
    """The status a connect or disconnect reply needs; the write-policy text and clock figures stay in `nt4_status`."""
    status = session.status()
    for key in ("write_reason", "server_time_us", "clock_offset_us"):
        status.pop(key, None)
    return status


@mcp.tool(annotations={**WRITE, "title": "Connect or disconnect"}, output_schema=None)
@tool_errors
async def nt4_connection(
    action: Annotated[Literal["connect", "disconnect"], Field(description="disconnect stops captures and drops our published topics")] = "connect",
    host: Annotated[str | None, Field(description="'127.0.0.1' (sim) or '10.TE.AM.2' (robot); default NT4_HOST")] = None,
    port: Annotated[int | None, Field(ge=1, le=65535, description="Default 5810 / NT4_PORT")] = None,
    identity: Annotated[str | None, Field(description="Client name")] = None,
    wait_s: ConnectWaitS = 5.0,
) -> ToolResult:
    """Connect (host defaults to NT4_HOST; a no-op when already connected there; changing `identity` needs action=disconnect first; switching host needs active requests finished) or disconnect. For status alone use nt4_status."""
    if action == "disconnect":
        await session.disconnect_safely()  # re-arms the idle timer itself
        return reply(_brief_status())
    host, port = _endpoint(host, port)
    if host is None:
        return reply({**session.status(), "hint": "pass `host` (or set NT4_HOST on the MCP server) to connect"})
    check_endpoint(host, port)
    same_endpoint = session.connected and (host, port) == (session.host, session.port)
    if same_endpoint and identity is not None and identity != session.identity:
        # session.connect keeps the live connection for the same endpoint, so a new name would be silently ignored.
        raise NT4Error(f"Already connected as {session.identity!r}; call nt4_connection with action='disconnect' first to use client name {identity!r}.")
    if not same_endpoint:
        await session.connect(host, port, identity or config.default_identity(), timeout_s=wait_s)
    else:
        session.note_activity()  # the re-arm this path gets: `session.connect` returns before reaching its own
    return reply(_brief_status())


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "Connection status"}, output_schema=None)
@tool_errors
async def nt4_status() -> ToolResult:
    """Connection status without connecting: server, clock sync, publishers and their expiry, captures and write policy."""
    # note_activity() only re-arms our local idle timer; it sends nothing to the robot, so READ_ONLY stays accurate.
    # It is kept so a student polling status is not disconnected mid-session.
    session.note_activity()
    return reply(session.status())
