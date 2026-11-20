"""Live data (NetworkTables) and logged data (WPILOG) keep parity: what you can ask of a log you can ask of the live robot, and the other way round.

Every tool of the log server and the live server is either one half of a pair below or has a stated reason to have no partner. A new tool therefore
forces a decision here: add its partner, or say why there is none. A pair is two tools that answer the same question over two sources; each tool does
one job, and the shared rules live in `frc_mcp_wpilib` (the scan, the swerve check, the reductions), never copied into a server.
"""

import asyncio

import pytest
from fastmcp import Client

from frc_mcp_core import command
from frc_mcp_core.toolset import set_gate_visibility

# the question -> (log tools, live tools)
PAIRS = {
    "which topics exist": (("wpilog_topics",), ("nt4_list_topics",)),
    "the values at a moment": (("wpilog_get_values",), ("nt4_get_values",)),
    "a signal reduced over a window": (("wpilog_query",), ("nt4_watch", "nt4_capture_query")),
    "when a condition holds": (("wpilog_when",), ("nt4_wait_for",)),
    "the usual trouble": (("wpilog_scan",), ("nt4_scan",)),
    "swerve modules": (("wpilog_swerve",), ("nt4_swerve",)),
    "which device drew the current": (("wpilog_power",), ("nt4_power",)),
    "one mechanism's tracking": (("wpilog_mechanism",), ("nt4_mechanism",)),
    "do two signals move together": (("wpilog_correlate",), ("nt4_correlate",)),
    "the robot pose": (("wpilog_pose",), ("nt4_pose",)),
}
LOG_ONLY = {
    "wpilog_list": "finds log files, which a live robot does not have",
    "wpilog_open": "opens a file; the live server connects instead (nt4_connection)",
    "wpilog_close": "frees an open file",
    "wpilog_text": "console and message text is recorded in a log; NetworkTables carries none",
    "wpilog_sync": "puts several recorded files on one clock; a live robot has one clock",
    "wpilog_diff": "compares two recorded files; there is no second live robot to compare",
}
LIVE_ONLY = {
    "nt4_connection": "connects to a robot, which a file does not need",
    "nt4_status": "the state of that connection",
    "nt4_inspect": "who is connected and who publishes a topic, which a log does not record",
    "nt4_health": "the Systemcore's own status topics (port 6810), which are not in a robot log",
    "nt4_get_widgets": "a dashboard widget view; in a log, read the widget's topics with wpilog_get_values",
    "nt4_capture_start": "starts a background recording; a log is already a recording",
    "nt4_capture_stop": "ends that recording",
    "nt4_set_value": "writes to the robot, which a log cannot take",
    "nt4_release_publisher": "undoes a write",
}


def tools_of(key: str) -> set[str]:
    async def load() -> set[str]:
        spec = command.load_spec(key)
        mcp, _ = spec.load()
        set_gate_visibility(mcp, dict.fromkeys(spec.gates, True), core=False)
        async with Client(mcp) as client:
            return {tool.name for tool in await client.list_tools()}

    return asyncio.run(load())


@pytest.fixture(scope="module")
def tools() -> tuple[set[str], set[str]]:
    return tools_of("wpilog"), tools_of("nt4")


def test_every_tool_is_in_a_pair_or_has_a_reason_to_have_no_partner(tools):
    log, live = tools
    paired_log = {name for logs, _ in PAIRS.values() for name in logs}
    paired_live = {name for _, lives in PAIRS.values() for name in lives}
    assert log == paired_log | LOG_ONLY.keys(), f"log tools without a pair or a reason: {sorted(log - paired_log - LOG_ONLY.keys())}; listed but missing: {sorted((paired_log | LOG_ONLY.keys()) - log)}"
    assert live == paired_live | LIVE_ONLY.keys(), f"live tools without a pair or a reason: {sorted(live - paired_live - LIVE_ONLY.keys())}; listed but missing: {sorted((paired_live | LIVE_ONLY.keys()) - live)}"


def test_a_pair_has_a_tool_on_each_side_and_a_reason_is_a_sentence():
    assert all(logs and lives for logs, lives in PAIRS.values())
    assert all(len(reason.split()) >= 3 for reason in (*LOG_ONLY.values(), *LIVE_ONLY.values()))


def test_the_shared_rules_are_not_copied_into_a_server():
    """The checks both sides run live in `frc_mcp_wpilib`; a server only supplies its source (rows, topics, a clock)."""
    from pathlib import Path

    servers = Path(__file__).resolve().parents[2] / "plugins" / "frc" / "servers"
    for name in ("scan_roles.py", "scan_loop_can.py", "swerve.py"):
        assert not list(servers.glob(f"*/*/{name}")), f"{name} belongs in frc_mcp_wpilib, shared by both servers"
