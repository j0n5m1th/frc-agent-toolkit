"""wpilog_sync builds under the same lock the queries take, so a sync, a query and a close can run together."""

import threading

from log_builder import build_sample_log

from frc_mcp_wpilib.schema import state as schema_state
from frc_mcp_wpilog import sync
from frc_mcp_wpilog.errors import WPILogError
from frc_mcp_wpilog.logfile import LOCK
from frc_mcp_wpilog.store import OpenLogs

TOPIC = "/Fake/Elevator/Current"


def two_logs(tmp_path):
    opened = OpenLogs()
    for i in range(2):
        opened.open(build_sample_log(tmp_path / f"s{i}.wpilog"))
    return opened


def make(vid, parts):
    return sync.build(vid, parts, "auto", [0.0])[0]


def test_a_sync_builds_while_holding_the_global_lock(tmp_path):
    opened = two_logs(tmp_path)
    owned = []
    opened.add_view(["l1", "l2"], lambda vid, parts: (owned.append(LOCK._is_owned()), make(vid, parts))[1])
    assert owned == [True] and not opened._lock._is_owned()  # and the id-table lock was not held for it


def test_syncing_while_queries_read_gives_no_errors_and_right_answers(tmp_path):
    opened = two_logs(tmp_path)
    _, log_a = opened.open(tmp_path / "s0.wpilog")
    expected = log_a.rows([TOPIC])[TOPIC]
    errors, stop = [], threading.Event()

    def reader():
        try:
            while not stop.is_set():
                log_a.activate_schemas()
                assert log_a.rows([TOPIC])[TOPIC] == expected
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    t = threading.Thread(target=reader)
    t.start()
    try:
        for _ in range(20):
            vid, view = opened.add_view(["l1", "l2"], make)
            assert view.rows([TOPIC])[TOPIC] == expected
            opened.close(vid)
    finally:
        stop.set()
        t.join(10)
    assert errors == [] and not t.is_alive()


def test_a_log_closed_while_it_is_being_synced_refuses_the_view_and_releases_it(tmp_path):
    opened = two_logs(tmp_path)
    inside, go = threading.Event(), threading.Event()

    def slow_make(vid, parts):
        view = make(vid, parts)
        inside.set()
        assert go.wait(5)
        return view

    result = []

    def sync_it():
        try:
            opened.add_view(["l1", "l2"], slow_make)
        except WPILogError as e:
            result.append(str(e))

    syncer = threading.Thread(target=sync_it)
    syncer.start()
    assert inside.wait(5)
    closer = threading.Thread(target=lambda: opened.close("l1"))  # release() waits for the global lock the sync holds
    closer.start()
    go.set()
    syncer.join(10)
    closer.join(10)
    assert not syncer.is_alive() and not closer.is_alive()
    assert result and "was closed while s1 was being synced" in result[0]
    assert opened.ids == ["l2"] and opened._views == {}


def test_releasing_a_synced_view_takes_the_global_lock(tmp_path):
    opened = two_logs(tmp_path)
    _, view = opened.add_view(["l1", "l2"], make)
    view.activate_schemas()
    owned = []
    real = schema_state.forget
    schema_state.forget = lambda owner: (owned.append(LOCK._is_owned()), real(owner))[1]
    try:
        view.release()
    finally:
        schema_state.forget = real
    assert owned == [True]
