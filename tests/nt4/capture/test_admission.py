"""Unit tests for the capture's gap list and its after-gap and before-gap placement checks."""

from types import SimpleNamespace

from frc_mcp_nt4.capture.admission import CaptureAdmission
from frc_mcp_nt4.capture.capture_clock import CaptureClock
from frc_mcp_nt4.capture.types import OmittedGaps
from frc_mcp_wpilib.reduce.types import Gap

ORIGIN_US = 1_000_000


def at(ms):
    """The local-clock microsecond stamp `ms` after the capture origin."""
    return ORIGIN_US + int(ms * 1000)


def value(time_us):
    """The minimal ntcore-value stand-in the admission checks read: only `time()` is used."""
    return SimpleNamespace(time=lambda: time_us)


def admission():
    clock = CaptureClock()
    clock.start(ORIGIN_US)
    return CaptureAdmission(clock)


def test_open_and_close_gap_track_the_newest_outage():
    adm = admission()
    assert not adm.has_open_gap
    adm.start_gap(10.0, max_gaps=5)
    assert adm.has_open_gap
    assert list(adm.gaps) == [Gap(10.0, None)]
    adm.close_gap(25.0)
    assert not adm.has_open_gap
    assert list(adm.gaps) == [Gap(10.0, 25.0)]


def test_trimming_the_oldest_gap_counts_it_and_keeps_its_end():
    adm = admission()
    adm.start_gap(10.0, max_gaps=2)
    adm.close_gap(20.0)
    adm.start_gap(30.0, max_gaps=2)
    adm.close_gap(40.0)
    assert adm.omitted is None
    adm.start_gap(50.0, max_gaps=2)  # trims Gap(10, 20)
    assert list(adm.gaps) == [Gap(30.0, 40.0), Gap(50.0, None)]
    assert adm.omitted == OmittedGaps(1, 20.0)
    adm.close_gap(60.0)
    adm.start_gap(70.0, max_gaps=2)  # trims Gap(30, 40)
    assert adm.omitted == OmittedGaps(2, 40.0)


def test_trimming_an_open_gap_reports_its_start_as_the_end():
    adm = admission()
    adm.start_gap(10.0, max_gaps=1)  # still open when the next outage arrives
    adm.start_gap(30.0, max_gaps=1)
    assert adm.omitted == OmittedGaps(1, 10.0)
    assert list(adm.gaps) == [Gap(30.0, None)]


def test_continuity_carries_the_gaps_and_the_omitted_end():
    adm = admission()
    assert list(adm.continuity().gaps) == []
    assert adm.continuity().omitted_gap_until is None
    adm.start_gap(10.0, max_gaps=1)
    adm.close_gap(20.0)
    adm.start_gap(30.0, max_gaps=1)
    window = adm.continuity()
    assert list(window.gaps) == [Gap(30.0, None)]
    assert window.omitted_gap_until == 20.0


def test_after_latest_gap_needs_a_closed_gap_and_a_stamp_inside_the_window():
    adm = admission()
    assert adm.after_latest_gap(value(at(5)), at(100))  # no gaps: any placeable stamp passes
    adm.start_gap(10.0, max_gaps=5)
    assert not adm.after_latest_gap(value(at(50)), at(100))  # still open
    adm.close_gap(20.0)
    assert not adm.after_latest_gap(value(0), at(100))  # untimestamped
    assert not adm.after_latest_gap(value(at(15)), at(100))  # stamped inside the outage
    assert not adm.after_latest_gap(value(at(20)), at(100))  # exactly at the gap end
    assert not adm.after_latest_gap(value(at(150)), at(100))  # stamped after now
    assert adm.after_latest_gap(value(at(30)), at(100))


def test_before_gap_returns_the_span_to_the_next_outage():
    adm = admission()
    adm.start_gap(100.0, max_gaps=5)
    adm.close_gap(200.0)
    assert adm.before_gap(value(at(50)), at(300)) == (50.0, 100.0)


def test_before_gap_is_none_inside_an_outage_or_after_all_of_them():
    adm = admission()
    adm.start_gap(100.0, max_gaps=5)
    adm.close_gap(200.0)
    assert adm.before_gap(value(at(150)), at(300)) is None  # inside the outage
    assert adm.before_gap(value(at(200)), at(300)) is None  # at the outage end
    assert adm.before_gap(value(at(250)), at(300)) is None  # after every outage


def test_before_gap_is_none_for_untimestamped_and_future_values():
    adm = admission()
    adm.start_gap(100.0, max_gaps=5)
    adm.close_gap(200.0)
    assert adm.before_gap(value(0), at(300)) is None
    assert adm.before_gap(value(at(50)), None) is None  # no clock reading
    assert adm.before_gap(value(at(350)), at(300)) is None  # stamped after now
