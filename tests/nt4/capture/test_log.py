"""CaptureLog: a bounded WPILOG file, its schemas, error capture and closing."""

import struct

import pytest
from wpiutil.log import DataLogReader

from frc_mcp_nt4.capture import log as capture_log
from frc_mcp_nt4.capture.log import CaptureLog
from frc_mcp_nt4.errors import NT4Error
from frc_mcp_wpilib.schema import state as schema_state


@pytest.fixture(autouse=True)
def no_schemas_left_over():
    schema_state.forget()
    yield
    schema_state.forget()


def read_log(path):
    """(entries, records): `{name: (type, metadata)}` and `[(name, t_us, value)]` as the official reader sees the file."""
    reader = DataLogReader(str(path))
    assert reader.isValid()
    entries, records = {}, []
    names = {}
    for rec in reader:
        if rec.isStart():
            data = rec.getStartData()
            names[data.entry] = data.name
            entries[data.name] = (data.type, data.metadata)
        elif not rec.isControl():
            name = names[rec.getEntry()]
            kind = entries[name][0]
            getter = {"double": rec.getDouble, "int64": rec.getInteger, "boolean": rec.getBoolean, "string": rec.getString,
                      "double[]": rec.getDoubleArray, "int64[]": rec.getIntegerArray}.get(kind)
            records.append((name, rec.getTimestamp(), getter() if getter else bytes(rec.getRaw())))
    return entries, records


def test_without_a_path_everything_is_a_no_op():
    log = CaptureLog(None)
    log.open()
    log.write("/a", "double", 1, 2.0)
    log.close()
    assert log.file is None and log.bytes == 0 and not log.truncated and log.error is None


def test_writes_a_wpilog_the_official_reader_reads_back(tmp_path):
    path = tmp_path / "cap.wpilog"
    log = CaptureLog(path)
    log.open()
    log.write("/Arm/Angle", "double", 5_000, 1.5)
    log.write("/Counter", "int", 6_000, 7)  # NT4 calls it int, WPILOG int64
    log.write("/Flags", "double[]", 7_000, [1.0, 2.0])
    log.write("/Arm/Angle", "double", 8_000, 2.5)
    log.close()
    entries, records = read_log(path)
    assert entries == {"NT:/Arm/Angle": ("double", ""), "NT:/Counter": ("int64", ""), "NT:/Flags": ("double[]", "")}
    assert records == [("NT:/Arm/Angle", 5_000, 1.5), ("NT:/Counter", 6_000, 7),
                       ("NT:/Flags", 7_000, [1.0, 2.0]), ("NT:/Arm/Angle", 8_000, 2.5)]
    assert log.bytes == path.stat().st_size
    assert log.file is None and not log.truncated and log.error is None


def test_open_refuses_to_overwrite_an_existing_file(tmp_path):
    path = tmp_path / "cap.wpilog"
    path.write_text("keep")
    with pytest.raises(NT4Error, match="Cannot create capture log"):
        CaptureLog(path).open()
    assert path.read_text() == "keep"


def test_the_log_stops_at_the_byte_limit_and_what_was_written_stays_readable(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_log, "MAX_LOG_BYTES", 200)
    path = tmp_path / "cap.wpilog"
    log = CaptureLog(path)
    log.open()
    for i in range(50):
        log.write("/a", "double", i * 1000, float(i))
    assert log.truncated and log.file is None
    assert log.bytes == path.stat().st_size <= 200
    _, records = read_log(path)
    assert records and records[0] == ("NT:/a", 0, 0.0)
    log.write("/a", "double", 99_000, 99.0)  # later writes are ignored
    assert path.stat().st_size == log.bytes


def test_a_write_error_is_recorded_and_stops_logging():
    class Broken:
        closed = False

        def write(self, _data):
            raise OSError("disk full")

        def close(self):
            self.closed = True

    log = CaptureLog(None)
    log.file = broken = Broken()
    log.write("/a", "double", 1, 1.0)
    assert log.error == "disk full" and log.file is None and broken.closed
    assert not log.truncated


def test_a_value_that_does_not_fit_its_type_is_reported_not_skipped_silently(tmp_path):
    log = CaptureLog(tmp_path / "cap.wpilog")
    log.open()
    log.write("/a", "double", 1, "not a number")
    assert "cannot encode a double value of /a" in log.error and log.file is None


def test_a_close_error_is_recorded_and_close_is_idempotent():
    class BadClose:
        def close(self):
            raise OSError("close failed")

    log = CaptureLog(None)
    log.file = BadClose()
    log.close()
    assert log.error == "close failed" and log.file is None
    log.close()  # nothing left to close


def test_struct_values_carry_their_schema_so_the_log_can_be_decoded(tmp_path):
    schema_state.add("/.schema/struct:Point", b"double x;double y")
    path = tmp_path / "cap.wpilog"
    log = CaptureLog(path)
    log.open()
    log.write("/Pose", "struct:Point", 1_000, struct.pack("<dd", 1.5, 2.5))
    log.close()
    entries, records = read_log(path)
    assert entries["/.schema/struct:Point"][0] == "structschema"
    assert entries["NT:/Pose"][0] == "struct:Point"
    assert ("/.schema/struct:Point", 0, b"double x;double y") in records


def test_a_schema_that_arrives_after_the_first_value_is_added_when_the_log_closes(tmp_path):
    path = tmp_path / "cap.wpilog"
    log = CaptureLog(path)
    log.open()
    log.write("/Pose", "struct:Point", 1_000, struct.pack("<dd", 1.5, 2.5))
    schema_state.add("/.schema/struct:Point", b"double x;double y")
    log.close()
    entries, _ = read_log(path)
    assert "/.schema/struct:Point" in entries


def test_a_log_without_struct_topics_has_no_schema_entries(tmp_path):
    schema_state.add("/.schema/struct:Point", b"double x;double y")
    path = tmp_path / "cap.wpilog"
    log = CaptureLog(path)
    log.open()
    log.write("/a", "double", 1, 1.0)
    log.close()
    assert list(read_log(path)[0]) == ["NT:/a"]


def test_a_topic_that_comes_back_with_another_type_starts_a_new_entry_and_logging_goes_on(tmp_path):
    path = tmp_path / "cap.wpilog"
    log = CaptureLog(path)
    log.open()
    log.write("/Arm", "double", 1_000, 1.5)
    log.write("/Arm", "int", 2_000, 7)  # republished as an integer
    log.write("/Arm", "int", 3_000, 8)
    log.close()
    assert log.error is None
    types, starts, finishes, values = {}, [], 0, []
    for rec in DataLogReader(str(path)):
        if rec.isStart():
            data = rec.getStartData()
            types[data.entry] = data.type
            starts.append((data.name, data.type))
        elif rec.isFinish():
            finishes += 1
        elif not rec.isControl():
            read = rec.getInteger if types[rec.getEntry()] == "int64" else rec.getDouble
            values.append((rec.getTimestamp(), read()))
    assert starts == [("NT:/Arm", "double"), ("NT:/Arm", "int64")] and finishes == 1
    assert values == [(1_000, 1.5), (2_000, 7), (3_000, 8)]


def test_schemas_are_collected_once_per_change_not_once_per_struct_topic(tmp_path, monkeypatch):
    schema_state.add("/.schema/struct:Point", b"double x;double y")
    calls = []
    real = schema_state.entries
    monkeypatch.setattr(capture_log.schema_state, "entries", lambda: calls.append(1) or real())
    log = CaptureLog(tmp_path / "cap.wpilog")
    log.open()
    for i in range(5):
        log.write(f"/Pose{i}", "struct:Point", 1_000, struct.pack("<dd", 1.0, 2.0))
    assert len(calls) == 1  # five struct topics, one pass over the schemas
    schema_state.add("/.schema/struct:Other", b"double z")
    log.write("/Pose5", "struct:Point", 2_000, struct.pack("<dd", 1.0, 2.0))
    assert len(calls) == 2  # a new schema arrived, so the next struct topic looks again
    log.close()
    assert len(calls) == 2  # and nothing changed since, so closing does not


def test_an_update_with_no_time_is_not_written_and_is_counted(tmp_path):
    path = tmp_path / "c.wpilog"
    log = CaptureLog(path)
    log.open()
    log.write("/a", "double", 1000, 1.0)
    log.write("/a", "double", None, 2.0)  # no clock to date it by: any time given to it would be made up
    log.write("/b", "double", None, 3.0)  # not even its entry is started
    log.close()
    entries, records = read_log(path)
    assert records == [("NT:/a", 1000, 1.0)] and list(entries) == ["NT:/a"]
    assert log.dropped_nonfinite_time == 2 and log.error is None
