"""The controller's log folder: listing it, and the rules that choose what may be pruned.
/home/systemcore/logs holds `.hoot` files (CTRE Phoenix, one folder per session) and `.wpilog` files (`WPILIB_<UTC date>_<time>`,
or `WPILIB_TBD_<hex>` when the clock was not set). The controller's clock is often wrong (no RTC, NTP off), so age comes from
the name where it has one, and file dates are only a fallback.
"""

import contextlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from . import config, state
from .errors import SystemcoreError
from .ssh import Session

ROOT = "/home/systemcore/logs"
MAX_DEPTH = 2
Kind = Literal["wpilog", "hoot", "other"]

_WPILOG = re.compile(r"WPILIB_(\d{8})_(\d{6})")
_STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})")


@dataclass(frozen=True)
class Entry:
    path: str  # relative to ROOT, forward slashes
    size: int
    mtime: float
    kind: Kind
    named_at: float | None  # seconds since the epoch read from the name, if it has one
    active: bool = False

    @property
    def sort_key(self) -> float:
        return self.named_at if self.named_at is not None else self.mtime


def kind_of(name: str) -> Kind:
    lower = name.lower()
    if lower.endswith(".wpilog"):
        return "wpilog"
    if lower.endswith(".hoot"):
        return "hoot"
    return "other"


def name_time(path: str) -> float | None:
    """The time in a log's name (the file's, else its folder's), as UTC seconds; None for `TBD` and other names."""
    for part in reversed(path.split("/")):
        if (m := _WPILOG.search(part) or _STAMP.search(part)) is None:
            continue
        try:
            return datetime.strptime("".join(m.groups()), "%Y%m%d%H%M%S").replace(tzinfo=UTC).timestamp()
        except ValueError:
            return None
    return None


def is_safe_relative(path: str) -> bool:
    """A path from a listing is used for deleting and downloading: no escapes, no absolute paths, no odd characters."""
    return bool(path) and not path.startswith("/") and ".." not in path.split("/") and re.fullmatch(r"[A-Za-z0-9_./-]+", path) is not None


def select_prune(entries: list[Entry], *, kind: Kind | None, keep_newest: int | None, max_total_bytes: int | None,
                 older_than_s: float | None, now: float, protected: set[str] | None = None) -> list[Entry]:
    """The entries to delete: a file goes when any rule marks it, except the newest of each kind and every active file.
    `protected` paths (for example not yet downloaded) stay too.
    keep_newest      keep this many newest files of each kind
    max_total_bytes  keep the newest files whose sizes add up to this much, per kind
    older_than_s     delete files older than this (by name time, else file date, against `now`)
    """
    if keep_newest is None and max_total_bytes is None and older_than_s is None:
        raise ValueError("give at least one rule")
    chosen: list[Entry] = []
    for k in ("wpilog", "hoot", "other"):
        if kind is not None and k != kind:
            continue
        group = sorted((e for e in entries if e.kind == k), key=lambda e: e.sort_key, reverse=True)
        total = 0
        for rank, entry in enumerate(group):
            total += entry.size
            if rank == 0 or entry.active or (protected and entry.path in protected):
                continue
            over_count = keep_newest is not None and rank >= keep_newest
            over_size = max_total_bytes is not None and total > max_total_bytes
            too_old = older_than_s is not None and now - entry.sort_key > older_than_s
            if over_count or over_size or too_old:
                chosen.append(entry)
    return chosen


# ---- reading the folder through an SSH session ----

ACTIVE_COMMAND = "ls -l /proc/[0-9]*/fd 2>/dev/null | grep -o '/home/systemcore/logs/[^ ]*' | sort -u"


def active_paths(output: str) -> set[str]:
    """Relative paths of log files some process has open, from the output of ACTIVE_COMMAND."""
    return {line.strip().removeprefix(ROOT + "/") for line in output.splitlines() if line.strip().startswith(ROOT + "/")}


def list_entries(session: Session) -> list[Entry]:
    """Every file in the log folder (two folder levels deep), marking the ones a process is writing right now."""
    _, out, _ = session.run(ACTIVE_COMMAND, timeout=15)
    active = active_paths(out)
    entries: list[Entry] = []

    def walk(folder: str, prefix: str, depth: int) -> None:
        for attr in session.listdir(folder):
            name = attr.filename
            rel = f"{prefix}{name}"
            if not is_safe_relative(rel):
                continue
            if attr.st_mode is not None and (attr.st_mode & 0o170000) == 0o040000:
                if depth < MAX_DEPTH:
                    walk(f"{folder}/{name}", f"{rel}/", depth + 1)
            elif attr.st_mode is not None and (attr.st_mode & 0o170000) == 0o100000:  # regular files only: never follow links
                entries.append(Entry(rel, attr.st_size or 0, float(attr.st_mtime or 0), kind_of(name), name_time(rel), rel in active))

    walk(ROOT, "", 1)
    return entries


def row(entry: Entry) -> dict[str, Any]:
    """One log as the tools show it."""
    out: dict[str, Any] = {"path": entry.path, "mb": round(entry.size / 1e6, 2), "kind": entry.kind}
    if entry.named_at is None:
        out["no_time_in_name"] = True
    if entry.active:
        out["active"] = True
    return out


def newest_first(entries: list[Entry]) -> list[Entry]:
    return sorted(entries, key=lambda e: e.sort_key, reverse=True)


def download(session: Session, host: str, entries: list[Entry], *, paths: list[str] | None, newest: int | None, kind: Kind | None) -> list[dict[str, Any]]:
    """Copy log files into the download folder (one folder per controller) and remember their sizes.
    A path that is not in `entries` is reported, never used, so there is no path to escape with. Without `paths`, the
    `newest` N files of `kind` are taken.
    """
    by_path = {e.path: e for e in entries}
    wanted = paths or [e.path for e in newest_first([e for e in entries if kind is None or e.kind == kind])[: newest or 0]]
    saved: list[dict[str, Any]] = []
    sizes: dict[str, int] = {}
    for path in wanted:
        if path not in by_path:
            saved.append({"path": path, "err": "not in the Systemcore log folder"})
            continue
        destination = config.download_dir() / host / path
        size = session.get(f"{ROOT}/{path}", destination)
        sizes[f"{host}/{path}"] = size
        saved.append({"path": path, "saved": str(destination), "mb": round(size / 1e6, 2)})
    if sizes:
        state.record_downloads(sizes)
    return saved


def plan_prune(entries: list[Entry], host: str, *, kind: Kind | None, keep_newest: int | None, max_total_gb: float | None,
               older_than_days: float | None, only_downloaded: bool, now: float) -> tuple[list[Entry], set[str]]:
    """The files the rules would delete, and the files held back only because they were not downloaded here yet."""
    known = state.load("downloads.json") if only_downloaded else {}
    protected = {e.path for e in entries if only_downloaded and known.get(f"{host}/{e.path}") != e.size}
    chosen = select_prune(entries, kind=kind, keep_newest=keep_newest, max_total_bytes=None if max_total_gb is None else int(max_total_gb * 1e9),
                          older_than_s=None if older_than_days is None else older_than_days * 86400, now=now, protected=protected)
    return chosen, protected


def delete(session: Session, chosen: list[Entry]) -> tuple[int, list[dict[str, str]]]:
    """Delete the chosen files, then any session folder left empty (a folder with anything in it stays). Returns (deleted, failures)."""
    deleted, failed = 0, []
    folders: set[str] = set()
    for entry in chosen:
        try:
            session.remove(f"{ROOT}/{entry.path}")
        except OSError as e:
            failed.append({"path": entry.path, "err": str(e)})
            continue
        deleted += 1
        if "/" in entry.path:
            folders.add(entry.path.rsplit("/", 1)[0])
    for folder in sorted(folders):
        with contextlib.suppress(OSError):
            session.rmdir(f"{ROOT}/{folder}")
    return deleted, failed


_SERVICE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")


def journal_command(lines: int, service: str | None, priority: str | None, boot: int) -> str:
    """The fixed journalctl command line for the journal tool: only a unit name and a priority come from the caller."""
    if service is not None and not _SERVICE.match(service):
        raise SystemcoreError("service must be a systemd unit name such as robot or limelight_hwmon.")
    return f"journalctl -b {boot} --no-pager -q -o short-iso -n {lines}" + (f" -u {service}" if service else "") + (f" -p {priority}" if priority else "")
