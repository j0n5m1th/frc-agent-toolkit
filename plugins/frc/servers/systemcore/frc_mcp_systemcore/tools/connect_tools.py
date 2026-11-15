"""systemcore_connect and systemcore_disconnect: join and leave the Systemcore's or the robot radio's Wi-Fi (the rules are in connection.py)."""

from typing import Annotated, Literal

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply

from .. import connection
from ..app import CONNECT, mcp, tool_errors

Network = Literal["systemcore", "radio"]


@mcp.tool(annotations={**CONNECT, "title": "Join Systemcore Wi-Fi"}, output_schema=None)
@tool_errors
async def systemcore_connect(
    network: Annotated[Network, Field(description="systemcore = its own Wi-Fi; radio = the robot radio")] = "systemcore",
    wait_s: Annotated[float, Field(ge=5, le=60)] = 20,
) -> ToolResult:
    """Connect to the Systemcore's or the robot radio's Wi-Fi with the saved settings. Does nothing if a Systemcore already answers. Otherwise it drops the current Wi-Fi: ask first."""
    return reply(await connection.connect(network, wait_s))


@mcp.tool(annotations={**CONNECT, "title": "Leave Systemcore Wi-Fi"}, output_schema=None)
@tool_errors
async def systemcore_disconnect(
    network: Annotated[Network, Field(description="Network to leave")] = "systemcore",
    rejoin: Annotated[bool, Field(description="Go back to the network connect left")] = True,
) -> ToolResult:
    """Disconnect Wi-Fi: leave the network systemcore_connect joined and, by default, rejoin the one it dropped. Ask first."""
    return reply(await connection.disconnect(network, rejoin))
