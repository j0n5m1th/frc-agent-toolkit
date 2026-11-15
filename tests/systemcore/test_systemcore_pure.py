"""The Systemcore server's pure parts: discovery replies, host checks, log rules, health parsing, saved state, host key policy."""

import asyncio
import os
import socket
import stat
import sys
import threading
import zipfile
from datetime import UTC, datetime

import pytest
from systemcore_helpers import FakeSession, make_zip

from frc_mcp_systemcore import bundle, discovery, info, logs, net, ssh, state
from frc_mcp_systemcore.errors import HostKeyChanged, SystemcoreError

REPLY = "v2:h:robot:interfaces:wlan0=172.30.0.1/255.255.255.0/172.30.0.255;usb0=172.27.0.1/255.255.255.0/172.27.0.255;"


# ---- discovery ----

def test_a_reply_gives_the_hostname_and_every_interface():
    parsed = discovery.parse_reply(REPLY)
    assert parsed == {"hostname": "robot", "interfaces": {
        "wlan0": {"ip": "172.30.0.1", "mask": "255.255.255.0", "broadcast": "172.30.0.255"},
        "usb0": {"ip": "172.27.0.1", "mask": "255.255.255.0", "broadcast": "172.27.0.255"}}}


@pytest.mark.parametrize("text", ["", "hello", "v1:h:robot", "v2:h::interfaces:wlan0=1.1.1.1/1.1.1.1/1.1.1.1;", "v2:h:robot:interfaces:", "v2:h:robot:interfaces:junk;"])
def test_anything_else_is_not_a_reply(text):
    assert discovery.parse_reply(text) is None


def test_broadcast_targets_always_include_the_usual_systemcore_subnets():
    targets = discovery.local_broadcasts()
    assert set(discovery.KNOWN_BROADCASTS) <= set(targets) and len(targets) == len(set(targets))


async def test_discover_finds_a_responder_and_ignores_other_chatter():
    responder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    responder.bind(("127.0.0.1", 0))
    port = responder.getsockname()[1]

    def answer():
        data, source = responder.recvfrom(100)
        assert data == b"LLPhoneHome"
        responder.sendto(b"not a reply", source)
        responder.sendto(REPLY.encode(), source)

    thread = threading.Thread(target=answer, daemon=True)
    thread.start()
    found = await discovery.discover(1.5, ["127.0.0.1"], port)
    thread.join()
    responder.close()
    assert [d["host"] for d in found] == ["127.0.0.1"] and found[0]["hostname"] == "robot"


# ---- which hosts are contacted ----

def test_robot_network_addresses_pass_and_others_are_refused(monkeypatch):
    assert net.check_host_sync("127.0.0.1") == "127.0.0.1" and net.check_host_sync("172.30.0.1") == "172.30.0.1"
    for bad in ("8.8.8.8", "", "http://172.30.0.1", "a b", "172.30.0.1/x", "-oProxyCommand=x"):
        with pytest.raises(SystemcoreError):
            net.check_host_sync(bad)
    monkeypatch.setenv("SYSTEMCORE_ALLOW_ANY_HOST", "1")
    assert net.check_host_sync("8.8.8.8") == "8.8.8.8"


# ---- logs: names, safety, rules ----

def test_names_give_a_time_and_kind():
    assert logs.name_time("WPILIB_20261029_192747.wpilog") == datetime(2026, 10, 29, 19, 27, 47, tzinfo=UTC).timestamp()
    assert logs.name_time("2026-10-15_01-14-36/2026-10-15_01-14-41.hoot") == datetime(2026, 10, 15, 1, 14, 41, tzinfo=UTC).timestamp()
    assert logs.name_time("WPILIB_TBD_5cea53a9e89d6e79.wpilog") is None and logs.name_time("WPILIB_20269999_999999.wpilog") is None
    assert [logs.kind_of(n) for n in ("a.wpilog", "B.HOOT", "c.txt")] == ["wpilog", "hoot", "other"]


@pytest.mark.parametrize("path,ok", [("a.wpilog", True), ("d/e.hoot", True), ("../x", False), ("/etc/passwd", False), ("a/../b", False),
                                    ("a b", False), ("a;rm", False), ("", False), ("$(x)", False)])
def test_only_plain_relative_paths_are_used(path, ok):
    assert logs.is_safe_relative(path) is ok


def entries(*specs):
    """(path, size_mb, days_ago, active) -> Entry list; the time comes from the file date so names do not matter."""
    now = 1_000_000_000.0
    return now, [logs.Entry(p, int(mb * 1e6), now - days * 86400, logs.kind_of(p), None, active) for p, mb, days, active in specs]


def test_prune_by_count_keeps_the_newest_of_each_kind():
    now, items = entries(("a.wpilog", 1, 1, False), ("b.wpilog", 1, 2, False), ("c.wpilog", 1, 3, False), ("x.hoot", 5, 9, False), ("y.hoot", 5, 8, False))
    chosen = logs.select_prune(items, kind=None, keep_newest=1, max_total_bytes=None, older_than_s=None, now=now)
    assert {e.path for e in chosen} == {"b.wpilog", "c.wpilog", "x.hoot"}


def test_prune_by_size_budget_and_age_each_mark_files():
    now, items = entries(("a.hoot", 40, 1, False), ("b.hoot", 40, 2, False), ("c.hoot", 40, 3, False), ("d.hoot", 40, 40, False))
    by_size = logs.select_prune(items, kind="hoot", keep_newest=None, max_total_bytes=int(100e6), older_than_s=None, now=now)
    assert {e.path for e in by_size} == {"c.hoot", "d.hoot"}  # the newest two (80 MB) fit; the third would make 120 MB
    by_age = logs.select_prune(items, kind="hoot", keep_newest=None, max_total_bytes=None, older_than_s=30 * 86400, now=now)
    assert [e.path for e in by_age] == ["d.hoot"]


def test_the_newest_file_active_files_and_protected_ones_always_stay():
    now, items = entries(("new.hoot", 1, 0.1, False), ("busy.hoot", 1, 50, True), ("keep.hoot", 1, 60, False), ("old.hoot", 1, 70, False))
    chosen = logs.select_prune(items, kind=None, keep_newest=None, max_total_bytes=None, older_than_s=86400, now=now, protected={"keep.hoot"})
    assert [e.path for e in chosen] == ["old.hoot"]
    even_zero = logs.select_prune(items[:1], kind=None, keep_newest=1, max_total_bytes=1, older_than_s=1, now=now)
    assert even_zero == []


def test_a_prune_needs_a_rule():
    now, items = entries(("a.hoot", 1, 1, False))
    with pytest.raises(ValueError):
        logs.select_prune(items, kind=None, keep_newest=None, max_total_bytes=None, older_than_s=None, now=now)


def test_listing_marks_the_file_being_written_and_never_follows_other_entries():
    files = {"WPILIB_20261029_192747.wpilog": b"x" * 5, "2026-10-15_01-14-36/a.hoot": b"y" * 7}
    session = FakeSession(files, active="/home/systemcore/logs/WPILIB_20261029_192747.wpilog\n/home/systemcore/logs/other/thing\n")
    listed = {e.path: e for e in logs.list_entries(session)}
    assert set(listed) == set(files) and listed["WPILIB_20261029_192747.wpilog"].active and not listed["2026-10-15_01-14-36/a.hoot"].active
    assert listed["2026-10-15_01-14-36/a.hoot"].size == 7 and listed["2026-10-15_01-14-36/a.hoot"].kind == "hoot"


# ---- health parsing and warnings ----

SHELL = """##mem
MemTotal:        4140704 kB
MemAvailable:     300000 kB
##load
2.04 2.07 2.03 3/473 9924
##temp
thermal cpu-thermal 82900
hwmon rp1_adc 76971
hwmon rpi_volt  1
##failed
foo.service
##robot
ActiveState=active
SubState=running
NRestarts=2
##errors
16
"""


def test_the_shell_readings_are_parsed():
    out = info.parse_shell_summary(SHELL)
    assert out["memory_mb"] == {"total": 4043, "available": 292} and out["load"] == [2.04, 2.07, 2.03]
    assert out["temps"] == {"cpu_c": 82.9, "rp1_c": 76.971} and out["undervoltage"] is True
    assert out["failed_units"] == ["foo.service"] and out["robot_service"] == {"state": "active", "sub": "running", "restarts": 2}
    assert out["error_lines_this_boot"] == 16
    assert info.parse_shell_summary("") == {} and info.parse_shell_summary("##mem\ngarbage\n") == {}


def test_warnings_say_what_needs_attention():
    found = info.warnings_for({**info.parse_shell_summary(SHELL), "disk": {"used_pct": 91.0}, "emmc": {"status": "good", "life_worst": 9},
                               "clock": {"skew_s": -9 * 86400}})
    text = " ".join(found)
    for part in ("91.0% full", "eMMC", "behind", "CPU is 83 C", "Undervoltage", "foo.service", "300000"[:0] + "292 MB"):
        assert part in text, part
    assert info.warnings_for({"disk": {"used_pct": 10.0}, "emmc": {"status": "good", "life_worst": 1}, "clock": {"skew_s": 5}}) == []


# ---- saved state ----

def test_the_key_is_created_once_and_its_public_line_is_stable():
    first = state.public_key_line()
    assert first.startswith("ssh-ed25519 ") and first.endswith(" mcp-systemcore") and state.public_key_line() == first
    assert state.fingerprint_of_public_line(first).startswith("SHA256:")


@pytest.mark.skipif(sys.platform == "win32", reason="Windows ignores POSIX mode bits")
def test_the_key_is_owner_only_from_the_moment_it_is_created(monkeypatch):
    def no_chmod(*args, **kwargs):
        raise AssertionError("the mode must be set at creation, not by a later chmod")
    monkeypatch.setattr(os, "chmod", no_chmod)
    path = state.ensure_key()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_an_existing_key_is_returned_untouched():
    path = state.key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a real key")
    assert state.ensure_key() == path
    assert path.read_bytes() == b"not a real key"


def test_losing_the_creation_race_returns_the_other_process_key(monkeypatch):
    path = state.key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    real_exists = type(path).exists
    monkeypatch.setattr(type(path), "exists", lambda self: False if self == path else real_exists(self))
    path.write_bytes(b"made by someone else")
    assert state.ensure_key() == path
    assert path.read_bytes() == b"made by someone else"


def test_a_failed_key_write_leaves_no_partial_file(monkeypatch):
    def broken_fdopen(fd, *args, **kwargs):
        os.close(fd)
        raise OSError("disk full")
    monkeypatch.setattr(os, "fdopen", broken_fdopen)
    with pytest.raises(OSError, match="disk full"):
        state.ensure_key()
    assert not state.key_path().exists()


def test_pins_devices_and_downloads_round_trip():
    assert state.pinned("1.2.3.4") is None
    state.pin("1.2.3.4", "SHA256:abc", "hash1")
    assert state.pinned("1.2.3.4")["fingerprint"] == "SHA256:abc" and state.forget("1.2.3.4") is True and state.forget("1.2.3.4") is False
    assert state.last_host() is None
    state.remember_device("1.2.3.4", hostname="robot", team=None)
    assert state.last_host() == "1.2.3.4" and state.device("1.2.3.4")["hostname"] == "robot" and "team" not in state.device("1.2.3.4")
    state.record_download("h/a.wpilog", 5)
    assert state.downloaded("h/a.wpilog") == 5 and state.downloaded("h/b") is None


def test_a_damaged_state_file_reads_as_empty():
    state.save("hosts.json", {"x": 1})
    state.key_path().parent.joinpath("hosts.json").write_text("{not json", encoding="utf-8")
    assert state.pinned("x") is None


# ---- the host key rule ----

def test_the_first_key_is_pinned_and_a_different_one_is_refused_with_both_fingerprints(monkeypatch):
    import paramiko

    key_a, key_b = paramiko.RSAKey.generate(2048), paramiko.RSAKey.generate(2048)
    policy = ssh._PinPolicy("10.0.0.2", "hash-old")
    policy.missing_host_key(None, "10.0.0.2", key_a)  # type: ignore[arg-type]
    assert policy.newly_pinned and state.pinned("10.0.0.2")["fingerprint"] == ssh.fingerprint(key_a)
    again = ssh._PinPolicy("10.0.0.2", "hash-old")
    again.missing_host_key(None, "10.0.0.2", key_a)  # type: ignore[arg-type]
    assert not again.newly_pinned
    with pytest.raises(HostKeyChanged) as raised:
        ssh._PinPolicy("10.0.0.2", "hash-new").missing_host_key(None, "10.0.0.2", key_b)  # type: ignore[arg-type]
    message = str(raised.value)
    assert ssh.fingerprint(key_a) in message and ssh.fingerprint(key_b) in message and "OS image changed" in message
    with pytest.raises(HostKeyChanged, match="suspicious"):
        ssh._PinPolicy("10.0.0.2", "hash-old").missing_host_key(None, "10.0.0.2", key_b)  # type: ignore[arg-type]


# ---- bundles ----

async def test_a_bad_download_is_reported_as_one_shot(tmp_path):
    bad = tmp_path / "x.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(SystemcoreError, match="one-shot"):
        bundle.inspect_zip(bad)
    good = tmp_path / "g.zip"
    good.write_bytes(make_zip())
    assert bundle.inspect_zip(good) == {"members": ["logs.zip", "manifest.bin"], "member_count": 2}
    assert zipfile.is_zipfile(good) and asyncio.get_running_loop() is not None


def test_hot_cpu_warning_names_the_poweroff_limit_and_an_unknown_update_is_silent():
    assert "95 C" in " ".join(info.warnings_for({"temps": {"cpu_c": 88.0}}))
    assert info.warnings_for({"os": {"release": "limelightosr-2027.0.0-alpha14"}}) == []
    update = {"os": {"release": "limelightosr-2027.0.0-alpha14"}, "os_update": {"tag": "limelightosr-2027.0.0-alpha18-422"}}
    assert "alpha18" in " ".join(info.warnings_for(update))


def test_ports_can_be_overridden_for_a_forwarded_or_test_controller(monkeypatch):
    from frc_mcp_systemcore import config

    assert net.port("ssh") == 22 and net.port("hwmon") == 4802 and net.port("discovery") == 5808
    monkeypatch.setenv("SYSTEMCORE_PORTS", "ssh=2222, hwmon=14802,,")
    assert config.ports() == {"ssh": 2222, "hwmon": 14802} and net.port("ssh") == 2222 and net.port("diag") == 4800
    for bad in ("ssh", "ssh=", "ssh=0", "ssh=70000", "ssh=x", "web=80", "=22"):
        monkeypatch.setenv("SYSTEMCORE_PORTS", bad)
        with pytest.raises(SystemcoreError, match="SYSTEMCORE_PORTS"):
            config.ports()


def test_the_journal_command_takes_only_a_unit_name_and_a_priority():
    assert logs.journal_command(5, "robot", "err", -1) == "journalctl -b -1 --no-pager -q -o short-iso -n 5 -u robot -p err"
    assert logs.journal_command(60, None, None, 0) == "journalctl -b 0 --no-pager -q -o short-iso -n 60"
    for bad in ("robot; reboot", "a b", "", "x" * 65, "$(id)"):
        with pytest.raises(SystemcoreError, match="unit name"):
            logs.journal_command(5, bad, None, 0)


def test_deleting_logs_reports_a_failure_and_keeps_that_folder():
    session = FakeSession({"s1/a.hoot": b"x", "s1/b.hoot": b"x", "s2/c.hoot": b"x", "top.wpilog": b"x"})
    real_remove = session.remove

    def remove(path):
        if path.endswith("b.hoot"):
            raise OSError("busy")
        real_remove(path)

    session.remove = remove
    entries = [logs.Entry(p, 1, 0.0, logs.kind_of(p), None, False) for p in ("s1/a.hoot", "s1/b.hoot", "s2/c.hoot", "top.wpilog")]
    deleted, failed = logs.delete(session, entries)
    assert deleted == 3 and failed == [{"path": "s1/b.hoot", "err": "busy"}]
    assert session.rmdirs == ["s2"]  # s1 still holds b.hoot, so only the emptied folder went


@pytest.mark.parametrize("spelling", ["fe80::1", "[fe80::1]", "FE80::1", "fe80::1%usb0", "[fe80::0001]", " fe80::1 "])
def test_one_address_is_one_host_key_pin_whatever_its_spelling(spelling):
    state.pin("fe80::1", "SHA256:abc", "hash1")
    assert state.pinned(spelling)["fingerprint"] == "SHA256:abc"
    state.pin(spelling, "SHA256:def")  # re-pinning under another spelling replaces the pin, it does not add a second one
    assert state.pinned("fe80::1")["fingerprint"] == "SHA256:def"
    assert state.forget(spelling) is True and state.pinned("fe80::1") is None


def test_a_name_pin_ignores_case_and_a_final_dot_and_an_old_spelling_is_still_found():
    state.pin("Robot.LOCAL.", "SHA256:abc")
    assert state.pinned("robot.local")["fingerprint"] == "SHA256:abc"
    state.save("hosts.json", {"Old.Name": {"fingerprint": "SHA256:old"}})  # stored as typed by an earlier version
    assert state.pinned("Old.Name")["fingerprint"] == "SHA256:old"
    assert state.forget("Old.Name") is True
