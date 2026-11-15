"""jfr_record: record a running JVM for a fixed time into JFR_DIR."""

import asyncio
import re
import time
from datetime import datetime
from typing import Annotated, Literal

from fastmcp.tools import ToolResult
from pydantic import Field

from frc_mcp_core.toolset import gated_by

from .. import config, jdk, recorder
from ..app import RECORD, mcp, tool_errors
from ..errors import JfrError
from ..support import reply

_LABEL_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")
_FILE_WAIT_S = 15.0  # how long after the recording ends to wait for JFR to finish writing the file


@mcp.tool(annotations={**RECORD, "title": "Record a JVM"}, output_schema=None, tags=gated_by(config.RECORD))
@tool_errors
async def jfr_record(
    pid: Annotated[int, Field(description="From jfr_list jvms")],
    seconds: Annotated[int, Field(ge=1, description="Waits this long")],
    settings: Annotated[Literal["profile", "default"], Field(description="profile has more detail")] = "profile",
    label: Annotated[str | None, Field(description="File name prefix")] = None,
    wait: Annotated[bool, Field(description="false: return at once")] = True,
) -> ToolResult:
    """Record a running JVM for `seconds` into JFR_DIR (90+ s for GC or leaks). wait=false returns at once: run your scenario, then read the file. Needs JFR_ALLOW_RECORD=1."""
    config.require_record()
    longest = config.max_seconds()
    if seconds > longest:
        raise JfrError(f"seconds may not exceed JFR_MAX_SECONDS ({longest:g}).")
    if label is not None and not _LABEL_RE.fullmatch(label):
        raise JfrError("label may use only letters, digits, '-' and '_' (up to 40 characters).")
    own = await jdk.jcmd_for(pid)  # the program's own JDK: versions always match, whichever JDK this server was set up with
    target = config.roots()[0] / f"{label or 'recording'}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jfr"
    if " " in str(target):
        raise JfrError(f"JFR_DIR may not contain spaces for recording (jcmd cannot take {str(target)!r}); use another folder.")
    await jdk.run("jcmd", [str(pid), "JFR.start", f"name=mcp{pid}", f"settings={settings}", f"duration={seconds}s", f"maxsize={int(config.max_bytes() // 2**20)}M", f"filename={target.as_posix()}", *jdk.NO_SECRET_EVENTS], tool_path=own)
    if not wait:
        recorder.start(target, pid, seconds)
        return reply({"file": target.name, "pid": pid, "recording_s": seconds, "hint": f"Recording for {seconds} s. Run your scenario; jfr_list shows when it is done."})
    await asyncio.sleep(seconds)
    deadline = time.monotonic() + _FILE_WAIT_S
    while not (target.is_file() and target.stat().st_size > 0):
        if time.monotonic() > deadline:
            raise JfrError(f"The recording did not appear at {target} within {_FILE_WAIT_S:g} s after it ended. Is JVM {pid} still running?")
        await asyncio.sleep(0.5)
    await asyncio.sleep(1)  # let JFR finish the last write
    return reply({"file": target.name, "mb": round(target.stat().st_size / 2**20, 1), "seconds": seconds, "pid": pid, "hint": "Next: jfr_summary."})
