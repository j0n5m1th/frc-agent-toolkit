"""Folders a CTRE server may read files from or write new files to, for hoot, MIDI, CHRP files and firmware.
A `Folders` is built from an environment setting (several folders separated by `;` on Windows, `:` elsewhere; the folder the server
started in when unset) and file suffixes. Nothing outside them is ever opened or written. A bare name is looked up under the
folders. Outputs follow `new_output_path`: beside their input, never over a file.
"""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from frc_mcp_core.folders import (
    AllowedFolders,
    check_size,
    file_row,
    newest_first,
    walk_folders,
)
from frc_mcp_core.settings import env_megabytes

from .errors import CtreError

_log = logging.getLogger(__name__)


class Folders:
    """The allowed folders for one kind of file.
    `env_name` lists them; `suffixes` are the file endings (lower case, with the dot); `noun` names the file in errors; `list_tool`
    lists them; `max_mb_env` optionally caps a file's size (default `default_max_mb`); `default_roots` is where to look when
    unset instead of the start folder (`default_label` names it in the hint); `recursive=False` looks only directly in each folder.
    """

    def __init__(self, env_name: str, suffixes: tuple[str, ...], noun: str, list_tool: str, *, max_mb_env: str | None = None,
                 default_max_mb: float = 1024, default_roots: Callable[[], list[Path]] | None = None, default_label: str | None = None,
                 recursive: bool = True) -> None:
        self.env_name, self.suffixes, self.noun, self.list_tool = env_name, suffixes, noun, list_tool
        self.max_mb_env, self.default_max_mb, self.default_roots, self.default_label = max_mb_env, default_max_mb, default_roots, default_label
        self.recursive = recursive
        self.allowed = AllowedFolders(env_name, CtreError)  # the shared rule: which folders, and what is inside them

    def roots(self) -> list[Path]:
        return self.allowed.roots(self.default_roots)

    def notice(self) -> str | None:
        """A hint to show while the setting is unset: where files are being looked for instead."""
        if self.allowed.is_set():
            return None
        roots = self.roots()
        if self.default_label and self.default_roots is not None and roots != [Path.cwd().resolve()]:
            return f"{self.env_name} is not set, so {self.noun}s are read from {self.default_label}."
        where = ", ".join(str(r) for r in roots)
        return f"{self.env_name} is not set, so {self.noun}s are looked for in {where}. Set {self.env_name} to the folder you mean."

    def max_bytes(self) -> int | None:
        if self.max_mb_env is None:
            return None
        return env_megabytes(self.max_mb_env, self.default_max_mb, 1, 1048576, CtreError)

    def inside(self, path: Path, roots: list[Path] | None = None) -> bool:
        return self.allowed.inside(path, roots or self.roots())

    def _matches_suffix(self, name: str) -> bool:
        return name.lower().endswith(self.suffixes)

    def find(self, name_contains: str | None = None) -> list[Path]:
        """Every matching file under the folders (a few folders deep), newest first."""
        found: set[Path] = set()
        needle = (name_contains or "").lower()
        if self.recursive:
            for folder, files in walk_folders(self.roots()):
                found |= {folder / f for f in files if self._matches_suffix(f) and needle in f.lower()}
        else:
            for root in self.roots():
                found |= self._top_level(root, needle)
        return newest_first(found)

    def _top_level(self, root: Path, needle: str) -> set[Path]:
        try:
            return {p for p in root.iterdir() if self._matches_suffix(p.name) and needle in p.name.lower() and p.is_file()}
        except OSError as e:
            _log.debug("%s could not be listed: %s", root, e)
            return set()

    def resolve(self, name: str) -> Path:
        """The file a name or path refers to, checked to be inside an allowed folder, of the right kind, and within the size limit."""
        allowed = self.roots()
        where = ", ".join(str(r) for r in allowed)
        shown = "/".join(self.suffixes)
        if not self._matches_suffix(name):
            raise CtreError(f"Only {shown} files can be read; {name!r} is not one.")
        given = Path(name).expanduser()
        located = self.allowed.locate(name, lambda: (p for p in self.find() if p.name == given.name), roots=allowed)
        matches = set(located.inside)
        if located.outside:
            raise CtreError(f"{name!r} is outside the folders this server may read ({where}). Put a copy in your project folder (or open Claude Code there); from a clone, set {self.env_name}.")
        if not matches:
            raise CtreError(f"No {self.noun} named {name!r} under {where}. Use {self.list_tool} to see them.")
        if len(matches) > 1:
            raise CtreError(f"{name!r} matches {len(matches)} {self.noun}s; give a longer path: " + ", ".join(str(p) for p in sorted(matches)[:3]))
        path = matches.pop()
        limit = self.max_bytes()
        if limit is not None and self.max_mb_env is not None:
            check_size(path, limit, self.max_mb_env, CtreError, "read this file")
        return path

    async def resolve_async(self, name: str) -> Path:
        """`resolve` off the event loop: finding a bare name walks the folders, which can take a while on a big tree."""
        return await asyncio.to_thread(self.resolve, name)

    def describe(self, label: str) -> list[str]:
        """The lines a server's `check` command prints about this setting: where files are looked for, how many were found, and the hint."""
        lines = [f"{label}: {', '.join(str(r) for r in self.roots())} ({len(self.find())} found)"]
        if notice := self.notice():
            lines.append(f"  Note: {notice}")
        return lines

    def listing(self, name_contains: str | None, limit: int, extra: Callable[[Path], dict[str, Any]] | None = None, *, extra_cols: tuple[str, ...] = ()) -> dict[str, Any]:
        """The reply every list tool gives, in columns (the column names are said once, not once per file): `total`, `cols`, `rows`, `omitted` and `hint`.

        A row is `[file (relative to a folder), mb, modified, *extra(path) values]`; `extra_cols` names the extra columns, `extra` returns a dict with those keys.
        """
        found = self.find(name_contains)
        roots = self.roots()
        rows = []
        for path in found[:limit]:
            row = file_row(path, roots)
            more = extra(path) if extra is not None else {}
            rows.append([row["file"], row["mb"], row["modified"], *(more.get(col) for col in extra_cols)])
        out: dict[str, Any] = {"total": len(found), "cols": ["file", "mb", "modified", *extra_cols], "rows": rows}
        if len(found) > limit:
            out["omitted"] = len(found) - limit
        if notice := self.notice():
            out["hint"] = notice
        return out


_RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", *(f"{d}{n}" for d in ("COM", "LPT") for n in "123456789")})


def new_output_path(source: Path, out: str | None, suffix: str, *, default_stem: str | None = None) -> Path:
    """Where a converted file goes: beside `source`, named `out` (a plain file name) or `<stem><suffix>`, never over an existing file.

    `suffix` includes the dot and is added when `out` lacks it. A name with a folder part, or one that is `.` or `..`, is refused.
    """
    name = out or f"{default_stem or source.stem}{suffix}"
    if Path(name).name != name or name in ("", ".", "..") or ":" in name or "\\" in name:
        raise CtreError("out must be a plain file name (no folder, no ':'); the converted file is written next to the input file.")
    if name.split(".")[0].rstrip(" ").upper() in _RESERVED_NAMES or name.endswith((" ", ".")):
        raise CtreError(f"{name!r} is not a usable file name (a reserved Windows device name, or it ends in a space or dot).")
    if not name.lower().endswith(suffix.lower()):
        name = f"{name}{suffix}"
    target = source.parent / name
    if target.exists():
        raise CtreError(f"{name} already exists next to {source.name}; it is never overwritten. Choose another `out` name.")
    return target
