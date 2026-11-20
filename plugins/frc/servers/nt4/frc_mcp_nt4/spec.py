"""The NT4 server's `SPEC`: what it is, its `check`, its command line (`frc-mcp nt4`), and what follows the toolset.

Importing this module imports no tool and no `ntcore`, and builds no server. NT4_TOOLSET=core hides the capture and write
tools; `SPEC.serve()` reads the setting at start and `apply_toolset()` re-reads it. Module layout: docs/ARCHITECTURE_SERVERS.md.
"""

import argparse
import asyncio
import math
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from fastmcp.tools import Tool

from frc_mcp_core.spec import ServerSpec
from frc_mcp_core.tools import SlimToolList, ToolTuning
from frc_mcp_core.toolset import gate_tag

from . import config


@asynccontextmanager
async def lifespan(_server: FastMCP):
    """Runs once at server start/stop; ensures the NT connection is closed on exit."""
    from .session import session  # imported here so that importing the spec stays cheap

    try:
        yield
    finally:
        await session.disconnect_safely(wait=True)


# Tools tagged FULL_ONLY_TAG (shared; see server_support) are hidden in the 'core' toolset (apply_toolset).
WATCH_HINT = " For triggers or windows use nt4_capture_start+query."


class CoreAwareToolList(SlimToolList):
    """The shared lean schemas, plus shorter descriptions in the `core` toolset (see `apply_toolset`)."""

    def __init__(self, core: bool) -> None:
        self.core = core

    def set_core(self, core: bool) -> None:
        self.core = core

    def tune(self, tool: Tool, update: ToolTuning) -> None:
        if self.core and tool.description:
            update["description"] = tool.description.replace(WATCH_HINT, "")


def server_instructions(core: bool) -> str:
    return (
        "Live robot values over NetworkTables: now or for a few seconds, and why a value is not updating. Past matches: use wpilog. Read-only toolset." if core else
        "Live robot values over NetworkTables: now or recorded for a few seconds, and why a value is not updating. Past matches: use wpilog. Read-only; write tools appear when turned on in settings."
    )


def _check(args: argparse.Namespace) -> int:
    from .diagnostics import (
        diagnose,  # imported here so that importing the spec stays cheap
    )

    timeout = 5.0 if args.timeout is None else args.timeout
    return asyncio.run(diagnose(args.host, args.port, timeout))


def _options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", help="NT4 host for check (default: NT4_HOST)")
    parser.add_argument("--port", type=int, help="NT4 port for check (default: NT4_PORT or 5810)")
    parser.add_argument("--timeout", type=float, default=None, help="Diagnostic connection timeout in seconds (default: 5)")


def _prepare(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.command == "check":
        timeout = 5.0 if args.timeout is None else args.timeout
        if not math.isfinite(timeout) or not 0 < timeout <= 60:
            parser.error("--timeout must be greater than 0 and at most 60 seconds")
    elif args.host is not None or args.port is not None or args.timeout is not None:
        parser.error("--host, --port and --timeout are only supported with check")


slim_tools = CoreAwareToolList(False)  # `on_toolset` keeps its `core` in step with the toolset

SPEC = ServerSpec(
    key="nt4",
    name="wpilib_nt4_mcp",
    description="WPILib NT4 MCP server; check is a read-only connection check.",
    instructions=server_instructions,
    toolset=config.TOOLSET,
    gates={gate_tag(config.allow_writes.env_name): config.writes_possible},  # the write tools are hidden until writes are on (and not read-only)
    tool_modules=(
        "frc_mcp_nt4.prompts_resources", "frc_mcp_nt4.tools.capture_tools", "frc_mcp_nt4.tools.capture_watch_tools", "frc_mcp_nt4.tools.connection_tools",
        "frc_mcp_nt4.tools.discovery_tools", "frc_mcp_nt4.tools.health_tools", "frc_mcp_nt4.tools.read_tools", "frc_mcp_nt4.tools.scan_tools", "frc_mcp_nt4.tools.swerve_tools", "frc_mcp_nt4.tools.write_tools",
    ),
    check=_check,
    doctor_skip="it connects to NT4_HOST, which may be a real robot",
    extra_args=_options,
    prepare=_prepare,
    slim=slim_tools,
    on_toolset=slim_tools.set_core,
    options={"lifespan": lifespan},
)


def main() -> None:
    SPEC.main()
