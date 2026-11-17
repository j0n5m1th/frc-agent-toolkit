"""What a robot project says about its CTRE software: the Phoenix vendordep versions, the WPILib year and the GradleRIO version.

Read from `vendordeps/*.json` (`name`, `version`, `fileName`), `.wpilib/wpilib_preferences.json` (`projectYear`, `teamNumber`) and
`build.gradle` (`edu.wpi.first.GradleRIO` version). Nothing is written. A vendordep file is data: only its name and version are used.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from frc_mcp_wpilib.gradle.jdk import read_preferences

from .errors import CtreError

_log = logging.getLogger(__name__)
_GRADLERIO = re.compile(r"""edu\.wpi\.first\.GradleRIO["']\s+version\s+["']([^"']+)["']""")
_PHOENIX6 = re.compile(r"phoenix\s*\(?v?6|phoenix6", re.IGNORECASE)
_PHOENIX5 = re.compile(r"phoenix\s*\(?v?5|phoenix5", re.IGNORECASE)


@dataclass(frozen=True)
class ProjectFacts:
    folder: Path
    year: str | None = None  # projectYear in .wpilib/wpilib_preferences.json
    team: int | None = None
    gradlerio: str | None = None
    phoenix6: str | None = None
    phoenix5: str | None = None
    ctre_vendordeps: dict[str, str] = field(default_factory=dict)  # every vendordep whose name starts with CTRE or contains Phoenix
    notes: list[str] = field(default_factory=list)


def _json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        _log.debug("skipped %s: %s", path, e)
        return {}
    return data if isinstance(data, dict) else {}


def read_project(folder: Path) -> ProjectFacts:
    """The CTRE-relevant facts of the robot project in `folder`; a CtreError if it does not look like one."""
    if not folder.is_dir():
        raise CtreError(f"The robot project folder {folder} does not exist. Open Claude Code in your robot project (from a clone, set CTRE_PROJECT_DIR).")
    if not any((folder / name).exists() for name in ("build.gradle", "vendordeps", ".wpilib")):
        raise CtreError(f"{folder} does not look like a WPILib robot project (no build.gradle, vendordeps or .wpilib). Open Claude Code in the project folder (from a clone, set CTRE_PROJECT_DIR).")
    notes: list[str] = []
    prefs = read_preferences(folder)
    year = str(prefs["projectYear"]) if prefs.get("projectYear") else None
    team = prefs["teamNumber"] if isinstance(prefs.get("teamNumber"), int) else None
    gradlerio = None
    try:
        if match := _GRADLERIO.search((folder / "build.gradle").read_text(encoding="utf-8", errors="replace")):
            gradlerio = match.group(1)
    except OSError:
        notes.append("build.gradle could not be read.")
    ctre: dict[str, str] = {}
    phoenix6 = phoenix5 = None
    for path in sorted((folder / "vendordeps").glob("*.json")):
        data = _json(path)
        name, version = str(data.get("name", "")), str(data.get("version", ""))
        if not name or not version:
            continue
        label = f"{name} {path.name}"
        if _PHOENIX6.search(label):
            phoenix6 = version
        elif _PHOENIX5.search(label):
            phoenix5 = version
        elif not name.upper().startswith("CTRE"):
            continue
        ctre[name] = version
    if phoenix6 is None and phoenix5 is None:
        notes.append("No Phoenix vendordep found in vendordeps/, so no CTRE library version is known for this project.")
    return ProjectFacts(folder, year, team, gradlerio, phoenix6, phoenix5, ctre, notes)
