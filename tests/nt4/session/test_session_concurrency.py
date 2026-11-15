"""Deterministic connection lifecycle race tests."""

import asyncio
import importlib
import time
from types import SimpleNamespace

import ntcore
import pytest
from capture_helpers import add_handle, set_instance
from session_helpers import attach_fake_instance, fire_idle

from frc_mcp_nt4.capture import EventRule

session_module = importlib.import_module("frc_mcp_nt4.session")


async def test_concurrent_announce_meta_callers_each_wait_out_one_grace_period(monkeypatch):
    subscribers = []

    def multi_subscriber(*args):
        subscribers.append(args)
        return object()

    backend = SimpleNamespace(MultiSubscriber=multi_subscriber, PubSubOptions=lambda **_kwargs: None)
    monkeypatch.setattr(session_module, "ntcore", backend)
    session = session_module.NT4Session()
    attach_fake_instance(session, object())  # announce_meta only needs the instance for the subscriber
    grace = 0.2

    async def call() -> float:
        start = time.monotonic()
        await session.announce_meta("$SmartDashboard", grace_s=grace)
        return time.monotonic() - start

    elapsed = await asyncio.gather(call(), call())
    # the 0.02 s slack absorbs the event loop's timer granularity (coarse on Windows); the bug returned at ~0 s
    assert all(e >= grace - 0.02 for e in elapsed), elapsed
    assert len(subscribers) == 1


@pytest.fixture
def delayed_backend(monkeypatch):
    ready = asyncio.Event()
    created = []

    class Instance:
        def __init__(self):
            self.destroyed = False
            self.removed = []

        def startClient4(self, _identity):
            pass

        def setServer(self, _host, _port):
            pass

        def addListener(self, *_args):
            return 1

        def removeListener(self, handle):
            self.removed.append(handle)

        def isConnected(self):
            if self.destroyed:
                raise RuntimeError("connection instance was destroyed while in use")
            return ready.is_set()

    def create():
        instance = Instance()
        created.append(instance)
        return instance

    backend = SimpleNamespace(
        NetworkTableInstance=SimpleNamespace(create=create, destroy=lambda inst: setattr(inst, "destroyed", True)),
        MultiSubscriber=lambda *_args: object(),
        PubSubOptions=lambda **_kwargs: None,
        EventFlags=ntcore.EventFlags,
    )
    monkeypatch.setattr(session_module, "ntcore", backend)

    async def skip_quiet_wait(_instance):
        pass

    monkeypatch.setattr(session_module.NT4Session, "_wait_until_quiet", staticmethod(skip_quiet_wait))

    async def no_name_lookup(_host, _port):
        pass  # these tests are about the instance's life; looking up "robot" in DNS can take seconds on some networks

    monkeypatch.setattr(session_module, "check_connection", no_name_lookup)
    return ready, created


async def test_concurrent_connects_do_not_destroy_an_inflight_connection(delayed_backend):
    ready, created = delayed_backend
    session = session_module.NT4Session()
    first = asyncio.create_task(session.connect("robot", 5810, "test", 2))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(session.connect("robot", 5810, "test", 2))
    await asyncio.sleep(0.01)
    ready.set()
    await asyncio.gather(first, second)
    assert len(created) == 1
    assert session.connected and not created[0].destroyed
    session.disconnect()


async def test_lazy_operations_wait_for_one_inflight_connection(delayed_backend, monkeypatch):
    ready, created = delayed_backend
    monkeypatch.setenv("NT4_HOST", "robot")
    session = session_module.NT4Session()

    async def read():
        async with session.operation():
            return session.connected

    tasks = [asyncio.create_task(read()) for _ in range(3)]
    await asyncio.sleep(0.1)
    ready.set()
    assert await asyncio.gather(*tasks) == [True, True, True]
    assert len(created) == 1
    session.disconnect()


async def test_cancelled_and_timed_out_connects_release_the_instance(delayed_backend):
    ready, created = delayed_backend
    session = session_module.NT4Session()
    task = asyncio.create_task(session.connect("robot", 5810, "test", 2))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert created[0].destroyed and session._inst is None
    with pytest.raises(session_module.NT4Error, match="No NT4 server"):
        await session.connect("robot", 5810, "test", 0.05)
    assert created[1].destroyed and session._inst is None
    ready.set()
    await session.connect("robot", 5810, "test", 1)
    assert session.connected and len(created) == 3
    session.disconnect()


async def test_failed_connection_setup_releases_the_instance(delayed_backend, monkeypatch):
    _, created = delayed_backend
    session = session_module.NT4Session()

    def fail(*_args):
        raise RuntimeError("subscriber setup failed")

    monkeypatch.setattr(session_module.ntcore, "MultiSubscriber", fail)
    with pytest.raises(RuntimeError, match="subscriber setup failed"):
        await session.connect("robot", 5810, "test", 1)
    assert created[0].destroyed and session._inst is None


async def test_failed_capture_setup_cleans_up_registered_listeners(delayed_backend, monkeypatch):
    ready, created = delayed_backend
    ready.set()
    session = session_module.NT4Session()
    await session.connect("robot", 5810, "test", 1)

    def fail(cap, inst, _loop):
        set_instance(cap, inst)
        add_handle(cap, 42)
        raise RuntimeError("capture setup failed")

    monkeypatch.setattr(session_module.Capture, "start", fail)
    with pytest.raises(RuntimeError, match="capture setup failed"):
        session.capture_start(session_module.CaptureSpec(topics=["/topic"], prefix=False, rate_hz=10, duration_s=10, all_changes=False), log=False)
    assert created[0].removed == [42] and not session.capture_registry._captures
    session.disconnect()


async def test_stopped_event_capture_is_not_implicitly_evicted(delayed_backend, monkeypatch):
    ready, _ = delayed_backend
    ready.set()
    session = session_module.NT4Session()
    await session.connect("robot", 5810, "test", 1)
    monkeypatch.setattr(session_module.Capture, "start", lambda self, inst, loop=None: None)
    cap = session.capture_start(session_module.CaptureSpec(
        topics=["/a"], prefix=False, rate_hz=10, duration_s=10, all_changes=True,
        event_rules=[EventRule(topic="/a", op="changed")]), log=False)
    cap.stop()
    monkeypatch.setattr(session_module, "MAX_CAPTURES", 1)
    with pytest.raises(session_module.NT4Error, match="retaining event data"):
        session.capture_start(session_module.CaptureSpec(topics=["/b"], prefix=False, rate_hz=10, duration_s=10, all_changes=False), log=False)
    assert session.get_capture("c1") is cap
    session.disconnect()


async def test_capture_duration_timer_is_cancelled_when_stopped_discarded_or_disconnected(delayed_backend, monkeypatch):
    ready, _ = delayed_backend
    ready.set()
    monkeypatch.setattr(session_module.Capture, "start", lambda self, inst, loop=None: None)
    session = session_module.NT4Session()
    await session.connect("robot", 5810, "test", 1)

    def start():
        return session.capture_start(session_module.CaptureSpec(topics=["/a"], prefix=False, rate_hz=10, duration_s=30, all_changes=False), log=False)

    discarded = start()
    timer = session.capture_registry._timers[discarded.id]
    session.capture_stop(discarded.id, discard=True)
    assert timer.cancelled() and discarded.id not in session.capture_registry._timers

    stopped = start()
    timer = session.capture_registry._timers[stopped.id]
    session.capture_stop(stopped.id, discard=False)
    assert timer.cancelled()

    running = start()
    timer = session.capture_registry._timers[running.id]
    session.disconnect()
    assert timer.cancelled() and not session.capture_registry._timers


async def test_different_host_connections_are_serialized(delayed_backend):
    ready, created = delayed_backend
    session = session_module.NT4Session()
    first = asyncio.create_task(session.connect("robot", 5810, "test", 1))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(session.connect("other", 5810, "test", 1))
    ready.set()
    await asyncio.gather(first, second)
    assert len(created) == 2 and created[0].destroyed
    assert session.host == "other" and session.connected and not created[1].destroyed
    session.disconnect()


async def test_active_operations_prevent_destructive_connection_changes(delayed_backend):
    ready, created = delayed_backend
    ready.set()
    session = session_module.NT4Session()
    await session.connect("robot", 5810, "test", 1)
    async with session.operation():
        with pytest.raises(session_module.NT4Error, match="Cannot disconnect"):
            await session.disconnect_safely()
        with pytest.raises(session_module.NT4Error, match="Cannot reconnect"):
            await session.connect("other", 5810, "test", 1)
        fire_idle(session)
        assert session.connected and not created[0].destroyed
    await session.disconnect_safely()
    assert created[0].destroyed


async def test_shutdown_waits_for_active_operation(delayed_backend):
    ready, created = delayed_backend
    ready.set()
    session = session_module.NT4Session()
    await session.connect("robot", 5810, "test", 1)
    async with session.operation():
        shutdown = asyncio.create_task(session.disconnect_safely(wait=True))
        await asyncio.sleep(0)
        assert not shutdown.done() and not created[0].destroyed
    await shutdown
    assert created[0].destroyed


async def test_cancelled_operation_releases_its_connection_lease(delayed_backend):
    ready, created = delayed_backend
    ready.set()
    session = session_module.NT4Session()
    await session.connect("robot", 5810, "test", 1)
    entered = asyncio.Event()

    async def read():
        async with session.operation():
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(read())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await session.disconnect_safely()
    assert created[0].destroyed and session.active_operations == 0
