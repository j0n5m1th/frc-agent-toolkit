"""How a log holds its rows: compact for number topics, one global row budget, one pass for several topics."""

import gc
import math
import struct
import time

import pytest
from log_builder import build_sample_log

from frc_mcp_wpilib.reduce.models import Condition
from frc_mcp_wpilib.wpilog_writer import LogWriter
from frc_mcp_wpilog import logfile, queries
from frc_mcp_wpilog.logfile import LogFile
from frc_mcp_wpilog.queries import Range
from frc_mcp_wpilog.rows import NumberRows, RowBuilder, compact_kind

CURRENT = "/Fake/Elevator/Current"
ANGLE = "/Fake/Arm/Angle"
RAW = "/Fake/Debug/Raw"


def mixed_log(path, *, n=40):
    """One topic of every storage-relevant type."""
    w = LogWriter()
    entries = {
        "double": w.start("/d", "double", "", 0), "float": w.start("/f", "float", "", 0), "int": w.start("/i", "int64", "", 0),
        "bool": w.start("/b", "boolean", "", 0), "text": w.start("/s", "string", "", 0), "arr": w.start("/a", "double[]", "", 0),
        "struct": w.start("/p", "struct:V", "", 0),
    }
    w.schema("struct:V", "double x;double y", t_us=0)
    for k in range(n):
        t = 1000 + k * 10
        w.append(entries["double"], k * 0.25 - 3, t)
        w.append(entries["float"], k * 0.5, t)
        w.append(entries["int"], k - 20, t)
        w.append(entries["bool"], bool(k % 3), t)
        w.append(entries["text"], f"line {k}", t)
        w.append(entries["arr"], [k, k + 0.5], t)
        w.append(entries["struct"], struct.pack("<2d", k, -k), t)
    return w.save(path)


def expected(n=40):
    times = [1000 + k * 10 for k in range(n)]
    return {
        "/d": [(t, k * 0.25 - 3) for k, t in enumerate(times)], "/f": [(t, k * 0.5) for k, t in enumerate(times)],
        "/i": [(t, k - 20) for k, t in enumerate(times)], "/b": [(t, bool(k % 3)) for k, t in enumerate(times)],
        "/s": [(t, f"line {k}") for k, t in enumerate(times)], "/a": [(t, [k, k + 0.5]) for k, t in enumerate(times)],
        "/p": [(t, struct.pack("<2d", k, -k)) for k, t in enumerate(times)],
    }


@pytest.mark.parametrize("preload", [2000, 0])  # rows read while indexing, or in the second pass
def test_every_type_reads_back_the_same_rows_whichever_way_it_is_stored(tmp_path, monkeypatch, preload):
    monkeypatch.setattr(logfile, "PRELOAD_ROWS_PER_TOPIC", preload)
    log = LogFile(mixed_log(tmp_path / "m.wpilog"))
    got = log.rows(list(expected()))
    for name, want in expected().items():
        assert got[name] == want and want == got[name], name
        assert list(got[name]) == want and len(got[name]) == len(want)
        assert got[name][-1] == want[-1] and got[name][3:6] == want[3:6] and list(reversed(got[name])) == want[::-1]
    for name in ("/d", "/f", "/i"):
        assert isinstance(got[name], NumberRows), name  # numbers are held compactly
    for name in ("/b", "/s", "/a", "/p"):
        assert isinstance(got[name], list), name  # everything else stays a list
    assert type(got["/i"][0][1]) is int and type(got["/d"][0][1]) is float and type(got["/f"][0][1]) is float


def test_number_rows_equal_lists_both_ways_and_keep_special_floats():
    builder = RowBuilder("d")
    for t, v in ((1, math.inf), (2, -0.0), (3, 1e300)):
        builder.add(t, v)
    rows = builder.finish()
    assert rows == [(1, math.inf), (2, -0.0), (3, 1e300)] and [(1, math.inf), (2, -0.0), (3, 1e300)] == rows
    assert rows != [(1, math.inf), (2, 0.0, 9)] and rows != [(1, 1.0)] and rows != "x"
    assert rows[0:2] == rows[:2] and len(rows[1:]) == 2 and not rows[:0]
    nan = RowBuilder("d")
    nan.add(1, math.nan)
    assert math.isnan(nan.finish()[0][1])


def test_unsorted_rows_are_sorted_stably_like_the_tuples_were():
    for kind in ("d", None):
        builder = RowBuilder(kind)
        for t, v in ((5, 1.0), (3, 2.0), (5, 3.0), (3, 4.0), (1, 5.0)):
            builder.add(t, v)
        assert list(builder.finish()) == [(1, 5.0), (3, 2.0), (3, 4.0), (5, 1.0), (5, 3.0)]


def test_a_time_or_value_too_big_for_the_arrays_turns_the_topic_into_a_list():
    for kind, bad in (("d", (2**64 - 1, 2.0)), ("q", (7, 2**70))):
        builder = RowBuilder(kind)
        builder.add(1, 1.0 if kind == "d" else 1)
        builder.add(*bad)
        builder.add(9, 3.0 if kind == "d" else 3)
        rows = builder.finish()
        assert isinstance(rows, list) and bad in rows and len(rows) == 3 and [r[0] for r in rows] == sorted(r[0] for r in rows)


def test_the_storage_choice_follows_all_the_entries_of_a_topic():
    assert compact_kind(["double"]) == "d" and compact_kind(["float", "double"]) == "d" and compact_kind(["int64"]) == "q"
    assert compact_kind(["double", "int64"]) is None and compact_kind(["string"]) is None and compact_kind([]) is None
    assert compact_kind(["boolean"]) is None and compact_kind(["double", "string"]) is None


def test_out_of_order_times_in_one_entry_are_sorted_the_same_with_or_without_preloading(tmp_path, monkeypatch):
    w = LogWriter()
    e = w.start("/x", "double", "", 0)
    for t, v in ((30, 3.0), (10, 1.0), (20, 2.0)):
        w.append(e, v, t)
    path = w.save(tmp_path / "o.wpilog")
    for preload in (2000, 0):
        monkeypatch.setattr(logfile, "PRELOAD_ROWS_PER_TOPIC", preload)
        assert LogFile(path).rows(["/x"])["/x"] == [(10, 1.0), (20, 2.0), (30, 3.0)]


def open_logs(tmp_path, monkeypatch, count=2, preload=0):
    monkeypatch.setattr(logfile, "PRELOAD_ROWS_PER_TOPIC", preload)
    return [LogFile(build_sample_log(tmp_path / f"s{i}.wpilog")) for i in range(count)]


def test_the_row_limit_is_shared_by_all_open_logs_and_drops_the_least_recently_used_topic(tmp_path, monkeypatch):
    first, second = open_logs(tmp_path, monkeypatch)
    monkeypatch.setattr(logfile, "MAX_CACHED_ROWS", 300)
    first.rows([CURRENT])  # 121 rows, the oldest use
    second.rows([CURRENT])  # 242 in all
    first.rows([ANGLE])  # 267 in all: still within the limit
    assert set(first._cache) == {CURRENT, ANGLE} and set(second._cache) == {CURRENT}
    second.rows([RAW])  # 277 in all: still fits
    second.rows([ANGLE])  # 302: over by 2, so the least recently used topic of ANY log goes: first's Current
    assert set(first._cache) == {ANGLE} and set(second._cache) == {CURRENT, RAW, ANGLE}
    assert first._cached_rows + second._cached_rows <= 300
    assert len(first.rows([CURRENT])[CURRENT]) == 121  # and an evicted topic is simply read again


def test_asking_again_makes_a_topic_recent_across_logs(tmp_path, monkeypatch):
    first, second = open_logs(tmp_path, monkeypatch)
    monkeypatch.setattr(logfile, "MAX_CACHED_ROWS", 260)
    first.rows([CURRENT])
    second.rows([CURRENT])
    first.rows([CURRENT])  # first's is now newer than second's
    first.rows([ANGLE])  # 267 > 260: second's Current is the oldest
    assert CURRENT in first._cache and CURRENT not in second._cache


def test_the_topics_just_asked_for_stay_even_when_they_alone_exceed_the_limit(tmp_path, monkeypatch):
    first, second = open_logs(tmp_path, monkeypatch)
    monkeypatch.setattr(logfile, "MAX_CACHED_ROWS", 100)
    second.rows([ANGLE])
    rows = first.rows([CURRENT, RAW])  # 131 rows, over the limit with nothing else to drop
    assert set(rows) == {CURRENT, RAW} and set(first._cache) == {CURRENT, RAW} and not second._cache
    assert len(rows[CURRENT]) == 121


def test_a_released_or_dropped_log_stops_counting(tmp_path, monkeypatch):
    first, second = open_logs(tmp_path, monkeypatch)
    first.rows([CURRENT])
    second.rows([CURRENT])
    first.release()
    assert first not in logfile._OPEN_FILES and first._cached_rows == 0
    assert second in logfile._OPEN_FILES
    del second
    gc.collect()
    assert not [log for log in logfile._OPEN_FILES if log.path.parent == tmp_path]  # the registry does not keep logs alive


def reader_passes(monkeypatch):
    """A counter of how many times the file is read through (install it after opening: indexing reads it too)."""
    count = {"n": 0}
    real = LogFile._reader

    def counted(self):
        count["n"] += 1
        return real(self)

    monkeypatch.setattr(LogFile, "_reader", counted)
    return count


def test_several_topics_asked_together_are_read_in_one_pass(tmp_path, monkeypatch):
    (log,) = open_logs(tmp_path, monkeypatch, count=1)
    passes = reader_passes(monkeypatch)
    log.rows([CURRENT, ANGLE, RAW, "/Fake/Match/Mode"])
    assert passes["n"] == 1
    log.rows([CURRENT, ANGLE])
    assert passes["n"] == 1  # cached


def test_a_when_with_also_conditions_reads_the_file_once(tmp_path, monkeypatch):
    (log,) = open_logs(tmp_path, monkeypatch, count=1)
    passes = reader_passes(monkeypatch)
    out = queries.when(log, CURRENT, ">", 40, None, Range(), 20,
                       also=[Condition(topic=ANGLE, op=">", value=5.0), Condition(topic=RAW, op=">=", value=0)])
    assert passes["n"] == 1
    assert out["n"] >= 1


def test_a_large_log_opens_queries_is_evicted_by_the_global_budget_and_keeps_its_skipped_counts(tmp_path, monkeypatch):
    """End to end at scale: 300k records, one bad record per topic, eviction by the shared row budget, then read again."""
    monkeypatch.setattr(logfile, "PRELOAD_ROWS_PER_TOPIC", 0)  # rows come from the second pass, as in a big file
    w = LogWriter()
    topics = [w.start(f"NT:/big/t{k}", "double", "", 0) for k in range(3)]
    per_topic = 100_000
    for i in range(per_topic):
        for entry in topics:
            w.append(entry, float(i), 1000 + i * 10)
    for entry in topics:
        w._record(entry, 5, b"")  # a double with a 1-byte payload: counted as skipped
    path = w.save(tmp_path / "big.wpilog")
    started = time.perf_counter()
    big, small = LogFile(path), LogFile(build_sample_log(tmp_path / "small.wpilog"))
    assert big.records >= 3 * per_topic
    monkeypatch.setattr(logfile, "MAX_CACHED_ROWS", int(1.5 * per_topic))  # room for one big topic only
    names = [f"/big/t{k}" for k in range(3)]
    first = big.rows(names[:1])[names[0]]
    assert len(first) == per_topic and big.skipped_records == 1
    big.rows(names[1:2])  # over the budget: the older topic is evicted
    assert names[0] not in big._cache and names[1] in big._cache
    small.rows([CURRENT])  # another log shares the same budget
    for _ in range(2):  # evicted topics are read again, and the skipped count does not grow
        for name in names:
            assert len(big.rows([name])[name]) == per_topic
    assert big.skipped_records == 3 and sum(big.skipped_reasons.values()) == 3
    assert big.rows(names[:1])[names[0]] == first
    assert big._cached_rows + small._cached_rows <= max(logfile.MAX_CACHED_ROWS, per_topic + 121)
    assert time.perf_counter() - started < 30  # generous: a few seconds on a slow machine
