"""Call paths, cumulative time, live JVM answers and recordings in progress (canned data, no JDK needed)."""

import time
from datetime import datetime
from pathlib import Path

import pytest

from frc_mcp_jfr import analysis, live, recorder
from frc_mcp_jfr.errors import JfrError

START = datetime(2026, 1, 1, 12, 0, 0)


def sample(*frames, ms=1.0, at_s=1):
    return {"type": "jdk.ExecutionSample", "startTime": f"2026-01-01T12:00:{at_s:02d}Z", "duration": f"PT{ms / 1000}S",
            "stackTrace": {"frames": [{"method": f} for f in frames]}}


def test_call_paths_group_by_the_innermost_frames():
    events = [sample("a.leaf", "a.mid", "a.root", ms=0), sample("a.leaf", "a.mid", "a.root", ms=0), sample("b.leaf", "a.root", ms=0)]  # samples: no duration
    out = analysis.hotspots(events, "stack", START, top=5, sort="count")
    assert out["rows"][0] == {"key": "a.leaf <- a.mid <- a.root", "n": 2} and out["rows"][1]["key"] == "b.leaf <- a.root"


def test_a_match_shows_the_callers_of_that_method_whatever_is_below_it():
    events = [sample("util.copy", "Target.run", "Loop.tick", "Main.main"), sample("util.other", "Target.run", "Loop.tick", "Main.main"),
              sample("util.copy", "Other.run", "Main.main")]
    out = analysis.hotspots(events, "stack", START, top=5, sort="count", match="Target.run")
    assert [r["key"] for r in out["rows"]] == ["Target.run <- Loop.tick <- Main.main"] and out["rows"][0]["n"] == 2  # both calls, one path
    assert analysis.hotspots(events, "stack", START, 5, "count", match="nothing")["rows"] == []


def test_cumulative_counts_every_frame_once_per_event_and_totals_count_each_event_once():
    events = [sample("a.leaf", "a.mid", "a.root", ms=2), sample("a.leaf", "a.leaf", "a.root", ms=3)]  # a recursive frame counts once
    out = analysis.hotspots(events, "cumulative", START, top=5, sort="total")
    by_key = {r["key"]: r for r in out["rows"]}
    assert by_key["a.root"]["n"] == 2 and by_key["a.leaf"]["n"] == 2 and by_key["a.mid"]["n"] == 1
    assert by_key["a.root"]["total_ms"] == 5 and out["total_ms"] == 5  # not 15: the events' own time, once
    assert analysis.hotspots(events, "cumulative", START, 5, "total", match="mid")["rows"][0]["key"] in {"a.leaf", "a.mid", "a.root"}


def test_stack_depth_asks_for_what_each_grouping_needs():
    assert [analysis.stack_depth(b) for b in (None, "frame", "stack", "cumulative", "section")] == [1, 1, 5, 16, 0]
    assert analysis.stack_depth("stack", "x") == 20


HISTOGRAM = """
 num     #instances         #bytes  class name (module)
-------------------------------------------------------
   1:          1000        1024000  [B (java.base@25)
   2:           100          32000  java.lang.String (java.base@25)
   3:            10           1600  [Ljava.lang.Object; (java.base@25)
Total          1110        1057600
"""


def test_histogram_growth_lists_the_classes_that_grew():
    before = live.parse_histogram(HISTOGRAM)
    assert before["byte[]"] == (1000, 1024000) and before["java.lang.Object[]"] == (10, 1600)
    after = live.parse_histogram(HISTOGRAM.replace("1000        1024000", "3000        3072000").replace("  100          32000", "  100          32000"))
    out = live.histogram_growth(before, after, top=5)
    assert out["rows"] == [{"class": "byte[]", "instances": "+2000", "kb": 2000, "of": 3000}] and out["growth_kb"] == 2000


THREADS = """
"main" #1 [11] prio=5 os_prio=0 cpu=10ms elapsed=5s tid=0x01 nid=11 waiting for monitor entry  [0x1]
   java.lang.Thread.State: BLOCKED (on object monitor)
\tat app.Worker.step(Worker.java:10)
\t- waiting to lock <0x00000001> (a java.lang.Object)
\tat app.Main.main(Main.java:3)

"worker-1" #20 [21] prio=5 os_prio=0 tid=0x02 nid=21 runnable  [0x2]
   java.lang.Thread.State: RUNNABLE
\tat app.Compute.loop(Compute.java:7)
\t- locked <0x00000001> (a java.lang.Object)
\tat java.lang.Thread.run(Thread.java:1)

"G1 Main Marker" os_prio=2 cpu=1ms tid=0x03 nid=3 runnable
"""


def test_thread_report_shows_who_blocks_whom_and_skips_internal_threads():
    out = live.thread_report(THREADS, top=5)
    assert out["threads"] == 2 and out["states"] == {"BLOCKED": 1, "RUNNABLE": 1} and out["deadlock"] is False
    blocked = out["rows"][0]
    assert blocked["name"] == "main" and blocked["waits_for"] == "java.lang.Object" and blocked["held_by"] == "worker-1"
    assert out["rows"][1]["frames"] == ["app.Compute.loop"]


def test_a_deadlock_report_is_passed_on_trimmed():
    text = THREADS + "\nFound one Java-level deadlock:\n=============================\n\"a\":\n  waiting to lock monitor 0x1 (object 0x2)\n\nFound 1 deadlock.\n"
    assert "Found one Java-level deadlock" in live.thread_report(text, 5)["deadlock"]


def test_a_recording_in_progress_says_how_long_to_wait(tmp_path):
    target = tmp_path / "later.jfr"
    recorder._pending.clear()  # the module keeps its own small registry across files
    recorder.start(target, pid=1, seconds=30)
    assert recorder.active()[0]["file"] == "later.jfr" and 25 <= recorder.active()[0]["recording_s_left"] <= 30
    with pytest.raises(JfrError, match=r"later.jfr is still being recorded \(about \d+ s left\)"):
        recorder.check_ready("later.jfr")
    recorder.check_ready("other.jfr")  # not ours: no objection
    recorder._pending["later.jfr"] = recorder.Pending(1, Path(target), time.monotonic() - 1)  # ended, file written
    target.write_bytes(b"x")
    recorder.check_ready("later.jfr")
    assert recorder.active() == []
