"""Paged topic listings (collapse, header, page fitting) and the struct field lookup behind `struct_fields`."""

from frc_mcp_wpilib.schema.protos import PROTOS
from frc_mcp_wpilib.schema.state import fields_by_type
from frc_mcp_wpilib.schema.structs import REGISTRY
from frc_mcp_wpilib.tooling.topic_pages import (
    HIDDEN_EXAMPLES_MAX,
    GroupEntry,
    TopicEntry,
    _make_page,
    collapse_topics,
    fit_page,
    list_header,
)


def topic(name, type_str="double", props=None):
    return {"name": name, "type": type_str, "props": props or {}}


# --- collapse_topics -------------------------------------------------------------
def test_collapse_without_depth_lists_every_topic_sorted():
    entries = collapse_topics([topic("/b"), topic("/a/x")], "", None)
    assert [(name, kind) for name, kind, _ in entries] == [("/a/x", "topic"), ("/b", "topic")]


def test_collapse_groups_deep_topics_and_keeps_lone_ones_listed():
    topics = [topic("/Drive/a/1"), topic("/Drive/a/2"), topic("/Drive/b/1"), topic("/top")]
    entries = collapse_topics(topics, "", 2)
    assert ("/Drive/a/", "group", 2) in entries
    assert any(kind == "topic" and name == "/Drive/b/1" for name, kind, _ in entries)  # lone: not collapsed
    assert any(name == "/top" for name, _, _ in entries)


def test_collapse_group_key_keeps_prefix():
    topics = [topic("/S/a/x"), topic("/S/a/y")]
    assert collapse_topics(topics, "/S", 1) == [("/S/a/", "group", 2)]


# --- _list_header ----------------------------------------------------------
def test_list_header_without_hidden_matches():
    assert list_header(3, 2, [], 6000) == {"total": 3, "total_items": 2}


def test_list_header_names_few_hidden_matches():
    header = list_header(3, 3, ["/a/.type"], 6000)
    assert header["hidden_matches"] == 1 and header["hidden_examples"] == ["/a/.type"]


def test_list_header_omits_examples_when_many_or_too_big():
    many = [f"/x{i}/.type" for i in range(HIDDEN_EXAMPLES_MAX + 1)]
    assert "hidden_examples" not in list_header(9, 9, many, 6000)
    big = list_header(9, 9, ["/" + "n" * 400], 500)
    assert "hidden_examples" not in big and big["hidden_examples_omitted"] == 1


# --- _make_page / _fit_page ------------------------------------------------
def test_make_page_splits_groups_topics_props_and_next_offset():
    page = [GroupEntry("/g/", "group", 4), TopicEntry("/t", "topic", topic("/t", "int", {"persistent": True}))]
    out = _make_page({"total": 9}, page, entry_count=5, offset=1, fields_of=None)
    assert out["groups"] == {"/g/": 4}
    assert out["topics"] == {"/t": "int"}
    assert out["props"] == {"/t": {"persistent": True}}
    assert out["next_offset"] == 3 and out["total"] == 9


def test_make_page_last_page_has_no_next_offset_or_props():
    page = [TopicEntry("/t", "topic", topic("/t"))]
    out = _make_page({}, page, entry_count=1, offset=0, fields_of=None)
    assert "next_offset" not in out and "props" not in out and "groups" not in out


def test_fit_page_shrinks_to_budget_and_points_to_next_offset():
    entries = [TopicEntry(f"/n{i}", "topic", topic(f"/n{i}", "x" * 50)) for i in range(20)]
    out = fit_page({}, entries, 0, 20, None, 400)
    assert 0 < len(out["topics"]) < 20
    assert out["next_offset"] == len(out["topics"])


def test_fit_page_reports_blocked_offset_when_one_item_is_too_big():
    entries = [TopicEntry("/n", "topic", topic("/n", "t" * 1000))]
    out = fit_page({}, entries, 0, 10, None, 500)
    assert out["truncated"] is True and out["blocked_offset"] == 0
    assert "next_offset" not in out


def test_fit_page_past_the_end_is_just_empty():
    out = fit_page({}, [("/n", "topic", topic("/n"))], 5, 10, None, 6000)
    assert out["topics"] == {} and "truncated" not in out


# --- fields_by_type ---------------------------------------------------
def test_struct_field_names_ignores_plain_and_unknown_types():
    assert fields_by_type(["double", "string[]"]) == {}
    assert fields_by_type(["struct:NoSuchThing", "proto:no.Such"]) == {}


def test_struct_field_names_uses_registry(monkeypatch):
    monkeypatch.setattr(REGISTRY, "field_names", lambda name: [f"{name}.a"])
    monkeypatch.setattr(PROTOS, "field_names", lambda name: ["p"])
    out = fields_by_type(["struct:Pose2d[]", "struct:Pose2d[]", "proto:pkg.Msg", "int"])
    assert out == {"proto:pkg.Msg": ["p"], "struct:Pose2d[]": ["Pose2d.a"]}
