"""The overview `wpilog_open` returns: what the log is, how long it ran, when the robot was enabled."""

from frc_mcp_wpilib.wpilog_writer import LogWriter
from frc_mcp_wpilog.logfile import LogFile
from frc_mcp_wpilog.overview import overview


def test_overview_leads_with_the_facts_a_student_wants_first(sample):
    out = overview(sample, "l1")
    assert out["log"] == "l1" and out["file"] == "sample.wpilog"
    assert out["duration_s"] == 12.0
    assert sample.size_bytes > 0 and out["size_mb"] == round(sample.size_bytes / 2**20, 1)
    assert out["topics"] == sum(1 for topic in sample.topics.values() if not topic.hidden)
    assert out["updates"] == sample.records and out["updates"] > 0


def test_overview_lists_the_enable_phases_and_the_match(sample):
    out = overview(sample, "l1")
    assert out["enabled"] == [
        {"mode": "auto", "from_s": 2.0, "to_s": 5.0, "for_s": 3.0},
        {"mode": "teleop", "from_s": 6.0, "to_s": 10.0, "for_s": 4.0},
    ]
    assert out["match"] == {"EventName": "Practice Field", "MatchNumber": 12}
    assert out["busiest"][0] == ["/Fake/Elevator/Current", 121]


def test_a_clean_log_carries_no_warnings(sample):
    assert "warnings" not in overview(sample, "l1")


def test_a_damaged_log_says_what_is_missing(tmp_path):
    """A real damaged file (not flags set on an object): an unreadable payload, a record for an unknown entry, a cut-off end."""
    w = LogWriter()
    good, bad = w.start("/good", "double", "", 0), w.start("/bad", "double", "", 0)
    for i in range(5):
        w.append(good, float(i), i * 1000)
    w._record(bad, 20, b"")  # a double with no bytes: counted as `decode`
    w._record(99, 30, bytes(8))  # data for an entry that was never started: `unknown_entry`
    w.append(good, 9.0, 6000)
    path = tmp_path / "damaged.wpilog"
    path.write_bytes(w.to_bytes()[:-3])  # power lost in the middle of the last record
    log = LogFile(path)
    warnings = overview(log, "l1")["warnings"]
    assert log.cut_short and log.skipped_reasons == {"unknown_entry": 1, "decode": 1}
    assert log.records == 7 and log.skipped_records == 2  # the cut-off last record is not counted, not even twice
    assert any("cut short" in note for note in warnings)
    assert any("records could not be read" in note and "1 decode" in note and "unknown_entry" in note for note in warnings)
    assert log.topics["/good"].count == 5  # the intact rows before the damage are all still there
