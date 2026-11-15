"""The Wi-Fi join and leave: the netsh and nmcli wrappers, and systemcore_connect / systemcore_disconnect."""

import json
import os

import pytest
from envelope_helpers import j
from fastmcp.exceptions import ToolError

from frc_mcp_systemcore import discovery, wifi
from frc_mcp_systemcore import state as server_state
from frc_mcp_systemcore.errors import SystemcoreError

SECRET = "s3cret&<pw>"
NETSH_INTERFACES = """There is 1 interface on the system:

    Name                   : Wi-Fi
    State                  : connected
    SSID                   : SYSTEMCORE-ABCD
    BSSID                  : aa:bb:cc:dd:ee:ff
    Network type           : Infrastructure
"""
NETSH_NETWORKS = """Interface name : Wi-Fi
There are 3 networks currently visible.

SSID 1 : SYSTEMCORE-ABCD
    Network type            : Infrastructure
SSID 2 : Home Network
SSID 3 :
    Network type            : Infrastructure
SSID 4 : SYSTEMCORE
"""


async def call(client, name, args=None):
    return j(await client.call_tool(name, args or {}))


class Commands:
    """A stand-in for wifi._run: records every command, answers from a table, and remembers the Windows profile file's content."""

    def __init__(self, answers):
        self.answers, self.calls, self.stdin, self.profile = answers, [], [], None

    def __call__(self, args, stdin=None, timeout=30):
        self.calls.append(args)
        self.stdin.append(stdin)
        if args[:4] == ["netsh", "wlan", "add", "profile"]:
            self.profile = open(args[4].removeprefix("filename="), encoding="utf-8").read()  # noqa: SIM115
        return next((answer for key, answer in self.answers.items() if args[: len(key)] == list(key)), (0, ""))


def windows(monkeypatch, answers):
    runner = Commands({("netsh", "wlan", "show", "interfaces"): (0, NETSH_INTERFACES), ("netsh", "wlan", "show", "networks"): (0, NETSH_NETWORKS), **answers})
    monkeypatch.setattr(wifi, "platform", lambda: "win32")
    monkeypatch.setattr(wifi, "_run", runner)
    return runner


def test_reading_the_current_network_and_the_scan_on_windows(monkeypatch):
    windows(monkeypatch, {})
    assert wifi.current_ssid() == "SYSTEMCORE-ABCD"  # not the BSSID line
    assert wifi.scan() == ["SYSTEMCORE-ABCD", "Home Network", "SYSTEMCORE"]  # a hidden network (empty name) is left out


def test_picking_the_systemcore_network():
    assert wifi.pick_systemcore(["Home", "SYSTEMCORE-ABCD"]) == "SYSTEMCORE-ABCD"
    with pytest.raises(SystemcoreError, match="No SYSTEMCORE"):
        wifi.pick_systemcore(["Home"])
    with pytest.raises(SystemcoreError, match="Several"):
        wifi.pick_systemcore(["SYSTEMCORE-A", "SYSTEMCORE-B"])


def test_joining_on_windows_writes_an_escaped_profile_and_removes_it(monkeypatch):
    runner = windows(monkeypatch, {("netsh", "wlan", "show", "interfaces"): (0, NETSH_INTERFACES.replace("SYSTEMCORE-ABCD", "Home"))})
    states = iter(["Home", "FRC-9999"])  # before the join, then after it
    monkeypatch.setattr(wifi, "current_ssid", lambda: next(states))
    left = wifi.join("FRC-9999", SECRET, wait_s=3)
    assert left == "Home"
    assert [c[:3] for c in runner.calls if c[2] != "show"] == [["netsh", "wlan", "add"], ["netsh", "wlan", "connect"]]
    assert "s3cret&amp;&lt;pw&gt;" in runner.profile and "<name>FRC-9999</name>" in runner.profile and "<keyType>passPhrase</keyType>" in runner.profile
    assert not os.path.exists(next(c for c in runner.calls if c[2] == "add")[3].removeprefix("filename="))  # the profile file is gone
    assert all(SECRET not in " ".join(call) for call in runner.calls)  # never on a command line


def test_a_failed_join_never_repeats_the_password(monkeypatch):
    windows(monkeypatch, {("netsh", "wlan", "add"): (1, f"bad key {SECRET}")})
    with pytest.raises(SystemcoreError) as error:
        wifi.join("FRC-9999", SECRET, wait_s=1)
    assert SECRET not in str(error.value) and "***" in str(error.value)


def test_joining_that_never_takes_effect_says_so(monkeypatch):
    windows(monkeypatch, {})
    monkeypatch.setattr(wifi, "current_ssid", lambda: "Home")
    monkeypatch.setattr(wifi.time, "sleep", lambda _: None)
    with pytest.raises(SystemcoreError, match="not on it"):
        wifi.join("FRC-9999", SECRET, wait_s=0.01)


def test_joining_on_linux_gives_the_password_on_stdin_not_the_command_line(monkeypatch):
    runner = Commands({("nmcli", "-t", "-f", "active,ssid"): (0, "no:Other\nyes:FRC-9999\n")})
    monkeypatch.setattr(wifi, "platform", lambda: "linux")
    monkeypatch.setattr(wifi, "_run", runner)
    wifi.join("FRC-9999", SECRET, wait_s=2)
    connect = next(i for i, call in enumerate(runner.calls) if "connect" in call)
    assert runner.calls[connect] == ["nmcli", "--ask", "device", "wifi", "connect", "FRC-9999"] and runner.stdin[connect] == SECRET + "\n"
    assert all(SECRET not in " ".join(call) for call in runner.calls)


def test_joining_is_not_supported_on_macos(monkeypatch):
    monkeypatch.setattr(wifi, "platform", lambda: "darwin")
    monkeypatch.setattr(wifi, "_run", Commands({}))
    with pytest.raises(SystemcoreError, match="not supported"):
        wifi.join("FRC-9999", SECRET)


class WifiLog(list):
    """The joins made (ssid, password), plus what the fake Wi-Fi did and what is saved on the computer."""

    def __init__(self):
        super().__init__()
        self.left, self.saved, self.current, self.reachable = [], {"Home"}, ["Home"], False  # reachable: a Systemcore answers before any join


@pytest.fixture
def wifi_ready(monkeypatch):
    """Joining allowed, a Windows-style scan, and a controller that answers the UDP query after the join."""
    monkeypatch.setenv("SYSTEMCORE_ALLOW_WIFI_JOIN", "1")
    for name in ("SYSTEMCORE_TEAM", "SYSTEMCORE_RADIO_SSID", "SYSTEMCORE_WIFI_SSID", "SYSTEMCORE_RADIO_PASSWORD", "SYSTEMCORE_WIFI_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    joined = WifiLog()
    current = ["Home"]

    def fake_join(ssid, password, wait_s):
        joined.append((ssid, password))
        current[0] = ssid
        return "Home"

    async def found(*args, **kwargs):  # the controller answers after a join, or from the start when `reachable` is set
        return [{"host": "172.30.0.1", "hostname": "robot", "interfaces": {}}] if joined or joined.reachable else []

    monkeypatch.setattr(wifi, "scan", lambda: ["Home", "SYSTEMCORE-ABCD"])
    monkeypatch.setattr(wifi, "current_ssid", lambda: current[0])
    def fake_leave(ssid, wait_s=10):
        joined.left.append(ssid)
        current[0] = None

    joined.current = current
    monkeypatch.setattr(wifi, "join", fake_join)
    monkeypatch.setattr(wifi, "has_profile", lambda ssid: ssid in joined.saved)
    monkeypatch.setattr(wifi, "leave", fake_leave)
    monkeypatch.setattr(discovery, "discover", found)
    return joined


async def test_connect_is_off_until_the_owner_allows_it(client, wifi_ready, monkeypatch):
    monkeypatch.delenv("SYSTEMCORE_ALLOW_WIFI_JOIN", raising=False)
    with pytest.raises(ToolError, match="SYSTEMCORE_ALLOW_WIFI_JOIN=1"):
        await client.call_tool("systemcore_connect", {})


async def test_connect_needs_a_saved_password_and_never_asks_for_one_in_chat(client, wifi_ready):
    with pytest.raises(ToolError, match="SYSTEMCORE_WIFI_PASSWORD") as error:
        await client.call_tool("systemcore_connect", {})
    assert "never taken from chat" in str(error.value) and wifi_ready == []


async def test_connect_joins_the_one_systemcore_network_and_reports_what_it_left(client, wifi_ready, monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_WIFI_PASSWORD", SECRET)
    monkeypatch.setenv("SYSTEMCORE_WIFI_SSID", "SYSTEMCORE-ABCD")
    out = await call(client, "systemcore_connect")
    assert wifi_ready == [("SYSTEMCORE-ABCD", SECRET)]
    assert out["ssid"] == "SYSTEMCORE-ABCD" and out["joined"] is True and out["left"] == "Home" and out["devices"] == [{"host": "172.30.0.1", "hostname": "robot"}]
    assert SECRET not in json.dumps(out)
    again = await call(client, "systemcore_connect")
    assert again["already_reachable"] is True and len(wifi_ready) == 1  # the controller answers now: nothing more to do


async def test_connect_to_the_radio_uses_the_team_number(client, wifi_ready, monkeypatch):
    with pytest.raises(ToolError, match="SYSTEMCORE_TEAM"):
        await client.call_tool("systemcore_connect", {"network": "radio"})
    monkeypatch.setenv("SYSTEMCORE_TEAM", "9999")
    monkeypatch.setenv("SYSTEMCORE_RADIO_PASSWORD", "radio-pw")
    out = await call(client, "systemcore_connect", {"network": "radio"})
    assert wifi_ready == [("FRC-9999", "radio-pw")] and out["ssid"] == "FRC-9999"


async def test_connect_uses_a_network_already_saved_on_this_computer_without_a_password(client, wifi_ready, monkeypatch):
    monkeypatch.setattr(wifi, "has_profile", lambda ssid: True)
    out = await call(client, "systemcore_connect")
    assert wifi_ready == [("SYSTEMCORE-ABCD", None)] and out["joined"] is True


def test_saved_networks_are_found_and_a_saved_join_adds_no_profile(monkeypatch):
    profiles = "User profiles\n-------------\n    All User Profile     : SYSTEMCORE\n    All User Profile     : Home Network\n"
    runner = windows(monkeypatch, {("netsh", "wlan", "show", "profiles"): (0, profiles)})
    assert wifi.has_profile("SYSTEMCORE") and not wifi.has_profile("SYSTEM")
    states = iter(["Home", "SYSTEMCORE"])
    monkeypatch.setattr(wifi, "current_ssid", lambda: next(states))
    wifi.join("SYSTEMCORE", None, wait_s=3)
    assert [c[:3] for c in runner.calls if c[2] != "show"] == [["netsh", "wlan", "connect"]]


async def test_connect_needs_no_password_when_already_on_the_systemcore_network(client, wifi_ready, monkeypatch):
    monkeypatch.setattr(wifi, "current_ssid", lambda: "SYSTEMCORE-ABCD")
    out = await call(client, "systemcore_connect")
    assert out["already_connected"] is True and out["ssid"] == "SYSTEMCORE-ABCD" and wifi_ready == []


async def test_disconnect_is_off_until_the_owner_allows_it(client, monkeypatch):
    monkeypatch.delenv("SYSTEMCORE_ALLOW_WIFI_JOIN", raising=False)
    with pytest.raises(ToolError, match="SYSTEMCORE_ALLOW_WIFI_JOIN=1"):
        await client.call_tool("systemcore_disconnect", {})


async def test_disconnect_leaves_and_goes_back_to_the_network_connect_dropped(client, wifi_ready, monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_WIFI_PASSWORD", SECRET)
    monkeypatch.setenv("SYSTEMCORE_WIFI_SSID", "SYSTEMCORE-ABCD")
    await call(client, "systemcore_connect")  # leaves Home, joins SYSTEMCORE-ABCD, and records it
    extra = wifi_ready
    out = await call(client, "systemcore_disconnect")
    assert extra.left == ["SYSTEMCORE-ABCD"] and out["disconnected"] is True and out["rejoined"] == "Home"
    assert extra.current[0] == "Home" and wifi_ready[-1] == ("Home", None)  # rejoined without a password
    assert SECRET not in json.dumps(out)
    again = await call(client, "systemcore_disconnect")
    assert again["connected"] is False and extra.left == ["SYSTEMCORE-ABCD"]  # not on it any more: nothing done


async def test_disconnect_leaves_alone_a_network_connect_did_not_join(client, wifi_ready, monkeypatch):
    monkeypatch.setattr(wifi, "current_ssid", lambda: "SYSTEMCORE-ABCD")  # the user joined it themselves
    out = await call(client, "systemcore_disconnect")
    assert out["disconnected"] is False and "did not join" in out["hint"] and wifi_ready.left == []


async def test_disconnect_can_stay_off_the_old_network(client, wifi_ready, monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_WIFI_PASSWORD", SECRET)
    monkeypatch.setenv("SYSTEMCORE_WIFI_SSID", "SYSTEMCORE-ABCD")
    await call(client, "systemcore_connect")
    quiet = await call(client, "systemcore_disconnect", {"rejoin": False})
    assert quiet["disconnected"] is True and "rejoined" not in quiet and wifi_ready.current[0] is None


async def test_disconnect_reports_when_it_cannot_go_back(client, wifi_ready, monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_WIFI_PASSWORD", SECRET)
    monkeypatch.setenv("SYSTEMCORE_WIFI_SSID", "SYSTEMCORE-ABCD")
    await call(client, "systemcore_connect")
    wifi_ready.saved.clear()  # Home is no longer saved on this computer
    out = await call(client, "systemcore_disconnect")
    assert out["disconnected"] is True and "rejoined" not in out and "Wi-Fi menu" in out["rejoin_failed"]


async def test_connect_changes_nothing_when_a_systemcore_already_answers(client, wifi_ready, monkeypatch):
    wifi_ready.reachable = True  # on its Wi-Fi, USB or Ethernet already
    monkeypatch.delenv("SYSTEMCORE_ALLOW_WIFI_JOIN", raising=False)  # no switch is needed when nothing changes
    out = await call(client, "systemcore_connect")
    assert out["already_reachable"] is True and out["devices"][0]["host"] == "172.30.0.1" and wifi_ready == [] and wifi_ready.current[0] == "Home"


async def test_connect_sees_a_controller_at_the_saved_address(client, device, wifi_ready):
    out = await call(client, "systemcore_connect")  # SYSTEMCORE_HOST points at the fake controller; UDP finds nothing
    assert out["already_reachable"] is True and out["devices"] == [{"host": "127.0.0.1", "hostname": None}] and wifi_ready == []


def test_leaving_on_windows_and_linux_and_never_deleting_a_saved_network(monkeypatch):
    runner = windows(monkeypatch, {})
    states = iter(["SYSTEMCORE", None])
    monkeypatch.setattr(wifi, "current_ssid", lambda: next(states))
    wifi.leave("SYSTEMCORE", wait_s=3)
    assert runner.calls == [["netsh", "wlan", "disconnect"]]
    assert not hasattr(wifi, "delete_profile")  # saved networks are never removed
    linux = Commands({})
    monkeypatch.setattr(wifi, "platform", lambda: "linux")
    monkeypatch.setattr(wifi, "_run", linux)
    monkeypatch.setattr(wifi, "current_ssid", lambda: None)
    wifi.leave("SYSTEMCORE")
    assert linux.calls == [["nmcli", "connection", "down", "id", "SYSTEMCORE"]]
    monkeypatch.setattr(wifi, "platform", lambda: "darwin")
    with pytest.raises(SystemcoreError, match="not supported"):
        wifi.leave("x")


def test_a_leave_that_never_takes_effect_says_so(monkeypatch):
    windows(monkeypatch, {})
    monkeypatch.setattr(wifi, "current_ssid", lambda: "SYSTEMCORE")
    monkeypatch.setattr(wifi.time, "sleep", lambda _: None)
    with pytest.raises(SystemcoreError, match="still on it"):
        wifi.leave("SYSTEMCORE", wait_s=0.01)


async def test_disconnect_does_not_scan_or_fail_when_this_computer_is_elsewhere(client, wifi_ready, monkeypatch):
    def no_scan():
        raise AssertionError("scanning is not needed to know we are not connected")

    monkeypatch.setattr(wifi, "scan", no_scan)
    out = await call(client, "systemcore_disconnect")  # on Home; the Systemcore network may even be out of range
    assert out["connected"] is False and wifi_ready.left == []
    monkeypatch.setattr(wifi, "current_ssid", lambda: None)  # not on any Wi-Fi at all
    assert (await call(client, "systemcore_disconnect"))["connected"] is False


async def test_an_old_join_record_is_not_trusted(client, wifi_ready, monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_WIFI_PASSWORD", SECRET)
    monkeypatch.setenv("SYSTEMCORE_WIFI_SSID", "SYSTEMCORE-ABCD")
    await call(client, "systemcore_connect")
    record = server_state.load("wifi.json")
    record["SYSTEMCORE-ABCD"]["at"] -= 13 * 3600  # joined yesterday, and the user may have rejoined by hand since
    server_state.save("wifi.json", record)
    out = await call(client, "systemcore_disconnect")
    assert out["disconnected"] is False and "recently" in out["hint"] and wifi_ready.left == []


def test_a_saved_profile_is_found_by_its_ssid_whatever_it_is_called(monkeypatch):
    profiles = "User profiles\n    All User Profile     : Garage Robot\n    All User Profile     : Home Network\n"
    detail = 'Profile Garage Robot on interface Wi-Fi:\n    SSID name              : "SYSTEMCORE"\n'
    runner = windows(monkeypatch, {("netsh", "wlan", "show", "profiles"): (0, profiles),
                                   ("netsh", "wlan", "show", "profile", "name=Garage Robot"): (0, detail)})
    assert wifi.saved_profile("SYSTEMCORE") == "Garage Robot" and wifi.saved_profile("Home Network") == "Home Network"
    assert wifi.saved_profile("Elsewhere") is None
    states = iter(["Home", "SYSTEMCORE"])
    monkeypatch.setattr(wifi, "current_ssid", lambda: next(states))
    wifi.join("SYSTEMCORE", None, wait_s=3)
    assert ["netsh", "wlan", "connect", "name=Garage Robot", "ssid=SYSTEMCORE"] in runner.calls


async def test_a_saved_password_without_a_network_name_joins_nothing(client, wifi_ready, monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_WIFI_PASSWORD", SECRET)  # no SYSTEMCORE_WIFI_SSID: any SYSTEMCORE network could be an impostor
    with pytest.raises(ToolError, match="SYSTEMCORE_WIFI_SSID") as error:
        await client.call_tool("systemcore_connect", {})
    assert wifi_ready == [] and SECRET not in str(error.value)


async def test_a_network_name_in_settings_is_the_only_one_joined(client, wifi_ready, monkeypatch):
    monkeypatch.setenv("SYSTEMCORE_WIFI_PASSWORD", SECRET)
    monkeypatch.setenv("SYSTEMCORE_WIFI_SSID", "SYSTEMCORE-MINE")
    monkeypatch.setattr(wifi, "scan", lambda: ["SYSTEMCORE-ABCD", "SYSTEMCORE-MINE"])
    out = await call(client, "systemcore_connect")
    assert wifi_ready == [("SYSTEMCORE-MINE", SECRET)] and out["ssid"] == "SYSTEMCORE-MINE"


def test_the_windows_profile_folder_is_removed_even_when_netsh_fails(monkeypatch):
    seen = []

    def runner(args, stdin=None, timeout=30):
        if args[:4] == ["netsh", "wlan", "add", "profile"]:
            path = args[4].removeprefix("filename=")
            seen.append(path)
            assert os.path.exists(path)
            return 1, "no"
        return 0, ""

    monkeypatch.setattr(wifi, "platform", lambda: "win32")
    monkeypatch.setattr(wifi, "_run", runner)
    monkeypatch.setattr(wifi, "current_ssid", lambda: None)
    with pytest.raises(SystemcoreError):
        wifi.join("FRC-9999", SECRET, wait_s=1)
    assert seen and not os.path.exists(seen[0]) and not os.path.exists(os.path.dirname(seen[0]))


def test_a_join_that_fails_removes_the_profile_it_added_but_never_a_saved_one(monkeypatch):
    runner = windows(monkeypatch, {("netsh", "wlan", "connect"): (1, "failed")})
    monkeypatch.setattr(wifi, "current_ssid", lambda: "Home")
    with pytest.raises(SystemcoreError, match="Could not join"):
        wifi.join("FRC-9999", SECRET, wait_s=1)
    assert ["netsh", "wlan", "delete", "profile", "name=FRC-9999"] in runner.calls
    saved = windows(monkeypatch, {("netsh", "wlan", "connect"): (1, "failed"), ("netsh", "wlan", "show", "profiles"): (0, "    All User Profile     : FRC-9999\n")})
    with pytest.raises(SystemcoreError):
        wifi.join("FRC-9999", None, wait_s=1)  # a profile that was saved before this call
    assert not any(c[2] == "delete" for c in saved.calls)


def test_a_failed_join_keeps_a_profile_the_user_had_saved_under_that_name(monkeypatch):
    runner = windows(monkeypatch, {("netsh", "wlan", "connect"): (1, "failed"), ("netsh", "wlan", "show", "profiles"): (0, "    All User Profile     : FRC-9999\n")})
    monkeypatch.setattr(wifi, "current_ssid", lambda: "Home")
    with pytest.raises(SystemcoreError, match="Could not join"):
        wifi.join("FRC-9999", SECRET, wait_s=1)
    assert not any(c[2] == "delete" for c in runner.calls)


def test_a_connect_that_cannot_run_still_removes_the_profile_with_the_password(monkeypatch):
    runner = windows(monkeypatch, {})

    def connect_breaks(args, stdin=None, timeout=30):
        if args[:3] == ["netsh", "wlan", "connect"]:
            raise SystemcoreError("Could not run netsh: timed out")
        return runner(args, stdin, timeout)

    monkeypatch.setattr(wifi, "_run", connect_breaks)
    monkeypatch.setattr(wifi, "current_ssid", lambda: "Home")
    with pytest.raises(SystemcoreError, match="timed out"):
        wifi.join("FRC-9999", SECRET, wait_s=1)
    assert ["netsh", "wlan", "delete", "profile", "name=FRC-9999"] in runner.calls


def test_a_failed_join_on_linux_removes_the_connection_it_saved_but_never_a_saved_one(monkeypatch):
    runner = Commands({("nmcli", "--ask"): (4, "Secrets were required")})
    monkeypatch.setattr(wifi, "platform", lambda: "linux")
    monkeypatch.setattr(wifi, "_run", runner)
    monkeypatch.setattr(wifi, "current_ssid", lambda: "Home")
    with pytest.raises(SystemcoreError, match="Could not join"):
        wifi.join("FRC-9999", SECRET, wait_s=1)
    assert ["nmcli", "connection", "delete", "id", "FRC-9999"] in runner.calls
    saved = Commands({("nmcli", "--ask"): (4, "failed"), ("nmcli", "-t", "-f", "name,type"): (0, "FRC-9999:802-11-wireless\n")})
    monkeypatch.setattr(wifi, "_run", saved)
    with pytest.raises(SystemcoreError):
        wifi.join("FRC-9999", SECRET, wait_s=1)
    assert not any("delete" in c for c in saved.calls)


def test_leaving_on_windows_never_drops_another_network(monkeypatch):
    runner = windows(monkeypatch, {})
    monkeypatch.setattr(wifi, "current_ssid", lambda: "School")
    wifi.leave("SYSTEMCORE", wait_s=1)
    assert runner.calls == []
