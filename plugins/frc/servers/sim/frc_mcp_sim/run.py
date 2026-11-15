"""One simulation run: a Gradle child process that reports building -> starting -> ready from what it prints."""

import time
from pathlib import Path
from typing import Literal

from frc_mcp_wpilib.gradle.run import EndReason, GradleRun


class SimRun(GradleRun):
    """A started simulation. `phase` goes building -> starting -> ready, then ends as exited, stopped or timed_out."""

    def __init__(self, project: Path, task: str, prop_args: list[str]) -> None:
        super().__init__(project, [task, "--console=plain", *prop_args])
        self.task = task
        self.prop_args = prop_args
        self.ready_at: float | None = None

    @property
    def phase(self) -> EndReason | Literal["building", "starting", "ready"]:
        if self.end_reason:
            return self.end_reason
        if self.console.ready:
            return "ready"
        return "starting" if self.console.starting_seen else "building"

    def _on_line(self, line: str) -> None:
        was_ready = self.console.ready
        super()._on_line(line)
        if self.console.ready and not was_ready:
            self.ready_at = time.monotonic()

    async def wait_until_ready_or_over(self, timeout_s: float) -> None:
        """Return when the program reported startup complete, the process ended, or `timeout_s` passed."""
        await self.wait_until(lambda: self.console.ready, timeout_s)
