"""passerine_list and passerine_convert."""

import asyncio
from typing import Annotated, Literal

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply
from frc_mcp_ctre.folders import new_output_path
from frc_mcp_wpilib.tooling.topic_params import Limit

from .. import config, passerine
from ..app import CONVERT, READ, File, mcp, tool_errors


@mcp.tool(annotations={**READ, "title": "List MIDI or CHRP files"}, output_schema=None)
@tool_errors
async def passerine_list(
    kind: Annotated[Literal["midi", "chrp"], Field(description="MIDI files to convert, or converted CHRP files")] = "midi",
    name_contains: Annotated[str | None, Field(description="Case-insensitive substring of the name")] = None,
    limit: Limit = 20,
) -> ToolResult:
    """MIDI (.mid/.midi) or CHRP files in PASSERINE_DIR, newest first, with size and time."""
    folders = config.MIDI if kind == "midi" else config.CHRP
    return reply(await asyncio.to_thread(folders.listing, name_contains, limit))


@mcp.tool(annotations={**CONVERT, "title": "Convert MIDI to CHRP"}, output_schema=None)
@tool_errors
async def passerine_convert(
    file: File,
    out: Annotated[str | None, Field(description="New file name (default: the MIDI's name)")] = None,
) -> ToolResult:
    """Convert a MIDI file to a new .chrp beside it; never overwrites."""
    midi = await config.MIDI.resolve_async(file)
    target = new_output_path(midi, out, config.CHRP_SUFFIX)
    return reply(await passerine.convert(midi, target))
