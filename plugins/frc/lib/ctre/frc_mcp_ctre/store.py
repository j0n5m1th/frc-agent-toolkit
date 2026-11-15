"""The CTRE tool versions installed on this computer.
Three places are read:
- This package's own folder (`<CTRE_TOOLS_DIR>/<folder>/<version>/<tool>-<version>-<platform>[.exe]`). Installs go only here.
- Tuner X's private `caniv.exe` in its app data (read only). It has no version in its name, so the version is found by matching its
  hash against the index (seen: same size as the `caniv` 1.1.1.0 file). Tuner X's other tools sit in a protected app folder and
  cannot be run from here.
- AdvantageScope's private Owlet copies (read only): `<AdvantageScope data folder>/owlet/owlet-<version>-C<compliancy>[.exe]`
  (seen on a real Windows machine; `C<n>` is the compliancy).
A file is run only when verified (`Installed.problem` is None):
- In our folder, a file from `ctre_install` has a `<file>.sha256` record (written after the download matched CTRE's hashes) and must
  still match it. A file with no record (placed by hand or by an older version) is trusted once, and given a record, only when it
  matches a hash CTRE published for that version.
- AdvantageScope's Owlet and Tuner X's caniv must match CTRE's published hash for their version every time they are read.
An unverified file is listed (so `ctre_status` can say so) but never run. A record next to the file cannot stop someone who can edit
both; it catches a damaged, swapped or mistakenly edited file, and one that never came from CTRE.
"""

import hashlib
import logging
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from frc_mcp_core.settings import env_str
from frc_mcp_wpilib.files import write_atomic

from . import catalog, config
from .errors import CtreError
from .index import Download, Index, ToolVersion, safe_version
from .versions import try_parse_version

_log = logging.getLogger(__name__)
SIDECAR_SUFFIX = ".sha256"
Source = Literal["ctre", "advantagescope", "tunerx"]
_ADVANTAGESCOPE_NAME = re.compile(r"^owlet-(?P<version>\d+(?:\.\d+){1,3}(?:-[0-9A-Za-z.\-]+?)?)-C(?P<compliancy>\d+)(?:\.exe)?$")
_OUTSIDE_PROBLEM = ("{whose} does not match the hash CTRE published for {what}, so it is not run. Install CTRE's own copy "
                    "with ctre_install (ctre-tools server, needs CTRE_ALLOW_INSTALL=1): that is the only way to enable it.")


@dataclass(frozen=True)
class Installed:
    tool: str
    version: str
    path: Path
    source: Source
    compliancy: int | None = None  # only known from the file name (AdvantageScope's copies); otherwise ask the index
    problem: str | None = None  # why this file must not be run (it is not verified); None for a file that may be run

    @property
    def verified(self) -> bool:
        return self.problem is None


def advantagescope_owlet_dir() -> Path | None:
    """AdvantageScope's Owlet folder; CTRE_ADVANTAGESCOPE_DIR (the AdvantageScope data folder) overrides, and `off` turns this source off."""
    configured = env_str("CTRE_ADVANTAGESCOPE_DIR")
    if configured and configured.lower() == "off":
        return None
    if configured:
        return Path(configured).expanduser() / "owlet"
    home = Path.home()
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "AdvantageScope" / "owlet"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "AdvantageScope" / "owlet"
    return home / ".config" / "AdvantageScope" / "owlet"


def tunerx_dirs() -> list[Path]:
    """Tuner X's app-data folders (`<Packages>/CTRElectronics.*/LocalCache`); CTRE_TUNERX_DIR names the Packages folder, `off` turns this source off."""
    configured = env_str("CTRE_TUNERX_DIR")
    if configured and configured.lower() == "off":
        return []
    if configured:
        packages = Path(configured).expanduser()
    elif sys.platform == "win32":
        packages = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Packages"
    else:
        return []  # Tuner X is a Windows app here; its data folders elsewhere are unknown
    return sorted(packages.glob("CTRElectronics.*/LocalCache")) if packages.is_dir() else []


_DIGESTS: dict[tuple[str, int, int], dict[str, str]] = {}  # (path, size, mtime_ns) -> digests: a file is only read again when it changed


def hash_all(path: Path) -> dict[str, str]:
    """The md5, sha1 and sha256 of a file, read once in chunks (CTRE publishes md5 and sha1; the record next to an install is sha256)."""
    hashes = {name: hashlib.new(name, usedforsecurity=False) for name in ("md5", "sha1", "sha256")}
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            for h in hashes.values():
                h.update(chunk)
    return {name: h.hexdigest() for name, h in hashes.items()}


def file_digests(path: Path) -> dict[str, str]:
    """`hash_all`, remembered while the file's path, size and modification time stay the same (a check runs on every tool call). Any change reads it again."""
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _DIGESTS:
        for old in [k for k in _DIGESTS if k[0] == key[0]]:
            del _DIGESTS[old]  # the file changed: forget what was known of it
        _DIGESTS[key] = hash_all(path)
    return _DIGESTS[key]


def sidecar_path(path: Path) -> Path:
    return path.with_name(path.name + SIDECAR_SUFFIX)


def read_sidecar(path: Path) -> str | None:
    """The sha256 recorded for an installed file, or None when there is no (readable, well-formed) record."""
    try:
        words = sidecar_path(path).read_text(encoding="utf-8").split()
    except OSError:
        return None
    return words[0].lower() if words and re.fullmatch(r"[0-9a-fA-F]{64}", words[0]) else None


def write_sidecar(path: Path, sha256: str) -> bool:
    """Record the sha256 of an installed file next to it; False (and nothing worse) when the record cannot be written."""
    try:
        write_atomic(sidecar_path(path), f"{sha256}  {path.name}\n")
    except OSError as e:
        _log.debug("could not record the hash of %s: %s", path, e)
        return False
    return True


def published_hashes(download: Download | None) -> dict[str, str]:
    """The md5 and sha1 CTRE published for a download (lower case); empty when it published none."""
    return {k: v.lower() for k, v in (("md5", download.md5), ("sha1", download.sha1)) if v} if download else {}


def matches_download(download: Download | None, digests: dict[str, str]) -> bool:
    """True when CTRE published at least one hash for this download and the file matches every one it published."""
    published = published_hashes(download)
    return bool(published) and all(digests[k] == v for k, v in published.items())


def matches_published(version: ToolVersion | None, digests: dict[str, str]) -> bool:
    """True when the file is the published file of any platform build of this tool version (a private copy does not say which platform it is for)."""
    return version is not None and any(matches_download(version.download(p), digests) for p in version.platforms())


def _scan_tunerx(tool: str, index: Index | None) -> list[Installed]:
    """Tuner X's own copy of a tool, identified by hash (so it is only used when the index knows that exact file; any other copy is listed as unverified)."""
    if index is None or tool.lower() != "caniv":
        return []
    found = []
    for folder in tunerx_dirs():
        exe = folder / "caniv.exe"
        if not exe.is_file():
            continue
        try:
            digests = file_digests(exe)
        except OSError as e:
            found.append(Installed(tool, "unknown", exe, "tunerx", problem=f"could not be read ({e})"))
            continue
        for item in index.versions(tool):
            if matches_download(item.download("windowsx86-64"), digests):
                found.append(Installed(tool, item.version, exe, "tunerx"))
                break
        else:
            found.append(Installed(tool, "unknown", exe, "tunerx", problem=_OUTSIDE_PROBLEM.format(whose="Tuner X's copy of caniv", what="any version of caniv")))
    return found


def _is_hash_file(name: str) -> bool:
    return name.endswith((".md5", ".sha1", SIDECAR_SUFFIX, ".part", ".tmp"))


def _scan_ctre(tool: str) -> list[Installed]:
    base = config.tools_dir() / catalog.info(tool).folder
    found: list[Installed] = []
    if not base.is_dir():
        return found
    for folder in sorted(base.iterdir()):
        if not folder.is_dir() or try_parse_version(folder.name) is None:
            continue
        for file in sorted(folder.iterdir()):
            if file.is_file() and file.name.startswith(f"{tool}-{folder.name}-") and not _is_hash_file(file.name):
                found.append(Installed(tool, folder.name, file, "ctre"))
                break
    return found


def _scan_advantagescope(tool: str) -> list[Installed]:
    base = advantagescope_owlet_dir()
    if tool.lower() != "owlet" or base is None or not base.is_dir():
        return []
    found = []
    for file in sorted(base.iterdir()):
        match = _ADVANTAGESCOPE_NAME.match(file.name)
        if match and file.is_file():
            found.append(Installed("owlet", match["version"], file, "advantagescope", int(match["compliancy"])))
    return found


def _check_own(item: Installed, index: Index | None) -> str | None:
    """Why a file in this package's own folder must not run, or None. The record written at install is the rule; a file without one is trusted once, only when published."""
    try:
        digests = file_digests(item.path)
    except OSError as e:
        return f"could not be read ({e})"
    if (recorded := read_sidecar(item.path)) is not None:
        if recorded == digests["sha256"]:
            return None
        return (f"has changed since it was installed (its sha256 no longer matches {sidecar_path(item.path).name}), so it is not run. "
                f"Replace it with CTRE's copy: ctre_install tool={item.tool} version={item.version} (ctre-tools server, needs CTRE_ALLOW_INSTALL=1).")
    published = index.find(item.tool, item.version) if index is not None else None
    if matches_published(published, digests):
        write_sidecar(item.path, digests["sha256"])  # first use of a file CTRE published (installed by an earlier version, or copied here): remember it
        return None
    return (f"was not installed by this server and does not match a hash CTRE published for {item.tool} {item.version}, so it is not run. "
            f"Install CTRE's copy with ctre_install tool={item.tool} version={item.version} (ctre-tools server, needs CTRE_ALLOW_INSTALL=1), or move this file away.")


def _check_outside(item: Installed, index: Index | None) -> str | None:
    """Why a copy kept by another program (AdvantageScope) must not run, or None: it must be a file CTRE published for its version."""
    try:
        digests = file_digests(item.path)
    except OSError as e:
        return f"could not be read ({e})"
    published = index.find(item.tool, item.version) if index is not None else None
    if matches_published(published, digests):
        return None
    what = f"{item.tool} {item.version}" if published else f"{item.tool} {item.version} (CTRE's list does not have it)"
    return _OUTSIDE_PROBLEM.format(whose=f"AdvantageScope's copy of {item.tool} {item.version}", what=what)


def _verified(item: Installed, index: Index | None) -> Installed:
    if item.problem is not None or item.source == "tunerx":  # a Tuner X copy was found by its hash already
        return item
    problem = _check_own(item, index) if item.source == "ctre" else _check_outside(item, index)
    return replace(item, problem=problem) if problem else item


def scan(tools: Iterable[str], index: Index | None = None) -> dict[str, list[Installed]]:
    """For each tool name, every copy found, newest first, each checked (`problem` says why one must not run).

    This package's own folder wins when sources share a version, unless its copy is unverified and another is not. `index` identifies Tuner X's copy of caniv
    by hash and gives the published hashes every copy is checked against; without it only a copy that has an install record passes.
    """
    result: dict[str, list[Installed]] = {}
    for tool in tools:
        by_version: dict[str, Installed] = {}
        for found in [*_scan_tunerx(tool, index), *_scan_advantagescope(tool), *_scan_ctre(tool)]:  # later (this package's own folder) replaces earlier
            item = _verified(found, index)
            earlier = by_version.get(item.version)
            if earlier is None or item.verified or not earlier.verified:
                by_version[item.version] = item
        ordered = sorted(by_version.values(), key=lambda i: (try_parse_version(i.version) or try_parse_version("0.0")).sort_key(), reverse=True)  # type: ignore[union-attr]
        result[tool] = ordered
    return result


def installed(tools: Iterable[str], index: Index | None = None) -> dict[str, list[Installed]]:
    """`scan` without the copies that must not run: for each tool name, its verified versions, newest first. What a server may run comes from here."""
    return {tool: [i for i in items if i.verified] for tool, items in scan(tools, index).items()}


def target_path(item: ToolVersion, platform: str) -> Path:
    """Where this package installs a version: its own folder layout, the file named as in the download url."""
    download = item.download(platform)
    if download is None:
        raise CtreError(f"{item.tool} {item.version} has no build for {platform} (it has: {', '.join(item.platforms()) or 'none'}).")
    name = Path(urlparse(download.url).path).name
    if not name or name != Path(name).name:
        raise CtreError(f"CTRE's index gives an unusable file name for {item.tool} {item.version}.")
    return config.tools_dir() / catalog.info(item.tool).folder / safe_version(item.version, item.tool) / name
