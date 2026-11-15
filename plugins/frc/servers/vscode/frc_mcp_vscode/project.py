"""The robot project's own files: `.wpilib/wpilib_preferences.json`, `build.gradle` facts and the `vendordeps/` folder.

These are the data the VS Code extension reads and writes (Open Project Information, Set Team Number, Manage Vendor Libraries).
"""

import json
import logging
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from frc_mcp_wpilib.files import write_atomic
from frc_mcp_wpilib.gradle import buildfile, jdk
from frc_mcp_wpilib.gradle import project as gradle_project
from frc_mcp_wpilib.gradle.jdk import PREFS_PATH

from .errors import VSCodeError

_log = logging.getLogger(__name__)

MAX_TEAM = 25599
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}")


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise VSCodeError(f"{path} does not exist.") from None
    except (OSError, ValueError) as e:
        raise VSCodeError(f"Cannot read {path}: {e}") from e
    if not isinstance(data, dict):
        raise VSCodeError(f"{path} is not a JSON object.")
    return data


def _file[T](path: Path, action: Callable[[], T]) -> T:
    """One file action on `path`, its OSError given the same shape of message as a read failure."""
    try:
        return action()
    except OSError as e:
        raise VSCodeError(f"Cannot use {path}: {e}") from e


def preferences(project: Path) -> dict[str, Any]:
    """The strict reader the tools answer from (the JDK choice uses the lenient `jdk.read_preferences` instead)."""
    return read_json(project / PREFS_PATH)


def check_team(team: int) -> int:
    if not 1 <= team <= MAX_TEAM:
        raise VSCodeError(f"Team number must be from 1 to {MAX_TEAM}, not {team}.")
    return team


def _as_team_number(value: Any) -> int | None:
    """A `teamNumber` as a plain int, or None when the stored value is not one: JSON booleans are excluded although
    Python counts them as ints, and a number kept as text is not a number either."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def team_number(project: Path) -> int:
    number = _as_team_number(preferences(project).get("teamNumber"))
    if number is None:
        raise VSCodeError(f"teamNumber in {PREFS_PATH} is not set; pass `team` or use wpilib_set_team.")
    return check_team(number)


def set_team(project: Path, team: int) -> int | None:
    """Write teamNumber into the preferences file (other keys kept) and return the old number.

    The old value goes through the same rule as `team_number`, so `was` is a plain int or None, never a JSON bool."""
    data = preferences(project)
    old = _as_team_number(data.get("teamNumber"))
    data["teamNumber"] = check_team(team)
    _file(project / PREFS_PATH, lambda: write_atomic(project / PREFS_PATH, json.dumps(data, indent=4) + "\n"))
    return old


def build_facts(project: Path) -> dict[str, Any]:
    """GradleRIO version, Java target and deploy targets read from build.gradle (only what is there)."""
    return buildfile.facts(gradle_project.read_build_text(project))


def _vendordep_files(folder: Path) -> dict[str, dict[str, Any]]:
    """name (file stem) -> parsed JSON for each vendordep in `folder`; unreadable files are skipped."""
    found: dict[str, dict[str, Any]] = {}
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            try:
                found[path.stem] = read_json(path)
            except VSCodeError as e:  # unreadable or malformed: this folder lists the files that did parse
                _log.debug("skipped vendordep %s: %s", path, e)
                continue
    return found


def offline_source(project: Path, prefs: dict[str, Any] | None = None) -> Path:
    """The WPILib install's vendordeps folder for this project's year (projectYear in the preferences)."""
    year = (prefs if prefs is not None else preferences(project)).get("projectYear")
    return jdk.wpilib_home() / str(year or "") / "vendordeps"


def info(project: Path) -> dict[str, Any]:
    """Everything wpilib_info reports, read in one go (the preferences file once): blocking, so call it in a thread."""
    prefs = preferences(project)
    chosen = jdk.select(project, VSCodeError, prefs)
    return {"project": project.name, "team": prefs.get("teamNumber"), "year": prefs.get("projectYear"),
            "language": prefs.get("currentLanguage"), "jdk": {"home": str(chosen.home), "from": chosen.source} if chosen else jdk.FALLBACK,
            **build_facts(project), **vendordeps(project, prefs)}


def vendordeps(project: Path, prefs: dict[str, Any]) -> dict[str, Any]:
    """Installed vendordeps {name: version} and the offline ones from the WPILib install that are not installed (or differ)."""
    installed = _vendordep_files(project / "vendordeps")
    out: dict[str, Any] = {"installed": {name: dep.get("version") for name, dep in installed.items()}}
    offline = _vendordep_files(offline_source(project, prefs))
    other = {name: dep.get("version") for name, dep in offline.items() if name not in installed}
    newer = {name: f"{installed[name].get('version')} -> {dep.get('version')}"
             for name, dep in offline.items() if name in installed and dep.get("version") != installed[name].get("version")}
    if other:
        out["offline_available"] = other
    if newer:
        out["offline_differs"] = newer
    return out


def _conflicts(dep: dict[str, Any]) -> set[Any]:
    """The uuids `dep` conflicts with; an entry without one names nothing and is skipped."""
    return {c["uuid"] for c in dep.get("conflictsWith", []) if isinstance(c, dict) and c.get("uuid")}


def check_vendordep_name(name: str) -> str:
    if not _NAME_RE.fullmatch(name):
        raise VSCodeError(f"Vendordep name {name!r} is not allowed: use the file name without .json, e.g. CommandsV2.")
    return name


def install_vendordep(project: Path, name: str) -> dict[str, Any]:
    """Copy `<name>.json` from the WPILib install into vendordeps/ (replacing an older copy); refuse a conflicting library."""
    check_vendordep_name(name)
    source = offline_source(project) / f"{name}.json"
    if not source.is_file():
        have = ", ".join(sorted(_vendordep_files(source.parent))) or "none found"
        raise VSCodeError(f"No offline vendordep {name!r} in {source.parent} (have: {have}). Set WPILIB_HOME if WPILib is installed elsewhere.")
    new = read_json(source)
    installed = _vendordep_files(project / "vendordeps")
    clash = _conflicts(new)
    for other_name, other in installed.items():
        if other_name != name and (other.get("uuid") in clash or new.get("uuid") in _conflicts(other)):
            raise VSCodeError(f"{name} conflicts with the installed {other_name}; remove {other_name} first.")
    target = project / "vendordeps" / f"{name}.json"
    old = installed.get(name, {}).get("version")
    def copy_in() -> None:
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)

    _file(target, copy_in)
    return {"name": name, "version": new.get("version"), "replaced": old}


def remove_vendordep(project: Path, name: str) -> dict[str, Any]:
    check_vendordep_name(name)
    target = project / "vendordeps" / f"{name}.json"
    if not target.is_file():
        raise VSCodeError(f"{name} is not in vendordeps/ (wpilib_info lists what is installed).")
    try:
        version = read_json(target).get("version")
    except VSCodeError:  # an unreadable file can still be removed
        version = None
    _file(target, target.unlink)
    return {"name": name, "removed": version}
