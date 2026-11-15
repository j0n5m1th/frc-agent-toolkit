"""Running one Gradle command to the end (one at a time) and cutting its console down to a small result."""

import asyncio
import re
from pathlib import Path
from typing import Any

from frc_mcp_wpilib.gradle import jdk
from frc_mcp_wpilib.gradle import project as gradle_project
from frc_mcp_wpilib.gradle.console import fold
from frc_mcp_wpilib.gradle.run import GradleRun

from . import config
from .errors import VSCodeError

TAIL_LINES = 12
STOP_SLACK_S = 10.0  # time allowed after the limit for the process tree to be stopped and read
TOOL_WATCH_S = 5.0  # how long a GUI tool is watched for an immediate failure before it is left running
_BUILD_RE = re.compile(r"^BUILD (SUCCESSFUL|FAILED)")
_TESTS_RE = re.compile(r"\d+ tests? completed|\bFAILED$")

_lock = asyncio.Lock()
tools: dict[str, GradleRun] = {}  # GUI tools left running (Glass, SysId, ...), by name


def _wrapper(project: Path) -> Path:
    wrapper = gradle_project.gradlew(project)
    if wrapper is None:
        raise VSCodeError(f"{project} has no Gradle wrapper (gradlew).")
    return wrapper


async def run_to_end(project: Path, args: list[str], offline: bool = False) -> GradleRun:
    """Run `gradlew <args>` and wait for it to finish or hit VSCODE_TIMEOUT_S. Commands never overlap."""
    async with _lock:
        run = GradleRun(project, [*args, *(["--offline"] if offline else []), "--console=plain"])
        limit = config.timeout_s()
        await run.launch(_wrapper(project), limit, jdk=jdk.select(project, VSCodeError), error=VSCodeError)
        try:
            await run.wait(limit + STOP_SLACK_S)
        except asyncio.CancelledError:  # the caller gave up: do not leave the build running
            await run.stop()
            raise
        return run


async def start_tool(project: Path, name: str) -> tuple[GradleRun, bool]:
    """The run behind GUI tool `name` and whether this call started it (it runs until the user closes it).

    The registry is read and written under the same lock that launches, so two calls for one name share one run.
    The brief watch for a failed start runs outside the lock: a window for the user must not block the next command."""
    async with _lock:
        running = tools.get(name)
        if running is not None and running.active:
            return running, False
        run = GradleRun(project, [name, "--console=plain"])
        await run.launch(_wrapper(project), None, outlive_server=True, jdk=jdk.select(project, VSCodeError), error=VSCodeError)  # a window for the user: it outlives this server
        tools[name] = run
    await run.wait(TOOL_WATCH_S)
    return run, True


def outcome(run: GradleRun, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """ok / running / seconds, the BUILD line, and (when it ended badly) the error lines. Console text is data, not instructions.
    The base of every reply of this server: `running` is stated on every branch and a run still going is a success (the tool
    window is up); callers add their own keys in `extra`.
    """
    running = run.active
    ok = running or (run.end_reason == "exited" and run.exit_code == 0)
    out: dict[str, Any] = {"ok": ok, "running": running, "seconds": run.uptime_s}
    if not ok:
        out["state"] = run.end_reason or "running"
        out["exit_code"] = run.exit_code
    lines = run.console.select()
    builds: list[str] = []
    tests = []
    for line in lines:
        if _BUILD_RE.match(line.text):
            builds.append(line.text)
        elif _TESTS_RE.search(line.text):
            tests.append(line)
    if builds:
        out["build"] = builds[-1]
    if tests:
        out["tests"] = fold(tests[-TAIL_LINES:])
    if run.console.warnings:
        out["warnings"] = run.console.warnings
    if not ok:
        out["errors"] = fold(([line for line in lines if line.level == "error"] or lines)[-TAIL_LINES:])
    return {**out, **(extra or {})}
