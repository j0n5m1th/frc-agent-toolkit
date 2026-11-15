"""Opening a log: the index, name handling, type mapping, schemas, row loading and the cache."""

import pytest
from log_builder import BASE, S, build_sample_log, pose_bytes

from frc_mcp_wpilib.wpilog_writer import LogWriter
from frc_mcp_wpilog import logfile
from frc_mcp_wpilog.errors import WPILogError
from frc_mcp_wpilog.logfile import LogFile, TopicFilter, nt_type


def test_the_index_describes_the_sample_log(sample):
    assert sample.duration_s == 12.0 and sample.start_us == BASE
    assert sample.records > 200 and sample.skipped_records == 0
    current = sample.topics["/Fake/Elevator/Current"]  # the NT: prefix is dropped, as on the live server
    assert (current.type_str, current.count, current.unit) == ("double", 121, "A")
    assert sample.topics["/Fake/Debug/Raw"].type_str == "int"  # int64 is spelled the NetworkTables way
    assert sample.topics["DS:enabled"].log_type == "boolean" and sample.topics["messages"].type_str == "string"


def test_schema_entries_are_kept_and_hidden(sample):
    assert set(sample.schemas) == {"/.schema/struct:Pose2d", "/.schema/struct:Translation2d", "/.schema/struct:Rotation2d"}
    assert sample.topics["/.schema/struct:Pose2d"].hidden
    rows, hidden = sample.topic_rows(TopicFilter(prefix="/", include_hidden=False))
    assert "/.schema/struct:Pose2d" in hidden and all("/.schema/" not in r["name"] for r in rows)


def test_busiest_lists_visible_topics_by_update_count(sample):
    assert sample.busiest(2)[0] == ("/Fake/Elevator/Current", 121)


def test_nt_type_only_renames_the_integer_types():
    names = ("int64", "int64[]", "double", "string[]", "struct:Pose2d")
    assert [nt_type(t) for t in names] == ["int", "int[]", "double", "string[]", "struct:Pose2d"]


def test_topic_rows_filter_by_prefix_text_and_type_and_can_count(sample):
    rows, _ = sample.topic_rows(TopicFilter(prefix="/Fake/", name_contains="ARM"))
    assert [r["name"] for r in rows] == ["/Fake/Arm/Angle"]
    rows, _ = sample.topic_rows(TopicFilter(type_str="boolean[]"))
    assert [r["name"] for r in rows] == ["/Fake/Flags"]
    rows, _ = sample.topic_rows(TopicFilter(prefix="/Fake/Elevator", counts=True))
    assert rows[0]["props"] == {"n": 121, "unit": "A"}


def test_rows_are_decoded_in_time_order_and_cached(sample):
    rows = sample.rows(["/Fake/Arm/Angle", "/nope"])
    assert list(rows) == ["/Fake/Arm/Angle"]  # unknown names are left out
    assert rows["/Fake/Arm/Angle"][:3] == [(BASE, 0.0), (BASE + S // 2, 5.0), (BASE + S, 10.0)]
    assert sample.rows(["/Fake/Arm/Angle"])["/Fake/Arm/Angle"] is rows["/Fake/Arm/Angle"]  # the same cached list


def test_every_value_type_is_decoded(sample):
    r = sample.rows(["/Fake/Flags", "/Fake/Debug/Raw", "/Fake/Drive/Pose", "messages"])
    assert r["/Fake/Flags"][0][1] == [True, False]
    assert r["/Fake/Debug/Raw"][3] == (BASE + 3 * S, 3)
    assert r["/Fake/Drive/Pose"][0][1] == pose_bytes(0.0, 1.0, 0.0)  # struct values stay bytes until shaped
    assert r["messages"][1][1] == "WARNING: battery low"


def test_a_big_topic_is_read_in_a_second_pass_and_a_small_one_while_indexing(tmp_path, monkeypatch):
    monkeypatch.setattr(logfile, "PRELOAD_ROWS_PER_TOPIC", 50)
    log = LogFile(build_sample_log(tmp_path / "s.wpilog"))
    assert "/Fake/Arm/Angle" in log._cache and "/Fake/Elevator/Current" not in log._cache  # 25 rows vs 121
    assert len(log.rows(["/Fake/Elevator/Current"])["/Fake/Elevator/Current"]) == 121


def test_the_cache_drops_the_least_recently_used_topics(tmp_path, monkeypatch):
    monkeypatch.setattr(logfile, "PRELOAD_ROWS_PER_TOPIC", 0)
    monkeypatch.setattr(logfile, "MAX_CACHED_ROWS", 130)
    log = LogFile(build_sample_log(tmp_path / "s.wpilog"))
    log.rows(["/Fake/Elevator/Current"])
    log.rows(["/Fake/Arm/Angle"])  # 121 + 25 rows is over the cap: the older topic goes
    assert list(log._cache) == ["/Fake/Arm/Angle"]
    assert len(log.rows(["/Fake/Elevator/Current"])["/Fake/Elevator/Current"]) == 121  # and can be read again


def test_a_topic_restarted_under_a_new_entry_id_is_one_topic(tmp_path):
    w = LogWriter()
    first = w.start("NT:/x", "double", "", S)
    w.append(first, 1.0, 2 * S)
    w.finish(first, 3 * S)
    second = w.start("NT:/x", "double", "", 4 * S)
    w.append(second, 2.0, 5 * S)
    log = LogFile(w.save(tmp_path / "r.wpilog"))
    assert log.topics["/x"].count == 2
    assert log.rows(["/x"])["/x"] == [(2 * S, 1.0), (5 * S, 2.0)]


def test_a_name_that_exists_with_and_without_the_prefix_keeps_the_prefix(tmp_path):
    w = LogWriter()
    w.append(w.start("NT:/x", "double", "", S), 1.0, S)
    w.append(w.start("/x", "double", "", S), 2.0, S)
    log = LogFile(w.save(tmp_path / "c.wpilog"))
    assert set(log.topics) == {"/x", "NT:/x"}


def test_metadata_set_later_gives_the_unit(tmp_path):
    w = LogWriter()
    entry = w.start("NT:/x", "double", "", S)
    w.set_metadata(entry, '{"unit":"deg"}', S)
    w.append(entry, 1.0, S)
    assert LogFile(w.save(tmp_path / "m.wpilog")).topics["/x"].unit == "deg"


def test_bad_unit_metadata_is_ignored(tmp_path):
    w = LogWriter()
    w.append(w.start("NT:/x", "double", "not json", S), 1.0, S)
    assert LogFile(w.save(tmp_path / "m.wpilog")).topics["/x"].unit is None


def test_records_for_unknown_entries_and_bad_payloads_are_counted_not_fatal(tmp_path):
    w = LogWriter()
    good = w.start("NT:/x", "double", "", S)
    w.append(good, 1.0, S)
    w._record(99, 2 * S, b"\x00" * 8)  # an entry nobody started
    w._record(good, 3 * S, b"\x01")  # a double with a 1-byte payload
    log = LogFile(w.save(tmp_path / "bad.wpilog"))
    assert log.skipped_records >= 1
    assert log.rows(["/x"])["/x"] == [(S, 1.0)]


@pytest.mark.parametrize("preload_rows", [2000, 0])  # bad record counted while indexing, or only when the topic is read later


def test_a_bad_record_is_counted_once_however_often_its_topic_is_re_read(tmp_path, monkeypatch, preload_rows):
    from frc_mcp_wpilog import logfile

    monkeypatch.setattr(logfile, "PRELOAD_ROWS_PER_TOPIC", preload_rows)
    w = LogWriter()
    good = w.start("NT:/x", "double", "", S)
    w.append(good, 1.0, S)
    w._record(good, 2 * S, b"")  # a double with a 1-byte payload
    w.append(good, 3.0, 3 * S)
    log = LogFile(w.save(tmp_path / "bad.wpilog"))
    for _ in range(4):
        log._cache.clear()  # evicted, so the next question reads the file again
        log._cached_rows = 0
        assert log.rows(["/x"])["/x"] == [(S, 1.0), (3 * S, 3.0)]
    assert log.skipped_reasons["decode"] == 1 and log.skipped_records == 1


def test_a_file_that_is_not_a_log_is_refused(tmp_path):
    path = tmp_path / "junk.wpilog"
    path.write_bytes(b"this is not a log file at all")
    with pytest.raises(WPILogError, match="not a valid WPILOG"):
        LogFile(path)


def test_an_empty_log_opens_with_zero_duration(tmp_path):
    log = LogFile(LogWriter().save(tmp_path / "empty.wpilog"))
    assert log.duration_s == 0 and log.topics == {} and log.busiest() == []


def test_activating_a_log_loads_its_schemas_once(sample, monkeypatch):
    from frc_mcp_wpilib.schema import state

    calls = []
    monkeypatch.setattr(state, "add", lambda name, raw: calls.append(name))
    sample.activate_schemas()
    sample.activate_schemas()
    assert sorted(calls) == sorted(sample.schemas)


def test_two_threads_asking_about_logs_with_different_schemas_never_see_each_others(tmp_path):
    """Tools run in threads; the shared schema registries must not be swapped out from under a decode."""
    import threading

    from log_builder import POSE_SCHEMAS

    from frc_mcp_wpilog import queries

    first = LogFile(build_sample_log(tmp_path / "a.wpilog"))
    w = LogWriter()
    for name, text in POSE_SCHEMAS.items():
        w.schema(name, text.replace("double x;double y", "double y;double x"))  # the same type, with its fields swapped
    pose = w.start("NT:/p", "struct:Pose2d", "", S)
    w.append(pose, pose_bytes(1.0, 2.0, 0.0), S)
    second = LogFile(w.save(tmp_path / "b.wpilog"))
    errors, results = [], {}

    def ask(key, log, name):
        try:
            for _ in range(200):
                out = queries.values_at(log, [name], None, ["translation.x"], "start")
                results[key] = out[name]["v"]["translation.x"]
        except (WPILogError, KeyError) as e:  # pragma: no cover - only on a regression
            errors.append(repr(e))

    threads = [threading.Thread(target=ask, args=("a", first, "/Fake/Drive/Pose")), threading.Thread(target=ask, args=("b", second, "/p"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and results == {"a": 3.0, "b": 2.0}  # the second log's schema puts y first


def _rows(log, name):
    return [(t - BASE, v) for t, v in log.rows([name])[name]]


def test_an_entry_id_reused_after_finish_is_a_different_topic(tmp_path):
    w = LogWriter()
    first = w.start("NT:/A", "double", "", BASE)
    w.append(first, 1.0, BASE + S)
    w.finish(first, BASE + 2 * S)
    w._next_id = first  # the id is free again after Finish; a later Start may take it
    second = w.start("NT:/B", "string", "", BASE + 3 * S)
    w.append(second, "hello", BASE + 4 * S)
    log = LogFile(w.save(tmp_path / "reuse.wpilog"))
    assert (log.topics["/A"].count, log.topics["/B"].count) == (1, 1)
    assert (log.topics["/A"].type_str, log.topics["/B"].type_str) == ("double", "string")
    assert _rows(log, "/A") == [(S, 1.0)] and _rows(log, "/B") == [(4 * S, "hello")]
    reloaded = LogFile(tmp_path / "reuse.wpilog")
    reloaded._cache.clear()  # the second pass over the file (topics too big to keep while indexing) must agree
    assert _rows(reloaded, "/B") == [(4 * S, "hello")] and _rows(reloaded, "/A") == [(S, 1.0)]


def test_data_after_finish_is_skipped_not_attributed_to_the_topic(tmp_path):
    w = LogWriter()
    entry = w.start("/A", "double", "", BASE)
    w.append(entry, 1.0, BASE)
    w.finish(entry, BASE + S)
    w.append(entry, 2.0, BASE + 2 * S)
    log = LogFile(w.save(tmp_path / "late.wpilog"))
    assert log.topics["/A"].count == 1 and log.skipped_records == 1


def test_a_topic_restarted_with_another_type_decodes_each_entry_by_its_own_type(tmp_path, monkeypatch):
    w = LogWriter()
    one = w.start("/A", "double", "", BASE)
    w.append(one, 1.5, BASE)
    w.finish(one, BASE + S)
    two = w.start("/A", "int64", "", BASE + S)
    w.append(two, 7, BASE + 2 * S)
    monkeypatch.setattr(logfile, "PRELOAD_ROWS_PER_TOPIC", 0)  # force the second pass over the file
    log = LogFile(w.save(tmp_path / "retyped.wpilog"))
    assert _rows(log, "/A") == [(0, 1.5), (2 * S, 7)]


def test_releasing_a_log_drops_its_rows_and_its_loaded_schemas(sample):
    from frc_mcp_wpilib.schema import state as schema_state

    sample.activate_schemas()
    sample.rows(["/Fake/Arm/Angle"])
    assert schema_state._STATE.owner == sample._schema_owner  # the facade is the one owner
    sample.release()
    assert schema_state._STATE.owner is None and not sample._cache and sample._cached_rows == 0
    assert "struct:Pose2d" not in schema_state.fields_by_type(["struct:Pose2d"])


def test_a_whole_file_is_not_cut_short_but_one_ending_mid_record_is(sample, log_dir):
    assert not sample.cut_short
    data = (log_dir / "sample.wpilog").read_bytes()
    for lost in (1, 3, 7):  # a power loss leaves part of the last record behind
        (log_dir / "cut.wpilog").write_bytes(data[:-lost])
        cut = LogFile(log_dir / "cut.wpilog")
        assert cut.cut_short and cut.topics.keys() == sample.topics.keys()  # what came before is still readable
        assert cut.records == sample.records - 1  # exactly the damaged record is lost, no repeat of the one before it
        lost_topics = [n for n, t in sample.topics.items() if t.count != cut.topics[n].count]
        assert len(lost_topics) == 1 and sample.topics[lost_topics[0]].count - cut.topics[lost_topics[0]].count == 1


def test_an_empty_log_is_not_reported_as_cut_short(tmp_path):
    assert not LogFile(LogWriter().save(tmp_path / "empty.wpilog")).cut_short


def test_an_in_memory_log_ending_in_an_empty_value_is_not_cut_short(tmp_path):
    w = LogWriter()
    text = w.start("/msg", "string", "", 0)
    w.append(text, "hello", S)
    w.append(text, "", 2 * S)  # a zero-size payload is the last record
    path = w.save(tmp_path / "empty_tail.wpilog")
    in_memory = LogFile(path, path.read_bytes(), [path])
    assert not in_memory.cut_short and not LogFile(path).cut_short
