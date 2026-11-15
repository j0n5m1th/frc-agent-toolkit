"""The profiling answers computed from canned JFR events (no JDK needed)."""

from datetime import datetime

import pytest

from frc_mcp_jfr import analysis
from frc_mcp_jfr.errors import JfrError

START = datetime(2026, 1, 1, 12, 0, 0)


def ev(section, ms, at_s=1.0, kind="my.Periodic"):
    return {"type": kind, "startTime": f"2026-01-01T12:00:{int(at_s):02d}.{int(at_s % 1 * 1000):03d}Z", "duration": f"PT{ms / 1000}S", "section": section}


def test_durations_are_iso_strings():
    assert analysis.duration_ms("PT0.0071S") == pytest.approx(7.1)
    assert analysis.duration_ms("PT1M2.5S") == pytest.approx(62500)
    assert analysis.duration_ms(None) == 0 and analysis.duration_ms("junk") == 0


def test_hotspots_rank_by_total_and_report_the_worst_moment():
    events = [ev("A", 1, 1), ev("A", 1, 2), ev("A", 9, 3), ev("B", 5, 4)]
    out = analysis.hotspots(events, "section", START, top=5, sort="total")
    assert [r["key"] for r in out["rows"]] == ["A", "B"]
    a = out["rows"][0]
    assert a["n"] == 3 and a["total_ms"] == 11 and a["max_us"] == 9000 and a["max_at_ms"] == 3000
    assert out["total_ms"] == 16 and out["events"] == 4
    by_max = analysis.hotspots(events, "section", START, top=1, sort="max")
    assert by_max["rows"][0]["key"] == "A" and by_max["omitted_keys"] == 1


def test_grouping_by_the_innermost_stack_frame_and_helpful_errors():
    sample = {"type": "jdk.ExecutionSample", "stackTrace": {"frames": [{"method": {"type": {"name": "a.B"}, "name": "run"}}]}}
    out = analysis.hotspots([sample, sample], None, None, top=3, sort="count")
    assert out["rows"] == [{"key": "a.B.run", "n": 2}]  # samples have no duration: counted, not timed
    with pytest.raises(JfrError, match="no stack trace"):
        analysis.hotspots([ev("A", 1)], None, None, 3, "total")
    with pytest.raises(JfrError, match="no field 'nope'.*section"):
        analysis.hotspots([ev("A", 1)], "nope", None, 3, "total")


def test_compare_sums_every_label_so_moved_time_is_not_counted_as_saved():
    before = [ev("target", 10), ev("other", 10)]
    after = [ev("target", 2), ev("other", 17)]  # the cost moved to another label
    out = analysis.compare(before, after, "section", top=5)
    assert out["total_before_ms"] == 20 and out["total_after_ms"] == 19 and out["net_ms"] == -1 and out["net_pct"] == -5.0
    assert out["rows"][0]["key"] == "target" and out["rows"][0]["delta_ms"] == -8
    only = analysis.compare([ev("gone", 1)], [ev("new", 1)], "section", 5)
    assert sorted(only["in_only_one_recording"]) == ["gone", "new"]


def gc(at_s, ms, cause="G1 Evacuation Pause"):
    return {"type": "jdk.GarbageCollection", "startTime": f"2026-01-01T12:00:{at_s:02d}Z", "name": "G1Young", "cause": cause,
            "longestPause": f"PT{ms / 1000}S", "sumOfPauses": f"PT{ms / 1000}S", "duration": f"PT{ms / 1000}S"}


def heap(at_s, mb):
    return {"type": "jdk.GCHeapSummary", "startTime": f"2026-01-01T12:00:{at_s:02d}Z", "when": "After GC", "heapUsed": int(mb * 2**20)}


def test_gc_report_lists_pauses_and_flags_only_a_real_trend():
    report = analysis.gc_report([gc(5, 3.5), gc(9, 7.0)], [heap(5, 8), heap(9, 8.5)], START, top=5)
    assert report["gcs"] == 2 and report["pause_max_ms"] == 7.0 and report["longest_pauses"][0]["at_ms"] == 9000
    assert report["heap_after_gc_mb"]["last"] == 8.5 and report["heap_growing"] is False  # too few collections to call a trend
    rising = analysis.gc_report([gc(i, 1) for i in range(1, 6)], [heap(i, 10 * i) for i in range(1, 6)], START, 5)
    assert rising["heap_growing"] is True
    flat = analysis.gc_report([gc(i, 1) for i in range(1, 6)], [heap(i, 8) for i in range(1, 6)], START, 5)
    assert flat["heap_growing"] is False


def test_a_zero_longest_pause_is_not_replaced_by_the_summed_pauses():
    # PT0S is 0.0, which is falsy: the two fields are read with explicit None tests, not `or`.
    zero = {"type": "jdk.GarbageCollection", "startTime": "2026-01-01T12:00:05Z", "name": "G1Young",
            "cause": "G1 Evacuation Pause", "longestPause": "PT0S", "sumOfPauses": "PT0.009S"}
    report = analysis.gc_report([zero], [heap(5, 8)], START, top=5)
    assert report["longest_pauses"][0]["ms"] == 0.0  # the event's own 0, not the summed 9
    no_longest = {k: v for k, v in zero.items() if k != "longestPause"}
    assert analysis.gc_report([no_longest], [], START, top=5)["longest_pauses"][0]["ms"] == 9.0


def alloc(cls, mb, frames=("x.Y.m",)):
    return {"type": "jdk.ObjectAllocationSample", "weight": mb * 2**20, "objectClass": {"name": cls},
            "stackTrace": {"frames": [{"method": {"type": {"name": f.rsplit(".", 1)[0]}, "name": f.rsplit(".", 1)[1]}} for f in frames]}}


def test_internal_class_names_are_shown_as_java_writes_them():
    assert [analysis.java_class(n) for n in ("[B", "[[I", "[Ljava/lang/String;", "java/util/regex/Matcher", "a.B")] == [
        "byte[]", "int[][]", "java.lang.String[]", "java.util.regex.Matcher", "a.B"]


def test_allocation_by_class_and_the_real_stacks_for_a_match():
    samples = [alloc("byte[]", 6, ("a.B.copy", "a.B.run")), alloc("org.ejml.Matrix", 3, ("sim.Physics.update", "sim.Loop.run")), alloc("byte[]", 1)]
    out = analysis.allocation_report(samples, "class", None, top=5, seconds=2)
    assert out["total_mb"] == 10 and out["mb_per_s"] == 5 and out["rows"][0] == {"key": "byte[]", "mb": 7, "pct": 70.0}
    stacks = analysis.allocation_report(samples, "class", "ejml", top=5, seconds=None)
    assert stacks["rows"][0]["key"] == "org.ejml.Matrix" and stacks["stacks"][0]["frames"] == ["sim.Physics.update", "sim.Loop.run"]
    site = analysis.allocation_report(samples, "site", None, 5, None)
    assert {r["key"] for r in site["rows"]} == {"a.B.copy", "sim.Physics.update", "x.Y.m"}


def test_around_finds_gc_and_compilation_overlapping_a_moment():
    events = [gc(10, 7.0), {"type": "jdk.Compilation", "startTime": "2026-01-01T12:00:09.950Z", "duration": "PT0.134S", "method": {"type": {"name": "a.B"}, "name": "run"}},
              gc(30, 1.0)]
    found = analysis.around(events, START, at_ms=10000, window_ms=100)
    assert [(f["type"], f["what"]) for f in found] == [("Compilation", "a.B.run"), ("GarbageCollection", "G1Young G1 Evacuation Pause")]
    assert analysis.around(events, START, at_ms=20000, window_ms=100) == []
