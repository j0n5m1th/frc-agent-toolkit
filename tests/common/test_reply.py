"""Response budget and JSON validity tests."""

import json

import pytest

from frc_mcp_core.errors import FrcMcpError
from frc_mcp_core.reply import (
    BASE_CHARS,
    CEILING_CHARS,
    HALVING_HINT,
    MAX_CHARS_ENV,
    PER_TOPIC_CHARS,
    compact_json,
    fits,
    reply,
    reply_halving,
    response_budget,
    to_jsonable,
)

CAPTURE_KEYS = ("data", "constants", "evidence")


@pytest.mark.parametrize("budget", [500, 750, 1000, 6000])
def test_escaped_responses_are_valid_and_within_budget(budget):
    data = {f"/topic/{i}": {"v": 'quotes " and backslashes \\ and unicode \u03bb' * 8} for i in range(50)}
    text = reply(data, max_chars=budget).content[0].text
    result = json.loads(text)
    assert len(text) <= budget
    assert result["truncated"] and result["omitted_items"] > 0
    assert all(value == data[key] for key, value in result["partial"].items())


def test_capture_partial_keeps_columnar_samples_together():
    data = {
        "id": "c1", "active": True, "elapsed_s": 1.0,
        "sampling": {"mode": "all_changes"},
        "data": {f"/t/{i}": {"t_ms": list(range(30)), "v": list(range(30))} for i in range(30)},
    }
    text = reply(data, CAPTURE_KEYS, max_chars=700).content[0].text
    result = json.loads(text)
    assert len(text) <= 700 and result["truncated"]
    assert result["partial"]["id"] == "c1"
    assert all(value == data["data"][key] for key, value in result["partial"]["data"].items())
    assert result["omitted_items"] > 0


def test_capture_evidence_is_bounded_in_complete_per_topic_items():
    data = {"elapsed_s": 1.0, "sampling": {"mode": "sampled"}, "data": {},
            "evidence": {f"/t/{i}": {"retained_ms": [0, 500], "dropped": i} for i in range(100)}}
    result = json.loads(reply(data, CAPTURE_KEYS, max_chars=700).content[0].text)
    assert result["truncated"] and result["omitted_items"] > 0
    assert all(value == data["evidence"][key] for key, value in result["partial"]["evidence"].items())


def test_nonfinite_values_are_standard_json():
    result = reply({"values": [float("nan"), float("inf"), float("-inf")]}).content[0].text
    parsed = json.loads(result, parse_constant=lambda value: pytest.fail(f"invalid JSON constant {value}"))
    assert parsed == {"values": ["nan", "inf", "-inf"]}


def test_without_bulk_keys_a_section_is_kept_or_dropped_whole():
    data = {"id": "c1", "data": {f"/t/{i}": {"v": list(range(30))} for i in range(30)}}
    result = json.loads(reply(data, max_chars=700).content[0].text)
    assert result["truncated"] and "data" not in result["partial"]


def test_fits_measures_compact_json():
    assert compact_json({"a": [1, 2]}) == '{"a":[1,2]}'
    assert fits({"a": [1, 2]}, 11) and not fits({"a": [1, 2]}, 10)


def test_the_budget_grows_with_the_topics_a_reply_covers_up_to_a_ceiling(monkeypatch):
    monkeypatch.delenv(MAX_CHARS_ENV, raising=False)
    assert response_budget() == BASE_CHARS
    assert response_budget(3) == BASE_CHARS + 3 * PER_TOPIC_CHARS
    assert response_budget(10_000) == CEILING_CHARS


def test_the_documented_budget_numbers_are_the_real_ones():
    # docs/ARCHITECTURE.md quotes these: change them together (see evaluations/README.md)
    assert (BASE_CHARS, PER_TOPIC_CHARS, CEILING_CHARS) == (4000, 500, 12000)


def test_reply_sizes_its_budget_from_the_topics_in_its_bulk_sections(monkeypatch):
    monkeypatch.delenv(MAX_CHARS_ENV, raising=False)
    few = {"data": {f"/t/{i}": {"v": "x" * 200} for i in range(2)}}
    many = {"data": {f"/t/{i}": {"v": "x" * 200} for i in range(60)}}
    assert "truncated" not in json.loads(reply(few, ("data",)).content[0].text)
    text = reply(many, ("data",)).content[0].text
    assert len(text) <= CEILING_CHARS and json.loads(text)["truncated"]


def test_the_environment_fixes_the_cap_and_rejects_nonsense(monkeypatch):
    monkeypatch.setenv(MAX_CHARS_ENV, "800")
    assert response_budget(30) == 800
    for bad in ("many", "10", "999999"):
        monkeypatch.setenv(MAX_CHARS_ENV, bad)
        with pytest.raises(FrcMcpError, match=MAX_CHARS_ENV):
            response_budget()


def test_to_jsonable_makes_bytes_and_non_finite_numbers_json_safe():
    assert to_jsonable({"b": bytes([1, 255]), "x": [float("nan"), (1.5, float("inf"))], "ok": 2}) == {"b": "01ff", "x": ["nan", [1.5, "inf"]], "ok": 2}
    assert compact_json({"x": float("nan")}) == '{"x":"nan"}'  # never an invalid-JSON `NaN` token


# -- the halving policy (the profiling server's) ------------------------------------------------------------------------


def test_halving_returns_a_small_reply_as_it_is():
    assert reply_halving({"rows": [1, 2], "n": 2}, 500).content[0].text == '{"rows":[1,2],"n":2}'


def test_halving_cuts_the_longest_list_until_it_fits_and_counts_what_it_cut():
    data = {"rows": [{"i": i, "text": "x" * 20} for i in range(200)], "short": [1, 2, 3], "n": 200}
    out = json.loads(reply_halving(data, 600).content[0].text)
    assert out["truncated"] is True and out["hint"] == HALVING_HINT and out["n"] == 200 and out["short"] == [1, 2, 3]
    assert 0 < len(out["rows"]) < 200 and out["omitted_items"] + len(out["rows"]) == 200
    assert len(reply_halving(data, 600).content[0].text) <= 600  # the final reply, added fields included


def test_halving_with_no_list_left_to_cut_says_so_and_takes_a_hint_of_its_own():
    out = json.loads(reply_halving({"a": "x" * 2000, "one": [1]}, 500, hint="shorter please").content[0].text)
    assert out == {"truncated": True, "hint": "shorter please"}


def test_a_lone_surrogate_in_a_reply_is_escaped_so_the_stdio_writer_can_encode_it():
    """A file name that is not valid text arrives as a lone surrogate; encoding it used to stop the whole server."""
    name = "b\udc80.wpilog"
    for out in (reply({"files": [name], name: 1}), reply({"files": [name] * 3000}, max_chars=500), reply_halving({"files": [name] * 300}, 500)):
        text = out.content[0].text
        text.encode("utf-8")  # must not raise
        assert json.loads(text)  # and is still valid JSON
    assert reply({"files": [name]}).content[0].text == r'{"files":["b\udc80.wpilog"]}'  # a plain-ASCII JSON escape on the wire


def test_halving_keeps_the_whole_reply_including_the_added_fields_within_the_cap():
    for cap in (200, 300, 500, 1000, 6000):
        data = {"rows": [{"i": i, "text": "x" * 20} for i in range(500)], "n": 500}
        text = reply_halving(data, cap).content[0].text
        assert len(text) <= cap, (cap, len(text))
        assert json.loads(text)["truncated"] is True
