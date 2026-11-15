"""Settings of the profiling server (JFR_*), read at call time, and where recordings may be read and written. The list is in
docs/SETTINGS.md. Nothing outside JFR_DIR is ever opened or written; name one folder explicitly, since the default is the folder
the server started in. `jfr_record` is off until JFR_ALLOW_RECORD=1; reading recordings and listing JVMs need no switch.
"""

import shutil
from pathlib import Path

from frc_mcp_core.folders import AllowedFolders, check_size, newest_first, walk_folders
from frc_mcp_core.settings import (
    Capability,
    env_megabytes,
    env_number,
    env_str,
    nonblank,
)
from frc_mcp_core.toolset import Toolset

from .errors import JfrError

SUFFIX = ".jfr"
FOLDERS = AllowedFolders("JFR_DIR", JfrError)  # the shared rule for which folders may be read from and written to



def max_seconds() -> float:
    return env_number("JFR_MAX_SECONDS", 600, 1, 86400, JfrError)


def max_bytes() -> int:
    return env_megabytes("JFR_MAX_MB", 512, 1, 65536, JfrError)


def max_output_bytes() -> int:
    return env_megabytes("JFR_MAX_OUTPUT_MB", 200, 1, 65536, JfrError)


def timeout_s() -> float:
    return env_number("JFR_TIMEOUT_S", 120, 5, 3600, JfrError)


def max_chars() -> int:
    return int(env_number("JFR_MAX_CHARS", 6000, 500, 50000, JfrError))


RECORD = Capability("JFR_ALLOW_RECORD", "Recording a JVM", JfrError, why="it attaches a profiler to another running program", getenv=nonblank, expects="1 or 0")  # blank counts as off


def require_record() -> None:
    """Raise unless JFR_ALLOW_RECORD is on: recording attaches a profiler to another process (an IDE or Gradle daemon holds tokens)."""
    RECORD.require()


def roots() -> list[Path]:
    """The folders recordings may be read from and written to (JFR_DIR, else the working directory)."""
    return FOLDERS.roots()


def root_notice() -> str | None:
    if FOLDERS.is_set():
        return None
    return f"JFR_DIR is not set, so recordings are read from and written to the folder the server started in ({Path.cwd().resolve()}). Put recordings in your project folder (or open Claude Code there); from a clone, set JFR_DIR."


TOOLSET = Toolset("JFR_TOOLSET", JfrError)  # read it with TOOLSET.current(); `create_server` makes the server's apply_toolset from it


def jdk_tool(name: str) -> str:
    """The path of a JDK command-line tool (`jfr`, `jcmd`): JFR_JAVA_HOME, then JAVA_HOME, then PATH."""
    for home in (env_str("JFR_JAVA_HOME"), env_str("JAVA_HOME")):
        if home and (found := shutil.which(name, path=str(Path(home) / "bin"))):
            return found
    if found := shutil.which(name):
        return found
    raise JfrError(f"The JDK tool `{name}` was not found. Install a JDK (17 or newer) or set JFR_JAVA_HOME to its folder.")


def find_recordings(name_contains: str | None = None) -> list[Path]:
    """Every `.jfr` under the roots (a few folders deep), newest first."""
    needle = (name_contains or "").lower()
    return newest_first(folder / f for folder, files in walk_folders(roots()) for f in files if f.lower().endswith(SUFFIX) and needle in f.lower())


def resolve_recording(name: str) -> Path:
    """The `.jfr` a name or path refers to, checked to be inside an allowed folder and within the size limit."""
    allowed = roots()
    where = ", ".join(str(r) for r in allowed)
    if not name.lower().endswith(SUFFIX):
        raise JfrError(f"Only {SUFFIX} files can be read; {name!r} is not one.")
    given_name = Path(name).expanduser().name
    located = FOLDERS.locate(name, lambda: (p for p in find_recordings() if p.name == given_name), roots=allowed)
    matches = set(located.inside)
    if located.outside:
        raise JfrError(f"{name!r} is outside the folders this server may read ({where}). Put a copy in your project folder (or open Claude Code there); from a clone, set JFR_DIR.")
    if not matches:
        raise JfrError(f"No recording named {name!r} under {where}. Use jfr_list to see them.")
    if len(matches) > 1:
        raise JfrError(f"{name!r} matches {len(matches)} recordings; give a longer path: " + ", ".join(str(p) for p in sorted(matches)[:3]))
    path = matches.pop()
    check_size(path, max_bytes(), "JFR_MAX_MB", JfrError, "read this recording")
    return path
