"""Settings of the VS Code command server (VSCODE_*, WPILIB_*), read at call time. The list is in docs/SETTINGS.md.
Nothing outside VSCODE_PROJECT_DIR is ever run or edited. Deploying replaces the code on the robot, so it is off until
VSCODE_ALLOW_DEPLOY=1. Fail closed: only the fixed Gradle commands run (no free-form task), team numbers and vendordep names are
validated, no shell is used. WPILIB_HOME and WPILIB_JAVA_HOME are shared with the simulation server (frc_mcp_wpilib/gradle/jdk.py).
"""

from frc_mcp_core.settings import Capability, env_number
from frc_mcp_core.toolset import Toolset
from frc_mcp_wpilib.gradle.project import ProjectSource

from .errors import VSCodeError

source = ProjectSource("VSCODE_PROJECT_DIR", VSCodeError)
deploy = Capability("VSCODE_ALLOW_DEPLOY", "Deploying", VSCodeError, why="it replaces the program on the robot")


TOOLSET = Toolset("VSCODE_TOOLSET", VSCodeError)  # read it with TOOLSET.current(); `create_server` makes the server's apply_toolset from it


def timeout_s() -> float:
    return env_number("VSCODE_TIMEOUT_S", 600, 10, 3600, VSCodeError)

