"""`with_status` marks a reply that is not a full answer, and leaves a good one alone (so it costs no tokens)."""

from frc_mcp_core.reply import NO_MATCH_HINT, topic_err, with_status


def test_a_reply_with_every_result_good_is_left_alone():
    data = {"/a": {"v": 1}}
    assert with_status(data, data.values()) is data


def test_every_result_failing_is_no_match_with_the_hint_first():
    data = {"/a": topic_err("not announced")}
    out = with_status(data, data.values())
    assert list(out)[:2] == ["status", "hint"] and out["status"] == "no_match" and out["hint"] == NO_MATCH_HINT and out["/a"] == data["/a"]


def test_no_results_at_all_is_no_match_and_a_reason_is_kept():
    out = with_status({"n": 0}, (), reason="nothing to search")
    assert out == {"status": "no_match", "reason": "nothing to search", "hint": NO_MATCH_HINT, "n": 0}


def test_some_results_failing_is_partial_and_an_existing_hint_is_kept():
    data = {"/a": {"v": 1}, "/b": topic_err("gone"), "hint": "mine"}
    out = with_status(data, [data["/a"], data["/b"]])
    assert out["status"] == "partial" and out["hint"] == "mine"


def test_an_existing_hint_is_not_replaced_on_no_match():
    assert with_status({"hint": "mine"}, [topic_err("x")])["hint"] == "mine"


def test_good_answers_outside_the_results_count_as_ok():
    failed = {"/a": topic_err("gone")}
    assert with_status(failed, failed.values(), ok=1)["status"] == "partial"  # one good answer was collapsed away
    assert with_status({"x": 1}, (), ok=2) == {"x": 1}
