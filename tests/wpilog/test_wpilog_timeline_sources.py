"""Every place a log can say the robot was enabled (the set AdvantageScope reads): each gives the same periods.

Each case writes the same match: disabled at 1 s, autonomous 2-4 s, disabled 4-5 s, teleop 5-7 s, disabled after.
"""

import pytest

from frc_mcp_wpilib.wpilog_writer import LogWriter
from frc_mcp_wpilog import sync, timeline
from frc_mcp_wpilog.logfile import LogFile

S = 1_000_000
STATES = ((1, None), (2, "auto"), (4, None), (5, "teleop"), (7, None))  # (seconds, mode or None for disabled)
EXPECTED = [("auto", 2 * S, 4 * S), ("teleop", 5 * S, 7 * S)]


def flags(prefix: str, enabled_entry: str, *, mode_entry: str | None, mode_values: dict[str | None, object],
          auto_entry: str | None = None):
    """A writer for an enabled boolean plus a mode entry (string or number) or an autonomous boolean."""
    def write(w: LogWriter) -> None:
        enabled = w.start(prefix + enabled_entry, "boolean", "", S)
        mode = None
        if mode_entry:
            value = mode_values["auto"]
            mode = w.start(prefix + mode_entry, "string" if isinstance(value, str) else "int64", "", S)
        auto = w.start(prefix + auto_entry, "boolean", "", S) if auto_entry else None
        for t, state in STATES:
            if mode is not None:
                w.append(mode, mode_values[state], t * S)
            if auto is not None:
                w.append(auto, state == "auto", t * S)
            w.append(enabled, state is not None, t * S)
    return write


def int_word(name: str, encode) -> object:
    def write(w: LogWriter) -> None:
        entry = w.start(name, "int64", "", S)
        for t, state in STATES:
            w.append(entry, encode(state), t * S)
    return write


def phoenix(w: LogWriter) -> None:
    enabled, mode = w.start("RobotEnable", "boolean", "", S), w.start("RobotMode", "string", "", S)
    for t, state in STATES:
        w.append(mode, {"auto": "Autonomous", "teleop": "Teleop", None: "Disabled"}[state], t * S)
        w.append(enabled, state is not None, t * S)


STRING_MODES = {"auto": "autonomous", "teleop": "teleoperated", None: "teleoperated"}
NUMBER_MODES = {"auto": 1, "teleop": 2, None: 2}
CASES = {
    "DataLog 2027 controlWord fields": flags("DS:controlWord/", "enabled", mode_entry="robotMode", mode_values=NUMBER_MODES),
    "AdvantageKit over NT, 2027": flags("/AdvantageKit/DriverStation/", "Enabled", mode_entry="RobotMode", mode_values=STRING_MODES),
    "AdvantageKit over NT, before 2027": flags("/AdvantageKit/DriverStation/", "Enabled", mode_entry=None, mode_values={}, auto_entry="Autonomous"),
    "NT 2027 ControlWord fields": flags("/DriverStation/ControlWord/", "enabled", mode_entry="robotMode", mode_values=STRING_MODES),
    "NT FMSControlData": int_word("NT:/FMSInfo/FMSControlData", lambda s: {None: 0x30, "auto": 0x33, "teleop": 0x31}[s]),
    "FIRST Driver Station ControlWord": int_word("DS:/Dscomm/Control/Robot/ControlData/ControlWord",
                                                 lambda s: {None: 2 << 1, "auto": 1 << 1 | 1, "teleop": 2 << 1 | 1}[s]),
    "Phoenix RobotEnable and RobotMode": phoenix,
}


@pytest.mark.parametrize("write", CASES.values(), ids=CASES.keys())
def test_every_source_gives_the_same_enabled_periods(tmp_path, write):
    w = LogWriter()
    write(w)
    w.append(w.start("/x", "double", "", S), 0.0, 8 * S)  # the log runs on past the last disable
    log = LogFile(w.save(tmp_path / "source.wpilog"))
    assert [(p.mode, p.start_us, p.end_us) for p in timeline.phases(log)] == EXPECTED
    assert [(e.t_us, e.rising) for e in sync.enable_edges(log)] == [(2 * S, True), (4 * S, False), (5 * S, True), (7 * S, False)]


def test_fms_control_data_test_bit_and_dscomm_utility_mode():
    assert timeline._mode(timeline._fms_control_data(0b101)) == "test"
    assert timeline._mode(timeline._dscomm_control_word(3 << 1 | 1)) == "utility"
    assert timeline._mode(timeline._dscomm_control_word(1 << 1)) is None  # autonomous selected but disabled


def test_the_driver_station_booleans_still_win_over_a_later_source(tmp_path):
    w = LogWriter()
    CASES["Phoenix RobotEnable and RobotMode"](w)  # says enabled 2-4 s and 5-7 s
    en = w.start("DS:enabled", "boolean", "", S)
    for t, on in ((1, False), (3, True), (6, False)):
        w.append(en, on, t * S)
    log = LogFile(w.save(tmp_path / "both.wpilog"))
    assert [(p.start_us, p.end_us) for p in timeline.phases(log)] == [(3 * S, 6 * S)]


def test_a_log_that_starts_enabled_in_an_unknown_mode_says_so_and_has_no_first_edge(tmp_path):
    """Seen in real AdvantageKit logs from a Systemcore: Enabled true and RobotMode UNKNOWN from the first record."""
    from frc_mcp_wpilog.overview import overview

    w = LogWriter()
    enabled, mode = w.start("/DriverStation/Enabled", "boolean", "", S), w.start("/DriverStation/RobotMode", "string", "", S)
    w.append(mode, "UNKNOWN", S)
    w.append(enabled, True, S)
    w.append(enabled, False, 3 * S)
    w.append(enabled, True, 4 * S)
    w.append(w.start("/x", "double", "", S), 0.0, 6 * S)
    log = LogFile(w.save(tmp_path / "akit.wpilog"))
    assert [(p.mode, p.start_us) for p in timeline.phases(log)] == [("unknown", S), ("unknown", 4 * S)]
    assert overview(log, "l1")["enabled"][0] == {"mode": "unknown", "already_enabled": True, "from_s": 0.0, "to_s": 2.0, "for_s": 2.0}
    assert "already_enabled" not in overview(log, "l1")["enabled"][1]
    assert [(e.t_us, e.rising) for e in sync.enable_edges(log)] == [(3 * S, False), (4 * S, True)]  # no edge at the log's start


def test_a_log_that_starts_enabled_still_syncs_on_the_edges_it_saw(tmp_path):
    reference = LogWriter()
    CASES["DataLog 2027 controlWord fields"](reference)  # enabled 2-4 s and 5-7 s
    reference.append(reference.start("/x", "double", "", S), 0.0, 8 * S)
    late = LogWriter()  # started logging at reference 3 s, while the robot was already enabled
    en = late.start("DS:enabled", "boolean", "", 0)
    for t, on in ((0, True), (1, False), (2, True), (4, False)):
        late.append(en, on, t * S)
    late.append(late.start("/y", "double", "", 0), 0.0, 5 * S)
    ref_log, late_log = LogFile(reference.save(tmp_path / "ref.wpilog")), LogFile(late.save(tmp_path / "late.wpilog"))
    placed = sync.place(ref_log, late_log, "l2", "enable")
    assert placed.offset_us == 3 * S and placed.notes["edges_matched"] == "3/3"
