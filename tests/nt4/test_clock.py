import pytest

from frc_mcp_nt4.clock import update_age


def test_update_age_uses_nt4_local_clock():
    assert update_age(1_000_000, 2_000_000, 2_500_000, True) == {"age_ms": 1500.0}


@pytest.mark.parametrize(("local", "server", "now", "synced", "reason"), [
    (0, 100, 200, True, "timestamp unavailable"),
    (100, 0, 200, True, "timestamp unavailable"),
    (100, 100, None, True, "clock unavailable"),
    (100, 100, 200, False, "not synchronized"),
    (300, 100, 200, True, "in the future"),
])
def test_update_age_reports_missing_or_unreliable_time(local, server, now, synced, reason):
    result = update_age(local, server, now, synced)
    assert result["age_ms"] is None and reason in result["reason"]


def test_without_an_ntcore_clock_nothing_can_be_dated(monkeypatch):
    import ntcore

    from frc_mcp_nt4 import clock

    monkeypatch.delattr(ntcore, "_now", raising=False)  # an ntcore build with no `_now`
    assert clock.local_now_us() is None
    assert clock.estimate_server_now_us(ntcore.NetworkTableInstance.create()) is None


def test_a_missing_clock_makes_an_arrival_infinite_and_it_has_no_log_time_not_a_crash():
    import math

    from frc_mcp_nt4.capture.capture_clock import CaptureClock
    from frc_mcp_nt4.capture.listeners import _log_us

    clock = CaptureClock()
    clock.start(1_000_000)
    assert clock.arrival_ms(None) == math.inf  # the capture path used to hand this to round()
    assert _log_us(math.inf) is None and _log_us(math.nan) is None  # 0 would date the update to the capture's start
    assert _log_us(-5.0) == 0 and _log_us(1.5) == 1500


def test_a_malformed_schema_value_never_escapes_the_listener():
    from types import SimpleNamespace

    from frc_mcp_nt4.schema_listener import on_schema
    from frc_mcp_wpilib.schema import state

    state.forget()
    bad = SimpleNamespace(data=SimpleNamespace(value=SimpleNamespace(value=lambda: b"double x"),
                                               topic=SimpleNamespace(getName=lambda: None)))  # a name that cannot be parsed
    on_schema(bad)  # must not raise: it runs on an ntcore thread
    on_schema(SimpleNamespace(data=None))
    good = SimpleNamespace(data=SimpleNamespace(value=SimpleNamespace(value=lambda: b"double x"),
                                                topic=SimpleNamespace(getName=lambda: "/.schema/struct:Ok")))
    on_schema(good)
    assert state.decode("struct:Ok", bytes(8)) == {"x": 0.0}
    state.forget()
