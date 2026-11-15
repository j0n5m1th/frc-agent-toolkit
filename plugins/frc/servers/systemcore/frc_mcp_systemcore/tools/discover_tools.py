"""systemcore_discover: find Systemcores on the networks this machine is on."""

from typing import Annotated

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply

from .. import discovery, net
from ..app import READ_ONLY, Host, mcp, tool_errors


@mcp.tool(annotations={**READ_ONLY, "title": "Find Systemcores"}, output_schema=None)
@tool_errors
async def systemcore_discover(
    timeout_s: Annotated[float, Field(ge=0.5, le=10)] = 2.0,
    host: Host = None,
) -> ToolResult:
    """Find Systemcores on the local networks (UDP). A single result becomes the default."""
    targets = [await net.check_host(host)] if host else None
    found = await discovery.discover(timeout_s, targets)
    discovery.remember_single(found)
    out: dict[str, object] = {"devices": found}
    if not found:
        out["hint"] = ("Nothing answered. Check the Systemcore is on and that this computer is on its network (its Wi-Fi is "
                       "SYSTEMCORE-<suffix> on image 17 and later, address 172.30.0.1; USB 172.26.0.1 on Windows, 172.27.0.1 on Linux/Mac). "
                       "Then try again with host set.")
    elif len(found) > 1:
        out["hint"] = "Several answered: pass host to the other tools to choose one."
    return reply(out)
