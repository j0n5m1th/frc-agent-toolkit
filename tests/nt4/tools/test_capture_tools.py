"""Capture tool wiring against fake session/capture objects: no ntcore server, no shared ports."""

import asyncio
import json
import time

import pytest
from fastmcp.exceptions import ToolError

from frc_mcp_nt4.capture import wait_ended
from frc_mcp_nt4.errors import NT4Error
from frc_mcp_nt4.tools import capture_tools as tcap
from frc_mcp_nt4.tools import capture_watch_tools as twatch


class Op:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeCapture:
    id = "cap1"
    log_path = None

    def __init__(self, result=None, active=False):
        self.active = active
        self.stopped = 0
        self.result = result if result is not None else {"id": "cap1", "active": False, "data": {}}
        self.queries = []

    def stop(self):
        self.stopped += 1
        self.active = False

    def info(self):
        return {"id": self.id, "active": self.active}

    def execute_query(self, spec):
        self.queries.append((spec, spec.rel_trigger))
        return dict(self.result)


class FakeSession:
    def __init__(self, cap):
        self.cap = cap
        self.started = []
        self.stopped = []
        self.touched = 0
        self.error = None
        self.watched = []

    def operation(self, *_a, **_k):
        self.note_activity()  # as the real seam does: operation() re-arms the idle timer itself
        return Op()

    def note_activity(self):
        self.touched += 1

    def capture_start(self, spec, *, log):
        if self.error:
            raise self.error
        self.started.append((spec, log))
        return self.cap

    async def capture_wait(self, cid, timeout_s):
        cap = self.get_capture(cid)
        await wait_ended(cap, timeout_s)
        return cap

    async def watch_once(self, spec, query):
        self.watched.append((spec, query))
        return dict(self.cap.result)

    def get_capture(self, cid):
        if cid != self.cap.id:
            raise NT4Error(f"Unknown capture {cid}.")
        return self.cap

    def capture_stop(self, cid, discard):
        self.stopped.append((cid, discard))
        return self.cap


@pytest.fixture
def cap():
    return FakeCapture()


@pytest.fixture
def fake(monkeypatch, cap):
    fake = FakeSession(cap)
    monkeypatch.setattr(tcap, "session", fake)
    monkeypatch.setattr(twatch, "session", fake)  # the same object in the watch module
    return fake


def body(result):
    return json.loads(result.content[0].text)


# --- wait_ended (capture/registry.py) ----------------------------------------
async def test_wait_ended_returns_at_once_for_finished_capture():
    start = time.monotonic()
    await wait_ended(FakeCapture(active=False), 5)
    assert time.monotonic() - start < 0.5


async def test_wait_ended_gives_up_at_deadline():
    cap = FakeCapture(active=True)
    start = time.monotonic()
    await wait_ended(cap, 0.12)
    assert 0.1 <= time.monotonic() - start < 1 and cap.active


async def test_wait_ended_notices_capture_ending():
    cap = FakeCapture(active=True)
    asyncio.get_running_loop().call_later(0.1, cap.stop)
    start = time.monotonic()
    await wait_ended(cap, 5)
    assert not cap.active and time.monotonic() - start < 2


# --- nt4_capture_start -----------------------------------------------------
async def test_capture_start_returns_id_only_by_default(fake):
    assert body(await tcap.nt4_capture_start(["/a"])) == {"id": "cap1"}
    spec, log = fake.started[0]
    assert spec.topics == ["/a"] and log is False
    assert (spec.prefix, spec.rate_hz, spec.duration_s, spec.all_changes, spec.trigger, spec.event_rules) == (
        False, 10, 60, False, None, None)


async def test_capture_start_reports_trigger_log_and_rules(fake, cap):
    cap.log_path = "/tmp/x.wpilog"
    out = body(await tcap.nt4_capture_start(
        ["/a"], trigger={"topic": "/a", "op": "==", "value": True},
        event_rules=[{"id": "r", "topic": "/a", "kind": "rise"}]))
    assert out["trigger"] == "armed" and out["log_file"].endswith("x.wpilog") and out["event_rules"] == 1


async def test_capture_start_converts_errors(fake):
    fake.error = NT4Error("Too many captures.")
    with pytest.raises(ToolError, match="Too many"):
        await tcap.nt4_capture_start(["/a"])


# --- nt4_capture_query -----------------------------------------------------
async def test_query_unknown_id_is_tool_error(fake):
    with pytest.raises(ToolError, match="Unknown capture"):
        await tcap.nt4_capture_query("nope")


async def test_query_reads_once_and_leaves_the_capture_running(fake, cap):
    out = body(await tcap.nt4_capture_query("cap1", reduce="latest"))
    assert out["id"] == "cap1" and len(cap.queries) == 1 and cap.stopped == 0
    assert fake.touched == 1 and fake.stopped == []


# --- nt4_capture_stop ------------------------------------------------------
async def test_stop_keeps_the_capture_by_default(fake, cap):
    out = body(await twatch.nt4_capture_stop("cap1"))
    assert out == {"id": "cap1", "active": False, "discarded": False}
    assert fake.stopped == [("cap1", False)]


async def test_stop_reports_where_the_log_was_written(fake, cap):
    cap.log_path = "/tmp/x.wpilog"
    assert body(await twatch.nt4_capture_stop("cap1"))["log_file"] == "/tmp/x.wpilog"


async def test_stop_discard_frees_the_capture(fake, cap):
    out = body(await twatch.nt4_capture_stop("cap1", discard=True))
    assert out["discarded"] is True and fake.stopped == [("cap1", True)]


async def test_stop_unknown_id_is_tool_error(fake):
    fake.capture_stop = lambda *_a, **_k: (_ for _ in ()).throw(NT4Error("Unknown capture nope."))
    with pytest.raises(ToolError, match="Unknown capture"):
        await twatch.nt4_capture_stop("nope")


async def test_query_forwards_options_to_capture(fake, cap):
    await tcap.nt4_capture_query("cap1", topics=["/a"], reduce="edges", threshold=0.5, max_samples=5,
                                 max_topics=3, include_evidence=True, rel_trigger=True)
    spec, rel_trigger = cap.queries[0]
    assert spec.topics == ["/a"] and spec.reduce == "edges" and spec.max_samples == 5
    assert spec.threshold == 0.5 and spec.max_topics == 3
    assert spec.include_evidence is True and rel_trigger is True


async def test_query_waits_for_capture_end(fake, cap):
    cap.active = True
    start = time.monotonic()
    await tcap.nt4_capture_query("cap1", wait_s=0.15)
    assert time.monotonic() - start >= 0.1


async def test_query_reply_respects_the_size_cap(fake, cap, monkeypatch):
    monkeypatch.setenv("MCP_WPILIB_MAX_CHARS", "500")
    cap.result = {"id": "cap1", "data": {f"/t{i}": {"v": "x" * 60} for i in range(30)}}
    text = (await tcap.nt4_capture_query("cap1")).content[0].text
    assert len(text) <= 500


# --- nt4_watch -------------------------------------------------------------
async def test_watch_hands_arguments_to_session_and_passes_result_through(fake, cap):
    cap.result = {"data": {}}  # the registry already drops the id and active keys of a one-shot
    out = body(await twatch.nt4_watch(["/a"], duration_s=0.05))
    assert out == {"data": {}}
    spec, query = fake.watched[0]
    assert spec.topics == ["/a"] and spec.duration_s == 0.05 and query.reduce == "summary"
    assert query.topics is None and query.since_ms is None and query.until_ms is None


async def test_watch_reply_respects_the_size_cap(fake, cap, monkeypatch):
    monkeypatch.setenv("MCP_WPILIB_MAX_CHARS", "500")
    cap.result = {"id": "cap1", "active": False, "data": {f"/t{i}": {"v": "x" * 60} for i in range(30)}}
    text = (await twatch.nt4_watch(["/a"], duration_s=0.01)).content[0].text
    assert len(text) <= 500 and json.loads(text)["truncated"]


async def test_watch_reports_session_errors(fake, cap):
    async def boom(*_a, **_k):
        raise NT4Error("bad reduce")

    fake.watch_once = boom
    with pytest.raises(ToolError, match="bad reduce"):
        await twatch.nt4_watch(["/a"], duration_s=0.01)
