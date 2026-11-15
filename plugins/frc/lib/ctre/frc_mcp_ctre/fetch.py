"""Get CTRE's index and download tools, safely.
The index comes from, in order: `CTRE_INDEX_FILE`; a saved copy younger than `CTRE_INDEX_MAX_AGE_H`; the network (then saved); an
older saved copy; the snapshot shipped in this package (so every tool works offline). Every URL, including each redirect and each
download link in the index, must be https on a host in `config.ALLOWED_HOSTS`: the index is data, not a list of places to go.
A download is kept only when its size is within the limit and it matches a hash CTRE published for it.
"""

import contextlib
import functools
import logging
import os
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib import resources
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlparse

from frc_mcp_wpilib.files import write_atomic

from . import config
from .errors import CtreError
from .index import Index, ToolVersion, parse_index
from .store import (
    file_digests,
    hash_all,
    matches_download,
    published_hashes,
    read_sidecar,
    target_path,
    write_sidecar,
)

_log = logging.getLogger(__name__)
_pace_lock = threading.Lock()
_last_request = 0.0  # time.monotonic() of the latest request to CTRE


class NotModified(Exception):
    """The server says the saved copy is still current (HTTP 304)."""


def _pace() -> None:
    """Wait until CTRE_REQUEST_DELAY_S has passed since the previous request, so a batch of downloads never hammers CTRE's server."""
    global _last_request
    with _pace_lock:  # held while sleeping: requests go one at a time, in order
        wait = config.request_delay_s() - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
MAX_INDEX_BYTES = 5 * 2**20
KNOWN_JSON_VERSION = "1.0.0.0"  # the index format this package was written against (AdvantageScope refuses any other)
MIN_FREE_BYTES = 200 * 2**20  # a download needs this much free space on top of its own size
_USER_AGENT = "mcp-ctre-tools"


@dataclass(frozen=True)
class IndexResult:
    index: Index
    source: str  # "file" | "cache" | "network" | "stale-cache" | "snapshot"
    age_s: float | None  # how old the data is, when known
    note: str | None = None


def check_url(url: str) -> str:
    """The url unchanged if it is https on an allowed host; otherwise a CtreError."""
    parts = urlparse(url)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in config.ALLOWED_HOSTS:
        raise CtreError(f"Refusing {url!r}: only https links on {', '.join(sorted(config.ALLOWED_HOSTS))} are used.")
    return url


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(url: str, sink: BinaryIO, limit: int, timeout: float, *, etag: str | None = None) -> str | None:
    """Copy the body of `url` into `sink`, refusing more than `limit` bytes; returns the answer's ETag. The one place this package touches the network.

    Paced (`_pace`), never retried. With `etag` the server may answer 304, which raises NotModified.
    """
    check_url(url)
    headers = {"User-Agent": _USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers)
    _pace()
    opener = urllib.request.build_opener(_CheckedRedirects)
    try:
        with opener.open(request, timeout=timeout) as response:
            if (length := response.headers.get("Content-Length")) and length.isdigit() and int(length) > limit:
                raise CtreError(f"{url} is {int(length) / 2**20:.0f} MB, over the CTRE_MAX_DOWNLOAD_MB limit.")
            total = 0
            while chunk := response.read(1 << 16):
                total += len(chunk)
                if total > limit:
                    raise CtreError(f"{url} is bigger than the allowed {limit / 2**20:.0f} MB.")
                sink.write(chunk)
            return response.headers.get("ETag")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            raise NotModified from e
        slow = " It is asking for fewer requests: wait a few minutes before trying again." if e.code in (429, 503) else ""
        raise CtreError(f"CTRE's server answered {e.code} for {url}.{slow}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise CtreError(f"Could not reach {url}: {getattr(e, 'reason', e)}.") from e


def _cache_file() -> Path:
    return config.tools_dir() / ".mcp-ctre" / "index.json"


def _etag_file() -> Path:
    return _cache_file().with_name("index.etag")


def _read_etag() -> str | None:
    try:
        return _etag_file().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _read_cache() -> tuple[Index, float] | None:
    path = _cache_file()
    try:
        return parse_index(path.read_bytes()), max(0.0, time.time() - path.stat().st_mtime)
    except (OSError, CtreError) as e:
        _log.debug("no usable saved index at %s: %s", path, e)
        return None


@functools.cache
def snapshot() -> Index:
    """The index as of when this package was written. Out of date by design: only the last resort. Parsed once (an `Index` is never changed)."""
    return parse_index(resources.files("frc_mcp_ctre").joinpath("data/ctre-index.snapshot.json").read_bytes())


def _age(seconds: float) -> str:
    return f"{seconds / 86400:.0f} days" if seconds >= 2 * 86400 else f"{seconds / 3600:.0f} hours"


def load_index(*, refresh: bool = False, offline: bool = False) -> IndexResult:
    """The best index available now (see the module docstring). `refresh` ignores a fresh saved copy; `offline` never uses the network.

    A format version other than the known one is not refused (the parser already fails loudly on a changed shape) but is reported in `note`.
    """
    result = _load_index(refresh=refresh, offline=offline)
    if result.index.json_version != KNOWN_JSON_VERSION:
        warning = f"CTRE's index format is version {result.index.json_version!r}; this server was written for {KNOWN_JSON_VERSION!r}, so check its answers."
        return IndexResult(result.index, result.source, result.age_s, f"{result.note} {warning}" if result.note else warning)
    return result


def _load_index(*, refresh: bool, offline: bool) -> IndexResult:
    if (override := config.index_file()) is not None:
        try:
            return IndexResult(parse_index(override.read_bytes()), "file", None)
        except OSError as e:
            raise CtreError(f"CTRE_INDEX_FILE {override} cannot be read: {e}") from e
    cached = _read_cache()
    if cached and not refresh and (offline or cached[1] < config.index_max_age_s()):
        return IndexResult(cached[0], "cache", cached[1])
    problem = None
    if not offline:
        try:
            raw = BytesIO()
            url = config.index_url()
            known = _read_etag() if cached else None  # ask "has it changed?" only when a saved copy exists to fall back on
            new_etag = http_get(url, raw, MAX_INDEX_BYTES, config.timeout_s(), **({"etag": known} if known else {}))
            fresh = parse_index(raw.getvalue())
        except NotModified:
            assert cached is not None
            with contextlib.suppress(OSError):
                os.utime(_cache_file())  # still current: count it as freshly fetched
            return IndexResult(cached[0], "cache", 0.0)
        except CtreError as e:
            problem = str(e)
        else:
            try:
                path = _cache_file()
                path.parent.mkdir(parents=True, exist_ok=True)
                write_atomic(path, raw.getvalue())
                if new_etag:
                    write_atomic(_etag_file(), new_etag)
            except OSError as e:
                _log.debug("could not save the index: %s", e)
            return IndexResult(fresh, "network", 0.0)
    if cached:
        note = f"Could not refresh CTRE's index ({problem}); using the copy saved {_age(cached[1])} ago." if problem else None
        return IndexResult(cached[0], "stale-cache", cached[1], note)
    why = f" ({problem})" if problem else ""
    return IndexResult(snapshot(), "snapshot", None, f"CTRE's live index was not used{why}; this is the list bundled with the server, so newer releases may be missing.")


def is_installed(item: ToolVersion, platform: str) -> bool:
    """True when the file at this version's target path is CTRE's published file (it is then given an install record if it has none).

    False when nothing is there, or when the file is one this server installed that has since changed (a download replaces it). Raises CtreError for a file
    that differs from CTRE's and has no install record here: it may be the user's own, so it is never replaced silently.
    """
    target = target_path(item, platform)
    if not target.is_file():
        return False
    try:
        digests = file_digests(target)
    except OSError as e:
        raise CtreError(f"{target} cannot be read: {e}") from e
    recorded = read_sidecar(target)
    if matches_download(item.download(platform), digests):
        if recorded != digests["sha256"]:
            write_sidecar(target, digests["sha256"])
        return True
    if recorded is None:
        raise CtreError(f"{target} is already there but is not the file CTRE published for {item.tool} {item.version}, and this server did not install it, so it is not replaced. "
                        "Move or delete it, then install again.")
    return not published_hashes(item.download(platform)) and recorded == digests["sha256"]  # with no published hash, the file this server installed counts; a changed one is replaced


def download_tool(item: ToolVersion, platform: str) -> Path:
    """Download one version into the tools folder and return the file.
    An existing file is kept only when it matches CTRE's published hashes (`is_installed`); a damaged copy this server installed
    is replaced, and a different file it did not install is refused. The download must match every hash CTRE publishes (md5 and
    sha1), and at least one must exist. A sha256 record written next to the file is checked by every later run.
    """
    target = target_path(item, platform)
    if is_installed(item, platform):
        return target
    download = item.download(platform)
    assert download is not None  # target_path raised otherwise
    expected = published_hashes(download)
    if not expected:
        raise CtreError(f"CTRE published no hash for {item.tool} {item.version} ({platform}), so it is not downloaded.")
    check_url(download.url)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise CtreError(f"Cannot create {target.parent}: {e}") from e
    free = shutil.disk_usage(target.parent).free
    if free < MIN_FREE_BYTES:
        raise CtreError(f"Only {free / 2**20:.0f} MB is free where {target.parent} is; at least {MIN_FREE_BYTES / 2**20:.0f} MB is needed to download safely.")
    handle, name = tempfile.mkstemp(dir=target.parent, prefix=f"{target.name}.", suffix=".part")  # unique, so two installs of one file cannot trample each other
    os.close(handle)
    partial = Path(name)
    try:
        with partial.open("wb") as sink:
            http_get(download.url, sink, config.max_download_bytes(), config.timeout_s())
        actual = hash_all(partial)
        wrong = [name for name, value in expected.items() if actual[name] != value]
        if wrong:
            raise CtreError(f"The download of {item.tool} {item.version} failed its {'/'.join(wrong)} check, so it was deleted. Try again; if it repeats, tell CTRE.")
        os.replace(partial, target)
        if not platform.startswith("windows"):
            target.chmod(0o755)
        write_sidecar(target, actual["sha256"])
    except OSError as e:
        raise CtreError(f"Could not save {target}: {e}") from e
    finally:
        partial.unlink(missing_ok=True)
    return target
