"""Allowed folders: the one rule every server uses before it opens, lists or runs something a user named.
A server names an environment setting (`WPILOG_DIR`, `JFR_DIR`, ...) holding one or more folders (`;` on Windows, `:` elsewhere);
the folder the server started in is used when unset. Nothing outside them is ever opened. A path is resolved (symlinks followed,
`..` removed) and then checked to be inside a folder, so a link or `..` cannot lead out; a bare name is looked up under the folders.
If any reading of a name leads outside, it is refused, even when another stays inside. Each server keeps its own setting names and
messages; this module decides only *where* and *what is inside*.
"""

import logging
import os
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .settings import env_str

_log = logging.getLogger(__name__)

# Folder walk shared by the file-listing tools: build/VCS folders hold no log or recording worth listing, and a
# shallow limit keeps a nested Gradle tree from being crawled for every answer.
WALK_SKIP_FOLDERS = frozenset({".git", ".venv", "node_modules", "__pycache__", ".gradle", "build", "bin"})
WALK_DEPTH_LIMIT = 4


def too_broad(path: Path) -> bool:
    """True for a drive root (the C: drive, or `/`) or the user's home folder: never a sensible folder to search by default."""
    resolved = path.resolve()
    if resolved.parent == resolved:
        return True
    try:
        return resolved == Path.home().resolve()
    except (RuntimeError, OSError):
        return False


def check_size(path: Path, limit_bytes: int, env_name: str, error: type[Exception], action: str) -> None:
    """Raise `error` if the file at `path` is over `limit_bytes`, naming the setting `env_name` that raises the limit and what it then allows (`action`)."""
    size = path.stat().st_size
    if size > limit_bytes:
        raise error(f"{path.name} is {size / 2**20:.0f} MB, over the {env_name} limit; raise it to {action}.")


def inside(path: Path, roots: Iterable[Path]) -> bool:
    """True if `path` (already resolved) is one of the `roots` or below one."""
    return any(path.is_relative_to(root) for root in roots)


def file_row(path: Path, roots: Iterable[Path]) -> dict[str, Any]:
    """One list-tool row: `file` (relative to the folder it is under), `mb` and `modified`."""
    info = path.stat()
    shown = next((str(path.relative_to(root)) for root in roots if path.is_relative_to(root)), str(path))
    return {"file": shown, "mb": round(info.st_size / 2**20, 1), "modified": datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M")}


def _is_link(path: Path) -> bool:
    """A symlink or (Windows) junction: `os.walk` does not follow the first, but does follow the second."""
    return path.is_symlink() or os.path.isjunction(path)


def walk_folders(roots: Iterable[Path]) -> Iterator[tuple[Path, list[str]]]:
    """Each folder under the roots, a few levels deep, with the names of the files directly in it.

    Folders that start with a dot and the build/VCS folders in `WALK_SKIP_FOLDERS` are skipped, and links to folders are not followed.
    """
    for root in roots:
        base = len(root.parts)
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in WALK_SKIP_FOLDERS and not d.startswith(".") and not _is_link(Path(folder, d))]
            if len(Path(folder).parts) - base >= WALK_DEPTH_LIMIT:
                dirs[:] = []
            yield Path(folder), files


def newest_first(paths: Iterable[Path]) -> list[Path]:
    """The distinct paths, most recently modified first; one that vanished since it was found is left out."""
    stamped: list[tuple[float, Path]] = []
    for path in set(paths):
        try:
            stamped.append((path.stat().st_mtime, path))
        except OSError as e:  # deleted since the walk: it is simply not there to list
            _log.debug("%s could not be timed: %s", path, e)
    return [path for _, path in sorted(stamped, reverse=True)]


@dataclass(frozen=True)
class Located:
    """What a name refers to: the `inside` paths (resolved, no repeats, in the order found) and the `outside` ones (a caller refuses when there is any)."""

    inside: list[Path] = field(default_factory=list)
    outside: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class AllowedFolders:
    """The folders one server may use: the ones listed in the environment setting `env_name`, else the folder it started in."""

    env_name: str
    error: type[Exception] = ValueError  # raised when the unset setting would fall back to a drive root or the home folder

    def configured(self) -> list[str]:
        """The folders as written in the setting (empty when it is unset or blank)."""
        entries = (p.strip().strip("\"'").strip() for p in (env_str(self.env_name) or "").split(os.pathsep))  # spaces and quotes around an entry are not part of the path
        return [p for p in entries if p]

    def is_set(self) -> bool:
        return bool(self.configured())

    def roots(self, fallback: Callable[[], list[Path]] | None = None) -> list[Path]:
        """The allowed folders, resolved: the setting, else what `fallback` finds (if given and non-empty), else the working directory.

        A drive root or the home folder is never used: not as the implicit working-directory default, and not when the setting itself names it
        (the plugin sets these to the project folder, so opening Claude Code in the home folder would otherwise search all of it).
        """
        if configured := self.configured():
            resolved = [Path(p).expanduser().resolve() for p in configured]
            for path in resolved:
                if too_broad(path):
                    raise self.error(f"{self.env_name} names {path}, a drive root or your home folder, which is too broad to search. "
                                     f"Set {self.env_name} to the folder you mean.")
            return resolved
        if fallback is not None and (found := fallback()):
            return [p.resolve() for p in found]
        cwd = Path.cwd().resolve()
        if too_broad(cwd):
            raise self.error(f"{self.env_name} is not set and the folder the server started in ({cwd}) is a drive root or your home folder, which is too broad to search. "
                             f"Set {self.env_name} to the folder you mean.")
        return [cwd]

    def where(self, roots: list[Path] | None = None) -> str:
        """The allowed folders as one sentence part, for messages."""
        return ", ".join(str(r) for r in roots or self.roots())

    def inside(self, path: Path, roots: list[Path] | None = None) -> bool:
        return inside(path, roots or self.roots())

    def locate(self, name: str, search: Callable[[], Iterable[Path]] | None = None, *, directory: bool = False,
               roots: list[Path] | None = None) -> Located:
        """The existing file (or folder, with `directory=True`) a name or path refers to, sorted into inside and outside the allowed folders.

        An absolute path is taken as it is; a relative one is tried under each folder. When nothing is found that way and the name is relative, `search()`
        supplies the candidates (a server's listing, already narrowed to the ones that match the name). Every candidate is resolved before it is judged.
        """
        allowed = roots or self.roots()
        given = Path(name).expanduser()
        found: dict[Path, None] = {}  # no repeats, in the order found
        for candidate in [given] if given.is_absolute() else [root / given for root in allowed]:
            resolved = candidate.resolve()
            if resolved.is_dir() if directory else resolved.is_file():
                found[resolved] = None
        if not found and not given.is_absolute() and search is not None:
            found = dict.fromkeys(p.resolve() for p in search())
        return Located(inside=[p for p in found if inside(p, allowed)], outside=[p for p in found if not inside(p, allowed)])
