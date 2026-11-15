"""OS release names and ordering, the newest-release lookup, the update-file download, and the tools built on them."""

import asyncio
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from envelope_helpers import j
from fastmcp.exceptions import ToolError

from frc_mcp_systemcore import info, releases, state
from frc_mcp_systemcore.errors import SystemcoreError

ALPHA14 = {"version": "2027.0.0", "stage": "alpha", "image": 14, "build": None}
IMAGE = b"image-bytes" * 1000
DIGEST = "sha256:" + hashlib.sha256(IMAGE).hexdigest()


def release(tag, *files):
    return {"tag": tag, "prerelease": True, "published": "2026-10-28T11:00:49Z", "assets": list(files)}


def asset(name, url="", data=IMAGE, digest=DIGEST):
    return {"name": name, "size": len(data), "digest": digest, "url": url}


LIST = [release("limelightosr-2027.0.0-alpha18-422", asset("limelightsystemcore-limelightosr-2027.0.0-alpha18.llupdate"),
                asset("limelightsystemcore-usb-limelightosr-2027.0.0-alpha18.llupdate")),
        release("limelightosr-2027.0.0-beta19-300", asset("limelightsystemcorebeta-limelightosr-2027.0.0-beta19.llupdate")),
        release("limelightosr-2027.0.0-alpha17-403")]


# ---- names and ordering ----

@pytest.mark.parametrize(("name", "expected"), [
    ("limelightosr-2027.0.0-alpha14", ALPHA14),
    ("limelightosr-2027.0.0-alpha18-422", {"version": "2027.0.0", "stage": "alpha", "image": 18, "build": 422}),
    ("limelightosr-2027.0.0-beta18-252", {"version": "2027.0.0", "stage": "beta", "image": 18, "build": 252}),
    ("limelightosr-2027.1.3", {"version": "2027.1.3", "stage": None, "image": None, "build": None}),
    ("limelightosr-2028.0.0-rc2-9", {"version": "2028.0.0", "stage": "rc", "image": 2, "build": 9}),
    ("limelightosr-alpha-12-354", {}), ("limelightosr-release-10", {}), ("Limelight_SYSTEMCORE-177", {}), ("mc0.7.1", {}), (None, {}), (14, {}),
])
def test_release_names(name, expected):
    assert releases.parse_release(name) == expected


def test_versions_order_by_season_then_stage_then_image():
    names = ["limelightosr-2027.0.0-alpha18", "limelightosr-2027.0.0-beta1", "limelightosr-2027.0.0-rc1", "limelightosr-2027.0.0",
             "limelightosr-2027.0.1-alpha1", "limelightosr-2027.1.0-alpha1", "limelightosr-2028.0.0-alpha1"]
    keys = [releases.sort_key(releases.parse_release(n)) for n in names]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    assert releases.sort_key(releases.parse_release("limelightosr-2027.0.0-alpha9")) < releases.sort_key(ALPHA14)
    assert releases.sort_key(releases.parse_release("limelightosr-2027.0.0-alpha18-1")) > releases.sort_key(ALPHA14)
    assert releases.sort_key(releases.parse_release("limelightosr-2027.10.0")) > releases.sort_key(releases.parse_release("limelightosr-2027.9.0"))


def test_newest_for_stays_on_the_devices_hardware_line_but_accepts_production():
    assert releases.newest_for(ALPHA14, LIST)["tag"] == "limelightosr-2027.0.0-alpha18-422"  # beta19 is the other hardware
    beta = {"version": "2027.0.0", "stage": "beta", "image": 18, "build": None}
    assert releases.newest_for(beta, LIST)["tag"] == "limelightosr-2027.0.0-beta19-300"
    production = [*LIST, release("limelightosr-2027.0.0")]
    assert releases.newest_for(ALPHA14, production)["tag"] == "limelightosr-2027.0.0"
    assert releases.newest_for({}, LIST) is None and releases.newest_for({"release": "x"}, LIST) is None
    assert releases.newest_for(ALPHA14, []) is None


def test_is_newer_never_claims_for_an_unrecognized_device_or_tag():
    assert releases.is_newer("limelightosr-2027.0.0-alpha18-422", ALPHA14)
    assert not releases.is_newer("limelightosr-2027.0.0-alpha14-380", ALPHA14) and not releases.is_newer("limelightosr-2027.0.0-alpha9", ALPHA14)
    assert not releases.is_newer("limelightosr-2027.0.0-alpha18-422", {}) and not releases.is_newer("nonsense", ALPHA14)


def test_slim_keeps_only_systemcore_images():
    raw = {"tag_name": "limelightosr-2027.0.0-alpha18-422", "prerelease": True, "published_at": "t", "assets": [
        {"name": "limelightsystemcore-limelightosr-2027.0.0-alpha18.llupdate", "size": 5, "digest": "sha256:x", "browser_download_url": "u"},
        {"name": "LICENSES.txt", "size": 1}, {"name": "../evil.llupdate"}, "junk"]}
    assert [a["name"] for a in releases._slim(raw)["assets"]] == ["limelightsystemcore-limelightosr-2027.0.0-alpha18.llupdate"]
    assert releases._slim({**raw, "draft": True}) is None and releases._slim({"tag_name": "mc0.7.1"}) is None


def test_pick_asset_chooses_the_boot_source():
    latest = LIST[0]
    assert "-usb-" not in releases.pick_asset(latest, usb=False)["name"] and "-usb-" in releases.pick_asset(latest, usb=True)["name"]
    with pytest.raises(SystemcoreError, match="0 matching"):
        releases.pick_asset(LIST[2], usb=False)


# ---- the summary reads only the saved list ----

@pytest.mark.slow
def test_summary_warning_needs_a_saved_list_that_shows_a_newer_release(device):
    assert not any("newer OS" in w for w in asyncio.run(info.summary("127.0.0.1"))["warnings"])  # nothing saved: no claim
    state.save(releases.CACHE, {"fetched_at": 1, "releases": LIST})
    out = asyncio.run(info.summary("127.0.0.1"))
    assert "alpha18" in " ".join(out["warnings"]) and "os_update" not in out  # said once, in the warning
    state.save(releases.CACHE, {"fetched_at": 1, "releases": [release("limelightosr-2027.0.0-alpha14-380"), release("limelightosr-2027.0.0-alpha13")]})
    assert not any("newer OS" in w for w in asyncio.run(info.summary("127.0.0.1"))["warnings"])  # the device is not behind this list


# ---- GitHub, faked on a local port ----

class Github:
    """Serves the release list and one image file (with Range support) like GitHub does."""

    def __init__(self):
        self.list_body = b"[]"
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                outer.requests.append((self.path, self.headers.get("Range")))
                if self.path.startswith("/list"):
                    return self._send(200, outer.list_body)
                start = int(self.headers["Range"].split("=")[1].rstrip("-")) if self.headers.get("Range") else 0
                return self._send(206 if start else 200, IMAGE[start:])

            def _send(self, code, body):
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def github(monkeypatch):
    fake = Github()
    monkeypatch.setattr(releases, "LIST_URL", fake.base + "/list")
    monkeypatch.setattr(releases, "DOWNLOAD_PREFIX", fake.base + "/dl/")
    yield fake
    fake.stop()


def api_list(base=""):
    return json.dumps([
        {"tag_name": "limelightosr-2027.0.0-alpha18-422", "prerelease": True, "published_at": "2026-10-28T11:00:49Z", "assets": [
            {"name": "limelightsystemcore-limelightosr-2027.0.0-alpha18.llupdate", "size": len(IMAGE), "digest": DIGEST,
             "browser_download_url": base + "/dl/a.llupdate"}]},
        {"tag_name": "mc0.7.1", "assets": []}]).encode()


async def run(client, name, args=None):
    return j(await client.call_tool(name, args or {}))


async def test_latest_compares_the_device_with_github_and_saves_the_list(client, device, github):
    github.list_body = api_list(github.base)
    out = await run(client, "systemcore_os_latest")
    assert out["device"] == "limelightosr-2027.0.0-alpha14" and out["behind"] is True
    assert out["newest"]["tag"] == "limelightosr-2027.0.0-alpha18-422"
    assert [r["tag"] for r in releases.cached()["releases"]] == ["limelightosr-2027.0.0-alpha18-422"]  # the mc tag is not kept


@pytest.mark.slow
async def test_latest_falls_back_to_the_saved_list_and_errors_when_there_is_none(client, device, monkeypatch):
    monkeypatch.setattr(releases, "LIST_URL", "http://127.0.0.1:9/list")  # a closed port
    with pytest.raises(ToolError, match="Cannot reach GitHub"):
        await client.call_tool("systemcore_os_latest", {})
    state.save(releases.CACHE, {"fetched_at": 5, "releases": LIST})
    out = await run(client, "systemcore_os_latest")
    assert out["behind"] is True and out["checked_at"] == 5


async def test_latest_without_a_reachable_device_lists_releases_and_compares_nothing(client, github, monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_TIMEOUT_S", "1")
    monkeypatch.setenv("SYSTEMCORE_HOST", "127.0.0.1")  # the default service ports are closed here
    github.list_body = api_list(github.base)
    out = await run(client, "systemcore_os_latest")
    assert "behind" not in out and "not reachable" in out["hint"] and out["releases"]


async def test_download_saves_verifies_and_installs_nothing(client, device, github, tmp_path):
    github.list_body = api_list(github.base)
    await client.call_tool("systemcore_os_latest", {})
    out = await run(client, "systemcore_os_download")
    assert out["sha256_verified"] is True and out["release"] == "limelightosr-2027.0.0-alpha18-422"
    saved = tmp_path / "downloads" / "os-images" / "a.llupdate"
    assert not saved.exists()  # the file is named after the asset, not the URL
    saved = tmp_path / "downloads" / "os-images" / "limelightsystemcore-limelightosr-2027.0.0-alpha18.llupdate"
    assert saved.read_bytes() == IMAGE and not saved.with_suffix(".llupdate.part").exists() and "configure" in out["hint"]
    again = await run(client, "systemcore_os_download")
    assert again["already_downloaded"] is True


async def test_download_refuses_unknown_tags_and_a_missing_usb_file(client, device, github):
    github.list_body = api_list(github.base)
    await client.call_tool("systemcore_os_latest", {})
    with pytest.raises(ToolError, match="not in the saved release list"):
        await client.call_tool("systemcore_os_download", {"tag": "limelightosr-2027.0.0-alpha99"})
    with pytest.raises(ToolError, match="0 matching"):
        await client.call_tool("systemcore_os_download", {"usb_boot": True})


# ---- the download itself ----

async def test_a_partial_file_is_resumed_and_a_bad_digest_is_removed(github, tmp_path):
    name = "x.llupdate"
    good = asset(name, github.base + "/dl/x.llupdate")
    part = tmp_path / (name + ".part")
    part.write_bytes(IMAGE[:100])
    saved = await releases.download(good, tmp_path)
    assert saved["sha256_verified"] is True and (tmp_path / name).read_bytes() == IMAGE
    assert ("/dl/x.llupdate", "bytes=100-") in github.requests
    (tmp_path / name).unlink()
    with pytest.raises(SystemcoreError, match="SHA-256"):
        await releases.download({**good, "digest": "sha256:" + "0" * 64}, tmp_path)
    assert not (tmp_path / name).exists() and not part.exists()


async def test_download_refuses_files_outside_the_release_repository(tmp_path):
    for url in ("https://evil.example/x.llupdate", "file:///etc/passwd", "", None):
        with pytest.raises(SystemcoreError, match="Refusing"):
            await releases.download(asset("x.llupdate", url), tmp_path)
    with pytest.raises(SystemcoreError, match="Refusing"):
        await releases.download(asset("../x.zip", releases.DOWNLOAD_PREFIX + "x"), tmp_path)
    huge = {**asset("x.llupdate", releases.DOWNLOAD_PREFIX + "x"), "size": releases.MAX_IMAGE_BYTES + 1}
    with pytest.raises(SystemcoreError, match="limit"):
        await releases.download(huge, tmp_path)


async def test_a_list_saved_within_the_hour_is_used_without_asking_github(client, device, github):
    state.save(releases.CACHE, {"fetched_at": int(time.time()), "releases": LIST})
    out = await run(client, "systemcore_os_latest")
    assert out["behind"] is True and github.requests == []  # nothing was fetched
    github.list_body = api_list(github.base)
    await run(client, "systemcore_os_latest", {"refresh": True})  # asked again only when told to
    assert [path for path, _ in github.requests] == ["/list"]


async def test_an_old_saved_list_is_refreshed(client, device, github):
    state.save(releases.CACHE, {"fetched_at": int(time.time()) - 2 * 3600, "releases": LIST})
    github.list_body = api_list(github.base)
    out = await run(client, "systemcore_os_latest")
    assert [path for path, _ in github.requests] == ["/list"] and out["newest"]["tag"] == "limelightosr-2027.0.0-alpha18-422"


async def test_a_downloaded_file_of_the_right_size_but_the_wrong_content_is_fetched_again(client, device, github, tmp_path):
    github.list_body = api_list(github.base)
    await client.call_tool("systemcore_os_latest", {})
    first = await run(client, "systemcore_os_download")
    saved = tmp_path / "downloads" / "os-images" / "limelightsystemcore-limelightosr-2027.0.0-alpha18.llupdate"
    saved.write_bytes(b"x" * len(IMAGE))  # damaged, same size
    again = await run(client, "systemcore_os_download")
    assert "already_downloaded" not in again and again["sha256_verified"] is True and saved.read_bytes() == IMAGE and first["sha256_verified"] is True


@pytest.mark.parametrize("digest", [None, "", "md5:abc", "sha1:abc"])
async def test_an_image_without_a_published_sha256_is_not_downloaded(github, tmp_path, digest):
    with pytest.raises(SystemcoreError, match="no SHA-256"):
        await releases.download(asset("n.llupdate", github.base + "/dl/n.llupdate", digest=digest), tmp_path)
    assert list(tmp_path.iterdir()) == [] and not github.requests
