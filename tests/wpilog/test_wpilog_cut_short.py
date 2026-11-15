"""A log cut short by a power loss: the index, the topic counts and the rows read later must all agree on the records."""

import struct

from log_builder import build_sample_log

from frc_mcp_wpilib.wpilog_writer import LogWriter, _le
from frc_mcp_wpilog.logfile import PRELOAD_ROWS_PER_TOPIC, LogFile


def complete_records(data: bytes) -> tuple[int, bool]:
    """How many data records (entry id above 0) are whole in `data`, and whether `data` ends exactly at a record's end.
    Written from the file format alone, so it does not share the reader's behavior."""
    at = 12 + int.from_bytes(data[8:12], "little")
    data_records = 0
    while at < len(data):
        b = data[at]
        entry_len, size_len, stamp_len = (b & 3) + 1, ((b >> 2) & 3) + 1, ((b >> 4) & 7) + 1
        head = 1 + entry_len + size_len + stamp_len
        if at + head > len(data):
            return data_records, False
        entry = int.from_bytes(data[at + 1:at + 1 + entry_len], "little")
        size = int.from_bytes(data[at + 1 + entry_len:at + 1 + entry_len + size_len], "little")
        if at + head + size > len(data):
            return data_records, False
        data_records += entry != 0
        at += head + size
    return data_records, True


def check_open(log: LogFile, data: bytes, offset: int) -> None:
    expected, clean = complete_records(data)
    assert log.records == expected, offset
    assert log.cut_short == (not clean), offset
    rows = log.rows(list(log.topics))
    for name, topic in log.topics.items():
        assert topic.count == len(rows[name]), (offset, name)
    assert sum(t.count for t in log.topics.values()) + log.skipped_reasons["decode"] == log.records, offset


def test_every_cut_of_a_small_log_gives_one_consistent_answer(tmp_path):
    full = build_sample_log(tmp_path / "sample.wpilog").read_bytes()
    first = 12 + int.from_bytes(full[8:12], "little")
    path = tmp_path / "cut.wpilog"
    path.write_bytes(full)  # `sources` must exist
    for n in range(first, len(full) + 1):  # every offset, in memory (a converted Driver Station log reads the same way)
        check_open(LogFile(path, data=full[:n], sources=[path]), full[:n], n)
    for n in [*range(first, len(full), 16), *range(len(full) - 64, len(full) + 1)]:  # and as a file, thinned to keep it quick
        path.write_bytes(full[:n])
        check_open(LogFile(path), full[:n], n)


def test_a_topic_over_the_preload_limit_is_cut_like_the_index(tmp_path):
    w = LogWriter()
    e = w.start("/x", "double", "", 0)
    n = PRELOAD_ROWS_PER_TOPIC + 501
    for i in range(n):
        w.append(e, float(i), 1000 * (i + 1))
    w.append(e, -1.0, 10_000_000)  # the record that gets cut
    full = w.to_bytes()
    path = tmp_path / "big.wpilog"
    for cut in [*range(1, 40), 100, 1000, 5000]:  # inside the last record, then well into the file
        path.write_bytes(full[:-cut])
        log = LogFile(path)
        assert "/x" not in log._cache  # so the rows below are read by `_load`, not kept while indexing
        check_open(log, full[:-cut], len(full) - cut)
    path.write_bytes(full[:-3])
    rows = LogFile(path).rows(["/x"])["/x"]
    assert len(rows) == n and rows[-1] == (1000 * n, float(n - 1))


def test_a_complete_last_record_with_wide_header_fields_is_kept(tmp_path):
    w = LogWriter()
    e = w.start("/x", "double", "", 0)
    w.append(e, 1.0, 100)
    w.append(e, 2.0, 200)
    payload = struct.pack("<d", 3.0)
    record = bytes([0 | (0 << 2) | (3 << 4)]) + _le(e, 1) + _le(len(payload), 1) + _le(300, 4) + payload  # a 4-byte timestamp
    path = tmp_path / "wide.wpilog"
    path.write_bytes(w.to_bytes() + record)
    log = LogFile(path)
    assert not log.cut_short and log.topics["/x"].count == 3
    assert list(log.rows(["/x"])["/x"]) == [(100, 1.0), (200, 2.0), (300, 3.0)]
    path.write_bytes(w.to_bytes() + record[:-2])  # and cut, it is still a cut log with two records
    cut = LogFile(path)
    assert cut.cut_short and cut.topics["/x"].count == 2


def test_a_log_with_no_records_after_the_header_opens_empty(tmp_path):
    full = LogWriter().to_bytes()
    path = tmp_path / "empty.wpilog"
    path.write_bytes(full)
    log = LogFile(path)
    assert log.records == 0 and not log.cut_short and not log.topics
