"""Which JDK a robot project is built and run with: the same Java for every WPILib server that launches Gradle."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from frc_mcp_core.settings import env_str

from ..errors import WPILibMCPError

JAVA_HOME_ENV = "WPILIB_JAVA_HOME"  # an explicit choice: wins over everything below
WPILIB_HOME_ENV = "WPILIB_HOME"
PREFS_PATH = Path(".wpilib") / "wpilib_preferences.json"  # the one name of the project's preferences file
_JAVA = "java.exe" if os.name == "nt" else "java"
FALLBACK = "JAVA_HOME or PATH"


@dataclass(frozen=True)
class Jdk:
    """The JDK chosen for a project: where it is and why (`source` is an environment variable or 'WPILib install')."""

    home: Path
    source: str

    def __str__(self) -> str:
        return f"{self.home} (from {self.source})"


def describe(jdk: Jdk | None) -> str:
    """The choice in words, for a status line: the JDK and its source, or where Java comes from when none was chosen."""
    return str(jdk) if jdk else FALLBACK


def wpilib_home() -> Path:
    """The WPILib install folder: WPILIB_HOME, else the installer's default for this system."""
    configured = env_str(WPILIB_HOME_ENV)
    return Path(configured).expanduser() if configured else (Path("C:/Users/Public/wpilib") if os.name == "nt" else Path.home() / "wpilib")


def read_preferences(project: Path) -> dict[str, Any]:
    """The project's `.wpilib/wpilib_preferences.json` as a dict, or {} when it is missing or not a JSON object."""
    try:
        data = json.loads((project / PREFS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _has_java(home: Path) -> bool:
    return (home / "bin" / _JAVA).is_file()


def select(project: Path, error: type[WPILibMCPError], prefs: dict[str, Any] | None = None) -> Jdk | None:
    """The JDK to run this project's Gradle with; None means "use the environment as it is". Order:
    1. WPILIB_JAVA_HOME if set (always wins; a folder without bin/java is an error).
    2. The JDK the WPILib installer put next to the project's year (`<WPILIB_HOME>/<projectYear>/jdk`), which is what the VS Code
       extension uses, so every server sees the same Java.
    3. Nothing: JAVA_HOME and PATH as started.
    `prefs` is the project's preferences if already read. `error` is required: the calling server's own error class.
    """
    if configured := env_str(JAVA_HOME_ENV):
        home = Path(configured).expanduser()
        if not _has_java(home):
            raise error(f"{JAVA_HOME_ENV} is {configured!r}, which has no bin/{_JAVA}. Point it at a JDK folder, or unset it.")
        return Jdk(home, JAVA_HOME_ENV)
    year = (prefs if prefs is not None else read_preferences(project)).get("projectYear")
    if year and _has_java(home := wpilib_home() / str(year) / "jdk"):
        return Jdk(home, "WPILib install")
    return None
