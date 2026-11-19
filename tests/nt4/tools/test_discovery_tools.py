"""Discovery tools: nt4_list_topics wiring and inspect target validation."""

import pytest
from fastmcp.exceptions import ToolError

from frc_mcp_nt4.errors import NT4Error
from frc_mcp_nt4.tools import discovery_tools as td


def topic(name, type_str="double", props=None):
    return {"name": name, "type": type_str, "props": props or {}}


class Op:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    identity = "mcp@host"

    def __init__(self, topics=()):
        self.topics = list(topics)
        self.rows = {}

    def operation(self, *_args, **_kwargs):
        return Op()

    async def announce_meta(self, _prefix):
        return None

    def list_topics(self, f):
        return [t for t in self.topics if t["name"].startswith(f.prefix)], []

    async def inspect_meta(self, meta):
        if meta not in self.rows:
            raise NT4Error(f"no meta {meta}")
        return self.rows[meta]


@pytest.fixture
def fake(monkeypatch):
    fake = FakeSession()
    monkeypatch.setattr(td, "session", fake)
    return fake


# --- client labels ---------------------------------------------------------
def test_our_client_name_needs_numeric_suffix(fake):
    assert td._is_our_client_name("mcp@host@3")
    assert not td._is_our_client_name("mcp@host@x")
    assert not td._is_our_client_name("other@3")
    fake.identity = None
    assert not td._is_our_client_name("")


def test_label_clients_marks_server_and_self(fake):
    row = td._label_clients({"client": "", "id": "mcp@host@2", "x": 1})
    assert row == {"client": "(server)", "id": "(self)", "x": 1}
    assert td._label_clients({"client": "dashboard@1"}) == {"client": "dashboard@1"}


# --- _inspect_targets ------------------------------------------------------
def test_inspect_targets_accepts_string_and_dedupes(fake):
    assert td._inspect_targets(td._as_names("/a"), "topic") == (["/a"], 0)
    assert td._inspect_targets(td._as_names(["/a", "/b", "/a"]), "topic") == (["/a", "/b"], 0)


def test_inspect_targets_expands_prefix_and_caps(fake):
    fake.topics = [topic(f"/p/{i}") for i in range(td.MAX_INSPECT_TARGETS + 3)]
    names, omitted = td._inspect_targets(td._as_names("/p/*"), "topic")
    assert len(names) == td.MAX_INSPECT_TARGETS and omitted == 3


@pytest.mark.parametrize("target, kind, message", [
    ([""], "topic", "non-empty"),
    ([5], "topic", "non-empty"),
    (["/a*"], "client", "trailing"),
    (["/nothing*"], "topic", "No topics match"),
    (None, "topic", "No topics match"),
])
def test_inspect_targets_errors(fake, target, kind, message):
    with pytest.raises(NT4Error, match=message):
        td._inspect_targets(td._as_names(target), kind)


def test_as_names_gives_the_one_form_the_module_uses():
    assert td._as_names("/a") == ["/a"]
    assert td._as_names(["/a", "/b"]) == ["/a", "/b"]
    assert td._as_names(None) == []


# --- tools through the fake session ----------------------------------------
async def test_inspect_requires_target_for_topic_kinds(fake):
    with pytest.raises(ToolError, match="required"):
        await td.nt4_inspect("subscribers")


async def test_inspect_rejects_target_list_for_targetless_what(fake):
    with pytest.raises(ToolError, match="not used"):
        await td.nt4_inspect("clients", ["/a"])


async def test_inspect_rejects_single_target_for_targetless_what(fake):
    with pytest.raises(ToolError, match="not used"):
        await td.nt4_inspect("server_subs", "/a")


async def test_inspect_single_labels_rows_and_limits(fake):
    fake.rows["$clients"] = [{"client": ""}, {"client": "dash@1"}]
    text = (await td.nt4_inspect("clients", max_rows_per_target=1)).content[0].text
    assert '"n":2' in text and "(server)" in text
    assert "dash@1" not in text  # limited to one row


async def test_inspect_many_reports_per_target_errors(fake):
    fake.rows["$sub$/a"] = [{"client": "x@1"}]
    text = (await td.nt4_inspect("subscribers", ["/a", "/b"])).content[0].text
    assert '"/a":{"n":1' in text and '"/b":{"err"' in text


async def test_list_topics_pages_through_fake_session(fake):
    fake.topics = [topic(f"/a/{i}") for i in range(5)]
    text = (await td.nt4_list_topics(prefix="/a", limit=2)).content[0].text
    assert '"total":5' in text and '"next_offset":2' in text


async def test_inspect_with_nothing_asked_lists_the_clients(fake):
    fake.rows["$clients"] = [{"client": ""}, {"client": "dash@1"}]
    assert '"n":2' in (await td.nt4_inspect()).content[0].text


async def test_inspect_with_a_target_but_no_kind_names_the_choices(fake):
    with pytest.raises(ToolError, match="what=subscribers or publishers"):
        await td.nt4_inspect(target="/a")
