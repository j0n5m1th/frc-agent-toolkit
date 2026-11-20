"""`wpilog_scan` finds the usual trouble in one call; `by_mode`, `sample`, `peaks`, `buckets` and `a|b` name search answer in one call what took several."""

import json

import pytest
from log_builder import BASE, S

from frc_mcp_wpilib.reduce.stats import Shaper, reduce_rows
from frc_mcp_wpilib.tooling.query_bases import contains_any
from frc_mcp_wpilib.wpilog_writer import LogWriter
from frc_mcp_wpilog.tools import data_tools, file_tools, topic_tools

ELEVATOR = "/Fake/Elevator/Current"


def text(result):
    return json.loads(result.content[0].text)


def at(seconds):
    return BASE + int(seconds * S)


def build_trouble_log(path):
    """Enabled 1-9 s. Battery sags to 6.5 V at 4 s with a brownout flag; a busy signal stops at 2 s; 6 loop overruns; one error."""
    w = LogWriter()
    enabled = w.start("DS:enabled", "boolean", "", BASE)
    w.append(enabled, False, BASE)
    w.append(enabled, True, at(1))
    w.append(enabled, False, at(9))
    battery = w.start("NT:/SystemStats/BatteryVoltage", "double", '{"unit":"V"}', BASE)
    brown = w.start("NT:/SystemStats/BrownedOut", "boolean", "", BASE)
    stalled = w.start("NT:/Fake/Stalled", "double", "", BASE)
    alive = w.start("NT:/Fake/Alive", "double", "", BASE)
    for n in range(0, 301):
        t = n * 0.04
        w.append(battery, 6.5 if 4.0 <= t < 4.4 else 12.0, at(t))
        w.append(brown, 4.0 <= t < 4.4, at(t))
        w.append(alive, float(n), at(t))
    for n in range(200):
        w.append(stalled, float(n), at(n * 0.01))
    messages = w.start("messages", "string", "", BASE)
    for n in range(6):
        w.append(messages, f"Loop time of 0.02s overrun by {n}.5 ms", at(2 + n))
    w.append(messages, "ERROR: CAN device 7 not found", at(5))
    w.save(path)


@pytest.fixture
def trouble(log_dir):
    build_trouble_log(log_dir / "trouble.wpilog")
    return "trouble.wpilog"


async def test_scan_ranks_the_trouble_it_finds(trouble):
    await file_tools.wpilog_open(trouble)
    out = text(await data_tools.wpilog_scan())
    flags = out["flags"]
    assert flags[0].startswith("high: brownout flag") and any(f.startswith("high: battery fell to 6.5 V") for f in flags)
    assert any("1 error(s)" in f for f in flags) and any("6 loop overrun" in f for f in flags)
    assert any("stopped early" in f and "/Fake/Stalled" in f for f in flags)
    assert [f.split(":")[0] for f in flags] == sorted((f.split(":")[0] for f in flags), key=["high", "med", "low"].index)
    assert out["battery"]["min_v"] == 6.5 and out["battery"]["below_7v_s"] == pytest.approx(0.4, abs=0.05)
    assert out["messages"]["overruns"] == 6 and out["messages"]["errors"] == 1
    assert "Alive" not in json.dumps(out["stopped_early"])


async def test_scan_says_what_it_could_not_check(log_dir):
    await file_tools.wpilog_open("sample.wpilog")
    out = text(await data_tools.wpilog_scan())
    assert out["link"]["checked"] is False and out["battery"]["checked"] is False
    assert out["messages"]["errors"] == 1 and out["messages"]["warnings"] == 1
    assert out["t0"] == "first auto" and "not_checked" in out
    assert len(json.dumps(out)) < 1500


async def test_scan_of_a_clean_log_says_nothing_was_found(log_dir):
    w = LogWriter()
    enabled = w.start("DS:enabled", "boolean", "", BASE)
    for t, v in ((0, False), (1, True), (5, False)):
        w.append(enabled, v, at(t))
    w.save(log_dir / "clean.wpilog")
    await file_tools.wpilog_open("clean.wpilog")
    out = text(await data_tools.wpilog_scan())
    assert out["flags"] == ["none found by these checks"]


async def test_query_by_mode_answers_once_per_enabled_period(log_dir):
    await file_tools.wpilog_open("sample.wpilog")
    out = text(await data_tools.wpilog_query([ELEVATOR], reduce="summary", by_mode=True))
    assert list(out["modes"]) == ["auto", "teleop"]
    assert out["modes"]["auto"]["from_s"] == 2.0 and out["modes"]["auto"]["data"][ELEVATOR]["max"] == 42.5
    assert out["modes"]["teleop"]["data"][ELEVATOR]["max"] == 41.0


async def test_by_mode_and_compare_with_do_not_mix(log_dir):
    from fastmcp.exceptions import ToolError

    await file_tools.wpilog_open("sample.wpilog")
    with pytest.raises(ToolError):
        await data_tools.wpilog_query([ELEVATOR], by_mode=True, compare_with=["l2"])


async def test_when_can_sample_other_topics_at_each_interval_start(log_dir):
    await file_tools.wpilog_open("sample.wpilog")
    out = text(await data_tools.wpilog_when(ELEVATOR, ">", 40, align="auto", sample=["/Fake/Arm/Angle", "/Fake/Match/Mode"]))
    assert out["n"] == 2
    assert len(out["sampled"]["/Fake/Arm/Angle"]) == 2
    assert out["sampled"]["/Fake/Match/Mode"] == ["auto", "teleop"]


async def test_when_sample_rejects_an_unknown_topic(log_dir):
    from fastmcp.exceptions import ToolError

    await file_tools.wpilog_open("sample.wpilog")
    with pytest.raises(ToolError):
        await data_tools.wpilog_when(ELEVATOR, ">", 40, sample=["/Nope"])


def test_peaks_lists_separate_events_not_neighbouring_samples():
    rows = [(float(t), 5.0) for t in range(0, 10_000, 100)]
    rows[10] = (1000.0, 40.0)
    rows[11] = (1100.0, 39.0)  # the same spike
    rows[60] = (6000.0, 30.0)
    out = reduce_rows(rows, Shaper("double", None, 3), "peaks", 20)
    assert out["high"][:2] == [[1000.0, 40.0], [6000.0, 30.0]]
    assert len(out["high"]) <= 5 and out["median"] == 5.0 and out["low"] == []  # nothing dips below a flat baseline


def test_a_signal_running_in_reverse_shows_up_as_valleys():
    rows = [(float(t), 0.0) for t in range(0, 10_000, 100)]
    for i in range(20, 40):
        rows[i] = (rows[i][0], -3000.0)  # a flywheel spinning backwards
    out = reduce_rows(rows, Shaper("double", None, 3), "peaks", 20)
    assert out["low"][0][1] == -3000.0 and out["high"] == []


def test_sparse_data_lists_only_real_peaks_and_valleys():
    rows = [(0.0, 0.0), (1000.0, 43.0), (2000.0, 0.0), (3000.0, -2.4), (4000.0, 0.0)]
    out = reduce_rows(rows, Shaper("double", None, 3), "peaks", 20)
    assert out["high"] == [[1000.0, 43.0]] and out["low"] == [[3000.0, -2.4]]


def test_buckets_keep_a_spike_that_samples_would_miss():
    rows = [(float(t), 1.0) for t in range(0, 10_000, 10)]
    rows[503] = (5030.0, 99.0)
    out = reduce_rows(rows, Shaper("double", None, 3), "buckets", 10)
    assert len(out["t_ms"]) <= 10 and max(out["max"]) == 99.0 and min(out["min"]) == 1.0
    assert out["bucket_ms"] == pytest.approx(999.0, abs=1.0)


def test_peaks_and_buckets_need_numbers():
    from frc_mcp_wpilib.errors import WPILibMCPError

    with pytest.raises(WPILibMCPError):
        reduce_rows([(0.0, "a"), (1.0, "b")], Shaper("string", None, 3), "peaks", 20)


def test_name_search_accepts_either_of_two_words():
    assert contains_any("battery|volt", "/SystemStats/BatteryVoltage") and contains_any("arm|elev", "/Elevator/x")
    assert not contains_any("arm|elev", "/Drive/x") and contains_any("", "anything") and contains_any("a|", "abc")


async def test_topic_and_text_search_use_alternation(log_dir):
    await file_tools.wpilog_open("sample.wpilog")
    topics = text(await topic_tools.wpilog_topics(name_contains="arm|elevator"))
    assert set(topics["topics"]) == {ELEVATOR, "/Fake/Arm/Angle"}
    found = text(await data_tools.wpilog_text(name_contains="warning|error"))
    assert found["n"] == 2


async def test_many_signals_stopping_together_is_a_low_flag_not_a_fault_per_signal(log_dir):
    w = LogWriter()
    enabled = w.start("DS:enabled", "boolean", "", BASE)
    for t, v in ((0, False), (1, True), (30, False)):
        w.append(enabled, v, at(t))
    alive = w.start("NT:/Fake/Alive", "double", "", BASE)
    checks = [w.start(f"NT:/Check/Step{k}", "double", "", BASE) for k in range(6)]
    for n in range(3000):
        w.append(alive, float(n), at(n * 0.01))
        if n < 200:
            for entry in checks:
                w.append(entry, float(n), at(n * 0.01))
    w.save(log_dir / "sequence.wpilog")
    await file_tools.wpilog_open("sequence.wpilog")
    out = text(await data_tools.wpilog_scan())
    stopped = [f for f in out["flags"] if "stopped early" in f]
    assert len(stopped) == 1 and stopped[0].startswith("low:") and "6 busy signals" in stopped[0]
    assert not any(f.startswith("med:") for f in out["flags"])


async def test_the_word_default_is_not_an_error(log_dir):
    w = LogWriter()
    messages = w.start("messages", "string", "", BASE)
    for n, line in enumerate(("Using default auto routine", "Loaded default constants", "CAN fault on device 3", "2 exceptions thrown")):
        w.append(messages, line, at(n))
    w.save(log_dir / "words.wpilog")
    await file_tools.wpilog_open("words.wpilog")
    assert text(await data_tools.wpilog_scan())["messages"]["errors"] == 2


async def test_a_zero_volt_battery_is_not_a_brownout(log_dir):
    w = LogWriter()
    battery = w.start("NT:/SystemStats/BatteryVoltage", "double", '{"unit":"V"}', BASE)
    for n in range(10):
        w.append(battery, 0.0, at(n))
    w.save(log_dir / "bench.wpilog")
    await file_tools.wpilog_open("bench.wpilog")
    out = text(await data_tools.wpilog_scan())
    assert out["battery"]["checked"] is False and not any("battery fell" in f for f in out["flags"])


async def test_can_utilization_as_a_fraction_is_read_as_percent(log_dir):
    w = LogWriter()
    can = w.start("NT:/SystemStats/CANBus/Utilization", "double", "", BASE)
    for n in range(10):
        w.append(can, 0.9, at(n))
    w.save(log_dir / "can.wpilog")
    await file_tools.wpilog_open("can.wpilog")
    out = text(await data_tools.wpilog_scan())
    assert out["link"]["can_util_pct"] == 90.0 and any("can_util_pct" in f for f in out["flags"])


async def test_by_mode_refuses_a_window_it_would_ignore(log_dir):
    from fastmcp.exceptions import ToolError

    await file_tools.wpilog_open("sample.wpilog")
    for extra in ({"since_ms": 1000}, {"until_ms": 5000}, {"align": "teleop"}):
        with pytest.raises(ToolError, match="enabled period"):
            await data_tools.wpilog_query([ELEVATOR], by_mode=True, **extra)


def test_a_long_plateau_is_one_peak():
    rows = [(float(t), 0.0) for t in range(0, 6000, 100)]
    for i in range(10, 40):
        rows[i] = (rows[i][0], 3000.0)  # 3 s at speed
    out = reduce_rows(rows, Shaper("double", None, 3), "peaks", 20)
    assert out["high"] == [[1000.0, 3000.0]]


def test_buckets_name_the_unweighted_mean_as_such():
    out = reduce_rows([(0.0, 1.0), (500.0, 3.0), (1000.0, 5.0)], Shaper("double", None, 3), "buckets", 2)
    assert "sample_mean" in out and "mean" not in out


def test_spaces_beside_the_bar_are_ignored_and_a_plain_needle_is_kept_as_written():
    assert contains_any("error | exception", "Exception in thread") and contains_any("battery |voltage", "BatteryVoltage")
    assert contains_any("arm|elev", "elevator") and not contains_any("arm|elev", "drive")
    assert contains_any("state ", "robot state ok") and not contains_any("state ", "robot state")  # no bar: spaces count
    assert contains_any("|", "anything") and contains_any("a|", "abc")


async def test_a_unitless_percent_below_one_is_not_scaled_but_a_systemstats_fraction_is(log_dir):
    w = LogWriter()
    cpu = w.start("NT:/DSLog/CPUUtilization", "double", "", BASE)  # percent, no unit: 0.9 means 0.9 %
    for n in range(10):
        w.append(cpu, 0.9, at(n))
    w.save(log_dir / "pct.wpilog")
    await file_tools.wpilog_open("pct.wpilog")
    out = text(await data_tools.wpilog_scan())
    assert out["link"]["cpu_pct"] == 0.9 and not any("cpu_pct" in f for f in out["flags"])


async def test_a_zero_volt_stretch_is_not_counted_as_sag(log_dir):
    w = LogWriter()
    battery = w.start("NT:/SystemStats/BatteryVoltage", "double", '{"unit":"V"}', BASE)
    for t, v in ((0, 12.0), (2, 9.0), (3, 0.0), (9, 12.0)):
        w.append(battery, v, at(t))
    w.save(log_dir / "gap.wpilog")
    await file_tools.wpilog_open("gap.wpilog")
    assert text(await data_tools.wpilog_scan())["battery"]["below_10v_s"] == pytest.approx(1.0, abs=0.01)  # the 9 V second, not the 6 s at 0 V


async def test_when_sample_checks_its_names_even_for_a_window_past_the_end(log_dir):
    from fastmcp.exceptions import ToolError

    await file_tools.wpilog_open("sample.wpilog")
    with pytest.raises(ToolError):
        await data_tools.wpilog_when(ELEVATOR, ">", 40, since_ms=10_000_000, sample=["/nope"])
    out = text(await data_tools.wpilog_when(ELEVATOR, ">", 40, since_ms=10_000_000, sample=["/Fake/Arm/Angle"]))
    assert out["n"] == 0 and out["sampled"] == {"/Fake/Arm/Angle": []}


async def test_a_brownout_topic_with_no_samples_does_not_stop_the_scan(log_dir):
    w = LogWriter()
    w.start("NT:/SystemStats/BrownedOut", "boolean", "", BASE)  # started, never written
    battery = w.start("NT:/SystemStats/BatteryVoltage", "double", '{"unit":"V"}', BASE)
    w.append(battery, 12.0, at(0))
    w.save(log_dir / "empty.wpilog")
    await file_tools.wpilog_open("empty.wpilog")
    assert text(await data_tools.wpilog_scan())["flags"][0] == "low: no Driver Station data, so the robot's enabled periods are unknown"


async def test_changes_are_sampled_at_their_exact_time_not_the_rounded_one(log_dir):
    w = LogWriter()
    mode = w.start("NT:/Fake/Mode", "int64", "", BASE)
    other = w.start("NT:/Fake/Other", "int64", "", BASE)
    for t_us, v in ((0, 0), (1040, 1)):  # a change 1.04 ms in: reported as 1.0 ms, but sampled at 1.04
        w.append(mode, v, BASE + t_us)
        w.append(other, v + 10, BASE + t_us)
    w.save(log_dir / "fine.wpilog")
    await file_tools.wpilog_open("fine.wpilog")
    out = text(await data_tools.wpilog_when("/Fake/Mode", "changed", sample=["/Fake/Other"]))
    assert out["changes_ms"] == [1.0] and out["sampled"] == {"/Fake/Other": [11]}


def _log_with(log_dir, name, topics):
    """A log file `name` holding each `(topic, value)` as a double sampled once a second."""
    w = LogWriter()
    for topic, value in topics:
        entry = w.start(topic, "double", '{"unit":"V"}', BASE)
        for n in range(10):
            w.append(entry, value, at(n))
    w.save(log_dir / name)
    return name


async def test_a_topic_with_only_a_suggestive_name_is_listed_not_used(log_dir):
    await file_tools.wpilog_open(_log_with(log_dir, "rail.wpilog", [("/SystemStats/UserVoltage5V", 5.0), ("/Drive/BatteryTemp", 30.0)]))
    out = text(await data_tools.wpilog_scan())["battery"]
    assert out["checked"] is False and "min_v" not in out
    assert out["candidates"] == ["/Drive/BatteryTemp", "/SystemStats/UserVoltage5V"] and "pass battery=" in out["hint"]


async def test_a_conventional_name_is_used_and_says_so_and_lists_the_others(log_dir):
    topics = [("/SystemStats/BatteryVoltage", 12.0), ("/Robot/PDH/Voltage", 11.0), ("/SystemStats/UserVoltage5V", 5.0)]
    await file_tools.wpilog_open(_log_with(log_dir, "conv.wpilog", topics))
    out = text(await data_tools.wpilog_scan())["battery"]
    assert out["topic"] == "/SystemStats/BatteryVoltage" and out["chosen_by"] == "convention" and out["min_v"] == 12.0
    assert out["others"] == ["/Robot/PDH/Voltage"]  # the 5 V rail is not even a candidate once a convention matched


async def test_a_voltage_leaf_under_a_power_distribution_path_is_a_convention(log_dir):
    await file_tools.wpilog_open(_log_with(log_dir, "pdh.wpilog", [("/Robot/PowerDistribution[1]/Voltage", 9.0)]))
    out = text(await data_tools.wpilog_scan())["battery"]
    assert out["chosen_by"] == "convention" and out["min_v"] == 9.0


async def test_the_caller_can_name_the_battery_and_a_wrong_name_is_an_error_not_a_fallback(log_dir):
    await file_tools.wpilog_open(_log_with(log_dir, "named.wpilog", [("/Robot/Rail", 8.0), ("/SystemStats/BatteryVoltage", 12.0)]))
    out = text(await data_tools.wpilog_scan(battery="/Robot/Rail"))["battery"]
    assert out["topic"] == "/Robot/Rail" and out["chosen_by"] == "explicit" and out["min_v"] == 8.0
    with pytest.raises(Exception, match="not in this log"):
        await data_tools.wpilog_scan(battery="/No/Such")


async def test_a_boolean_that_only_looks_like_a_brownout_is_a_candidate(log_dir):
    w = LogWriter()
    flag = w.start("/Robot/BrownoutWarning", "boolean", "", BASE)
    w.append(flag, False, BASE)
    w.save(log_dir / "warn.wpilog")
    await file_tools.wpilog_open("warn.wpilog")
    out = text(await data_tools.wpilog_scan())
    assert out["brownout_candidates"] == ["/Robot/BrownoutWarning"] and "brownout_s" not in out


async def test_a_named_battery_must_be_a_number(log_dir):
    w = LogWriter()
    flag = w.start("/Robot/Flag", "boolean", "", BASE)
    w.append(flag, True, BASE)
    w.save(log_dir / "flag.wpilog")
    await file_tools.wpilog_open("flag.wpilog")
    with pytest.raises(Exception, match="must be a number"):
        await data_tools.wpilog_scan(battery="/Robot/Flag")


async def test_an_integer_topic_counts_as_a_number(log_dir):
    w = LogWriter()
    pct = w.start("/SystemStats/CANBus/Utilization", "int64", "", BASE)
    for n in range(10):
        w.append(pct, 95, at(n))
    w.save(log_dir / "int.wpilog")
    await file_tools.wpilog_open("int.wpilog")
    assert text(await data_tools.wpilog_scan())["link"]["can_util_pct"] == 95.0
