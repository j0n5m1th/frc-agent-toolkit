"""File tools: wpilog_list (which logs exist), wpilog_open (read one's index), wpilog_sync (several on one clock) and wpilog_close."""

import asyncio
from datetime import datetime
from typing import Annotated, Literal

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.reply import reply

from .. import config, store, sync
from ..app import (
    FULL_ONLY_TAG,
    READ_ONLY_LOCAL,
    Limit,
    LogId,
    NameContains,
    mcp,
    tool_errors,
)
from ..overview import overview
from ..store import logs


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "List logs"}, output_schema=None)
@tool_errors
async def wpilog_list(
    name_contains: NameContains = None,
    limit: Limit = 20,
) -> ToolResult:
    """Match logs (.wpilog and Driver Station .dslog) this server may read, newest first (name, size, date). Open one with wpilog_open."""
    found = await asyncio.to_thread(config.find_logs, None, name_contains)
    roots = config.log_roots()
    rows = []
    for path in found[:limit]:
        info = path.stat()
        shown = next((str(path.relative_to(root)) for root in roots if path.is_relative_to(root)), str(path))
        rows.append({"file": shown, "mb": round(info.st_size / 2**20, 1),
                     "modified": datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M")})
    out: dict[str, object] = {"total": len(found), "logs": rows}
    if len(found) > limit:
        out["omitted"] = len(found) - limit
    if not found:
        out["hint"] = f"No logs under {', '.join(str(r) for r in roots)}. {config.HOW_TO_FIND}"
    elif notice := config.default_root_notice():
        out["hint"] = notice
    return reply(out)


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "Open log"}, output_schema=None)
@tool_errors
async def wpilog_open(path: Annotated[str, Field(description="File name or path of a .wpilog or .dslog (see wpilog_list)")]) -> ToolResult:
    """Open a log: duration, when the robot was enabled (auto/teleop/test), match info and the busiest topics. Becomes the default log. Reopening a file that changed on disk re-reads it."""
    resolved = config.resolve_log(path)
    log_id, log = await asyncio.to_thread(logs.open, resolved)
    return reply(await asyncio.to_thread(overview, log, log_id))


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "Close log"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def wpilog_close(log: LogId = None) -> ToolResult:
    """Close a log and free its memory."""
    closed = await asyncio.to_thread(logs.close, log)  # release waits for a running query: not on the event loop
    return reply({"closed": closed, "open": logs.ids})


@mcp.tool(annotations={**READ_ONLY_LOCAL, "title": "Sync logs"}, output_schema=None, tags={FULL_ONLY_TAG})
@tool_errors
async def wpilog_sync(
    logs: Annotated[list[str], Field(min_length=2, max_length=4, description="Open log ids, e.g. a robot log, its Driver Station log, a converted hoot; the first sets the clock")],
    method: Annotated[Literal["auto", "clock", "enable"], Field(description="auto | clock (wall-clock time both logs know) | enable (line up enable/disable edges)")] = "auto",
    offsets_ms: Annotated[list[float] | None, Field(description="Skip detection: ms to add to each log after the first")] = None,
) -> ToolResult:
    """Sync open logs to the first one's clock and open them as one log (id s1, ...): every tool then works across them, including `also` conditions on topics from different files. Names in more than one log get a `<log id>:` prefix."""
    def make(view_id: str, parts: dict) -> sync.SyncedLog:
        view, placed = sync.build(view_id, parts, method, offsets_ms)
        made["placed"] = placed
        return view

    made: dict = {}
    view_id, view = await asyncio.to_thread(store.logs.add_view, logs, make)
    assert isinstance(view, sync.SyncedLog)
    out = await asyncio.to_thread(overview, view, view_id)
    out.pop("file", None)
    out["synced"] = [{"log": p.log_id, "file": view.parts[p.log_id].path.name, "method": p.method,
                      **({"offset_ms": round(p.offset_us / 1000, 1)} if p.method != "reference" else {}), **p.notes}
                     for p in made["placed"]]
    if view.renamed:
        out["renamed"] = {log_id: f"{n} topic names also in an earlier log now start with '{log_id}:'" for log_id, n in view.renamed.items()}
    return reply(out)
