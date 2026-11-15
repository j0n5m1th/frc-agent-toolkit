"""The partial reply keeps what fits, one entry at a time, and stays fast on huge results."""

import json
import time

from frc_mcp_core.reply import reply

HINT = "Partial result; ask for fewer topics, a shorter time window, or a smaller reduce/max_samples."
DATA = {"first": 1, "rows": {"a": "x" * 30, "b": "y" * 400, "c": "z", "d": [1] * 300, "e": 7}, "last": "tail"}


def _text(max_chars):
    return reply(DATA, ("rows",), max_chars=max_chars).content[0].text


def test_an_entry_that_is_too_big_is_skipped_and_later_small_ones_still_fit():
    assert _text(200) == ('{"truncated":true,"original_chars":1098,"omitted_items":5,"hint":"' + HINT
                          + '","partial":{"first":1,"rows":{"c":"z"}}}')
    assert _text(300) == ('{"truncated":true,"original_chars":1098,"omitted_items":2,"hint":"' + HINT
                          + '","partial":{"first":1,"rows":{"a":"' + "x" * 30 + '","c":"z","e":7},"last":"tail"}}')


def test_a_huge_bulk_section_is_trimmed_quickly_to_the_biggest_prefix_that_fits():
    big = {"rows": {f"t{i}": [i, i + 1] for i in range(50000)}, "tail": list(range(50000))}
    started = time.perf_counter()
    result = reply(big, ("rows",), max_chars=12000).content[0].text
    assert time.perf_counter() - started < 1.0
    assert len(result) <= 12000
    parsed = json.loads(result)
    kept = parsed["partial"]["rows"]
    assert list(kept) == [f"t{i}" for i in range(len(kept))]  # the first children, in order
    assert parsed["omitted_items"] == 50000 - len(kept) + 1  # plus the `tail` entry
