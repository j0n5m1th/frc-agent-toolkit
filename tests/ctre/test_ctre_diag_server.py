"""The Phoenix Diagnostic Server server through the MCP protocol, against a real local HTTP server that answers the way the real one was seen to (see diag_fake.py)."""

import pytest
from ctre_helpers import call
from fake_diag import BUS, BUS2, PIGEON, FakeDiag, Server
from fastmcp import Client
from fastmcp.exceptions import ToolError

from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre_diag import client as diag_client
from frc_mcp_ctre_diag.spec import SPEC
from frc_mcp_ctre_diag.tools import diag_tools

mcp, apply_toolset = SPEC.load()


@pytest.fixture
def fake(monkeypatch, index_file):
    state = FakeDiag()
    with Server(state) as server:
        monkeypatch.setenv("DIAG_HOST", "127.0.0.1")
        monkeypatch.setenv("DIAG_PORT", str(server.port))
        yield state


@pytest.fixture
async def client(fake):
    async with Client(mcp) as c:
        yield c


async def test_the_server_offers_six_tools(client):
    assert sorted(t.name for t in await client.list_tools()) == ["diag_blink", "diag_bus", "diag_config", "diag_device", "diag_read", "diag_status"]


async def test_the_core_toolset_hides_config_and_blink(client, monkeypatch):
    monkeypatch.setenv("DIAG_TOOLSET", "core")
    try:
        apply_toolset()
        async with Client(mcp) as core:
            assert sorted(t.name for t in await core.list_tools()) == ["diag_bus", "diag_device", "diag_read", "diag_status"]
    finally:
        apply_toolset("full")
    monkeypatch.setenv("DIAG_TOOLSET", "tiny")
    with pytest.raises(CtreError, match="DIAG_TOOLSET must be 'full' or 'core'"):
        apply_toolset()


# -- status ----------------------------------------------------------------------------------------------------------


async def test_status_gives_the_server_the_devices_as_rows_and_what_ctre_lists_for_its_level(client):
    out = await call(client, "diag_status")
    assert out["server"] == {"version": "2026.70.0 (Sep 18 2026, 00:15:58)", "compliancy": 21, "release": "FRC2026", "system": "Systemcore", "listed": "26.70.0-alpha-2"}
    assert out["cols"] == ["name", "model", "id", "fw", "bus", "status"]
    assert out["devices"][0] == ["0 - DT-GYRO", "Pigeon 2 vers. S", 0, "26.70.0.0 (Phoenix 6)", "canfd", "Running Application."]
    assert len(out["devices"]) == 3 and "bus_util_pct" not in out and "hint" not in out  # the server answers -1 for a bus use it does not know


async def test_status_with_no_devices_says_what_to_check(client, fake):
    fake.devices = []
    out = await call(client, "diag_status")
    assert out["devices"] == [] and "CAN wiring" in out["hint"]


async def test_a_known_bus_use_is_reported_and_an_unknown_platform_has_no_listed_version(client, fake, monkeypatch):
    original = fake.reply

    def other(params):
        status, body = original(params)
        return status, body.replace(b'"Systemcore"', b'"SomethingElse"').replace(b'"BusUtilPerc": -1.0', b'"BusUtilPerc": 12.34')

    monkeypatch.setattr(fake, "reply", other)
    out = await call(client, "diag_status")
    assert out["bus_util_pct"] == 12.3 and "listed" not in out["server"]


async def test_no_server_listening_is_an_error_that_says_where_it_looked(monkeypatch):
    monkeypatch.setenv("DIAG_HOST", "127.0.0.1")
    monkeypatch.setenv("DIAG_PORT", "9")  # nothing listens there
    async with Client(mcp) as c:
        with pytest.raises(ToolError, match=r"No Phoenix diagnostic server answered at 127\.0\.0\.1:9.*Set DIAG_HOST or pass `host`"):
            await call(c, "diag_status")


@pytest.mark.parametrize(("mode", "message"), [("http500", "answered HTTP 500"), ("html", "is not JSON"), ("nogeneral", "this is not a Phoenix diagnostic server")])
async def test_something_that_is_not_a_diagnostic_server_is_recognised(client, fake, mode, message):
    fake.mode = mode
    with pytest.raises(ToolError, match=message):
        await call(client, "diag_status")


# -- which hosts ------------------------------------------------------------------------------------------------------


async def test_only_robot_network_addresses_are_contacted_unless_the_owner_allows_more(client, fake):
    with pytest.raises(ToolError, match="not a private address.*DIAG_ALLOW_ANY_HOST=1"):
        await call(client, "diag_status", {"host": "8.8.8.8"})
    with pytest.raises(ToolError, match="not a host name or IP address"):
        await call(client, "diag_status", {"host": "bad host!"})
    assert fake.requests == []


async def test_a_name_is_checked_for_every_address_and_connected_to_by_its_literal(client, fake, monkeypatch):
    import socket

    real = socket.getaddrinfo

    def lookup(host, port, family=0, *args, **kwargs):
        if host == "robot.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))] + ([(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", 0, 0, 0))] if public[0] else [])
        return real(host, port, family, *args, **kwargs)

    public = [True]
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    with pytest.raises(ToolError, match="not a private address"):
        await call(client, "diag_status", {"host": "robot.example"})  # one public (AAAA) answer is enough to refuse
    assert fake.requests == []
    public[0] = False
    await call(client, "diag_status", {"host": "robot.example"})  # the name exists only in this fake: the request went to 127.0.0.1
    assert fake.requests


async def test_the_default_host_is_the_systemcores_own_wifi_address(monkeypatch):
    from frc_mcp_ctre_diag import config

    monkeypatch.delenv("DIAG_HOST", raising=False)
    assert config.host() == "172.30.0.1" and config.host("192.168.1.5") == "192.168.1.5"


# -- what is ever sent ------------------------------------------------------------------------------------------------


async def test_only_read_actions_and_the_blink_are_ever_sent(client, fake):
    await call(client, "diag_status")
    await call(client, "diag_bus")
    await call(client, "diag_device", {"device": "0", "details": True})
    await call(client, "diag_read", {"device": "0", "signals": ["Yaw"]})
    await call(client, "diag_config", {"device": "0", "all": True})
    await call(client, "diag_blink", {"device": "0"})
    assert set(fake.actions()) <= diag_client.ALLOWED_ACTIONS
    assert {"getversion", "getdevices", "getcanbusstats", "deviceinformation", "getsignals", "getsignalvalue", "getconfigv2", "blink"} == set(fake.actions())


@pytest.mark.parametrize("action", ["setconfigv2", "setid", "setname", "factorydef", "fieldupgrade", "activatelicense", "runcaniv", "pigeon2workaround", "selftest", "anything"])
def test_an_action_that_changes_a_device_cannot_be_sent_at_all(action):
    from frc_mcp_ctre.errors import CtreError

    with pytest.raises(CtreError, match="is not an action this server sends"):
        diag_client._request_sync("127.0.0.1", action, {}, 1)


async def test_a_device_is_addressed_by_its_exact_model_id_and_bus_with_spaces_encoded(client, fake):
    await call(client, "diag_blink", {"device": "0 - DT-GYRO"})
    assert fake.requests[-1] == {"action": "blink", "model": PIGEON, "id": "0", "canbus": BUS}


# -- buses ------------------------------------------------------------------------------------------------------------


async def test_bus_statistics_list_only_the_counters_that_are_not_zero(client):
    out = await call(client, "diag_bus")
    assert out["buses"] == [{"bus": "canfd", "util_pct": 2.0, "tec": 0, "rec": 0, "state": 0},
                            {"bus": "can_s1", "util_pct": 41.5, "tec": 9, "rec": 3, "state": 1, "counters": {"ArbitrationLostCnt": 2, "BusErrorCnt": 7, "BusOffCnt": 1}}]


async def test_one_bus_by_its_label_or_its_serial_and_an_unknown_one_is_passed_to_the_server(client, fake):
    assert [b["bus"] for b in (await call(client, "diag_bus", {"bus": "CAN_S1"}))["buses"]] == ["can_s1"]
    assert [b["bus"] for b in (await call(client, "diag_bus", {"bus": BUS}))["buses"]] == ["canfd"]
    with pytest.raises(ToolError, match="not found"):  # the server's own answer; a bus no device was seen on may still exist there
        await call(client, "diag_bus", {"bus": "nope"})
    assert fake.requests[-1] == {"action": "getcanbusstats", "canbus": "nope"}


async def test_a_bus_with_error_counters_at_128_is_marked_passive(client, fake):
    fake.stats[BUS2]["TEC"] = 128
    rows = (await call(client, "diag_bus"))["buses"]
    assert [r.get("passive") for r in rows] == [None, True]


async def test_a_bus_whose_devices_all_vanished_is_still_reported(client, fake):
    """Seen with the CAN low wire pulled: the device list went empty while the CANivore's bus counters showed the failure."""
    await call(client, "diag_status")  # the buses are remembered from the devices listed here
    fake.devices = []
    fake.stats[BUS].update(TEC=128, ArbitrationLostCnt=9046, CANState=2)
    out = await call(client, "diag_bus")
    assert out["devices"] == 0
    assert [b["bus"] for b in out["buses"]] == ["canfd", "can_s1"]
    assert out["buses"][0]["passive"] is True and out["buses"][0]["counters"] == {"ArbitrationLostCnt": 9046}
    assert "Buses seen earlier: canfd, can_s1" in (await call(client, "diag_status"))["hint"]


async def test_a_remembered_bus_that_has_no_device_now_is_marked(client, fake):
    """A device that moved to another bus leaves its old bus remembered; that row says so instead of looking like a failure."""
    await call(client, "diag_status")
    fake.devices = [d for d in fake.devices if d["CANbus"] != BUS2]
    rows = {r["bus"]: r for r in (await call(client, "diag_bus"))["buses"]}
    assert rows["can_s1"]["no_device"] is True and "no_device" not in rows["canfd"]


async def test_a_device_id_that_is_not_a_number_is_a_clear_error(client, fake):
    fake.devices[0]["ID"] = "x"
    with pytest.raises(ToolError, match="device id"):
        await call(client, "diag_status")


async def test_signals_are_asked_again_after_a_firmware_change(client, fake):
    await call(client, "diag_read", {"device": "0"})
    first = fake.actions().count("getsignals")
    fake.devices[0]["CurrentVers"] = "27.0.0.0 (Phoenix 6)"
    await call(client, "diag_read", {"device": "0"})
    assert fake.actions().count("getsignals") == first + 1


async def test_with_no_devices_ever_listed_a_bus_must_be_named(client, fake):
    fake.devices = []
    assert "Name one" in (await call(client, "diag_bus"))["hint"]
    assert [b["bus"] for b in (await call(client, "diag_bus", {"bus": BUS}))["buses"]] == [BUS]  # a serial named by the caller


# -- devices ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["0 - DT-GYRO", "0 - dt-gyro", "pigeon 2 vers. s#0", "Pigeon 2 vers. S", "0"])
async def test_a_device_can_be_named_in_several_ways(client, name):
    assert (await call(client, "diag_device", {"device": name}))["name"] == "0 - DT-GYRO"


async def test_a_name_that_fits_two_devices_or_none_says_which(client):
    with pytest.raises(ToolError, match=r"'5' matches 2 devices \('5 - Turret', '5 - Spare'\)"):
        await call(client, "diag_device", {"device": "5"})
    with pytest.raises(ToolError, match=r"'TalonFX vers\. C' matches 2 devices"):
        await call(client, "diag_device", {"device": "TalonFX vers. C"})
    with pytest.raises(ToolError, match=r"lists no device 'nope'. It lists: '0 - DT-GYRO', '5 - Turret', '5 - Spare'"):
        await call(client, "diag_device", {"device": "nope"})
    assert (await call(client, "diag_device", {"device": "5 - Spare"}))["bus"] == "can_s1"


async def test_device_shows_identity_a_license_summary_without_the_license_ids_and_the_faults(client):
    out = await call(client, "diag_device", {"device": "0"})
    assert out["model"] == PIGEON and out["fw"] == "26.70.0.0 (Phoenix 6)" and out["hw"] == "2.2" and out["status"] == "Running Application."
    assert out["license"] == {"pro": True, "valid": True, "n": 3, "capacity": 5} and "LIC-" not in str(out)  # the ids themselves are never returned
    assert out["faults"] == [] and out["sticky"] == ["Undervoltage", "DataAcquiredLate"] and out["faults_read"] == 5 and "details" not in out


async def test_a_fault_that_is_set_now_is_listed_and_details_come_only_when_asked(client, fake):
    fake.values[PIGEON][1001] = 1.0
    out = await call(client, "diag_device", {"device": "0", "details": True})
    assert out["faults"] == ["Hardware"] and out["details"] == {"Frc Lock": "True", "LED1 On Color": "Orange"}


# -- signals ----------------------------------------------------------------------------------------------------------


async def test_without_signal_names_the_available_signals_are_listed_with_units_and_can_be_filtered(client, monkeypatch):
    out = await call(client, "diag_read", {"device": "0"})
    assert out["total"] == 11 and out["matches"] == 11 and "Yaw (deg)" in out["signals"] and "VersionMajor" in out["signals"] and "Temperature (℃)" in out["signals"]
    monkeypatch.setattr(diag_tools, "MAX_NAMES", 2)
    few = await call(client, "diag_read", {"device": "0", "name_contains": "FAULT"})
    assert few["matches"] == 5 and len(few["signals"]) == 2 and few["omitted"] == 3


async def test_signal_values_are_read_live_by_name_in_any_case_and_rounded(client, fake):
    out = await call(client, "diag_read", {"device": "0", "signals": ["yaw", "SupplyVoltage", "VersionMajor"]})
    assert out == {"device": "0 - DT-GYRO", "values": {"Yaw": 189.2313, "SupplyVoltage": 13.13, "VersionMajor": 26}}
    assert fake.requests[-1]["signals"] == "949,973,734"


async def test_an_unknown_signal_name_suggests_similar_ones_and_too_many_are_refused(client):
    with pytest.raises(ToolError, match=r"has no signal 'Yawn'\. Use diag_read without `signals`"):
        await call(client, "diag_read", {"device": "0", "signals": ["Yawn"]})
    with pytest.raises(ToolError, match=r"has no signal 'Pitc'\. Similar: Pitch\."):
        await call(client, "diag_read", {"device": "0", "signals": ["Pitc"]})
    with pytest.raises(ToolError, match="at most 30"):
        await call(client, "diag_read", {"device": "0", "signals": [f"S{i}" for i in range(31)]})


async def test_a_models_signal_list_is_asked_for_once_per_process(client, fake):
    for _ in range(3):
        await call(client, "diag_read", {"device": "0", "signals": ["Yaw"]})
    await call(client, "diag_read", {"device": "5 - Turret", "signals": ["Position"]})
    assert fake.actions().count("getsignals") == 2  # one per model: the Pigeon 2 and the TalonFX


# -- configs ----------------------------------------------------------------------------------------------------------


async def test_config_lists_only_what_differs_from_the_default_with_booleans_as_booleans(client):
    out = await call(client, "diag_config", {"device": "0"})
    assert out["total"] == 4 and out["differ"] == 2 and out["cols"] == ["group", "name", "value", "default", "units"]
    assert out["rows"] == [["Mount Pose", "Mount Pose Yaw", 90, 0, "deg"], ["Pigeon 2 Features", "Enable Compass", True, False, None]]


async def test_config_all_and_a_name_filter_and_a_limit(client, monkeypatch):
    monkeypatch.setattr(diag_tools, "MAX_CONFIGS", 3)
    everything = await call(client, "diag_config", {"device": "0", "all": True})
    assert len(everything["rows"]) == 3 and everything["omitted"] == 1
    mounted = await call(client, "diag_config", {"device": "0", "all": True, "name_contains": "mount"})
    assert [r[1] for r in mounted["rows"]] == ["Mount Pose Yaw", "Mount Pose Pitch"]


async def test_a_model_without_configs_gets_the_servers_own_words_and_a_hint(client):
    with pytest.raises(ToolError, match=r"not supported for this device model\. \(error -135\)\. This device model does not offer that\."):
        await call(client, "diag_config", {"device": "5 - Turret"})


# -- blink and errors ------------------------------------------------------------------------------------------------


async def test_blink_names_the_device_that_blinked(client, fake):
    assert await call(client, "diag_blink", {"device": "5 - Spare"}) == {"device": "5 - Spare", "blinked": True}
    assert fake.requests[-1]["canbus"] == BUS2


async def test_an_error_code_inside_an_http_200_answer_becomes_a_message_with_a_hint(client, fake, monkeypatch):
    original = fake.reply

    def vanished(params):
        if params.get("action") == "deviceinformation":  # listed by getdevices, but gone when asked about (a cable pulled between the two requests)
            return 200, b'{"GeneralReturn":{"Action":"deviceinformation","CANbus":"","Error":-120,"ErrorMessage":"Specified device was not found. Verify the device is connected and powered, and that the CAN bus is terminated.","ID":0,"Model":""}}'
        return original(params)

    monkeypatch.setattr(fake, "reply", vanished)
    with pytest.raises(ToolError, match=r"Specified device was not found.*\(error -120\)\. Check the device is powered"):
        await call(client, "diag_device", {"device": "0 - DT-GYRO", "details": True})


async def test_a_redirect_from_the_diagnostic_server_is_an_error_and_never_followed(monkeypatch, index_file):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    hits = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_address[1]}/api/wifi")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    redirect = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    for server in (target, redirect):
        threading.Thread(target=lambda s=server: s.serve_forever(poll_interval=0.01), daemon=True).start()
    try:
        monkeypatch.setenv("DIAG_HOST", "127.0.0.1")
        monkeypatch.setenv("DIAG_PORT", str(redirect.server_address[1]))
        async with Client(mcp) as c:
            with pytest.raises(ToolError, match="redirect.*not followed"):
                await c.call_tool("diag_status", {})
        assert hits == []
    finally:
        for server in (target, redirect):
            server.shutdown()
            server.server_close()
