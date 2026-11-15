"""Functional test of `mcp-wpilib-nt4`: the installed command, over stdio, against the evaluation's fake robot.

The fake robot (`evaluations/fake_robot.py`) is started on a free local port for the run and stopped afterwards.
"""

import asyncio
import shutil
import socket
import sys
from pathlib import Path
from subprocess import PIPE, STDOUT, Popen

from harness import (
    EVALUATIONS,
    OUTPUT,
    Report,
    call,
    client,
    is_unknown_tool,
    tool_names,
)

READ_TOOLS = {"nt4_capture_query", "nt4_capture_start", "nt4_capture_stop", "nt4_connection", "nt4_get_values",
              "nt4_get_widgets", "nt4_inspect", "nt4_list_topics", "nt4_status", "nt4_wait_for", "nt4_watch"}
WRITE_TOOLS = {"nt4_release_publisher", "nt4_set_value"}  # listed only with NT4_ALLOW_WRITES=1, an allowed prefix and no NT4_READ_ONLY
TOOLS = READ_TOOLS | WRITE_TOOLS
WRITE_SETTINGS = {"NT4_ALLOW_WRITES": "1", "NT4_WRITE_PREFIXES": "/Fake/Tunable"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_robot(port: int) -> Popen[str]:
    robot = Popen([sys.executable, str(EVALUATIONS / "fake_robot.py"), "--port", str(port)],
                  stdout=PIPE, stderr=STDOUT, text=True)
    assert robot.stdout is not None
    if "listening" not in robot.stdout.readline():  # its one startup line; an empty read means it exited
        robot.kill()
        raise SystemExit("the fake robot did not start")
    return robot


async def run(keep: bool = False) -> bool:
    report = Report("nt4 server")
    port = free_port()
    robot = start_robot(port)
    logs = OUTPUT / "nt4-logs"  # where capture logs go for this run
    shutil.rmtree(logs, ignore_errors=True)
    env = {"NT4_HOST": "127.0.0.1", "NT4_PORT": str(port), "NT4_LOG_DIR": str(logs)}
    try:
        async with client("mcp-wpilib-nt4", {**env, **WRITE_SETTINGS}) as c:
            report.check("with writes on and an allowed prefix, offers exactly the 13 documented tools", await tool_names(c) == TOOLS)

            connected = await call(c, "nt4_connection")
            report.check("connects to the robot", connected.get("connected") is True, connected)
            report.check("status says connected", (await call(c, "nt4_status")).get("connected") is True)

            listing = await call(c, "nt4_list_topics", {"prefix": "/Fake/", "limit": 100})
            report.check("lists the robot's topics", "/Fake/Elevator/Current" in listing.get("topics", {}), listing)

            values = await call(c, "nt4_get_values", {"topics": ["/Fake/Tunable/Gain"]})
            report.check("reads a value (Gain is 2.0)", values.get("/Fake/Tunable/Gain", {}).get("v") == 2.0, values)

            widget = await call(c, "nt4_get_widgets", {"bases": ["/Fake/Auto Chooser"]})
            report.check("reads the chooser widget's options", all(o in str(widget) for o in ("Left", "Center", "Right")), widget)

            mode = await call(c, "nt4_wait_for", {"topic": "/Fake/Match/Mode", "op": "==", "value": "teleop", "wait_s": 12})
            report.check("wait_for sees the mode become teleop", mode.get("met") is True, mode)
            timeout = await call(c, "nt4_wait_for", {"topic": "/Fake/Elevator/Current", "op": ">", "value": 1000, "wait_s": 0.5})
            report.check("a wait that times out reports met=false, not an error", timeout.get("met") is False, timeout)

            wrote = await call(c, "nt4_set_value", {"topic": "/Fake/Tunable/Setpoint", "value": 3.0, "type_str": "double"})
            report.check("writes under an allowed prefix", "EXC" not in wrote, wrote)
            command = await call(c, "nt4_wait_for", {"topic": "/Fake/Output/Command", "op": "==", "value": 6.0, "wait_s": 5})
            report.check("the robot reacted: Command = Setpoint x Gain = 6.0", command.get("met") is True, command)
            refused = await call(c, "nt4_set_value", {"topic": "/Fake/Elevator/Current", "value": 0.0, "type_str": "double"})
            report.check("a write outside the allowed prefixes is refused", "EXC" in refused, refused)
            released = await call(c, "nt4_release_publisher", {"topic": "/Fake/Tunable/Setpoint"})
            report.check("releases its publisher", "EXC" not in released, released)

            report.check("nt4_inspect lists clients", "EXC" not in await call(c, "nt4_inspect", {"what": "clients"}))

            watch = await call(c, "nt4_watch", {"topics": ["/Fake/Elevator/Current"], "duration_s": 6, "rate_hz": 50,
                                                "all_changes": True})
            report.check("nt4_watch sees both the 42.5 A spike and the 5.0 A baseline",
                         "EXC" not in watch and "42.5" in str(watch) and "5.0" in str(watch), watch)

            started = await call(c, "nt4_capture_start", {"topics": ["/Fake/Arm/Angle"], "rate_hz": 20, "duration_s": 30})
            capture = started.get("id")
            report.check("starts a capture", bool(capture), started)
            await asyncio.sleep(2)
            queried = await call(c, "nt4_capture_query", {"capture_id": capture, "reduce": "summary"})
            report.check("queries the capture", "EXC" not in queried and "Arm/Angle" in str(queried), queried)
            report.check("stops and discards it", "EXC" not in await call(c, "nt4_capture_stop", {"capture_id": capture, "discard": True}))

            saved = await call(c, "nt4_capture_start", {"topics": ["/Fake/Arm/Angle"], "rate_hz": 20, "duration_s": 30, "log": True})
            await asyncio.sleep(2)
            stopped = await call(c, "nt4_capture_stop", {"capture_id": saved.get("id")})
            report.check("a capture started with log=true reports its .wpilog file", str(stopped.get("log_file", "")).endswith(".wpilog"), stopped)

            report.check("disconnects", "EXC" not in await call(c, "nt4_connection", {"action": "disconnect"}))

        async with client("mcp-wpilib-nt4", env) as c:
            report.check("by default the 2 write tools are not listed", await tool_names(c) == READ_TOOLS)
            hidden = await call(c, "nt4_set_value", {"topic": "/Fake/Tunable/Setpoint", "value": 9.0, "type_str": "double"})
            report.check("a write by name is an unknown tool while NT4_ALLOW_WRITES is off", is_unknown_tool(hidden, "nt4_set_value"), hidden)

        async with client("mcp-wpilib-nt4", {**env, "NT4_ALLOW_WRITES": "1"}) as c:
            report.check("NT4_ALLOW_WRITES=1 with no NT4_WRITE_PREFIXES still lists no write tool", await tool_names(c) == READ_TOOLS)

        async with client("mcp-wpilib-nt4", {**env, "NT4_READ_ONLY": "1", **WRITE_SETTINGS}) as c:
            report.check("NT4_READ_ONLY=1 hides the write tools even when writes are on", await tool_names(c) == READ_TOOLS)
            blocked = await call(c, "nt4_set_value", {"topic": "/Fake/Tunable/Setpoint", "value": 9.0, "type_str": "double"})
            report.check("NT4_READ_ONLY=1 refuses writes (unknown tool)", is_unknown_tool(blocked, "nt4_set_value"), blocked)
            read = await call(c, "nt4_get_values", {"topics": ["/Fake/Tunable/Setpoint"]})
            report.check("NT4_READ_ONLY=1 still reads (and connects on demand)", "EXC" not in read, read)

        async with client("mcp-wpilib-wpilog", {"WPILOG_DIR": str(logs)}) as c:
            opened = await call(c, "wpilog_open", {"path": Path(stopped["log_file"]).name})
            report.check("the log server opens the live capture", opened.get("log") == "l1", opened)
            arm = await call(c, "wpilog_query", {"topics": ["/Fake/Arm/Angle"], "reduce": "summary"})
            stats = arm.get("data", {}).get("/Fake/Arm/Angle", {})
            report.check("the saved arm angle is a real signal (many samples, within -30..90)",
                         stats.get("n", 0) > 5 and stats.get("min", 99) >= -30.01 and stats.get("max", -99) <= 90.01, arm)
    finally:
        robot.terminate()
        robot.wait(timeout=10)
        if not keep:
            shutil.rmtree(logs, ignore_errors=True)
    return report.summary()
