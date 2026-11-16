"""Settings of the simulation server (SIM_*), read at call time, and the checks on what may be launched. The list is in
docs/SETTINGS.md. Nothing outside SIM_PROJECT_DIR is ever launched. Fail closed: only tasks named `simulate*` run, properties
are plain `-Pname=value` pairs, no shell is involved. WPILIB_JAVA_HOME and WPILIB_HOME (which JDK Gradle uses) are shared with
the commands server; see frc_mcp_wpilib/gradle/jdk.py.
"""

import re
from pathlib import Path

from frc_mcp_core.settings import env_int, env_number, env_str
from frc_mcp_core.toolset import Toolset
from frc_mcp_wpilib.gradle import buildfile
from frc_mcp_wpilib.gradle.project import ProjectSource, read_build_text

from .errors import SimError

source = ProjectSource("SIM_PROJECT_DIR", SimError)
_TASK_RE = re.compile(r"simulate[A-Za-z0-9]*")


TOOLSET = Toolset("SIM_TOOLSET", SimError)  # read it with TOOLSET.current(); `create_server` makes the server's apply_toolset from it


def check_task(task: str) -> str:
    if task != "run" and not _TASK_RE.fullmatch(task):  # `run` is the 2027 way: GradleRIO 2027 made simulateJava fail on purpose
        raise SimError(f"Only the Gradle tasks run and simulate* can be run (for example simulateJava); {task!r} is not one.")
    return task


def _is_2027(project: Path) -> bool:
    return str(buildfile.facts(read_build_text(project)).get("gradlerio", "")).startswith("2027")


def check_task_for(task: str, project: Path) -> str:
    """`check_task`, and refuse `simulateJava` on a 2027 project, where GradleRIO makes it fail with a message the console tail hides."""
    if check_task(task) == "simulateJava" and _is_2027(project):
        raise SimError("GradleRIO 2027 removed the simulateJava task; it only fails. Use the `run` task (leave `task` empty, and unset SIM_TASK if it names simulateJava).")
    return task


def default_task(project: Path | None = None) -> str:
    """SIM_TASK, else `run` for a 2027 GradleRIO project (its `simulateJava` only throws), else `simulateJava`."""
    if configured := env_str("SIM_TASK"):
        return check_task_for(configured, project) if project is not None else check_task(configured)
    if project is not None and _is_2027(project):
        return "run"
    return "simulateJava"


def nt_port() -> int:
    return env_int("SIM_NT_PORT", 5810, 1, 65535, SimError)


def max_run_s() -> float:
    return env_number("SIM_MAX_RUN_S", 1800, 1, 86400, SimError)
