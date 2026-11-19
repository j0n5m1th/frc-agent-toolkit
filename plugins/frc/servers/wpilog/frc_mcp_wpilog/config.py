"""Settings of the log server (WPILOG_*), read at call time. The list is in docs/SETTINGS.md.

WPILOG_DIR names the folders logs may be read from (`.wpilog`, `.dslog`, `.dsevents`); nothing outside them is ever opened. Name
one explicitly: the default is the folder the server started in, which may be a huge tree such as the home folder.
WPILOG_TOOLSET=core hides `wpilog_diff`, `wpilog_close` and `wpilog_sync`.
"""

from pathlib import Path

from frc_mcp_core.folders import (
    AllowedFolders,
    check_size,
    inside,
    newest_first,
    too_broad,
    walk_folders,
)
from frc_mcp_core.settings import Capability, env_megabytes, nonblank
from frc_mcp_core.toolset import Toolset
from frc_mcp_wpilib.wpilog_writer import SUFFIX

from . import dslog
from .errors import WPILogError

FOLDERS = AllowedFolders("WPILOG_DIR", WPILogError)  # the shared rule for which folders may be read from

DEFAULT_MAX_MB = 2048

# Logs elsewhere (a USB stick, the Driver Station's folder) are read only with this on. The plugin turns it on and Claude Code asks the person before each such call
# (`frc-mcp hook ask-outside`); the server still opens only `.wpilog`, `.dslog` and `.dsevents` files, read-only.
OUTSIDE = Capability("WPILOG_ALLOW_OUTSIDE", "Reading logs outside the project folder", WPILogError, why="in the plugin, turn on \"Read logs from other folders\"; it asks you before each such read", getenv=nonblank, expects="1 or 0")


TOOLSET = Toolset("WPILOG_TOOLSET", WPILogError)  # read it with TOOLSET.current(); `create_server` makes the server's apply_toolset from it


# What to do about a log the server may not read. The plugin allows only the project folder and its user cannot change that, so
# they copy the file in; someone running the server themselves can name more folders.
HOW_TO_ALLOW = "Copy the file into the project folder, or turn on \"Read logs from other folders\" in the plugin. (If you run the server yourself, add its folder to WPILOG_DIR or set WPILOG_ALLOW_OUTSIDE=1.)"
HOW_TO_FIND = "Put the logs in the project folder. (If you run the server yourself, set WPILOG_DIR to the folder that has them.)"


def log_roots() -> list[Path]:
    """The folders logs may be read from (WPILOG_DIR, else the working directory)."""
    return FOLDERS.roots()


def default_root_notice() -> str | None:
    """A nudge to set WPILOG_DIR when the server is searching its working directory instead, else None."""
    if FOLDERS.is_set():
        return None
    return (f"WPILOG_DIR is not set, so the server is searching the folder it was started in ({Path.cwd().resolve()}). "
            "Set WPILOG_DIR to the folder that holds your logs.")  # only when it is unset: someone running the server themselves


def max_log_bytes() -> int:
    return env_megabytes("WPILOG_MAX_MB", DEFAULT_MAX_MB, 1, 65536, WPILogError)


def _is_log(file_name: str) -> bool:
    return file_name.lower().endswith((SUFFIX, *dslog.SUFFIXES))


def search_roots(folder: str | None) -> list[Path]:
    """The folders `wpilog_list` searches: the allowed ones, or `folder` alone (outside them only with WPILOG_ALLOW_OUTSIDE; never a drive root or the home folder)."""
    roots = log_roots()
    if not folder:
        return roots
    chosen = Path(folder).expanduser().resolve()
    if not chosen.is_dir():
        raise WPILogError(f"{folder!r} is not a folder.")
    if not inside(chosen, roots):
        OUTSIDE.require()
        if too_broad(chosen):
            raise WPILogError(f"{chosen} is a drive root or your home folder, which is too broad to search. Name the folder that holds the logs.")
    return [chosen]


def find_logs(roots: list[Path] | None = None, name_contains: str | None = None) -> list[Path]:
    """Every `.wpilog` and Driver Station session under the roots (a few folders deep), newest first.

    A Driver Station session is listed once, by its `.dslog` (or its `.dsevents` when it has no `.dslog`)."""
    found: list[Path] = []
    sessions: dict[tuple[str, str], list[Path]] = {}  # (folder, lower-case stem) -> the session's files as on disk
    needle = (name_contains or "").lower()
    for folder, files in walk_folders(roots or log_roots()):
        for f in files:
            path = folder / f
            if dslog.is_ds(path):
                sessions.setdefault((str(folder), path.stem.lower()), []).append(path)
            elif _is_log(f) and needle in f.lower():
                found.append(path)
    for session in sessions.values():  # one entry per session, by its .dslog when it has one; either file name matches
        if any(needle in f.name.lower() for f in session):
            found.append(min(session, key=lambda f: f.suffix.lower() != dslog.DSLOG))
    return newest_first(found)


def resolve_log(name: str) -> Path:
    """The file a user-given name or path refers to, checked to be a `.wpilog` inside an allowed folder.
    Raises WPILogError: wrong suffix, path outside the allowed folders, missing or ambiguous name, or over WPILOG_MAX_MB.
    """
    roots = log_roots()
    where = ", ".join(str(r) for r in roots)
    if not _is_log(name):
        raise WPILogError(f"Only {SUFFIX}, {dslog.DSLOG} and {dslog.DSEVENTS} files can be opened; {name!r} is not one.")
    given = Path(name).expanduser()

    def same(p: Path) -> bool:  # a session is listed by one of its two files; either name finds it
        if dslog.is_ds(given):
            return dslog.is_ds(p) and p.stem.lower() == given.stem.lower()
        return p.name == given.name

    located = FOLDERS.locate(name, lambda: (p for p in find_logs(roots) if same(p)), roots=roots)
    matches, outside = located.inside, located.outside
    elsewhere = bool(outside) and OUTSIDE.enabled()  # a log outside the folders, allowed by the setting (the plugin has asked first)
    if elsewhere:
        matches, outside = matches + outside, []
    if dslog.is_ds(given):  # either file of a session opens the session; its other file is judged too (it may be a link out)
        canonical = [dslog.canonical(m).resolve() for m in matches]
        # every file the session reads (the .dslog AND the .dsevents) must resolve inside the folders, not only the one named
        safe = [c for c in canonical if OUTSIDE.enabled() or all(inside(f.resolve(), roots) for f in dslog.pair(c) if f is not None)]
        matches, outside = safe, outside + [c for c in canonical if c not in safe]
    if outside:
        raise WPILogError(f"{name!r} is outside the folders this server may read ({where}). {HOW_TO_ALLOW}")
    unique = sorted(set(matches))
    if not unique:
        raise WPILogError(f"No log named {name!r} under {where}. Use wpilog_list to see the logs.")
    if len(unique) > 1:
        raise WPILogError(f"{name!r} matches {len(unique)} logs; give a longer path: " + ", ".join(str(p) for p in unique[:3]))
    check_size(unique[0], max_log_bytes(), "WPILOG_MAX_MB", WPILogError, "open this log")
    return unique[0]
