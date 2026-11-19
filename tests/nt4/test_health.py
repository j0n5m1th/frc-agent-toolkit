"""`nt4_health`'s judgement over plain values: what is flagged, in what order, and what it admits it did not check."""

from frc_mcp_nt4.health import ALERTS, assess

HEALTHY = {
    "/sys/battery": 12.4, "/sys/brownout": False, "/sys/cpu": 20.0, "/sys/ramutil": 40.0, "/sys/storageutil": 30.0, "/sys/temp": 51.0,
    "/sys/faults/canbus_down": 0, "/sys/faults/canbus_unavail": 0, "/canbus/s0/name": "rio", "/canbus/s0/up": True, "/canbus/s0/error": False,
    "/diagnostics/canbusutil": 0.25, "/Netcomm/Status/HasUserCode": True, "/Netcomm/Status/HasUserCodeReady": True,
}


def test_a_healthy_controller_has_no_flags_and_says_what_was_not_checked():
    out = assess(HEALTHY, {})
    assert out["flags"] == ["none found by these checks"] and "not_checked" in out
    assert out["controller"]["battery_v"] == 12.4 and out["controller"]["can_util_pct"] == 25.0
    assert out["controller"]["can_buses"] == {"s0": {"name": "rio", "up": True, "error": False}}


def test_trouble_is_flagged_and_ranked_high_to_low():
    values = {**HEALTHY, "/sys/battery": 6.5, "/sys/brownout": True, "/sys/cpu": 97.0, "/sys/faults/canbus_down": 1, "/Netcomm/Status/HasUserCode": False,
              "/sys/faultcounts/canbus": 3, "/canbus/s1/error": True, "/canbus/s1/up": True, "/diagnostics/canbusutil": 91.0}
    flags = assess(values, {})["flags"]
    severities = [f.split(":")[0] for f in flags]
    assert severities == sorted(severities, key=["high", "med", "low"].index)
    joined = " ".join(flags)
    for expected in ("battery reads 6.5 V", "brownout", "CAN fault flag(s) set: canbus_down", "no robot code is running", "cpu_pct is 97", "CAN bus s1 reports an error",
                     "CAN bus use is 91%", "1 fault counter(s)"):
        assert expected in joined


def test_a_low_resting_battery_is_medium_not_high():
    flags = assess({**HEALTHY, "/sys/battery": 11.0}, {})["flags"]
    assert flags[0].startswith("med: battery reads 11.0 V")


def test_active_alerts_are_listed_most_severe_first_with_their_text():
    values = {**HEALTHY, f"{ALERTS}Drive/2/a1/active": True, f"{ALERTS}Arm/0/b2/active": True, f"{ALERTS}Arm/1/c3/active": False}
    texts = {f"{ALERTS}Arm/0/b2": "Arm encoder lost", f"{ALERTS}Drive/2/a1": "Slow loop"}
    out = assess(values, texts)
    assert [a["text"] for a in out["alerts"]] == ["Arm encoder lost", "Slow loop"]
    assert out["flags"][0] == "high: alert: Arm encoder lost" and out["flags"][-1] == "low: alert: Slow loop"


def test_a_server_without_sys_topics_is_not_checked_and_says_how_to_connect():
    out = assess({"/Telemetry/x": 1.0}, {})
    assert out["controller"]["checked"] is False and "port=6810" in out["controller"]["why"]
    assert out["flags"][0].startswith("low: controller health not checked")


def test_text_flags_read_as_off_when_empty_or_false():
    assert assess({**HEALTHY, "/sys/brownout": "false"}, {})["flags"] == ["none found by these checks"]
    assert assess({**HEALTHY, "/sys/brownout": "true"}, {})["flags"][0].startswith("high")
