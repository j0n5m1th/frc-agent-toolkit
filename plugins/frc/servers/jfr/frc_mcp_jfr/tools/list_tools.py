"""jfr_list: the recordings this server may read, or the JVMs it could record."""

import asyncio
from datetime import datetime
from typing import Annotated, Literal

from fastmcp.tools import ToolResult
from pydantic import Field

from .. import config, jdk, recorder
from ..app import LOCAL_ONLY, Limit, mcp, tool_errors
from ..support import reply


@mcp.tool(annotations={**LOCAL_ONLY, "title": "List recordings or JVMs"}, output_schema=None)
@tool_errors
async def jfr_list(
    kind: Annotated[Literal["recordings", "jvms"], Field(description="Saved .jfr files, or running JVMs you can record")] = "recordings",
    name_contains: Annotated[str | None, Field(description="Case-insensitive substring to match (a name, or a line of text)")] = None,  # own copy of frc_mcp_wpilib.tooling.topic_params.NameContains
    limit: Limit = 20,
) -> ToolResult:
    """Recordings (newest first) or running JVMs (pid, main class). The sim or app JVM may differ from its Gradle launcher: check the main class."""
    if kind == "jvms":
        jvms = await jdk.list_jvms(name_contains)
        # The list-reply pair every server in this project uses: total, plus omitted when the cap dropped rows.
        out: dict[str, object] = {"total": len(jvms), "jvms": [{"pid": j.pid, "command": j.command[:160]} for j in jvms[:limit]]}
        if len(jvms) > limit:
            out["omitted"] = len(jvms) - limit
        return reply(out)
    in_progress = recorder.active()
    still_recording = {r["file"] for r in in_progress}
    found = [p for p in await asyncio.to_thread(config.find_recordings, name_contains) if p.name not in still_recording]
    roots = config.roots()
    rows = []
    for path in found[:limit]:
        info = path.stat()
        shown = next((str(path.relative_to(root)) for root in roots if path.is_relative_to(root)), str(path))
        rows.append({"file": shown, "mb": round(info.st_size / 2**20, 1), "modified": datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M")})
    out = {"total": len(found), "recordings": [*in_progress, *rows]}
    if len(found) > limit:
        out["omitted"] = len(found) - limit
    if notice := config.root_notice():
        out["hint"] = notice
    return reply(out)
