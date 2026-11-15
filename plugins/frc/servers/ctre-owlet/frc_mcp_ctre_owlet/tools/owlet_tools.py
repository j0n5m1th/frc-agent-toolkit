"""owlet_list, owlet_info, owlet_signals and owlet_convert."""

import asyncio
from typing import Annotated

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply
from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.folders import new_output_path
from frc_mcp_wpilib.tooling.topic_params import Limit

from .. import config, owlet
from ..app import CONVERT, READ, File, NameContains, mcp, tool_errors


@mcp.tool(annotations={**READ, "title": "List hoot files"}, output_schema=None)
@tool_errors
async def owlet_list(name_contains: NameContains = None, limit: Limit = 20) -> ToolResult:
    """Hoot files (newest first) as rows of `cols`: file, mb, modified, `c` = compliancy (the Owlet level each needs)."""
    return reply(await asyncio.to_thread(config.HOOTS.listing, name_contains, limit, lambda path: {"c": owlet.header_level(path)}, extra_cols=("c",)))


@mcp.tool(annotations={**READ, "title": "Hoot file info"}, output_schema=None)
@tool_errors
async def owlet_info(file: File) -> ToolResult:
    """The log's compliancy, whether it has Phoenix Pro devices, and the installed Owlet of exactly that level (else the ctre_install command to get it)."""
    hoot = await config.HOOTS.resolve_async(file)
    return reply(await owlet.info(hoot, await owlet.pick(hoot)))


@mcp.tool(annotations={**READ, "title": "Hoot signals"}, output_schema=None)
@tool_errors
async def owlet_signals(
    file: File,
    name_contains: NameContains = None,
    limit: Limit = 20,
    offset: Annotated[int, Field(ge=0, description="Skip matches")] = 0,
) -> ToolResult:
    """Hoot signals as [name, id] pairs, plus the biggest device groups. Names look like Phoenix6/<bus>/<device>/<Signal>."""
    hoot = await config.HOOTS.resolve_async(file)
    picked = await owlet.pick(hoot)
    signals = await owlet.scan(hoot, picked)
    matches = [s for s in signals if not name_contains or name_contains.lower() in s.name.lower()]
    page = matches[offset:offset + limit]
    out: dict[str, object] = {"file": hoot.name, "total": len(signals), "matches": len(matches), "signals": [[s.name, s.id] for s in page]}
    if not name_contains:
        out["groups"], more = owlet.groups(signals)
        if more:
            out["more_groups"] = more
    if offset + limit < len(matches):
        out["next_offset"] = offset + limit
    return reply(out)


@mcp.tool(annotations={**CONVERT, "title": "Convert hoot to wpilog"}, output_schema=None)
@tool_errors
async def owlet_convert(
    file: File,
    signals: Annotated[list[str] | None, Field(description="Ids or exact names")] = None,
    name_contains: Annotated[str | None, Field(description="Also names containing this")] = None,
    all_signals: Annotated[bool, Field(description="Everything (can exceed 1 GB)")] = False,
    out: Annotated[str | None, Field(description="New file name")] = None,
    unlicensed: Annotated[bool, Field(description="Skip Pro checks (Pro signals dropped)")] = False,
) -> ToolResult:
    """Convert signals to a new .wpilog beside it (WPILOG_DIR must include the folder); never overwrites. Needs signals, name_contains or all_signals."""
    hoot = await config.HOOTS.resolve_async(file)
    if not (signals or name_contains or all_signals):
        raise CtreError("Say which signals: signals=[...], name_contains=..., or all_signals=true. Use owlet_signals to look first; a full export can exceed 1 GB.")
    if all_signals and (signals or name_contains):
        raise CtreError("Use all_signals alone, or name signals; not both.")
    target = new_output_path(hoot, out, owlet.SUFFIX)
    picked = await owlet.pick(hoot)
    chosen = None
    if not all_signals:
        chosen = owlet.select_signals(await owlet.scan(hoot, picked), signals, name_contains)
    return reply(await owlet.convert(hoot, picked, target, chosen, unlicensed))
