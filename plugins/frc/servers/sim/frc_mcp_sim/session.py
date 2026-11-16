"""The one simulation this server runs at a time, and the checks around starting and stopping it."""

import asyncio
from typing import Any

from frc_mcp_wpilib.gradle import jdk
from frc_mcp_wpilib.gradle.project import gradlew

from . import config
from .errors import SimError
from .run import SimRun


async def port_open(port: int, host: str = "127.0.0.1") -> bool:
    """True if something accepts connections on host:port (the simulated robot's NetworkTables server, or a stale one)."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 0.5)
    except (OSError, TimeoutError):
        return False
    writer.close()
    return True


async def wait_port_closed(port: int, timeout_s: float = 5.0) -> bool:
    """Wait for the port to stop answering (the OS and the dying process need a moment after the kill). True once it is closed."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while await port_open(port):
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.25)
    return True


class Session:
    """Holds the current (or last) run. Bounded on purpose: one sim at a time, so there is nothing to leak or lose track of."""

    def __init__(self) -> None:
        self.run: SimRun | None = None
        self._lock = asyncio.Lock()  # one sim at a time, even for concurrent sim_start calls

    async def start(self, project: str | None, task: str | None, props: dict[str, str] | None, run_s: float | None) -> SimRun:
        async with self._lock:  # held from the 'already running' check to the assignment, so exactly one launch wins
            if self.run and self.run.active:
                raise SimError(f"A sim is already running (pid {self.run.pid}, {self.run.task}); call sim_stop first.")
            folder = await config.source.resolve_async(project)
            wrapper = gradlew(folder)
            if wrapper is None:  # explicit so `python -O` keeps the message
                raise SimError(f"{folder} has no Gradle wrapper; give a project folder with gradlew.")
            chosen = config.check_task_for(task, folder) if task else config.default_task(folder)
            args = config.source.props_to_args(props)
            cap = config.max_run_s()
            if run_s is not None and run_s > cap:
                raise SimError(f"run_s may not exceed SIM_MAX_RUN_S ({cap:g} s).")
            port = config.nt_port()
            if await port_open(port):
                raise SimError(f"Port {port} is already in use: a sim (or a robot) is already running. Stop it first (sim_stop if it is yours; otherwise find the owner with netstat -ano / lsof -i and end only that process).")
            run = SimRun(folder, chosen, args)
            await run.launch(wrapper, run_s or cap, jdk=jdk.select(folder, SimError), error=SimError)
            self.run = run
            return run

    async def stop(self) -> SimRun | None:
        async with self._lock:  # the same guard as start(): the slot is read, stopped and returned as one snapshot
            run = self.run
            if run:
                await run.stop()
        return run

    async def status(self) -> dict[str, Any]:
        async with self._lock:  # one read of the slot, so the reply describes one run
            run = self.run
        port = config.nt_port()
        port_is_open = await port_open(port)
        # The effective settings from config.py, on every branch: what `sim_start` would use, the way nt4_status and
        # wpilib_info report their own. `task` below stays the one the current run actually ran.
        settings = {"nt_port": port, "max_run_s": config.max_run_s(), "default_task": config.default_task()}
        if run is None:
            return {"state": "idle", "nt_port_open": port_is_open, **settings}
        out: dict[str, Any] = {
            "state": run.phase, "pid": run.pid, "project": run.project.name, "task": run.task,
            "uptime_s": run.uptime_s, "lines": run.console.total, "errors": run.console.errors, "warnings": run.console.warnings,
            **settings,
        }
        if run.prop_args:
            out["prop_args"] = run.prop_args
        if run.jdk:
            out["java"] = str(run.jdk.home)
        if run.ready_at is not None:
            out["ready_after_s"] = round(run.ready_at - run.started, 1)
        if run.console.datalog:
            out["wpilog"] = run.console.datalog  # the log server opens it (set WPILOG_DIR to its folder)
        if run.exit_code is not None:
            out["exit_code"] = run.exit_code
        if run.console.dropped:
            out["dropped_lines"] = run.console.dropped
        out["nt_port_open"] = port_is_open
        return out


session = Session()
