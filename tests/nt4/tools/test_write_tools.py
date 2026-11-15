"""Writing tools against a fake session: default-applied detection, read-back and error conversion."""

import json

import pytest
from fastmcp.exceptions import ToolError

from frc_mcp_nt4.access.publishing import PublishOptions, SetResult
from frc_mcp_nt4.access.reading import Observed, ReadMiss
from frc_mcp_nt4.errors import NT4Error
from frc_mcp_nt4.tools import write_tools as tw


class Op:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Val:
    def __init__(self, value, t=1):
        self._v, self._t = value, t

    def value(self):
        return self._v

    def time(self):
        return self._t


class FakeSession:
    def __init__(self):
        self.observed = {}
        self.set_result = SetResult("/t", "int", written=1)
        self.set_calls = []
        self.expiry = None
        self.error = None
        self.unpublished = []

    def operation(self, *_a, **_k):
        return Op()

    async def get_values(self, topics, timeout_s):
        return {n: self.observed[n] for n in topics}

    def set_value(self, topic, value, type_str, options):
        if self.error:
            raise self.error
        self.set_calls.append((topic, value, type_str, options))
        return self.set_result

    def publisher_expiry(self, topic):
        return self.expiry

    def unpublish(self, topic):
        if self.error:
            raise self.error
        self.unpublished.append(topic)
        return True


@pytest.fixture
def fake(monkeypatch):
    fake = FakeSession()
    monkeypatch.setattr(tw, "session", fake)
    return fake


def body(result):
    return json.loads(result.content[0].text)


async def test_default_applied_true_only_for_weak_value(fake):
    fake.observed["/t"] = Observed("double", Val(1.0, t=0))
    assert await tw._default_applied("/t") is True
    fake.observed["/t"] = Observed("double", Val(1.0, t=12345))
    assert await tw._default_applied("/t") is False


async def test_default_applied_none_when_unreadable(fake):
    fake.observed["/t"] = ReadMiss("no value yet")
    assert await tw._default_applied("/t") is None


async def test_add_read_back_observed(fake):
    fake.observed["/t"] = Observed("double", Val(2.5))
    out = {}
    await tw._add_read_back(out, "/t", 0.1)
    assert out["read_back"]["status"] == "observed" and out["read_back"]["v"] == 2.5
    assert "not server acknowledgement" in out["read_back"]["note"]


async def test_add_read_back_unavailable(fake):
    fake.observed["/t"] = ReadMiss("not announced")
    out = {}
    await tw._add_read_back(out, "/t", 0.1)
    assert out["read_back"] == {"err": "not announced"}


async def test_set_reply_uses_publisher_expiry_after_read_back(fake):
    fake.observed["/t"] = Observed("double", Val(1.0))
    fake.expiry = 4.5
    fake.set_result = SetResult("/t", "double", written=1.0, publisher_expires_in_s=9)
    out = body(await tw.nt4_set_value("/t", 1.0, "double", read_back=True))
    assert out["publisher_expires_in_s"] == 4.5
    assert list(out) == ["topic", "type", "sent", "publisher_expires_in_s", "read_back"]


async def test_set_reply_flags_expired_publisher(fake):
    fake.observed["/t"] = Observed("double", Val(1.0))
    fake.set_result = SetResult("/t", "double", written=1.0, publisher_expires_in_s=9)
    out = body(await tw.nt4_set_value("/t", 1.0, "double", read_back=True))
    assert "publisher_expires_in_s" not in out and out["publisher_expired_or_released"] is True
    assert list(out)[-2:] == ["read_back", "publisher_expired_or_released"]


async def test_set_value_passes_arguments_through(fake):
    out = body(await tw.nt4_set_value("/t", 3, "int", publisher_ttl_s=5, timestamp_us=77))
    assert out == {"topic": "/t", "type": "int", "sent": 1}
    topic, value, type_str, options = fake.set_calls[0]
    assert (topic, value, type_str) == ("/t", 3, "int")
    assert options == PublishOptions(publisher_ttl_s=5, timestamp_us=77)


async def test_set_value_default_reports_whether_applied(fake):
    fake.set_result = SetResult("/t", "double", written=1.0, default=True)
    fake.observed["/t"] = Observed("double", Val(1.0, t=0))
    assert body(await tw.nt4_set_value("/t", 1.0, "double", default=True))["default_applied"] is True


async def test_set_value_read_back_is_optional(fake):
    fake.observed["/t"] = Observed("double", Val(1.0))
    assert "read_back" not in body(await tw.nt4_set_value("/t", 1.0, "double"))
    assert body(await tw.nt4_set_value("/t", 1.0, "double", read_back=True))["read_back"]["status"] == "observed"


async def test_write_errors_become_tool_errors(fake):
    fake.error = NT4Error("Writes are disabled.")
    with pytest.raises(ToolError, match="disabled"):
        await tw.nt4_set_value("/t", 1, "int")
    with pytest.raises(ToolError, match="disabled"):
        await tw.nt4_release_publisher("/t")


async def test_release_publisher_reports_result(fake):
    assert body(await tw.nt4_release_publisher("/t")) == {"topic": "/t", "released": True}
    assert fake.unpublished == ["/t"]
