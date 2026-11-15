"""The profiling server's own helper layer: annotations, one size-bounded reply, its error wrapper and its prompt."""

import json

import pytest
from fastmcp.exceptions import ToolError

from frc_mcp_core.reply import HALVING_HINT
from frc_mcp_core.reply import TRUNCATION_HINT as COMMON_TRUNCATION_HINT
from frc_mcp_core.reply import reply as common_reply
from frc_mcp_jfr.errors import JfrError
from frc_mcp_jfr.prompts import find_hot_spots
from frc_mcp_jfr.support import LOCAL_ONLY, RECORD, reply, tool_errors


def test_the_annotations_separate_reading_from_recording():
    assert LOCAL_ONLY == {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
    assert RECORD["readOnlyHint"] is False and RECORD["destructiveHint"] is False  # a recording writes one file, deletes nothing


def test_a_reply_is_one_compact_json_block():
    assert json.loads(reply({"rows": [1, 2], "n": 2}).content[0].text) == {"rows": [1, 2], "n": 2}


def test_a_long_list_is_halved_until_the_reply_fits(monkeypatch):
    monkeypatch.setenv("JFR_MAX_CHARS", "600")
    out = json.loads(reply({"rows": [{"i": i, "text": "x" * 20} for i in range(200)]}).content[0].text)
    assert out["truncated"] is True and out["omitted_items"] > 0 and len(out["rows"]) < 200
    assert len(json.dumps({"rows": out["rows"], "truncated": True, "omitted_items": out["omitted_items"]})) <= 600


def test_a_reply_with_nothing_left_to_cut_says_so(monkeypatch):
    monkeypatch.setenv("JFR_MAX_CHARS", "500")
    out = json.loads(reply({"a": "x" * 2000}).content[0].text)
    assert out == {"truncated": True, "hint": "Partial result; ask for fewer rows (top=) or a narrower question."}


async def test_a_jfr_error_reaches_the_model_as_written():
    @tool_errors
    async def one() -> dict[str, int]:
        raise JfrError("exactly this wording")

    with pytest.raises(ToolError, match="exactly this wording"):
        await one()


def test_the_profiling_prompt_names_the_tools_in_order():
    text = find_hot_spots()
    assert "jfr_list(kind='jvms')" in text and "jfr_record" in text and "jfr_summary" in text and "jfr_hotspots" in text
    assert "jfr_around" in text and "jfr_memory" in text
    assert "'Work'" not in text  # no match: nothing quoted
    assert "whose command line contains 'Work'" in find_hot_spots("Work", seconds=5) and "5 seconds" in find_hot_spots("Work", 5)


# This server's `reply` is the halving policy of `frc_mcp_core.reply.reply_halving` under `JFR_MAX_CHARS`; it is pinned against the adaptive `reply` here.


def test_both_replies_agree_in_budget_and_share_the_truncation_keys(monkeypatch) -> None:
    monkeypatch.setenv("JFR_MAX_CHARS", "500")
    payload = {"rows": [{"i": i} for i in range(150)], "n": 150}
    mine = json.loads(reply(dict(payload)).content[0].text)
    theirs = json.loads(common_reply(dict(payload), max_chars=500).content[0].text)
    shared = {"truncated", "omitted_items", "hint"}  # the keys both contracts keep flat
    assert shared <= set(mine) and shared <= set(theirs)
    assert mine["truncated"] is True and theirs["truncated"] is True
    assert mine["omitted_items"] > 0 and theirs["omitted_items"] > 0
    assert mine["n"] == 150 and theirs["partial"]["n"] == 150  # the whole scalar entry survives on both sides
    assert mine["hint"] == HALVING_HINT and theirs["hint"] == COMMON_TRUNCATION_HINT
    small = {"rows": [1, 2], "n": 2}
    assert reply(dict(small)).content[0].text == common_reply(dict(small), max_chars=500).content[0].text
