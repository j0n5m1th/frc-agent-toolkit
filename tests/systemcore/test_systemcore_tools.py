"""The Systemcore tools end to end: a real MCP client drives a fake device (local HTTP services, in-memory SSH)."""

import contextlib
import time

import pytest
from envelope_helpers import j
from fastmcp.exceptions import ToolError
from systemcore_helpers import FakeSession

from frc_mcp_systemcore import app, bundle, ssh, state
from frc_mcp_systemcore.errors import NeedsProvisioning

DAY = 86400
NEW, OLD = "WPILIB_20261029_192747.wpilog", "WPILIB_20260901_000000.wpilog"


async def call(client, name, args=None):
    return j(await client.call_tool(name, args or {}))


def log_session(**kwargs):
    files = {NEW: b"n" * 10, OLD: b"o" * 20, "2026-10-01_00-00-00/a.hoot": b"h" * 30, "2026-10-02_00-00-00/b.hoot": b"h" * 30}
    return FakeSession(files, **kwargs)


# ---- the tool surface ----

async def test_the_tool_surface_and_the_toolsets(client, monkeypatch):
    tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == {"systemcore_discover", "systemcore_info", "systemcore_bundle", "systemcore_logs", "systemcore_logs_download",
                          "systemcore_logs_prune", "systemcore_journal", "systemcore_ssh_status", "systemcore_ssh_forget",
                          "systemcore_os_latest", "systemcore_os_download", "systemcore_provision", "systemcore_connect", "systemcore_disconnect",
                          "systemcore_restart_robot_code", "systemcore_reboot"}
    assert tools["systemcore_logs"].annotations.read_only_hint and tools["systemcore_logs_prune"].annotations.destructive_hint
    assert not tools["systemcore_bundle"].annotations.destructive_hint
    try:
        assert app.apply_toolset("core") == "core"
        assert {t.name for t in await client.list_tools()} == {"systemcore_discover", "systemcore_info", "systemcore_bundle", "systemcore_logs",
                                                               "systemcore_logs_download", "systemcore_logs_prune", "systemcore_ssh_status",
                                                               "systemcore_os_latest", "systemcore_provision", "systemcore_connect", "systemcore_disconnect"}
    finally:
        app.apply_toolset("full")
    with pytest.raises(Exception, match="SYSTEMCORE_TOOLSET"):
        monkeypatch.setenv("SYSTEMCORE_TOOLSET", "x")
        app.apply_toolset()


# ---- info and the host ----

async def test_no_host_known_says_how_to_find_one(client):
    with pytest.raises(ToolError, match="systemcore_discover"):
        await call(client, "systemcore_info")


async def test_a_public_address_is_refused(client):
    with pytest.raises(ToolError, match="not a private address"):
        await call(client, "systemcore_info", {"host": "8.8.8.8"})


async def test_info_summarises_the_http_readings_and_warns(client, device, fake_ssh):
    fake_ssh.session = FakeSession({}, {"MemTotal": (0, "##mem\nMemTotal: 4000000 kB\nMemAvailable: 3000000 kB\n##failed\nbad.service\n", "")})
    out = await call(client, "systemcore_info")
    assert out["team"] == 9999 and out["os"] == {"release": "limelightosr-2027.0.0-alpha14", "hash": "0a1b2c3d", "slot": "A"}  # parsed parts stay inside
    assert out["disk"] == {"total_gb": 7.0, "free_gb": 1.0, "used_pct": 90.0} and out["cpu"]["slices"]["system.slice"] == 6.5
    assert out["emmc"]["status"] == "good" and out["memory_mb"]["total"] == 3906 and out["failed_units"] == ["bad.service"]
    assert any("90.0% full" in w for w in out["warnings"]) and any("clock" in w for w in out["warnings"]) and any("bad.service" in w for w in out["warnings"])
    assert state.last_host() == "127.0.0.1" and state.device("127.0.0.1")["team"] == 9999
    paths = {p for _, p, _ in device.requests}
    assert "/api/wifi" not in paths and not any("largest" in p or "journal-stats" in p for p in paths)  # the risky or secret ones are never asked


async def test_info_reports_each_missing_source_and_still_answers(client, device, monkeypatch):
    del device.routes[("GET", "/api/cpu/slices")]
    @contextlib.asynccontextmanager
    async def refuse(host, os_hash=None):
        raise NeedsProvisioning("key missing")
        yield

    monkeypatch.setattr(ssh, "session", refuse)
    out = await call(client, "systemcore_info")
    assert out["team"] == 9999 and "cpu" not in out
    assert "cpu" in out["errors"] and "ssh" in out["errors"] and "systemcore_ssh_status" in out["hint"]


@pytest.mark.slow
async def test_info_when_nothing_answers_names_the_fix(client, monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_HOST", "127.0.0.1")
    monkeypatch.setenv("SYSTEMCORE_TIMEOUT_S", "1")
    out = await call(client, "systemcore_info")  # the default ports are closed on this machine
    assert "oshash" in out["errors"] and "systemcore_discover" in out["errors"]["oshash"]


async def test_the_deeper_areas(client, device, fake_ssh):
    device.routes[("GET", "/api/usb")] = {"devices": [{"vendor_id": "cafe", "product_id": "4011", "description": "RT Subsystem"}]}
    device.routes[("GET", "/api/i2c")] = {"buses": [{"bus_number": 1, "device_count": 0, "devices": []}]}
    device.routes[("GET", "/api/network")] = {"interfaces": {"eth0": {"static_ip": "10.99.99.2", "use_dhcp": False, "dns_primary": "8.8.8.8", "secret": "x"}}}
    network = await call(client, "systemcore_info", {"area": "network"})
    assert network["hostname"] == "robot" and network["interfaces"] == [{"name": "wlan0", "ip": "172.30.0.1", "up": True}]
    assert network["configured"] == {"eth0": {"static_ip": "10.99.99.2", "use_dhcp": False}}
    hardware = await call(client, "systemcore_info", {"area": "hardware"})
    assert hardware["usb"] == ["cafe:4011 RT Subsystem"] and hardware["i2c"] == [{"bus": 1, "devices": 0}]
    fake_ssh.session = FakeSession({}, {"dmesg": (0, "[1] hello\n\n[2] world\n", "")})
    assert (await call(client, "systemcore_info", {"area": "dmesg"}))["lines"] == ["[1] hello", "[2] world"]


# ---- the support bundle ----

async def test_bundle_generates_waits_downloads_and_checks(client, device, tmp_path):
    out = await call(client, "systemcore_bundle", {"symptoms": "robot rebooted"})
    assert out["member_count"] == 2 and out["size"] > 0 and (tmp_path / "downloads").exists() and out["path"].endswith(".zip")
    generate = next(body for method, path, body in device.requests if path == "/api/support/generate")
    assert generate["symptoms"] == "robot rebooted" and generate["includeDsLog"] is False


async def test_bundle_failures_name_the_cause(client, device):
    device.routes[("POST", "/api/support/generate")] = (507, {"error": "no space"})
    with pytest.raises(ToolError, match="disk is full"):
        await call(client, "systemcore_bundle")
    device.routes[("POST", "/api/support/generate")] = {"jobId": "job1"}
    device.routes[("GET", "/api/support/status/job1")] = {"state": "error", "error": "boom"}
    with pytest.raises(ToolError, match="boom"):
        await call(client, "systemcore_bundle")
    device.routes[("GET", "/api/support/status/job1")] = {"state": "done"}
    device.routes[("GET", "/api/support/download/job1")] = b"not a zip"
    with pytest.raises(ToolError, match="generate it again"):
        await call(client, "systemcore_bundle")
    device.routes[("POST", "/api/support/generate")] = {"jobId": "../../x"}
    with pytest.raises(ToolError, match="job id"):
        await call(client, "systemcore_bundle")


async def test_a_bundle_that_is_not_ready_in_time_says_so(client, device, fast_time):
    fast_time(bundle)
    device.routes[("GET", "/api/support/status/job1")] = {"state": "running", "step": "logs"}
    started = time.monotonic()
    with pytest.raises(ToolError, match="not ready after 5 s"):
        await call(client, "systemcore_bundle", {"wait_s": 5})
    assert time.monotonic() - started < 15


# ---- logs ----

async def test_logs_list_newest_first_with_active_and_totals(client, fake_ssh):
    fake_ssh.session = log_session(active=f"/home/systemcore/logs/{NEW}\n")
    out = await call(client, "systemcore_logs", {"kind": "wpilog"})
    assert [f["path"] for f in out["files"]] == [NEW, OLD] and out["files"][0]["active"] is True and out["total"] == 2
    assert out["total_mb"]["hoot"] == 0.0 and "hint" not in out  # every listed name carries a date: nothing to explain
    tbd = await call(client, "systemcore_logs", {"kind": "hoot"})  # the .hoot folders are named with dates too
    assert "hint" not in tbd
    assert (await call(client, "systemcore_logs", {"limit": 1}))["omitted"] == 3


async def test_download_copies_files_remembers_them_and_refuses_unlisted_paths(client, fake_ssh, tmp_path):
    fake_ssh.session = log_session()
    out = await call(client, "systemcore_logs_download", {"paths": [NEW, "../../etc/passwd"]})
    assert out["files"][0]["saved"].endswith(NEW) and (tmp_path / "downloads" / "127.0.0.1" / NEW).read_bytes() == b"n" * 10
    assert "err" in out["files"][1] and state.downloaded(f"127.0.0.1/{NEW}") == 10
    newest = await call(client, "systemcore_logs_download", {"newest": 1, "kind": "hoot"})
    assert newest["files"][0]["path"] == "2026-10-02_00-00-00/b.hoot"
    with pytest.raises(ToolError, match="paths, or newest"):
        await call(client, "systemcore_logs_download")


async def test_prune_is_a_dry_run_by_default_and_deletes_nothing(client, fake_ssh):
    fake_ssh.session = log_session()
    out = await call(client, "systemcore_logs_prune", {"keep_newest": 1, "only_downloaded": False})
    assert out["dry_run"] is True and out["count"] == 2 and {f["path"] for f in out["files"]} == {OLD, "2026-10-01_00-00-00/a.hoot"}
    assert fake_ssh.session.removed == [] and "expect_count=2" in out["hint"]


async def test_prune_only_touches_what_was_downloaded_here(client, fake_ssh):
    fake_ssh.session = log_session()
    out = await call(client, "systemcore_logs_prune", {"keep_newest": 1})
    assert out["count"] == 0 and out["kept_not_downloaded"] >= 2
    await call(client, "systemcore_logs_download", {"paths": [OLD]})
    again = await call(client, "systemcore_logs_prune", {"keep_newest": 1})
    assert [f["path"] for f in again["files"]] == [OLD]


async def test_deleting_needs_the_setting_a_rule_and_the_counted_dry_run(client, fake_ssh, monkeypatch):
    fake_ssh.session = log_session()
    with pytest.raises(ToolError, match="SYSTEMCORE_ALLOW_PRUNE"):
        await call(client, "systemcore_logs_prune", {"keep_newest": 1, "only_downloaded": False, "dry_run": False, "expect_count": 2})
    monkeypatch.setenv("SYSTEMCORE_ALLOW_PRUNE", "1")
    with pytest.raises(ToolError, match="at least one rule"):
        await call(client, "systemcore_logs_prune", {"dry_run": False, "expect_count": 0})
    with pytest.raises(ToolError, match="dry run first"):
        await call(client, "systemcore_logs_prune", {"keep_newest": 1, "only_downloaded": False, "dry_run": False})
    with pytest.raises(ToolError, match="not 5"):
        await call(client, "systemcore_logs_prune", {"keep_newest": 1, "only_downloaded": False, "dry_run": False, "expect_count": 5})
    assert fake_ssh.session.removed == []
    out = await call(client, "systemcore_logs_prune", {"keep_newest": 1, "only_downloaded": False, "dry_run": False, "expect_count": 2})
    assert out["deleted"] == 2 and out["failed"] == [] and set(fake_ssh.session.removed) == {OLD, "2026-10-01_00-00-00/a.hoot"}
    assert fake_ssh.session.rmdirs == ["2026-10-01_00-00-00"]  # the emptied session folder goes; the one still holding a file stays


async def test_the_file_being_written_is_never_deleted(client, fake_ssh, monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_ALLOW_PRUNE", "1")
    fake_ssh.session = log_session(active=f"/home/systemcore/logs/{OLD}\n")
    out = await call(client, "systemcore_logs_prune", {"older_than_days": 1, "only_downloaded": False})
    assert OLD not in {f["path"] for f in out["files"]}


# ---- journal ----

async def test_journal_builds_a_fixed_command_and_rejects_odd_service_names(client, fake_ssh):
    fake_ssh.session = FakeSession({}, {"journalctl": (0, "line one\nline two\n", "")})
    out = await call(client, "systemcore_journal", {"service": "robot", "priority": "err", "lines": 5, "boot": -1})
    assert out["lines"] == ["line one", "line two"]
    assert fake_ssh.session.ran[-1] == "journalctl -b -1 --no-pager -q -o short-iso -n 5 -u robot -p err"
    for bad in ("robot; reboot", "$(x)", "a b", ""):
        with pytest.raises(ToolError, match="systemd unit"):
            await call(client, "systemcore_journal", {"service": bad})
    fake_ssh.session = FakeSession({}, {"journalctl": (1, "", "no such unit")})
    with pytest.raises(ToolError, match="no such unit"):
        await call(client, "systemcore_journal")


# ---- ssh status, trust, forget ----

async def test_ssh_status_reports_each_state(client, device, monkeypatch):
    presented = "SHA256:" + "A" * 43
    monkeypatch.setattr(ssh, "probe_host_key", lambda host, timeout=None: presented)
    closed = []

    class Open:
        def close(self):
            closed.append(True)

    monkeypatch.setattr(ssh, "connect_sync", lambda host, os_hash=None, timeout=None: Open())
    out = await call(client, "systemcore_ssh_status")
    assert out["login"] == "ok" and out["host_key"].startswith("unpinned") and out["key"].startswith("SHA256:") and closed

    def refuse(host, os_hash=None, timeout=None):
        raise NeedsProvisioning("run mcp-systemcore provision")

    monkeypatch.setattr(ssh, "connect_sync", refuse)
    state.pin("127.0.0.1", presented, "0a1b2c3d")
    out = await call(client, "systemcore_ssh_status")
    assert out["host_key"] == "matches" and out["login"] == "needs_provisioning" and "provision" in out["hint"]

    state.pin("127.0.0.1", "SHA256:" + "B" * 43, "oldhash")  # the OS image changed, so the key is expected to differ
    out = await call(client, "systemcore_ssh_status")
    assert out["host_key"] == "changed" and out["login"] == "not tried" and presented in out["hint"] and "OS image changed" in out["hint"]
    assert "frc-mcp systemcore trust" in out["hint"] and "systemcore_ssh_trust" not in out["hint"] and "fingerprint=" not in out["hint"]


async def test_forgetting_a_key_is_off_until_the_owner_allows_it(client, device, monkeypatch):
    with pytest.raises(ToolError, match="SYSTEMCORE_ALLOW_PROVISION=1"):
        await call(client, "systemcore_ssh_forget", {})
    monkeypatch.setenv("SYSTEMCORE_ALLOW_PROVISION", "1")
    state.pin("127.0.0.1", "SHA256:" + "D" * 43, "old")
    assert (await call(client, "systemcore_ssh_forget"))["forgotten"] is True and state.pinned("127.0.0.1") is None


async def test_no_tool_can_trust_a_host_key(client, device, monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_ALLOW_PROVISION", "1")
    assert "systemcore_ssh_trust" not in {t.name for t in await client.list_tools()}
    assert all("fingerprint" not in str(t.input_schema) for t in await client.list_tools())  # no tool takes a fingerprint to pin


async def test_discover_hint_when_nothing_answers_names_the_addresses(client, monkeypatch):
    from frc_mcp_systemcore import discovery

    async def nothing(*args, **kwargs):
        return []

    monkeypatch.setattr(discovery, "discover", nothing)
    out = await call(client, "systemcore_discover")
    assert out["devices"] == [] and "172.30.0.1" in out["hint"] and "172.26.0.1" in out["hint"] and "SYSTEMCORE-" in out["hint"]


async def test_os_latest_is_not_marked_read_only_because_it_goes_online_and_saves_a_file(client):
    tool = next(t for t in await client.list_tools() if t.name == "systemcore_os_latest")
    assert tool.annotations.read_only_hint is not True and tool.annotations.open_world_hint is not False  # the protocol defaults apply


async def test_the_logs_hint_appears_only_when_a_listed_file_has_no_date(client, fake_ssh):
    fake_ssh.session = FakeSession({"WPILIB_TBD_abc123.wpilog": b"x" * 5, NEW: b"y" * 5})
    out = await call(client, "systemcore_logs")
    assert "no_time_in_name" in str(out["files"]) and "clock" in out["hint"]
