"""The log server's `SPEC`: what it is, its `check`, its command line (`mcp-wpilib-wpilog`) and its instructions.

Layout: config.py/store.py (where logs may be read, and the open logs), logfile.py/overview.py (reading a WPILOG),
timeline.py/queries.py (enabled phases, alignment, the questions), tools/ (the MCP tools), app.py (FastMCP instance).
"""

import argparse

from frc_mcp_core.spec import ServerSpec
from frc_mcp_wpilib.errors import WPILibMCPError

from . import config


def server_instructions(core: bool) -> str:
    return (
        "Recorded robot logs (.wpilog, Driver Station): what happened in a match, past values, faults. Live values: use nt4. Read-only; logs under WPILOG_DIR, elsewhere only after you confirm. Start: wpilog_list, wpilog_scan." if core else
        "Recorded robot logs (.wpilog, Driver Station): what happened in a match, past values, faults; wpilog_diff compares two logs. Live values: use nt4. Read-only; logs under WPILOG_DIR, elsewhere only after you confirm. Start: wpilog_list, wpilog_scan."
    )


def _check_log(name: str) -> int:
    """Print what an assistant would see after `wpilog_open`, so a student can check a log is readable."""
    from .logfile import load  # imported here so that importing the spec stays cheap
    from .overview import overview

    try:
        info = overview(load(config.resolve_log(name)), "l1")
    except WPILibMCPError as e:  # every setting here reports itself the way the tools do
        print(f"Cannot open the log: {e}")
        return 1
    print(f"{info['file']}: {info['duration_s']} s, {info['topics']} topics, {info['updates']} updates, {info['size_mb']} MB")
    for phase in info["enabled"]:
        print(f"  enabled in {phase['mode']}: {phase['from_s']} s to {phase['to_s']} s")
    for key, value in info.get("match", {}).items():
        print(f"  {key}: {value}")
    print("  busiest topics: " + ", ".join(f"{name} ({n})" for name, n in info["busiest"]))
    for warning in info.get("warnings", []):
        print(f"  warning: {warning}")
    return 0


def _check_folders() -> int:
    roots = config.log_roots()
    found = config.find_logs(roots)
    print(f"Looking in: {', '.join(str(r) for r in roots)}")
    for path in found[:50]:
        print(f"  {path.name}  ({path.stat().st_size / 2**20:.1f} MB)")
    if not found:
        print("  No .wpilog files found. Set WPILOG_DIR to the folder that has them.")
    elif notice := config.default_root_notice():
        print(f"  Note: {notice}")
    return 0


def _check(args: argparse.Namespace) -> int:
    # One subcommand, two depths: the folders it looked in, or one log the way a tool would show it.
    return _check_log(args.file) if args.file else _check_folders()


def _options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file", nargs="?", help="log file name or path for check")


SPEC = ServerSpec(
    key="wpilog",
    name="wpilib_wpilog_mcp",
    description="WPILib log (.wpilog) MCP server; check is a read-only look at your logs.",
    instructions=server_instructions,  # follows the toolset: wpilog_diff is full-only
    toolset=config.TOOLSET,  # WPILOG_TOOLSET=core hides wpilog_diff, wpilog_close and wpilog_sync
    tool_modules=("frc_mcp_wpilog.prompts", "frc_mcp_wpilog.tools.data_tools", "frc_mcp_wpilog.tools.file_tools", "frc_mcp_wpilog.tools.topic_tools"),
    check=_check,
    extra_args=_options,
)


def main() -> None:
    SPEC.main()
