"""CTRE's device firmware index (`firmware-index.json`), read from CTRE Tuner X's saved copy.
Seen on a real Windows machine: Tuner X keeps it and the `.crf` files it downloaded in `%LOCALAPPDATA%/Packages/CTRElectronics.*/LocalCache`.
Shape seen: `{"Version", "Latest": {"v6": "2026", ...}, "Releases": {"v6": {"<season>": [{"Device", "LatestRelease", "LatestVersion",
"CriticalVersion"?, "Errata"?, "AvailRelease": [...]}]}}}`. Read from disk only: nothing here goes to the network (Tuner X fetches it
from CTRE's GitHub, a host this package is not allowed to talk to).
"""

import json
import logging
from pathlib import Path
from typing import Any

from .store import tunerx_dirs

_log = logging.getLogger(__name__)


_PARSED: dict[tuple[str, int, int], Any] = {}  # (path, size, mtime) -> parsed file: it is read again only when it changed


def _read(path: Path) -> Any:
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _PARSED:
        _PARSED[key] = json.loads(path.read_text(encoding="utf-8"))
    return _PARSED[key]


def load_firmware_index() -> dict[str, Any] | None:
    """The first readable saved firmware index, or None when Tuner X has not saved one (or it is unreadable)."""
    for folder in tunerx_dirs():
        path = folder / "firmware-index.json"
        try:
            data = _read(path)
        except (OSError, ValueError) as e:  # not there, unreadable, or not JSON
            _log.debug("skipped %s: %s", path, e)
            continue
        if isinstance(data, dict) and isinstance(data.get("Releases"), dict):
            return data
    return None


def device_releases(index: dict[str, Any] | None, device: str, line: str = "v6", seasons: int = 3) -> dict[str, dict[str, str]]:
    """For a device, the newest `seasons` seasons of the index as `{season: {"latest": version, "file": name, "critical"?: version, "errata"?: text}}`."""
    if index is None:
        return {}
    out: dict[str, dict[str, str]] = {}
    for season, entries in (index.get("Releases", {}).get(line) or {}).items():
        if len(out) >= seasons:
            break
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and str(entry.get("Device", "")).lower() == device.lower() and entry.get("LatestVersion"):
                row = {"latest": str(entry["LatestVersion"]), "file": str(entry.get("LatestRelease", ""))}
                if entry.get("CriticalVersion"):
                    row["critical"] = str(entry["CriticalVersion"])
                if entry.get("Errata"):
                    row["errata"] = str(entry["Errata"])[:160]
                out[str(season)] = row
                break
    return out
