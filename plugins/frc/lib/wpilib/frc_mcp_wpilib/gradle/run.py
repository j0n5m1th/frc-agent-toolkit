"""One Gradle child process: the console reader, an optional time limit, and stopping the whole process tree."""

import asyncio
import contextlib
import logging
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from frc_mcp_core.processes import (
    kill_tree_sync,
    new_session_kwargs,
    register_cleanup,
    track_child,
)

from ..errors import WPILibMCPError
from .console import Console
from .jdk import Jdk

_log = logging.getLogger(__name__)

STOP_GRACE_S = 5.0
_STREAM_LIMIT = 2**20  # a console line longer than this would otherwise stop the reader

# How a run stopped, once it is over; None while it still runs.
EndReason = Literal["exited", "stopped", "timed_out"]


_live: set["GradleRun"] = set()  # runs the server's exit must not leave behind


def _kill_live() -> None:
    for run in list(_live):
        if not run._ended.is_set() and run.pid:  # `active` is false early (stopped/timed_out) while the tree may still live
            kill_tree_sync(run.pid)


register_cleanup(_kill_live)  # at exit, and first thing when a termination signal arrives


class GradleRun:
    """A started `gradlew <args>`. `end_reason` is None while it runs, then 'exited', 'stopped' or 'timed_out'.

    It is set as soon as the run is over or given a reason, which can be a moment before the process is gone:
    `active` is false then, while `_ended` is what says the console reader has finished."""

    def __init__(self, project: Path, args: list[str]) -> None:
        self.project = project
        self.args = args
        self.console = Console()
        self.proc: asyncio.subprocess.Process | None = None
        self.started = time.monotonic()
        self.ended_at: float | None = None
        self.exit_code: int | None = None
        self.end_reason: EndReason | None = None
        self.jdk: Jdk | None = None
        self._reader: asyncio.Task[None] | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._ended = asyncio.Event()
        self._changed = asyncio.Event()
        self._kill: asyncio.Task[None] | None = None
        self._error: type[WPILibMCPError] = WPILibMCPError  # the raising class of the current launch

    async def launch(self, wrapper: Path, max_run_s: float | None = None, *, outlive_server: bool = False,
                     jdk: Jdk | None = None, error: type[WPILibMCPError]) -> None:
        """Start the wrapper (stdin closed, stderr merged into the console). `max_run_s` None means no time limit.
        The process tree is killed when the server exits, unless `outlive_server`. `jdk` sets JAVA_HOME (None leaves the
        environment). `error` is required: the calling server's own error class, for every failure of this launch.
        """
        self.jdk = jdk
        self._error = error  # so _read() raises the same class as this launch
        kwargs: dict[str, Any] = new_session_kwargs()
        try:
            self.proc = await asyncio.create_subprocess_exec(
                str(wrapper), *self.args, cwd=self.project, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, limit=_STREAM_LIMIT, env=({**os.environ, "JAVA_HOME": str(jdk.home)} if jdk else None), **kwargs)
        except OSError as e:
            raise error(f"Could not start {wrapper.name}: {e}") from e
        if not outlive_server:
            _live.add(self)
            track_child(self.proc.pid)  # Windows: the job ends the tree if the server dies; the atexit hook covers a clean exit
        self._reader = asyncio.create_task(self._read())
        if max_run_s is not None:
            self._timer = asyncio.get_running_loop().call_later(max_run_s, self._on_timeout)

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    @property
    def active(self) -> bool:
        return self.end_reason is None

    @property
    def uptime_s(self) -> float:
        return round((self.ended_at or time.monotonic()) - self.started, 1)

    def _on_line(self, line: str) -> None:
        """Hook: add one console line. Subclasses may also note what it means."""
        self.console.add(line)

    async def _read(self) -> None:
        if self.proc is None or self.proc.stdout is None:  # launch() sets both; explicit so `python -O` keeps the message
            raise self._error("This run has no process or console stream to read.")
        try:
            while True:
                try:
                    raw = await self.proc.stdout.readline()
                except ValueError as e:  # a line over the stream limit: skip it rather than stop reading
                    _log.debug("skipped a console line over the %d byte limit: %s", _STREAM_LIMIT, e)
                    self.console.note_skipped()  # it still counts as a line that did not survive
                    continue
                if not raw:
                    break
                self._on_line(raw.decode("utf-8", errors="replace"))
                self._changed.set()
            self.exit_code = await self.proc.wait()
        finally:  # the end bookkeeping runs even when the reader is cancelled mid-line
            if self.end_reason is None:
                self.end_reason = "exited"
            self.ended_at = time.monotonic()
            _live.discard(self)
            if self._timer:
                self._timer.cancel()
            self._ended.set()
            self._changed.set()

    def _on_timeout(self) -> None:
        if self.active:
            self.end_reason = "timed_out"
            if self.pid:  # taskkill blocks, so keep it off the event loop
                self._kill = asyncio.create_task(asyncio.to_thread(kill_tree_sync, self.pid))

    async def wait_until(self, done: Callable[[], bool], timeout_s: float) -> None:
        """Return when `done()` is true, the process ended, or `timeout_s` passed (the run is left as it is)."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(timeout_s):
                while self.active and not done():
                    self._changed.clear()
                    await self._changed.wait()

    async def wait(self, timeout_s: float) -> None:
        """Return when the process ended or `timeout_s` passed."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._ended.wait(), timeout_s)

    async def stop(self) -> None:
        """End the process tree and wait for the console to finish. A run that already ended is left as it is."""
        if self.active:
            self.end_reason = "stopped"
        if self.pid and not self._ended.is_set():
            await asyncio.to_thread(kill_tree_sync, self.pid)
            try:
                await asyncio.wait_for(self._ended.wait(), STOP_GRACE_S)
            except TimeoutError:
                await asyncio.to_thread(kill_tree_sync, self.pid)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._ended.wait(), STOP_GRACE_S)
