"""The capture registry itself: ids, the capture limit, stop/discard, teardown and the one-shot watch.

Imported directly (`frc_mcp_nt4.capture.registry`) so these tests pin the registry's own contract rather than
the tool replies; the ntcore instance and the log-path factory are stand-ins, so no server is needed.
"""

import time
from types import SimpleNamespace

import pytest
from capture_helpers import retained, seed_samples

from frc_mcp_nt4.capture import Capture, CaptureSpec, EventRule, QuerySpec
from frc_mcp_nt4.capture.registry import CaptureRegistry, wait_ended
from frc_mcp_nt4.errors import NT4Error


def spec(*topics, **options):
    options.setdefault("duration_s", 30)
    return CaptureSpec(topics=list(topics), prefix=False, rate_hz=10, all_changes=True, **options)


def query(reduce, **options):
    options.setdefault("topics", None)
    return QuerySpec(reduce=reduce, fields=None, since_ms=None, until_ms=None,
                     max_samples=20, precision=4, **options)


def instance():
    """The ntcore instance stand-in `Capture.start` needs to subscribe (no traffic is expected from it)."""
    return SimpleNamespace(
        getServerTimeOffset=lambda: 0,
        isConnected=lambda: True,
        addConnectionListener=lambda *_args: 1,
        addListener=lambda *_args: 2,
        removeListener=lambda *_args: None,
        getTopic=lambda _name: SimpleNamespace(genericSubscribe=lambda _opts: object()),
    )


async def test_register_keys_a_capture_by_its_own_id_and_get_names_the_live_ids():
    reg = CaptureRegistry()
    assert not reg and reg.all() == []  # an empty registry is falsy for the session's idle check
    cap = Capture("c7", spec("/a"), None)
    reg.register(cap)
    assert reg and reg.all() == [cap] and reg.get("c7") is cap
    with pytest.raises(NT4Error, match=r"No capture 'c8'\. Active ids: \['c7'\]\."):
        reg.get("c8")
    with pytest.raises(NT4Error, match="Active ids: none"):
        CaptureRegistry().get("c1")  # the same message for a registry that never held anything


async def test_start_numbers_captures_and_the_duration_budget_ends_the_wait(tmp_path):
    reg = CaptureRegistry()
    created = []

    def log_path(cid):
        created.append(cid)
        return tmp_path / f"{cid}.wpilog"

    logged = reg.start(instance(), spec("/a", duration_s=0.15), max_captures=5, new_log_path=log_path)
    plain = reg.start(instance(), spec("/b", duration_s=30), max_captures=5, new_log_path=None)
    assert [c.id for c in reg.all()] == ["c1", "c2"]  # ids come from the registry's own counter
    assert created == ["c1"]  # the factory is asked once per logging capture, with its id
    assert logged.log_path == tmp_path / "c1.wpilog" and logged.log_open
    assert plain.log_path is None and not plain.log_open  # no factory: nothing to open
    assert reg.is_busy()
    start = time.monotonic()
    await wait_ended(logged, 5)  # the 0.15 s budget, armed by start(), must end the wait long before its 5 s
    assert not logged.active and not logged.log_open and time.monotonic() - start < 1
    assert reg.get(logged.id) is logged  # still queryable after the budget stopped it
    reg.stop(plain.id, discard=False)
    assert not reg.is_busy()  # nothing recording and no event data retained: the connection may go idle


async def test_is_busy_covers_a_stopped_capture_only_while_it_retains_event_data():
    reg = CaptureRegistry()
    plain = Capture("c1", spec("/a"), None)
    plain.stop()
    reg.register(plain)
    assert not reg.is_busy()
    ruled = Capture("c2", spec("/b", event_rules=[EventRule(topic="/b", op="changed")]), None)
    ruled.stop()
    reg.register(ruled)
    assert reg.is_busy()  # its event inbox still needs the connection


async def test_the_limit_evicts_the_oldest_stopped_capture_and_keeps_the_newest():
    reg = CaptureRegistry()
    inst = instance()
    first = reg.start(inst, spec("/a"), max_captures=2, new_log_path=None)
    first.stop()  # stopped as the duration budget stops it: its timer is still armed
    timer = reg._timers[first.id]
    second = reg.start(inst, spec("/b"), max_captures=2, new_log_path=None)
    third = reg.start(inst, spec("/c"), max_captures=2, new_log_path=None)
    assert [c.id for c in reg.all()] == [second.id, third.id]  # the oldest stopped one gave way, not the newest
    assert third.id == "c3"  # ids keep counting up across evictions
    assert first.id not in reg._timers and timer.cancelled()  # an evicted capture leaves no budget behind


async def test_the_limit_refuses_while_every_retained_capture_keeps_event_data():
    reg = CaptureRegistry()
    reg.register(Capture("c1", spec("/a", event_rules=[EventRule(topic="/a", op="changed")]), None))
    with pytest.raises(NT4Error, match="1 captures are running or retaining event data"):
        reg.start(instance(), spec("/b"), max_captures=1, new_log_path=None)
    assert [c.id for c in reg.all()] == ["c1"]  # the refusal is the only change: nothing was started or dropped


async def test_stop_keeps_a_capture_queryable_and_discard_forgets_it():
    reg = CaptureRegistry()
    cap = reg.start(instance(), spec("/a"), max_captures=3, new_log_path=None)
    seed_samples(cap, "/a", [(100.0, 5.0, True)], "double")
    timer = reg._timers[cap.id]
    assert reg.stop(cap.id, discard=False) is cap
    assert not cap.active and timer.cancelled() and not reg._timers
    assert retained(reg.get(cap.id), "/a") == [(100.0, 5.0, True)]  # the samples survive the stop for one last query
    reg.stop(cap.id, discard=True)
    assert not reg.all()
    with pytest.raises(NT4Error, match="No capture"):
        reg.get(cap.id)


async def test_close_stops_and_forgets_every_capture_of_the_connection():
    reg = CaptureRegistry()
    running = reg.start(instance(), spec("/a"), max_captures=3, new_log_path=None)
    ruled = Capture("c2", spec("/b", event_rules=[EventRule(topic="/b", op="changed")]), None)
    reg.register(ruled)
    timers = dict(reg._timers)
    reg.close()
    assert not reg and reg.all() == [] and not reg._timers
    assert not running.active and not ruled.active  # both stopped, even the one that had never been started
    assert all(t.cancelled() for t in timers.values())  # no budget fires after the connection is gone


async def test_watch_answers_once_and_frees_its_one_shot_capture():
    reg = CaptureRegistry()
    result = await reg.watch(instance(), spec("/a", duration_s=0.05), query("samples"), max_captures=3)
    assert "id" not in result and "active" not in result  # a one-shot leaves nothing running to refer to
    assert result["data"] == {} and result["sampling"]["mode"] == "all_changes"
    assert not reg and not reg._timers


async def test_watch_frees_its_capture_when_the_query_fails(monkeypatch):
    reg = CaptureRegistry()

    def fail(_cap, _spec, **_kwargs):
        raise NT4Error("bad reduce")

    monkeypatch.setattr(Capture, "execute_query", fail)
    with pytest.raises(NT4Error, match="bad reduce"):
        await reg.watch(instance(), spec("/a", duration_s=0.05), query("samples"), max_captures=3)
    assert not reg and not reg._timers  # a rejected query still frees the one-shot
