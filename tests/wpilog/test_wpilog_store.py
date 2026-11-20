"""The set of open logs: ids, the default log, closing and the open limit."""

import threading

import pytest
from dslog_builder import build_session
from log_builder import build_sample_log

from frc_mcp_wpilib.schema import state as schema_state
from frc_mcp_wpilog import store, sync
from frc_mcp_wpilog.errors import WPILogError
from frc_mcp_wpilog.store import OpenLogs


def paths(tmp_path, n):
    return [build_sample_log(tmp_path / f"s{i}.wpilog") for i in range(n)]


def test_ids_count_up_and_reopening_a_file_returns_the_same_id(tmp_path):
    one, two = paths(tmp_path, 2)
    opened = OpenLogs()
    first, _ = opened.open(one)
    second, _ = opened.open(two)
    assert (first, second) == ("l1", "l2")
    assert opened.open(one)[0] == "l1" and opened.ids == ["l2", "l1"]  # reopening makes it the newest


def test_the_newest_log_is_the_default_and_ids_must_exist(tmp_path):
    one, two = paths(tmp_path, 2)
    opened = OpenLogs()
    _, first = opened.open(one)
    _, second = opened.open(two)
    assert opened.get() is second and opened.get("l1") is first
    with pytest.raises(WPILogError, match="No open log 'l9'. Open logs: l1, l2"):
        opened.get("l9")


def test_asking_before_anything_is_open_says_what_to_do():
    with pytest.raises(WPILogError, match="Call wpilog_list, then wpilog_open"):
        OpenLogs().get()


def test_close_forgets_a_log_and_rejects_unknown_ids(tmp_path):
    (one,) = paths(tmp_path, 1)
    opened = OpenLogs()
    opened.open(one)
    opened.close("l1")
    assert opened.ids == []
    with pytest.raises(WPILogError, match="No open log 'l1'. Open logs: none"):
        opened.close("l1")


def test_at_most_max_open_logs_can_be_open(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "MAX_OPEN", 2)
    one, two, three = paths(tmp_path, 3)
    opened = OpenLogs()
    opened.open(one)
    opened.open(two)
    with pytest.raises(WPILogError, match="2 logs are open already"):
        opened.open(three)
    opened.close("l1")
    assert opened.open(three)[0] == "l3"


def test_the_log_servers_error_is_its_own_type_but_still_a_shared_tool_error():
    from frc_mcp_wpilib.errors import WPILibMCPError

    assert issubclass(WPILogError, WPILibMCPError)
    assert WPILogError.__module__ == "frc_mcp_wpilog.errors"  # not NT4's: the servers do not share a failure type


def test_closing_a_log_releases_it_so_nothing_keeps_its_rows_alive(tmp_path):
    (one,) = paths(tmp_path, 1)
    opened = OpenLogs()
    _, log = opened.open(one)
    log.activate_schemas()
    opened.close("l1")
    assert schema_state._STATE.owner is None  # closing released the log's schemas


def test_resetting_the_store_releases_the_logs_it_forgets(tmp_path):
    (one,) = paths(tmp_path, 1)
    opened = OpenLogs()
    _, log = opened.open(one)
    log.activate_schemas()
    log.rows(["NT:/Fake/Elevator/Current"])  # fill the row cache release() is meant to drop
    opened.reset()
    assert opened.ids == []
    # Same exit as close(): an empty id table and none of the forgotten log's shared state left behind.
    assert schema_state._STATE.owner is None and not log._cache and log._cached_rows == 0


def test_opening_a_big_log_does_not_hold_the_lock_that_other_tools_need(tmp_path, monkeypatch):
    from frc_mcp_wpilog import logfile

    (one,) = paths(tmp_path, 1)
    held = []
    real_index = logfile.LogFile._index
    monkeypatch.setattr(logfile.LogFile, "_index", lambda self: (held.append(logfile.LOCK._is_owned()), real_index(self))[1])
    OpenLogs().open(one)
    assert held == [False]  # the scan of the whole file runs without the lock that queries and listings take


def test_the_reading_helpers_take_the_same_lock_as_the_mutators(tmp_path):
    (one,) = paths(tmp_path, 1)
    opened = OpenLogs()
    opened.open(one)
    owned: list[bool] = []

    class Probe(dict):  # every table read reports whether the store lock was held for it
        def __len__(self) -> int:
            owned.append(opened._lock._is_owned())
            return super().__len__()

    opened._logs = Probe(opened._logs)
    assert opened.resolve("l1") == "l1" and opened.get("l1").path == one and opened.ids == ["l1"]
    assert list(opened.pick(["l1"])) == ["l1"]
    assert owned and all(owned)  # resolve, get, ids and pick all read the table under the lock
    assert not opened._lock._is_owned()  # and each one released it


def test_two_opens_of_the_same_file_share_one_id(tmp_path):
    import threading

    (one,) = paths(tmp_path, 1)
    opened = OpenLogs()
    ids = []
    threads = [threading.Thread(target=lambda: ids.append(opened.open(one)[0])) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert ids == ["l1"] * 4 and opened.ids == ["l1"]


def test_close_without_an_id_closes_the_newest_and_an_empty_id_is_not_that(tmp_path):
    one, two = paths(tmp_path, 2)
    opened = OpenLogs()
    opened.open(one)
    opened.open(two)
    with pytest.raises(WPILogError, match="No open log ''"):
        opened.close("")
    assert opened.close() == "l2" and opened.ids == ["l1"]
    assert opened.close() == "l1"
    with pytest.raises(WPILogError, match="Call wpilog_list, then wpilog_open"):
        opened.close()


def test_reopening_a_file_that_changed_on_disk_indexes_it_afresh(tmp_path):
    (one,) = paths(tmp_path, 1)
    opened = OpenLogs()
    _, before = opened.open(one)
    assert opened.open(one)[1] is before  # unchanged: the same open log
    build_sample_log(one)  # rewritten (a log that was still being written, or replaced)
    one.write_bytes(one.read_bytes() + b"\x00")
    _, after = opened.open(one)
    assert after is not before and not before._cache and opened.ids == ["l2"]


def _block_release(log, entered, proceed):
    """Make `log.release` wait on `proceed` (as it does behind a running query), saying so on `entered`."""
    real = log.release

    def slow():
        entered.set()
        assert proceed.wait(5)
        real()

    log.release = slow


def _in_thread(fn):
    import threading

    t = threading.Thread(target=fn)
    t.start()
    return t


def test_close_releases_outside_the_id_table_lock(tmp_path):
    import threading

    one, two = paths(tmp_path, 2)
    opened = OpenLogs()
    _, log = opened.open(one)
    opened.open(two)
    entered, proceed = threading.Event(), threading.Event()
    _block_release(log, entered, proceed)
    closer = _in_thread(lambda: opened.close("l1"))
    try:
        assert entered.wait(5)  # close is now waiting inside release()
        got = []
        reader = _in_thread(lambda: got.append(opened.ids))  # needs the id table lock
        reader.join(2)
        assert not reader.is_alive() and got == [["l2"]]  # not blocked, and the entry is already gone from the table
    finally:
        proceed.set()
        closer.join(5)


def _release_is_outside_the_lock(path, action):
    import threading

    opened = OpenLogs()
    _, log = opened.open(path)
    entered, proceed = threading.Event(), threading.Event()
    _block_release(log, entered, proceed)
    if action == "reset":
        worker = _in_thread(opened.reset)
    else:
        path.write_bytes(path.read_bytes() + b"\x00")  # changed on disk: reopening drops the old index
        worker = _in_thread(lambda: opened.open(path))
    got: list[list[str]] = []
    try:
        assert entered.wait(5)
        reader = _in_thread(lambda: got.append(opened.ids))
        reader.join(2)
        finished = not reader.is_alive()  # checked while release is still blocked
    finally:
        proceed.set()
        worker.join(5)
    assert finished and got and "l1" not in got[0]


@pytest.mark.parametrize("action", ["reset", "stale"])


def test_reset_and_a_stale_reopen_release_outside_the_id_table_lock(tmp_path, action):
    (one,) = paths(tmp_path, 1)
    _release_is_outside_the_lock(one, action)


async def test_wpilog_close_does_not_block_other_tools_while_a_query_holds_the_log(log_dir):
    import asyncio
    import json
    import threading

    from frc_mcp_wpilog import logfile
    from frc_mcp_wpilog.tools import file_tools

    await file_tools.wpilog_open("sample.wpilog")
    held, free = threading.Event(), threading.Event()

    def slow_query():  # stands in for a running query: holds the lock `release` needs
        with logfile.LOCK:
            held.set()
            free.wait(5)

    query = _in_thread(slow_query)
    assert await asyncio.to_thread(held.wait, 5)
    closing = asyncio.create_task(file_tools.wpilog_close())
    try:
        await asyncio.sleep(0.2)
        assert not closing.done()  # waiting for the query, as it must
        listing = await asyncio.wait_for(file_tools.wpilog_list(), 2)  # but the event loop is still free
        assert json.loads(listing.content[0].text)["total"] == 1
    finally:
        free.set()
        query.join(5)
    assert json.loads((await asyncio.wait_for(closing, 5)).content[0].text) == {"closed": "l1", "open": []}


def test_reopening_a_changed_log_drops_the_synced_views_made_from_it(tmp_path):
    from frc_mcp_wpilog import sync

    one, two = paths(tmp_path, 2)
    opened = OpenLogs()
    opened.open(one)
    opened.open(two)
    view_id, view = opened.add_view(["l1", "l2"], lambda vid, parts: sync.build(vid, parts, "auto", [0.0])[0])
    assert view_id == "s1" and opened._views == {"s1": ["l1", "l2"]}
    one.write_bytes(one.read_bytes() + b"\x00")  # l1 changed on disk
    new_id, _ = opened.open(one)
    assert new_id == "l3"
    assert opened._views == {} and opened.ids == ["l2", "l3"]  # the view is gone from both tables
    with pytest.raises(WPILogError, match="No open log 's1'"):
        opened.get("s1")
    assert opened.add_view(["l2", "l3"], lambda vid, parts: sync.build(vid, parts, "auto", [0.0])[0])[0] == "s2"


def two_logs(tmp_path):
    from test_wpilog_sync import robot_log
    opened = OpenLogs()
    opened.open(robot_log(tmp_path / "robot.wpilog"))
    opened.open(build_session(tmp_path))
    return opened


def build_view(view_id, parts):
    return sync.build(view_id, parts, "auto", None)[0]


def test_views_being_built_count_against_the_limit(tmp_path, monkeypatch):
    opened = two_logs(tmp_path)
    monkeypatch.setattr(store, "MAX_VIEWS", 1)
    started, release = threading.Event(), threading.Event()
    errors: list[Exception] = []

    def slow(view_id, parts):
        started.set()
        release.wait(5)
        return build_view(view_id, parts)

    worker = threading.Thread(target=lambda: opened.add_view(["l1", "l2"], slow))
    worker.start()
    started.wait(5)
    try:
        opened.add_view(["l1", "l2"], build_view)
    except WPILogError as e:
        errors.append(e)
    release.set()
    worker.join(5)
    assert errors and "synced views are open already" in str(errors[0])
    assert opened.ids == ["l1", "l2", "s1"]


def test_a_log_closed_while_syncing_leaves_no_view_behind(tmp_path):
    opened = two_logs(tmp_path)

    def close_meanwhile(view_id, parts):
        opened.close("l2")
        return build_view(view_id, parts)

    with pytest.raises(WPILogError, match="was closed while s1 was being synced"):
        opened.add_view(["l1", "l2"], close_meanwhile)
    assert opened.ids == ["l1"]


def test_a_failed_sync_gives_its_id_back(tmp_path):
    opened = two_logs(tmp_path)

    def fail(view_id, parts):
        raise WPILogError("cannot sync")

    with pytest.raises(WPILogError, match="cannot sync"):
        opened.add_view(["l1", "l2"], fail)
    assert opened.add_view(["l1", "l2"], build_view)[0] == "s1"


def test_a_reset_while_syncing_leaves_no_view_and_no_stale_count(tmp_path, monkeypatch):
    opened = two_logs(tmp_path)

    def reset_meanwhile(view_id, parts):
        opened.reset()
        return build_view(view_id, parts)

    with pytest.raises(WPILogError, match="was closed while s1 was being synced"):
        opened.add_view(["l1", "l2"], reset_meanwhile)
    assert opened.ids == [] and opened._views_building == 0  # not -1: that would let the view limit be passed later


def test_a_failed_sync_after_a_reset_does_not_leave_a_negative_count(tmp_path):
    opened = two_logs(tmp_path)

    def reset_then_fail(view_id, parts):
        opened.reset()
        raise WPILogError("cannot sync")

    with pytest.raises(WPILogError, match="cannot sync"):
        opened.add_view(["l1", "l2"], reset_then_fail)
    assert opened._views_building == 0 and opened._next_view == 1


def test_an_open_log_that_was_replaced_refuses_queries_until_it_is_opened_again(tmp_path):
    (path,) = paths(tmp_path, 1)
    opened = OpenLogs()
    log_id, _ = opened.open(path)
    assert opened.get(log_id) and not opened.take_reloaded(log_id)
    path.write_bytes(path.read_bytes()[:-3])  # replaced by a shorter file
    with pytest.raises(WPILogError, match="changed on disk since it was opened; call wpilog_open"):
        opened.get(log_id)
    with pytest.raises(WPILogError, match="changed on disk"):
        opened.pick([log_id])
    again, fresh = opened.open(path)  # opening again reads the new file
    assert again != log_id and opened.get(again) is fresh  # the old id is gone and the new file has a new one
    with pytest.raises(WPILogError, match=f"No open log '{log_id}'"):
        opened.get(log_id)


def test_opening_a_changed_file_again_says_so_once(tmp_path):
    (path,) = paths(tmp_path, 1)
    opened = OpenLogs()
    opened.open(path)
    path.write_bytes(path.read_bytes()[:-3])
    log_id, _ = opened.open(path)
    assert opened.take_reloaded(log_id) is True and opened.take_reloaded(log_id) is False


def test_a_changed_log_can_still_be_closed(tmp_path):
    (path,) = paths(tmp_path, 1)
    opened = OpenLogs()
    log_id, _ = opened.open(path)
    path.write_bytes(path.read_bytes()[:-3])
    assert opened.close(log_id) == log_id and opened.ids == []


def test_an_open_log_that_only_grew_is_still_served_and_reopening_it_says_it_was_read_again(tmp_path):
    (path,) = paths(tmp_path, 1)
    opened = OpenLogs()
    log_id, _ = opened.open(path)
    path.write_bytes(path.read_bytes() + b"\x00")  # a log still being recorded
    assert opened.get(log_id)
    again, _ = opened.open(path)
    assert opened.take_reloaded(again) is True


def test_a_synced_view_names_the_file_that_was_replaced_not_itself(tmp_path):
    from frc_mcp_wpilog import sync

    one, two = paths(tmp_path, 2)
    opened = OpenLogs()
    opened.open(one)
    opened.open(two)
    view_id, _ = opened.add_view(["l1", "l2"], lambda vid, parts: sync.build(vid, parts, "auto", [0.0])[0])
    one.write_bytes(one.read_bytes()[:-3])
    with pytest.raises(WPILogError, match=r"s0\.wpilog changed on disk.*Close s1 and sync again"):
        opened.get(view_id)
