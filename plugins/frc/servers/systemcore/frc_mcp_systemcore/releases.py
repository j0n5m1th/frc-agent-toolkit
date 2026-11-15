"""OS releases: what the Systemcore runs, what is the newest, and fetching an update file.
Names look like `limelightosr-2027.0.0-alpha18-422`: the season version, an optional stage (`alpha`/`beta`, each a hardware
line) with its image number, and a build number the controller does not report (it says `limelightosr-2027.0.0-alpha14`).
The list comes from the public GitHub releases of LimelightVision/systemcore-os-public. The robot network usually has no
internet, so it is cached in the state folder and the health summary only reads the cache. Only that repository's release assets
are downloaded and nothing is installed: updating is the user's job (the web UI at /configure).
"""

import asyncio
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import net, state
from .errors import SystemcoreError

REPO = "LimelightVision/systemcore-os-public"
LIST_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=40"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
USER_AGENT = "mcp-systemcore"
CACHE = "releases.json"
MAX_LIST_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 3 * 1024**3
STABLE_RANK = 99  # a production release is newer than any alpha, beta or release candidate of the same version
_STAGE_RANK = {"alpha": 0, "beta": 1, "rc": 2}
_NAME = re.compile(r"limelightosr-(\d+)\.(\d+)\.(\d+)(?:-([A-Za-z]+)-?(\d+))?(?:-(\d+))?$")
_IMAGE_FILE = re.compile(r"^[A-Za-z0-9_.-]+\.llupdate$")


def parse_release(name: object) -> dict[str, Any]:
    """`{"version": "2027.0.0", "stage": "alpha"|None, "image": 18|None, "build": 422|None}`, or `{}` when the name is not one."""
    match = _NAME.search(name) if isinstance(name, str) else None
    if not match:
        return {}
    major, minor, patch, stage, image, build = match.groups()
    return {"version": f"{major}.{minor}.{patch}", "stage": stage.lower() if stage else None,
            "image": int(image) if image else None, "build": int(build) if build else None}


def sort_key(release: dict[str, Any]) -> tuple[int, int, int, int, int]:
    """Orders releases: version first, then production above rc above beta above alpha, then the image number."""
    major, minor, patch = (int(part) for part in release["version"].split("."))
    stage = release.get("stage")
    return major, minor, patch, STABLE_RANK if stage is None else _STAGE_RANK.get(stage, 1), release.get("image") or 0


def newest_for(device: dict[str, Any], releases: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest release the device could move to: its own stage's line (alpha and beta are different hardware) or a production one."""
    if not device.get("version"):
        return None  # an unrecognized release name cannot be compared
    best: dict[str, Any] | None = None
    best_key = None
    for item in releases:
        parsed = parse_release(item.get("tag"))
        if not parsed or parsed["stage"] not in (device.get("stage"), None):
            continue
        key = sort_key(parsed)
        if best_key is None or key > best_key:
            best, best_key = item, key
    return best


def is_newer(candidate_tag: str, device: dict[str, Any]) -> bool:
    parsed = parse_release(candidate_tag)
    return bool(parsed and device.get("version") and sort_key(parsed) > sort_key(device))


def _slim(release: dict[str, Any]) -> dict[str, Any] | None:
    tag = release.get("tag_name")
    if release.get("draft") or not parse_release(tag):
        return None
    assets = [{"name": a["name"], "size": a.get("size"), "digest": a.get("digest"), "url": a.get("browser_download_url")}
              for a in release.get("assets", []) if isinstance(a, dict) and isinstance(a.get("name"), str)
              and _IMAGE_FILE.match(a["name"])]
    return {"tag": tag, "prerelease": bool(release.get("prerelease")), "published": release.get("published_at"), "assets": assets}


def cached() -> dict[str, Any]:
    """`{"fetched_at": epoch, "releases": [...]}` from the last refresh, or an empty dict."""
    data = state.load(CACHE)
    return data if isinstance(data.get("releases"), list) else {}


def _fetch_sync(timeout: float) -> list[dict[str, Any]]:
    request = urllib.request.Request(LIST_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_LIST_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise SystemcoreError(f"GitHub answered {e.code} for the release list" + (" (rate limit; try later)" if e.code in (403, 429) else "") + ".") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise SystemcoreError(f"Cannot reach GitHub ({getattr(e, 'reason', e)}). This needs internet, which the Systemcore Wi-Fi usually lacks.") from e
    if len(raw) > MAX_LIST_BYTES:
        raise SystemcoreError("The GitHub release list is larger than expected; not read.")
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise SystemcoreError("GitHub did not return JSON for the release list.") from e
    if not isinstance(data, list):
        raise SystemcoreError("GitHub returned an unexpected release list.")
    return [slim for item in data if isinstance(item, dict) and (slim := _slim(item))]


async def refresh(timeout: float = 15) -> dict[str, Any]:
    """Fetch the release list and cache it. Raises SystemcoreError when GitHub cannot be reached (the cache is left alone)."""
    releases = await asyncio.to_thread(_fetch_sync, timeout)
    data = {"fetched_at": int(time.time()), "releases": releases}
    state.save(CACHE, data)
    return data


def pick_asset(release: dict[str, Any], *, usb: bool) -> dict[str, Any]:
    """The `.llupdate` of `release` for eMMC boot (default) or USB boot (`-usb-` in the name)."""
    found = [a for a in release.get("assets", []) if ("-usb-" in a["name"]) == usb]
    if len(found) != 1:
        raise SystemcoreError(f"{release['tag']} has {len(found)} matching update files for {'USB' if usb else 'eMMC'} boot; "
                              "download it from the releases page instead.")
    return found[0]


def _download_sync(asset: dict[str, Any], dest: Path, timeout: float) -> dict[str, Any]:
    url = asset.get("url")
    if not isinstance(url, str) or not url.startswith(DOWNLOAD_PREFIX) or not _IMAGE_FILE.match(asset["name"]):
        raise SystemcoreError("Refusing a download that is not a release file of the Systemcore OS repository.")
    if isinstance(size := asset.get("size"), int) and size > MAX_IMAGE_BYTES:
        raise SystemcoreError(f"{asset['name']} is {size} bytes, over the {MAX_IMAGE_BYTES} byte limit.")
    digest_text = asset.get("digest")
    if not (isinstance(digest_text, str) and digest_text.startswith("sha256:")):
        raise SystemcoreError(f"GitHub publishes no SHA-256 for {asset['name']}, so it is not downloaded: an image that cannot be checked is not saved.")
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": USER_AGENT, **({"Range": f"bytes={have}-"} if have else {})}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
            resumed = have > 0 and response.status == 206
            with part.open("ab" if resumed else "wb") as out:
                net.copy_stream(response, out, MAX_IMAGE_BYTES)
    except urllib.error.HTTPError as e:
        if e.code != 416:  # 416: the .part file is already whole (or stale): verify below, or start again on the next call
            raise SystemcoreError(f"GitHub answered {e.code} for {asset['name']}.") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise SystemcoreError(f"The download stopped ({getattr(e, 'reason', e)}). Run it again to resume from the partial file.") from e
    digest = hashlib.sha256()
    with part.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    actual = digest.hexdigest()
    if actual != digest_text[7:].lower():
        part.unlink(missing_ok=True)
        raise SystemcoreError(f"{asset['name']} failed its SHA-256 check; the partial file was removed. Run it again.")
    part.replace(dest)
    return {"path": str(dest), "size": dest.stat().st_size, "sha256": actual, "sha256_verified": True}


async def download(asset: dict[str, Any], dest_dir: Path, timeout: float = 60) -> dict[str, Any]:
    """Save a release file to `dest_dir` (kept as .part until whole, resumed on the next call) and check its SHA-256."""
    if not _IMAGE_FILE.match(str(asset.get("name"))):
        raise SystemcoreError("Refusing a download that is not a release file of the Systemcore OS repository.")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / asset["name"]
    if dest.exists() and isinstance(asset.get("size"), int) and dest.stat().st_size == asset["size"]:
        if await asyncio.to_thread(_matches_index, dest, asset.get("digest")):
            return {"path": str(dest), "size": asset["size"], "already_downloaded": True}
        dest.unlink(missing_ok=True)  # the same size but not the published file (damaged, or replaced): fetch it again
    return await asyncio.to_thread(_download_sync, asset, dest, timeout)


def _matches_index(path: Path, digest: object) -> bool:
    """Is `path` the file the release index describes? The size was already compared; with a published SHA-256 the content is too.

    Without a published digest only the size can be compared, so a damaged file of the right size is not noticed.
    """
    if not (isinstance(digest, str) and digest.startswith("sha256:")):
        return True
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            hasher.update(block)
    return hasher.hexdigest() == digest[7:].lower()
