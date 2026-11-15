"""Unit tests for the stateless helpers split out of `session.py`, plus offline `NT4Session.set_value` paths."""

import asyncio
import logging
import struct
from types import SimpleNamespace
from typing import Any

import pytest
from session_helpers import attach_fake_instance

from frc_mcp_nt4.access import waiting
from frc_mcp_nt4.access.publishing import (
    MAX_TTL_S,
    PublishOptions,
    check_ttl,
    convert_value,
    local_sample_time,
    normalize_properties,
)
from frc_mcp_nt4.access.topic_listing import TopicFilter, filter_topics
from frc_mcp_nt4.access.waiting import wait_until
from frc_mcp_nt4.capture import CaptureSpec, QuerySpec
from frc_mcp_nt4.errors import NT4Error
from frc_mcp_nt4.session import NT4Session
from frc_mcp_wpilib.conditions import StructDecodePending, extract
from frc_mcp_wpilib.errors import WPILibMCPError
from frc_mcp_wpilib.schema import state
from frc_mcp_wpilib.values import DEFAULT_PRECISION


def test_check_ttl_bounds_and_value_requirement():
    check_ttl(None, None)
    check_ttl(1.5, 3)
    for bad in (0, -1, float("nan"), float("inf"), MAX_TTL_S + 1, True):
        with pytest.raises(WPILibMCPError, match="at most"):
            check_ttl(bad, 3)
    with pytest.raises(WPILibMCPError, match="requires a value"):
        check_ttl(5, None)


def test_check_properties_rejects_non_bool_spec_props():
    assert normalize_properties(None) == {}
    assert normalize_properties({"retained": True, "x": 1}) == {"retained": True, "x": 1}
    with pytest.raises(WPILibMCPError, match="must be true or false"):
        normalize_properties({"persistent": "yes"})
    with pytest.raises(WPILibMCPError, match="JSON-serializable"):
        normalize_properties({"x": object()})


def test_local_sample_time_rules():
    clock = lambda: (10_000_000, 500)  # noqa: E731
    assert local_sample_time(None, 1, False, clock) == 0
    assert local_sample_time(9_000_000, 1, False, clock) == 9_000_000 - 500
    with pytest.raises(WPILibMCPError, match="properties-only"):
        local_sample_time(5, None, False, clock)
    with pytest.raises(WPILibMCPError, match="default=true"):
        local_sample_time(5, 1, True, clock)
    with pytest.raises(WPILibMCPError, match="positive whole"):
        local_sample_time(0, 1, False, clock)
    with pytest.raises(WPILibMCPError, match="not synchronized"):
        local_sample_time(5, 1, False, lambda: None)
    with pytest.raises(WPILibMCPError, match="in the future"):
        local_sample_time(20_000_000, 1, False, clock)
    with pytest.raises(WPILibMCPError, match="before this client"):
        local_sample_time(100, 1, False, clock)


def test_convert_value_reports_one_line():
    with pytest.raises(WPILibMCPError, match="Cannot convert"):
        convert_value("double", "not a number", 0)
    assert convert_value("double", 1.5, 0).value() == 1.5


def test_filter_topics_flags_hidden_and_props():
    cands = [
        ("/a/x", "double", {"retained": True, "source": "DogLog"}),
        ("/a/.type", "string", {}),
        ("/b/y", "double", {"cached": False}),
    ]
    topics, hidden = filter_topics(cands, TopicFilter("/"))
    assert [t["name"] for t in topics] == ["/a/x", "/b/y"]
    assert topics[0]["props"] == {"retained": True}
    assert hidden == ["/a/.type"]
    topics, _ = filter_topics(cands, TopicFilter("/", flag="uncached", include_hidden=True, all_props=True))
    assert [t["name"] for t in topics] == ["/b/y"]
    topics, _ = filter_topics(cands, TopicFilter("/", "A/", "string", include_hidden=True, all_props=True))
    assert [t["name"] for t in topics] == ["/a/.type"]


class _Sub:
    def __init__(self, batches: list[list[Any]]):
        self.batches = batches

    def readQueue(self) -> list[Any]:  # noqa: N802
        return self.batches.pop(0) if self.batches else []

    def get(self) -> Any:
        return _Val(None, valid=False)


class _Val:
    def __init__(self, raw: Any, valid: bool = True):
        self.raw, self.valid = raw, valid

    def isValid(self) -> bool:  # noqa: N802
        return self.valid

    def value(self) -> Any:
        return self.raw


def test_wait_until_met_and_timeout():
    topic = SimpleNamespace(getTypeString=lambda: "double")
    result = asyncio.run(wait_until(topic, _Sub([[_Val(1.0)], [_Val(5.0)]]), ">", 3, None, 2))
    assert result[:3] == (True, "double", 5.0)
    result = asyncio.run(wait_until(topic, _Sub([[_Val(1.0)]]), ">", 3, None, 0.05))
    assert result[:3] == (False, "double", 1.0)


def test_wait_until_keeps_waiting_past_undecodable_struct(monkeypatch):
    # an undecodable struct sample is skipped (StructDecodePending), not raised: the wait times out with met=False
    topic = SimpleNamespace(getTypeString=lambda: "struct:NoSuchStruct")
    result = asyncio.run(wait_until(topic, _Sub([[_Val(b"\x01")], [_Val(b"\x02")]]), "==", 1, "x", 0.05))
    assert result[:3] == (False, "struct:NoSuchStruct", b"\x02")

    # ...and a later sample that decodes can still satisfy the condition
    def extract(type_str, raw, field):
        if raw == b"\x01":
            raise StructDecodePending("not yet")
        return 5.0
    monkeypatch.setattr(waiting, "extract", extract)
    result = asyncio.run(wait_until(topic, _Sub([[_Val(b"\x01")], [_Val(b"\x02")]]), ">", 3, "x", 2))
    assert (result.met, result.raw) == (True, b"\x02")


def test_extract_names_the_reason_a_struct_is_pending_and_other_types_stay_errors():
    try:
        with pytest.raises(StructDecodePending, match="no schema for 'struct:Pose2d'"):
            extract("struct:Pose2d", b"\0" * 8, "x")
        state.add("/.schema/struct:Pose2d", b"double x")
        with pytest.raises(StructDecodePending, match="wrong length"):
            extract("struct:Pose2d", b"\0" * 7, "x")
        assert extract("struct:Pose2d", struct.pack("<d", 2.5), "x") == 2.5
        with pytest.raises(WPILibMCPError, match="needs a decodable struct topic"):
            extract("double", b"\0" * 8, "x")
    finally:
        state.forget()


def test_wait_for_rejects_missing_value_before_waiting():
    # no sample ever arrives here: the check must run before subscribing (the fake topic has no genericSubscribe)
    s = _session(_Topic(type_str="double"))
    with pytest.raises(WPILibMCPError, match="needs a `value`"):
        asyncio.run(s.wait_for("/x", ">", None, None, 5))


class _Topic:
    def __init__(self, exists: bool = False, type_str: str = ""):
        self._exists, self._type = exists, type_str
        self.props: dict[str, Any] = {}
        self.published: list[tuple[str, dict]] = []

    def exists(self) -> bool:
        return self._exists

    def getProperty(self, key: str) -> Any:  # noqa: N802
        return self.props.get(key)

    def getTypeString(self) -> str:  # noqa: N802
        return self._type

    def setProperties(self, props: dict) -> None:  # noqa: N802
        self.props.update(props)

    def genericPublishEx(self, type_str: str, props: dict) -> Any:  # noqa: N802
        self.published.append((type_str, props))
        self._exists, self._type = True, type_str
        return SimpleNamespace(set=lambda v: None, setDefault=lambda v: None)


def _session(topic: _Topic) -> NT4Session:
    s = NT4Session()
    attach_fake_instance(s, SimpleNamespace(getTopic=lambda name: topic, getServerTimeOffset=lambda: None))
    return s


class _WatchCapture:
    id = "w1"

    def __init__(self, fail: bool = False):
        self.active = True
        self.stopped = 0
        self.fail = fail

    def stop(self):
        self.stopped += 1
        self.active = False

    def execute_query(self, spec):
        if self.fail:
            raise NT4Error("bad reduce")
        return {"id": "w1", "active": False, "data": {}}


def _watch_session(monkeypatch, cap: _WatchCapture):
    session = NT4Session()
    attach_fake_instance(session, SimpleNamespace())  # watch_once only hands it to the (faked) registry
    started, stopped = [], []

    def start(inst, spec, **kwargs):
        started.append((spec, kwargs))
        session.capture_registry.register(cap)  # registered, as the real start does
        return cap

    monkeypatch.setattr(session.capture_registry, "start", start)
    monkeypatch.setattr(session.capture_registry, "stop", lambda cid, discard: stopped.append((cid, discard)) or cap)
    return session, started, stopped


def _watch_specs(topics, duration_s):
    """The (CaptureSpec, QuerySpec) pair that session.watch_once takes, with the defaults these tests use."""
    return (CaptureSpec(topics=topics, prefix=False, rate_hz=10, all_changes=False, duration_s=duration_s),
            QuerySpec(topics=None, reduce="summary", fields=None, since_ms=None, until_ms=None, max_samples=20,
                      precision=DEFAULT_PRECISION, threshold=None, max_topics=40))


async def test_watch_records_stops_and_frees(monkeypatch):
    cap = _WatchCapture()
    session, started, stopped = _watch_session(monkeypatch, cap)
    out = await session.watch_once(*_watch_specs(["/a"], 0.05))
    assert out["data"] == {} and cap.stopped == 1 and stopped == [("w1", True)]
    spec, kwargs = started[0]
    assert spec.topics == ["/a"] and spec.duration_s == pytest.approx(1.05) and kwargs["new_log_path"] is None


async def test_watch_frees_capture_when_query_fails(monkeypatch):
    cap = _WatchCapture(fail=True)
    session, _started, stopped = _watch_session(monkeypatch, cap)
    with pytest.raises(WPILibMCPError, match="bad reduce"):
        await session.watch_once(*_watch_specs(["/a"], 0.01))
    assert stopped == [("w1", True)]


@pytest.mark.parametrize("fail", [False, True])
async def test_watch_cleanup_tolerates_capture_removed_mid_watch(monkeypatch, fail):
    """close() (connection teardown) drops the capture while the wait runs: the cleanup must not raise NT4Error over the result."""
    cap = _WatchCapture(fail=fail)
    session = NT4Session()
    attach_fake_instance(session, SimpleNamespace())

    def start(inst, spec, **kwargs):
        session.capture_registry.register(cap)  # register it as the real start does
        return cap

    monkeypatch.setattr(session.capture_registry, "start", start)
    asyncio.get_running_loop().call_later(0.01, session.capture_registry.close)
    if fail:
        with pytest.raises(WPILibMCPError, match="bad reduce"):
            await session.watch_once(*_watch_specs(["/a"], 0.05))
    else:
        assert await session.watch_once(*_watch_specs(["/a"], 0.05)) == {"data": {}}
    assert cap.stopped >= 1 and not session.capture_registry


def test_set_value_publishes_and_replies(monkeypatch):
    monkeypatch.setenv("NT4_ALLOW_WRITES", "1")
    monkeypatch.setenv("NT4_WRITE_PREFIXES", "/T")
    topic = _Topic()
    s = _session(topic)
    out = s.set_value("/T/x", 2.5, "double", PublishOptions(default=True, properties={"retained": True}))
    assert out.written == 2.5 and out.default is True and out.properties == ("retained",)
    assert topic.published == [("double", {"retained": True})]
    assert "/T/x" in s._publishers
    with pytest.raises(WPILibMCPError, match="is type 'double'"):
        s.set_value("/T/x", "a", "string")
    with pytest.raises(WPILibMCPError, match="Nothing to do"):
        s.set_value("/T/x", None, None)
    with pytest.raises(WPILibMCPError, match="at most"):
        s.set_value("/T/x", 1.0, "double", PublishOptions(publisher_ttl_s=MAX_TTL_S + 1))


def test_writes_log_success_at_info_and_refusal_at_warning(monkeypatch, caplog):
    monkeypatch.setenv("NT4_ALLOW_WRITES", "1")
    monkeypatch.setenv("NT4_WRITE_PREFIXES", "/T")
    s = _session(_Topic())
    with caplog.at_level(logging.INFO, logger="frc_mcp_nt4.access.writer"):
        s.set_value("/T/x", [1.0, 2.0], "double[]")
        with pytest.raises(WPILibMCPError, match="outside the writable prefixes"):
            s.set_value("/Other/x", 1.0, "double")
    records = [r for r in caplog.records if r.name == "frc_mcp_nt4.access.writer"]
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (logging.INFO, "write topic=/T/x type=double[] value_size=2"),
        (logging.WARNING, "refused write to /Other/x: Topic '/Other/x' is outside the writable prefixes "
                          "['/T'] (NT4_WRITE_PREFIXES)."),
    ]


def test_set_value_ttl_arms_and_releases(monkeypatch):
    monkeypatch.setenv("NT4_ALLOW_WRITES", "1")
    monkeypatch.setenv("NT4_WRITE_PREFIXES", "/T")

    async def scenario() -> None:
        topic = _Topic()
        s = _session(topic)
        topic.getProperties = lambda: {}  # type: ignore[attr-defined]
        out = s.set_value("/T/x", 1.0, "double", PublishOptions(publisher_ttl_s=0.05))
        assert out.publisher_expires_in_s is not None and 0 < out.publisher_expires_in_s <= 0.05
        assert s.publisher_expiry("/T/x") is not None
        for _ in range(100):  # poll for the timer instead of a fixed sleep
            if "/T/x" not in s._publishers:
                break
            await asyncio.sleep(0.02)
        assert "/T/x" not in s._publishers and s.publisher_expiry("/T/x") is None

    asyncio.run(scenario())


def test_publish_options_rejects_unknown_keyword():
    with pytest.raises(TypeError):
        PublishOptions(bogus=1)  # type: ignore[call-arg]
