"""Reading tools against a fake session: value shaping, widget groups and wait_for result formatting."""

import json

import pytest
from fastmcp.exceptions import ToolError

from frc_mcp_nt4.access.reading import Observed, ReadMiss
from frc_mcp_nt4.access.waiting import WaitResult
from frc_mcp_nt4.errors import NT4Error
from frc_mcp_nt4.tools import read_tools as tr


class Op:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Val:
    def __init__(self, value, t=100, server=200):
        self._v, self._t, self._s = value, t, server

    def value(self):
        return self._v

    def time(self):
        return self._t

    def server_time(self):
        return self._s


class FakeSession:
    def clock_synchronized(self):
        return True

    def __init__(self):
        self.values = {}  # name -> (type, Val) | str
        self.units = {}
        self.tree = {}  # base -> (names, truncated)
        self.wait_result = WaitResult(True, "double", 3.0, 0.123)
        self.error = None

    def operation(self, *_a, **_k):
        return Op()

    async def get_values(self, topics, timeout_s):
        if self.error:
            raise self.error
        return {n: self.values[n] for n in topics}

    def topic_unit(self, name):
        return self.units.get(name)

    def expand(self, base):
        return self.tree.get(base, ([], False))

    async def wait_for(self, name, op, target, field, timeout_s, also=()):
        if self.error:
            raise self.error
        self.last_wait = (name, op, target, field, timeout_s)
        self.last_also = also
        return self.wait_result


@pytest.fixture
def fake(monkeypatch):
    fake = FakeSession()
    monkeypatch.setattr(tr, "session", fake)
    return fake


def body(result):
    return json.loads(result.content[0].text)


async def test_get_values_formats_values_errors_and_units(fake):
    fake.values = {"/a": Observed("double", Val(1.23456789)), "/b": ReadMiss("not announced")}
    fake.units["/a"] = "m"
    out = body(await tr.nt4_get_values(["/a", "/b"]))
    assert "type" not in out["/a"] and out["/a"]["unit"] == "m"
    assert out["/a"]["v"] == pytest.approx(1.2346, abs=1e-3)
    assert out["/b"] == {"err": "not announced"}
    assert "ts_us" not in out["/a"] and "last_update" not in out["/a"]


async def test_get_values_timestamps_hide_server_time_one(fake):
    fake.values = {"/mine": Observed("int", Val(1, server=1)), "/theirs": Observed("int", Val(2, server=555))}
    out = body(await tr.nt4_get_values(["/mine", "/theirs"], timestamps=True))
    assert out["/mine"]["ts_us"] is None and out["/theirs"]["ts_us"] == 555


async def test_get_values_include_age_adds_last_update(fake):
    fake.values = {"/a": Observed("int", Val(1))}
    out = body(await tr.nt4_get_values(["/a"], include_age=True))
    assert "last_update" in out["/a"]


async def test_get_values_converts_nt4_errors(fake):
    fake.error = NT4Error("Not connected.")
    with pytest.raises(ToolError, match="Not connected"):
        await tr.nt4_get_values(["/a"])


async def test_get_values_truncates_to_the_size_cap(fake, monkeypatch):
    monkeypatch.setenv("MCP_WPILIB_MAX_CHARS", "500")
    fake.values = {f"/t{i}": Observed("string", Val("x" * 80)) for i in range(40)}
    text = (await tr.nt4_get_values(list(fake.values))).content[0].text
    assert len(text) <= 500 and json.loads(text)["truncated"] is True


# --- _read_group / nt4_get_widgets ----------------------------------------
async def test_read_group_without_children_is_an_error(fake):
    assert await tr._read_group("/none", 0, None, False) == {"err": "no topics under this path"}


async def test_read_group_builds_relative_keys_and_unavailable(fake):
    fake.tree["/W"] = (["/W/x", "/W/y", "/W"], False)
    fake.values = {"/W/x": Observed("double", Val(1.0)), "/W/y": ReadMiss("no value yet"), "/W": Observed("string", Val("base"))}
    group = await tr._read_group("/W", 0, None, False)
    assert group["v"]["x"] == 1.0 and group["v"]["_"] == "base"
    assert group["unavailable"] == {"y": {"err": "no value yet"}}


async def test_read_group_marks_truncated_and_ages(fake):
    fake.tree["/W"] = (["/W/x"], True)
    fake.values = {"/W/x": Observed("double", Val(1.0))}
    group = await tr._read_group("/W", 0, None, True)
    assert group["truncated"] is True and set(group["last_updates"]) == {"x"}


async def test_read_group_recognises_a_string_chooser(fake):
    names = ["/C/.type", "/C/options", "/C/default", "/C/active", "/C/selected"]
    fake.tree["/C"] = (names, False)
    fake.values = {
        "/C/.type": Observed("string", Val("String Chooser")), "/C/options": Observed("string[]", Val(["a", "b"])),
        "/C/default": Observed("string", Val("a")), "/C/active": Observed("string", Val("b")), "/C/selected": Observed("string", Val("b")),
    }
    group = await tr._read_group("/C", 0, None, False)
    assert group["options"] == ["a", "b"] and group["default"] == "a" and group["active"] == "b"


async def test_get_widgets_returns_group_per_base(fake):
    fake.tree["/A"] = (["/A/x"], False)
    fake.values = {"/A/x": Observed("int", Val(4))}
    out = body(await tr.nt4_get_widgets(["/A", "/missing"]))
    assert out["/A"]["v"] == {"x": 4} and out["/missing"] == {"err": "no topics under this path"}


# --- nt4_wait_for ----------------------------------------------------------
async def test_wait_for_met_includes_shaped_value(fake):
    out = body(await tr.nt4_wait_for("/a", ">", 1))
    assert out == {"met": True, "waited_s": 0.12, "v": 3.0}  # a double needs no `type`: 3.0 says it
    assert fake.last_wait == ("/a", ">", 1, None, 10.0)


async def test_wait_for_timeout_without_value_adds_note(fake):
    fake.wait_result = WaitResult(False, None, None, 10.0)
    out = body(await tr.nt4_wait_for("/nope", "exists"))
    assert out["met"] is False and "no value received" in out["note"]


async def test_wait_for_timeout_with_value_has_no_note(fake):
    fake.wait_result = WaitResult(False, "double", 0.0, 1.0)
    out = body(await tr.nt4_wait_for("/a", ">", 5))
    assert out["met"] is False and "note" not in out and out["v"] == 0.0


async def test_wait_for_converts_errors(fake):
    fake.error = NT4Error("bad op")
    with pytest.raises(ToolError, match="bad op"):
        await tr.nt4_wait_for("/a", "==", 1)


async def test_wait_for_passes_extra_conditions_on_and_reports_their_values(fake):
    from frc_mcp_wpilib.reduce.models import Condition

    mode = Condition(topic="/mode", op="==", value="auto")
    fake.wait_result = WaitResult(True, "double", 50.0, 0.5, {"/mode": ("string", "auto")})
    out = body(await tr.nt4_wait_for("/a", ">", 40, also=[mode]))
    assert out == {"met": True, "waited_s": 0.5, "v": 50.0, "also": {"/mode": "auto"}}
    assert fake.last_also == [mode]
