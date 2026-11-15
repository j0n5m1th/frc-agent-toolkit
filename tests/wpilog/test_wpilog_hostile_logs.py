"""Logs that are damaged, odd or being written must give clear answers, never an uncaught exception or a wrong number."""

import struct
import sys
import threading
import time

import pytest
from dslog_builder import build_session
from log_builder import S

from frc_mcp_wpilib.values import DEFAULT_PRECISION
from frc_mcp_wpilib.wpilog_writer import LogWriter
from frc_mcp_wpilog import dslog, queries, store, timeline
from frc_mcp_wpilog import logfile as logfile_module
from frc_mcp_wpilog.errors import WPILogError
from frc_mcp_wpilog.logfile import LogFile
from frc_mcp_wpilog.overview import overview
from frc_mcp_wpilog.queries import QuerySpec, Range

NAN, INF = float("nan"), float("inf")


def save(tmp_path, writer, name="x.wpilog"):
    return writer.save(tmp_path / name)


def ask(log, names, reduce="summary", since=None, until=None, **spec):
    return queries.query(log, names, QuerySpec(reduce=reduce, **spec), Range("start", since, until))


def two_second_log(tmp_path):
    w = LogWriter()
    e = w.start("/v", "double", "", 0)
    for i in range(21):
        w.append(e, 50.0, i * S // 10)
    return LogFile(save(tmp_path, w))


# --- `when` must not hold a condition past the end of the log -------------------------------------------------
def test_when_a_value_holds_only_until_the_log_ends(tmp_path):
    log = two_second_log(tmp_path)
    out = queries.when(log, "/v", ">", 40, None, Range("start", None, 3_600_000), 10)
    assert out["intervals_ms"] == [[0.0, 2000.0]] and out["total_ms"] == 2000.0 and out["true_at_end"] is True
    assert queries.when(log, "/v", ">", 40, None, Range(), 10)["total_ms"] == 2000.0  # no `until` agrees


def test_when_a_window_inside_the_log_still_ends_at_its_own_until(tmp_path):
    log = two_second_log(tmp_path)
    out = queries.when(log, "/v", ">", 40, None, Range("start", 500, 1500), 10)
    assert out["intervals_ms"] == [[500.0, 1500.0]] and out["total_ms"] == 1000.0


def test_when_a_window_after_the_log_finds_nothing(tmp_path):
    log = two_second_log(tmp_path)
    assert queries.when(log, "/v", ">", 40, None, Range("start", 5000, 6000), 10) == {"n": 0, "total_ms": 0.0, "intervals_ms": []}
    assert queries.when(log, "/v", "changed", None, None, Range("start", 5000, 6000), 10) == {"n": 0, "changes_ms": []}


# --- a name restarted with another type -----------------------------------------------------------------------
def retyped_log(tmp_path):
    w = LogWriter()
    first = w.start("/x", "double", "", 0)
    w.append(first, 1.5, 100)
    w.finish(first, 200)
    second = w.start("/x", "string", "", 300)
    w.append(second, "hi", 400)
    other = w.start("/ok", "double", "", 0)
    w.append(other, 2.0, 100)
    w.append(other, 4.0, 200)
    return LogFile(save(tmp_path, w))


@pytest.mark.parametrize("reduce", ["summary", "percentiles", "rate", "edges", "durations", "time_weighted", "changes", "latest"])
def test_a_retyped_topic_gets_a_short_error_and_the_others_still_answer(tmp_path, reduce):
    log = retyped_log(tmp_path)
    data = ask(log, ["/x", "/ok"], reduce, threshold=1.0)["data"]
    assert "changed type" in data["/x"]["err"] and "double" in data["/x"]["err"] and "string" in data["/x"]["err"]
    assert "err" not in data["/ok"]


def test_a_retyped_topic_does_not_break_search_when_or_value_lookup(tmp_path):
    log = retyped_log(tmp_path)
    found = queries.search_text(log, ["/x"], "hi", Range(), 10)
    assert [item["v"] for item in found["items"]] == ["hi"]
    assert queries.search_text(log, ["/x"], "1.5", Range(), 10)["n"] == 1  # the number is searched as text
    with pytest.raises(WPILogError, match="changed type"):
        queries.when(log, "/x", ">", 1, None, Range(), 10)
    assert "changed type" in queries.values_at(log, ["/x"], None, None, "start")["/x"]["err"]


def test_numbers_restarted_as_other_numbers_are_not_an_error(tmp_path):
    w = LogWriter()
    a = w.start("/n", "double", "", 0)
    w.append(a, 1.0, 0)
    w.finish(a, 1)
    b = w.start("/n", "int64", "", 2)
    w.append(b, 5, 3)
    log = LogFile(save(tmp_path, w))
    assert log.topics["/n"].mixed_types == ()
    assert ask(log, ["/n"])["data"]["/n"]["max"] == 5.0


# --- NaN and infinity in a log --------------------------------------------------------------------------------
def test_non_finite_values_in_a_log_do_not_fail_the_query(tmp_path):
    w = LogWriter()
    e = w.start("/s", "double", "", 0)
    for i, v in enumerate([1.0, INF, 3.0, -INF, NAN, 5.0, 5.0, 5.0, 5.0, NAN]):
        w.append(e, v, i * 1000)
    log = LogFile(save(tmp_path, w))
    summary = ask(log, ["/s"])["data"]["/s"]
    assert summary["non_finite"] == 4 and (summary["min"], summary["max"]) == (1.0, 5.0)
    assert ask(log, ["/s"], "percentiles")["data"]["/s"]["non_finite"] == 4
    assert "err" not in ask(log, ["/s"], "step_response")["data"]["/s"]  # a NaN in the last 10% used to raise StopIteration
    rate = ask(log, ["/s"], "rate")["data"]["/s"]
    assert rate["non_finite"] == 4 and "err" not in rate  # samples, as in the summary
    assert ask(log, ["/s"], "time_weighted")["data"]["/s"]["non_finite"] == 4  # used to fail the whole topic
    edges = ask(log, ["/s"], "edges", threshold=4.0)["data"]["/s"]
    assert edges["non_finite"] == 4 and edges["rising_ms"] == [5.0]  # 3 -> 5 across the NaN at 4 ms: dated by the 5.0 at 5 ms


def test_a_log_of_only_nan_says_so_plainly_in_every_reduction(tmp_path):
    w = LogWriter()
    e = w.start("/n", "double", "", 0)
    for i in range(8):
        w.append(e, NAN, i * 1000)
    log = LogFile(save(tmp_path, w))
    for reduce in ("summary", "percentiles", "rate", "step_response", "time_weighted"):
        got = ask(log, ["/n"], reduce)["data"]["/n"]
        assert got["non_finite"] == 8 and "No finite values" in got["note"], reduce


# --- the query reads the window, not the whole topic ----------------------------------------------------------
def long_log(tmp_path, n=3000):
    w = LogWriter()
    e = w.start("/long", "double", "", 0)
    for i in range(n):
        w.append(e, float(i % 7), i * 1000)
    return LogFile(save(tmp_path, w))


def test_a_window_query_converts_only_the_rows_it_needs(tmp_path, monkeypatch):
    from frc_mcp_wpilog import reduce_queries

    log = long_log(tmp_path)
    converted = []
    real = reduce_queries.aligned
    monkeypatch.setattr(reduce_queries, "aligned", lambda rows, origin: converted.append(len(rows)) or real(rows, origin))
    ask(log, ["/long"], "summary", since=1000, until=1099)
    assert sum(converted) <= 103  # the 100 rows inside, the one held before and the one after


def untidy_log(tmp_path):
    """Sub-millisecond steps, a repeated timestamp every 7th row, values that change and repeat, and a topic that starts after the
    log does (so the origin is not the topic's first row)."""
    w = LogWriter()
    anchor = w.start("/anchor", "double", "", 0)
    w.append(anchor, 0.0, 5 * S)
    e = w.start("/r", "double", "", 0)
    for i in range(300):
        t = 5 * S + 1500 + i * 400
        w.append(e, float((i * 37) % 11), t)
        if i % 7 == 0:
            w.append(e, float((i * 5) % 11), t)  # the same microsecond again
    return LogFile(save(tmp_path, w))


@pytest.mark.parametrize("reduce", ["summary", "time_weighted", "percentiles", "edges", "changes", "rate", "latest", "durations", "samples", "step_response"])
@pytest.mark.parametrize(("since", "until"), [(None, None), (1.5, 1.5), (5.9, 5.9), (5.6, 20.1), (0.3, 0.7), (None, 3.7), (110.0, 9000), (9000, 9500), (-5, 3), (30, 20)])
def test_a_windowed_answer_equals_the_answer_over_all_the_rows(tmp_path, reduce, since, until):
    """The window is cut by bisect and only its neighbours are converted; the numbers must be the ones the full history gives,
    including at a boundary that falls on a repeated timestamp."""
    from frc_mcp_wpilib.reduce.segments import Window
    from frc_mcp_wpilib.reduce.stats import Shaper, reduce_rows
    from frc_mcp_wpilog.windows import _in_window, aligned

    log = untidy_log(tmp_path)
    rows = aligned(log.rows(["/r"])["/r"], log.start_us)
    inside = _in_window(rows, since, until)
    expected = reduce_rows(inside, Shaper("double", None, DEFAULT_PRECISION), reduce, 20, threshold=5.0,
                           window=Window(history=rows, since_ms=since, until_ms=until))
    spec = QuerySpec(reduce=reduce, threshold=5.0)
    got = queries.query(log, ["/r"], spec, Range("start", since, until), collapse_constants=False)["data"]["/r"]
    held, held_since = got.pop("held", None), got.pop("held_since_ms", None)
    before = [row for row in rows if since is not None and row[0] < since]
    if not inside and before:  # nothing inside the window: it says what the topic held through it
        assert held == before[-1][1] and held_since == round(before[-1][0], 1)
    else:
        assert held is None and held_since is None
    got.pop("unit", None)
    skipped, full = got.pop("skipped_nonincreasing_intervals", 0), expected.pop("skipped_nonincreasing_intervals", 0)
    assert skipped <= full and (since, until) != (None, None) or skipped == full  # a window counts the repeats inside it only
    assert got == expected


def test_a_window_with_no_rows_still_says_what_the_topic_held(tmp_path):
    w = LogWriter()
    e = w.start("/h", "double", "", 0)
    w.append(e, 7.0, 0)
    w.append(e, 8.0, 10 * S)
    log = LogFile(save(tmp_path, w))
    got = ask(log, ["/h"], since=2000, until=3000)["data"]["/h"]
    assert got["n"] == 0 and got["held"] == 7.0 and got["held_since_ms"] == 0.0


# --- the struct decode cache is bounded by bytes --------------------------------------------------------------
def test_the_struct_decode_cache_cost_is_tracked_and_bounded():
    from frc_mcp_wpilib.schema import structs
    from frc_mcp_wpilib.schema.structs import Registry

    r = Registry()
    r.add("/.schema/struct:Big", "double a[100]")
    for i in range(400):
        r.decode_strict("struct:Big[]", struct.pack("<100d", *([float(i)] * 100)))
    assert r._decoded_cost <= structs.MAX_DECODED_BYTES
    assert r._decoded_cost == sum(cost for cost, _ in r._decoded.values())  # the counter is the sum of what it holds


def test_the_decode_cache_estimate_is_not_below_the_real_memory_for_small_fields():
    """Small fields cost more per byte than doubles (a dict key per field, a new string for each): 8 bools x 100 was 4x under."""
    import os
    import tracemalloc

    from frc_mcp_wpilib.schema import structs
    from frc_mcp_wpilib.schema.structs import Registry

    r = Registry()
    r.add("/.schema/struct:B", "bool a;bool b;bool c;bool d;bool e;bool f;bool g;bool h")
    r.add("/.schema/struct:W", "B items[100]")
    r.add("/.schema/struct:Bytes", "uint8 a;uint8 b;uint8 c;uint8 d")
    for type_str, size in (("struct:W", 800), ("struct:Bytes[]", 4000)):
        r.add("/.schema/struct:B", "bool a;bool b;bool c;bool d;bool e;bool f;bool g;bool h")  # an empty cache for each case
        tracemalloc.start()
        try:
            base = tracemalloc.get_traced_memory()[0]
            for _ in range(100):
                r.decode_strict(type_str, os.urandom(size))
            measured = tracemalloc.get_traced_memory()[0] - base
        finally:
            tracemalloc.stop()
        assert r._decoded_cost >= measured, (type_str, r._decoded_cost, measured)
        assert r._decoded_cost < 6 * measured  # and it is not wildly high either, or the cache would hold almost nothing
    assert r._decoded_cost <= structs.MAX_DECODED_BYTES


def test_the_cache_counter_stays_equal_to_its_entries_while_schemas_change_under_it():
    from frc_mcp_wpilib.schema.structs import Registry

    r = Registry()
    r.add("/.schema/struct:P", "double a;double b")
    stop = threading.Event()
    errors = []

    def decode():
        try:
            for i in range(3000):
                r.decode_strict("struct:P", struct.pack("<2d", i % 50, -i))
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    def swap():
        while not stop.is_set():
            r.add("/.schema/struct:P", "double a;double b")  # swaps the dict and zeroes the counter

    workers = [threading.Thread(target=decode) for _ in range(4)]
    swapper = threading.Thread(target=swap)
    swapper.start()
    for t in workers:
        t.start()
    for t in workers:
        t.join(30)
    stop.set()
    swapper.join(10)
    assert errors == [] and r._decoded_cost == sum(cost for cost, _ in r._decoded.values())


def test_a_cache_over_its_byte_cap_starts_over_and_answers_stay_right(monkeypatch):
    from frc_mcp_wpilib.schema import structs
    from frc_mcp_wpilib.schema.structs import Registry

    monkeypatch.setattr(structs, "MAX_DECODED_BYTES", 10_000)
    r = Registry()
    r.add("/.schema/struct:P", "double a;double b")
    for i in range(200):
        assert r.decode_strict("struct:P", struct.pack("<2d", i, -i)) == {"a": float(i), "b": float(-i)}
        assert r._decoded_cost <= 10_000
    monkeypatch.setattr(structs, "MAX_DECODED_BYTES", 100)  # smaller than one entry: nothing is kept, everything decodes
    r2 = Registry()
    r2.add("/.schema/struct:P", "double a;double b")
    assert r2.decode_strict("struct:P", struct.pack("<2d", 1, 2)) == {"a": 1.0, "b": 2.0} and not r2._decoded


# --- damaged files --------------------------------------------------------------------------------------------
def test_a_last_record_with_a_timestamp_of_two_to_the_63_still_opens(tmp_path):
    w = LogWriter()
    e = w.start("/a", "double", "", 0)
    w.append(e, 1.0, 100)
    w.append(e, 2.0, 2**63 + 5)
    log = LogFile(save(tmp_path, w))  # used to raise OverflowError
    assert "/a" in log.topics and not log.cut_short


def test_a_real_damaged_file_is_reported_by_the_overview(tmp_path):
    w = LogWriter()
    good = w.start("/good", "double", "", 0)
    bad = w.start("/bad", "double", "", 0)
    for i in range(5):
        w.append(good, float(i), i * 1000)
    w.append(bad, 1.0, 10)
    w._record(bad, 20, b"")  # a zero-length payload for a double: the reader cannot decode it
    w.append(w.start("/late", "double", "", 6000), 9.0, 6000)
    big = w.start("/big", "double", "", 0)
    for i in range(logfile_module.PRELOAD_ROWS_PER_TOPIC + 5):  # not kept while indexing: `rows` reads it from the file
        w.append(big, float(i), 7000 + i)
    w.append(big, -1.0, 99_000)  # the record that is cut
    path = tmp_path / "damaged.wpilog"
    path.write_bytes(w.to_bytes()[:-3])  # the power went out in the middle of the last record
    log = LogFile(path)
    notes = overview(log, "l1")["warnings"]
    assert log.cut_short and any("cut short" in n for n in notes)
    assert log.skipped_reasons["decode"] == 1 and any("1 decode" in n for n in notes)
    big_n = logfile_module.PRELOAD_ROWS_PER_TOPIC + 5
    assert log.topics["/good"].count == 5 and log.records == 8 + big_n  # the cut-off record is not counted (the reader repeats the one before it)
    assert log.topics["/bad"].count == 2 and log.topics["/late"].count == 1
    assert log.topics["/big"].count == big_n and "/big" not in log._cache
    rows = log.rows(["/big", "/good", "/late"])
    assert len(rows["/big"]) == big_n and rows["/big"][-1] == (7000 + big_n - 1, float(big_n - 1))  # no repeat of the last row
    assert len(rows["/good"]) == 5 and len(rows["/late"]) == 1
    assert all(log.topics[n].count == len(r) for n, r in rows.items())


def test_a_diff_survives_a_topic_whose_only_record_failed_to_decode(tmp_path):
    w = LogWriter()
    ok = w.start("/ok", "double", "", 0)
    w.append(ok, 1.0, 0)
    broken = w.start("/broken", "double", "", 0)
    w._record(broken, 5, b"")
    one = LogFile(save(tmp_path, w, "a.wpilog"))
    out = queries.diff({"l1": one, "l2": one}, "/", None, 10)
    assert out["same"] == 1


def test_a_non_finite_control_word_does_not_stop_the_log_opening(tmp_path):
    w = LogWriter()
    e = w.start("/FMSInfo/FMSControlData", "double", "", 0)
    for i, v in enumerate([NAN, INF, 33.0, 32.0]):
        w.append(e, v, i * 1000)
    log = LogFile(save(tmp_path, w))
    phases = timeline.phases(log)  # the finite words still count; NaN and inf say nothing
    assert [(p.mode, p.start_us, p.end_us) for p in phases] == [("teleop", 2000, 3000)]  # 33 = enabled, 32 = disabled
    assert overview(log, "l1")["topics"] == 1


def test_a_match_type_logged_as_an_array_is_shown_as_logged(tmp_path):
    w = LogWriter()
    e = w.start("/FMSInfo/MatchType", "double[]", "", 0)
    w.append(e, [1.0, 2.0], 0)
    assert timeline.match_info(LogFile(save(tmp_path, w)))["MatchType"] == [1.0, 2.0]


# --- Driver Station files with bad times ----------------------------------------------------------------------
def header(seconds, fraction=0):
    return struct.pack(">iqQ", 4, seconds, fraction)


@pytest.mark.parametrize("seconds", [2**62, -(2**62)])
def test_a_driver_station_log_with_an_impossible_start_time_is_refused_clearly(tmp_path, seconds):
    path = tmp_path / "bad.dslog"
    path.write_bytes(header(seconds))
    with pytest.raises(WPILogError, match="not a real date"):
        dslog.convert(path)


def test_a_driver_station_event_with_an_impossible_time_ends_the_file_there(tmp_path):
    start = 3_800_000_000
    events = header(start)
    for seconds, text in ((start, b"hello"), (2**62, b"later")):
        events += struct.pack(">qQi", seconds, 0, len(text)) + text
    path = tmp_path / "x.dsevents"
    path.write_bytes(events)
    converted = dslog.convert(path)
    assert converted.cut_short
    log = LogFile(path, converted.data, converted.sources)
    assert [v for _, v in log.rows(["/DSEvents"])["/DSEvents"]] == ["hello"]
    assert log.end_us < 10 * S


def test_the_writer_refuses_a_record_it_could_not_write_correctly():
    w = LogWriter()
    e = w.start("/a", "double", "", 0)
    for bad in (2**64, -1):
        with pytest.raises(ValueError, match="cannot hold"):
            w.append(e, 1.0, bad)
    w.append(e, 1.0, 2**64 - 1)  # the largest time that fits


def test_a_good_driver_station_session_still_converts(tmp_path):
    assert dslog.convert(build_session(tmp_path)).data


# --- the file changed after it was indexed --------------------------------------------------------------------
def test_a_query_that_must_read_a_changed_file_says_to_reopen(tmp_path):
    w = LogWriter()
    e = w.start("/big", "double", "", 0)
    for i in range(logfile_module.PRELOAD_ROWS_PER_TOPIC + 50):  # too big to be read while indexing
        w.append(e, float(i), i)
    path = save(tmp_path, w)
    log = LogFile(path)
    assert "/big" not in log._cache
    path.write_bytes(path.read_bytes() + b"\x00")
    with pytest.raises(WPILogError, match="changed on disk.*wpilog_open"):
        log.rows(["/big"])


# --- synced views copy nothing --------------------------------------------------------------------------------
def test_a_shifted_view_of_rows_reads_like_the_rows(tmp_path):
    from frc_mcp_wpilog.rows import ShiftedRows

    log = long_log(tmp_path, 100)
    rows = log.rows(["/long"])["/long"]
    shifted = ShiftedRows(rows, 5000)
    assert len(shifted) == 100 and shifted[3] == (3000 + 5000, 3.0) and shifted[-1][0] == 99_000 + 5000
    assert list(shifted[2:4]) == [(7000, 2.0), (8000, 3.0)] and list(shifted)[0] == (5000, 0.0)
    assert list(shifted) == [(t + 5000, v) for t, v in rows]


def shift_pair(tmp_path, shift_us):
    """A synced view in which log l2's rows are shifted by `shift_us`, and one plain log that holds the same rows already there."""
    from frc_mcp_wpilog import sync

    def make(path, shifted_by):
        w = LogWriter()
        anchor = w.start("/anchor", "double", "", 0)
        w.append(anchor, 0.0, 0)
        e = w.start("/y", "double", "", 0)
        for i in range(300):
            t = 2000 + i * 700 + shifted_by
            w.append(e, float((i * 13) % 9), t)
            if i % 11 == 0:
                w.append(e, 4.0, t)
        return LogFile(save(tmp_path, w, path))

    base = LogFile(save(tmp_path, _anchor_only(), "l1.wpilog"))
    view, _ = sync.build("s1", {"l1": base, "l2": make("l2.wpilog", 0)}, "auto", [shift_us / 1000])
    return view, make("flat.wpilog", shift_us)


def _anchor_only():
    w = LogWriter()
    w.append(w.start("/anchor1", "double", "", 0), 0.0, 0)
    return w


@pytest.mark.parametrize("reduce", ["summary", "time_weighted", "edges", "rate", "durations", "percentiles"])
@pytest.mark.parametrize(("since", "until"), [(None, None), (40.0, 90.0), (500.0, 500.0)])
def test_a_synced_view_answers_windowed_questions_as_the_rows_moved_into_place_would(tmp_path, reduce, since, until):
    view, flat = shift_pair(tmp_path, 3_000_000)
    from frc_mcp_wpilog.rows import ShiftedRows

    assert isinstance(view.rows(["/y"])["/y"], ShiftedRows)  # no copy: the synced log hands out the view
    got = ask(view, ["/y"], reduce, since=since, until=until, threshold=4.0)["data"]["/y"]
    want = ask(flat, ["/y"], reduce, since=since, until=until, threshold=4.0)["data"]["/y"]
    assert got == want  # both start at 0, so the same window names the same rows
    assert "err" not in got


def test_when_over_a_synced_view_finds_the_same_stretches_as_the_rows_moved_into_place(tmp_path):
    view, flat = shift_pair(tmp_path, 3_000_000)
    got = queries.when(view, "/y", ">", 5, None, Range(), 50)
    want = queries.when(flat, "/y", ">", 5, None, Range(), 50)
    assert got["intervals_ms"] == want["intervals_ms"] and got["intervals_ms"]


# --- a log opened while others are queried and evicted --------------------------------------------------------
def test_opening_logs_while_another_thread_queries_and_evicts_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(logfile_module, "MAX_CACHED_ROWS", 50)  # tiny: every query evicts
    paths = []
    for n in range(3):
        w = LogWriter()
        for t in range(40):  # many small topics: the opening log stores them one by one into the shared cache
            e = w.start(f"/t{t}", "double", "", 0)
            for i in range(5):
                w.append(e, float(i), i)
        paths.append(save(tmp_path, w, f"l{n}.wpilog"))
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # switch threads as often as possible, to give a race its chance
    holder = store.OpenLogs()
    _, first = holder.open(paths[0])
    errors, stop = [], threading.Event()

    def query_and_evict():
        try:
            while not stop.is_set():
                for names in (["/t0", "/t1"], ["/t5"], ["/t2", "/t3", "/t4"], [f"/t{k}" for k in range(10, 30)]):
                    first.rows(names)
                time.sleep(0.001)  # let the opener in: the lock is not fair
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    worker = threading.Thread(target=query_and_evict)
    worker.start()
    try:
        for _ in range(150):
            opened = LogFile(paths[1])  # preloads its rows into the shared cache while the other thread evicts over it
            opened.rows(["/t5"])
            opened.release()
    finally:
        sys.setswitchinterval(old_interval)
        stop.set()
        worker.join(20)
    assert errors == [] and not worker.is_alive()
