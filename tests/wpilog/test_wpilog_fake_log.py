"""The practice log generator: deterministic, readable by the log server, and what its docstring promises."""

import fake_log
import pytest

from frc_mcp_wpilog import queries, timeline
from frc_mcp_wpilog.logfile import LogFile
from frc_mcp_wpilog.queries import QuerySpec, Range


@pytest.fixture
def practice(tmp_path):
    return LogFile(fake_log.build().save(tmp_path / "practice.wpilog"))


def test_the_log_is_identical_every_time():
    assert fake_log.build().to_bytes() == fake_log.build().to_bytes()


def test_the_match_phases_and_facts_are_what_the_docstring_says(practice):
    assert [(p.mode, (p.start_us - practice.start_us) / 1e6, (p.end_us - practice.start_us) / 1e6)
            for p in timeline.phases(practice)] == [("auto", 2.0, 17.0), ("teleop", 19.0, 29.0)]
    assert timeline.match_info(practice) == {"EventName": "Practice Field", "MatchNumber": 7, "MatchType": "practice"}
    assert practice.duration_s == 30.0


def test_the_elevator_spikes_every_five_seconds(practice):
    spikes = queries.when(practice, "/Fake/Elevator/Current", ">", 40, None, Range(), 20)
    assert spikes["n"] == 7 and spikes["intervals_ms"][1] == [5000.0, 5400.0]  # at 0, 5, ... 30 s (the last has just begun)
    assert spikes["true_at_end"] is True
    during_auto = queries.when(practice, "/Fake/Elevator/Current", ">", 40, None, Range("auto"), 20)
    assert during_auto["intervals_ms"][1] == [3000.0, 3400.0]  # the spike at 5 s is 3 s after auto started


def test_the_battery_sags_while_the_elevator_draws_current(practice):
    out = queries.query(practice, ["/Fake/Battery/Voltage"], QuerySpec(reduce="percentiles"), Range())
    voltage = out["data"]["/Fake/Battery/Voltage"]
    assert voltage["max"] == 12.6 and voltage["min"] == 11.475  # 12.6 - 0.03 * (42.5 - 5)


def test_drive_speed_comes_from_a_struct_field(practice):
    out = queries.query(practice, ["/Fake/Drive/Speeds"], QuerySpec(reduce="percentiles", fields=["vx"]), Range("auto"))
    assert out["data"]["/Fake/Drive/Speeds"]["fields"]["vx"]["max"] == 2.0
    pose = queries.values_at(practice, ["/Fake/Drive/Pose"], 15000, ["translation.x"], "auto")
    assert pose["/Fake/Drive/Pose"]["v"] == {"translation.x": 22.5}


def test_the_text_log_has_one_warning_and_one_error(practice):
    errors = queries.search_text(practice, None, "error", Range(), 5)
    assert [i["t_ms"] for i in errors["items"]] == [22000.0]
    assert queries.search_text(practice, None, "warning", Range(), 5)["items"][0]["t_ms"] == 12000.0


def test_the_command_line_writes_the_file_and_validates_the_length(tmp_path, capsys):
    out = tmp_path / "x.wpilog"
    assert fake_log.main(["--out", str(out)]) == 0 and out.is_file()
    assert "frc-mcp wpilog check x.wpilog" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        fake_log.main(["--out", str(out), "--seconds", "5"])


def test_the_scan_match_checks_find_what_the_practice_log_holds(practice):
    """The recipe in the `scan_match` prompt, step by step, on a log whose problems are known."""
    # (1) errors and warnings, dated from the start of autonomous
    problems = [i for word in ("error", "warn", "fault") for i in queries.search_text(practice, None, word, Range("auto"), 5)["items"]]
    assert sorted((i["t_ms"], i["v"].split(":")[0]) for i in problems) == [(10000.0, "WARNING"), (20000.0, "ERROR")]
    # (2) the battery sags below 12 volts but never reaches brownout territory
    battery = "/Fake/Battery/Voltage"
    assert queries.when(practice, battery, "<", 12, None, Range(), 20)["n"] > 0
    assert queries.when(practice, battery, "<", 7, None, Range(), 20)["n"] == 0
    # (3) with no time given, get_values reports each topic's last update: every key signal ran to the end of the log
    last = queries.values_at(practice, ["/Fake/Arm/Angle", "/Fake/Elevator/Current"], None, None, "start")
    assert all(entry["t_ms"] >= 29_000 for entry in last.values())
    # (4) the mode changes are listed
    assert queries.when(practice, "/Fake/Match/Mode", "changed", None, None, Range(), 20)["n"] == 4

