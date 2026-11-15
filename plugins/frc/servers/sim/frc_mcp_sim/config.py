"""Settings of the simulation server (SIM_*), read at call time, and the checks on what may be launched. The list is in
docs/SETTINGS.md. Nothing outside SIM_PROJECT_DIR is ever launched. Fail closed: only tasks named `simulate*` run, properties
are plain `-Pname=value` pairs, no shell is involved. WPILIB_JAVA_HOME and WPILIB_HOME (which JDK Gradle uses) are shared with
the commands server; see frc_mcp_wpilib/gradle/jdk.py.
"""

import re

from frc_mcp_core.settings import env_int, env_number, env_str
from frc_mcp_core.toolset import Toolset
from frc_mcp_wpilib.gradle.project import ProjectSource

from .errors import SimError

source = ProjectSource("SIM_PROJECT_DIR", SimError)
_TASK_RE = re.compile(r"simulate[A-Za-z0-9]*")


TOOLSET = Toolset("SIM_TOOLSET", SimError)  # read it with TOOLSET.current(); `create_server` makes the server's apply_toolset from it


def check_task(task: str) -> str:
    if not _TASK_RE.fullmatch(task):
        raise SimError(f"Only Gradle tasks named simulate* can be run (for example simulateJava); {task!r} is not one.")
    return task


def default_task() -> str:
    return check_task(env_str("SIM_TASK", "simulateJava") or "simulateJava")


def nt_port() -> int:
    return env_int("SIM_NT_PORT", 5810, 1, 65535, SimError)


def max_run_s() -> float:
    return env_number("SIM_MAX_RUN_S", 1800, 1, 86400, SimError)
