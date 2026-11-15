"""systemcore_bundle: generate and download the support bundle."""

from typing import Annotated

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply

from .. import bundle, config
from ..app import FETCH, Host, mcp, tool_errors
from ..target import resolve


@mcp.tool(annotations={**FETCH, "title": "Fetch support bundle"}, output_schema=None)
@tool_errors
async def systemcore_bundle(
    symptoms: Annotated[str, Field(max_length=500, description="What went wrong, one sentence")] = "",
    include_ds_log: Annotated[bool, Field(description="Also ask for Driver Station logs (unverified)")] = False,
    wait_s: Annotated[float, Field(ge=5, le=300)] = 60,
    host: Host = None,
) -> ToolResult:
    """Build the support bundle (about 8 s) and save the zip. Not during a match."""
    target = await resolve(host)
    out = await bundle.fetch(target, config.download_dir(), symptoms=symptoms, include_ds_log=include_ds_log, wait_s=wait_s)
    out["hint"] = "Ghost CSA accepts this zip as is. One-shot: run again for a fresh one."
    return reply(out)
