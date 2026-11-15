"""The profiling server's `SPEC`: what it is, its `check` and its command line (`mcp-jfr`).
Layout: config.py (folders, JDK tools, limits), jdk.py (running `jcmd`/`jfr`), analysis.py (events to small answers), support.py
(error conversion, lean schemas, bounded reply), tools/ and app.py. Standalone apart from `frc_mcp_core`.
"""

import argparse
import asyncio

from frc_mcp_core.spec import ServerSpec
from frc_mcp_core.toolset import capability_gates

from . import config, jdk
from .errors import JfrError


def _check(_args: argparse.Namespace) -> int:
    print(f"Recordings: {', '.join(str(r) for r in config.roots())} ({len(config.find_recordings())} found)")
    if notice := config.root_notice():
        print(f"  Note: {notice}")
    try:
        print(f"  jfr:  {config.jdk_tool('jfr')}\n  jcmd: {config.jdk_tool('jcmd')}")
        jvms = asyncio.run(jdk.list_jvms())
    except JfrError as e:
        print(f"  {e}")
        return 1
    print(f"  {len(jvms)} running JVM(s) can be recorded")
    return 0


SPEC = ServerSpec(
    key="jfr",
    name="jfr_mcp",
    description="JFR profiling MCP server; check is a read-only look at what it would use.",
    instructions="Java Flight Recorder profiling of any JVM (sim or app): slow code, GC pauses, leaks; jfr_record appears when turned on in settings. Robot values: use nt4. Times are ms; judge a change by the net total.",
    gates=capability_gates(config.RECORD),  # jfr_record is hidden until JFR_ALLOW_RECORD is on
    toolset=config.TOOLSET,  # JFR_TOOLSET=core hides jfr_live
    tool_modules=("frc_mcp_jfr.prompts", "frc_mcp_jfr.tools.list_tools", "frc_mcp_jfr.tools.live_tools", "frc_mcp_jfr.tools.read_tools", "frc_mcp_jfr.tools.record_tools"),
    check=_check,
)


def main() -> None:
    SPEC.main()
