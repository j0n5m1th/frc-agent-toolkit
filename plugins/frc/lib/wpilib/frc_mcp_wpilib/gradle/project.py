"""Which robot project a server may run: folders named by an environment variable, a Gradle wrapper, safe `-P` properties.

A server makes one `ProjectSource(env_name, error)`; its messages then name the right setting and raise the server's own error.
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from frc_mcp_core.folders import AllowedFolders

from ..errors import WPILibMCPError
from . import buildfile

_log = logging.getLogger(__name__)

BUILD_FILES = ("build.gradle", "build.gradle.kts")
_PROP_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_PROP_VALUE_RE = re.compile(r"[A-Za-z0-9_.,:;=+\-/@]{0,200}")  # no spaces, quotes or shell characters: Windows runs gradlew.bat through cmd


def gradlew(project: Path) -> Path | None:
    """The project's Gradle wrapper for this platform, if it has one."""
    wrapper = project / ("gradlew.bat" if os.name == "nt" else "gradlew")
    return wrapper if wrapper.is_file() else None


def read_build_text(folder: Path) -> str:
    """The text of the folder's build.gradle (or .kts), or '' when it has none or it cannot be read."""
    for name in BUILD_FILES:
        try:
            return (folder / name).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            _log.debug("%s of %s could not be read: %s", name, folder, e)
            continue
    return ""


def is_project(folder: Path) -> bool:
    """A WPILib project: a Gradle wrapper and a build file that mentions GradleRIO or wpilib."""
    return gradlew(folder) is not None and buildfile.is_wpilib(read_build_text(folder))


def find_projects(search: list[Path]) -> list[Path]:
    """Each folder that is a project, else its direct subfolders that are."""
    found: list[Path] = []
    for root in search:
        if is_project(root):
            found.append(root)
            continue
        try:
            found += [p for p in sorted(root.iterdir()) if p.is_dir() and not p.name.startswith(".") and is_project(p)]
        except OSError as e:
            _log.debug("%s could not be listed: %s", root, e)
            continue
    return found


@dataclass(frozen=True)
class ProjectSource:
    """The robot projects one server may run: where (the folders in `env_name`), and what it may pass them. Errors are `error`."""

    env_name: str
    error: type[WPILibMCPError]

    @property
    def folders(self) -> AllowedFolders:
        return AllowedFolders(self.env_name, self.error)  # the shared rule for which folders may be used

    def roots(self) -> list[Path]:
        """The folders projects may be run from (the variable, else the working directory)."""
        return self.folders.roots()

    def notice(self) -> str | None:
        """A nudge to set the variable when the server is looking in its working directory instead, else None."""
        if self.folders.is_set():
            return None
        return (f"{self.env_name} is not set, so the server looks in the folder it was started in ({Path.cwd().resolve()}). "
                f"Set {self.env_name} to your robot project's folder.")

    def resolve(self, name: str | None = None) -> Path:
        """The project to run: `name` (a path, absolute or under a root) or the only project under the roots.

        Raises the server's error: outside the allowed folders, not a WPILib project, none found, or several without a choice."""
        allowed = self.roots()
        where = ", ".join(str(r) for r in allowed)
        if name:
            located = self.folders.locate(name, directory=True, roots=allowed)
            if located.outside:
                raise self.error(f"{name!r} is outside the folders this server may run ({where}). Open Claude Code in that project's folder. (If you run the server yourself, add its folder to {self.env_name}.)")
            if not located.inside:
                raise self.error(f"No folder named {name!r} under {where}.")
            if not is_project(resolved := located.inside[0]):  # the first folder found, in the order of the allowed folders
                raise self.error(f"{resolved} is not a WPILib project: it needs gradlew and a build.gradle that uses GradleRIO.")
            return resolved
        found = find_projects(allowed)
        if not found:
            raise self.error(f"No WPILib project (gradlew + build.gradle with GradleRIO) under {where}; set {self.env_name} to your robot project's folder.")
        if len(found) > 1:
            raise self.error(f"{len(found)} projects found; pass `project`: " + ", ".join(str(p) for p in found[:5]))
        return found[0]

    async def resolve_async(self, name: str | None = None) -> Path:
        """`resolve` off the event loop (it stats and reads files)."""
        return await asyncio.to_thread(self.resolve, name)

    def props_to_args(self, props: dict[str, str] | None) -> list[str]:
        """`props` as Gradle arguments: {'mode': 'teleop'} -> ['-Pmode=teleop'], an empty value gives a flag ('-Pheadless')."""
        args = []
        for key, value in (props or {}).items():
            if not _PROP_KEY_RE.fullmatch(key):
                raise self.error(f"Property name {key!r} is not allowed: use letters, digits, '_' and '.'.")
            if not _PROP_VALUE_RE.fullmatch(value):
                raise self.error(f"Value of property {key!r} has a character that is not allowed (letters, digits and _.,:;=+-/@ only, no spaces).")
            args.append(f"-P{key}={value}" if value else f"-P{key}")
        return args
