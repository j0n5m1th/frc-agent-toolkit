import json

import benchmark_naive
import eval_output
import pytest

PREFIX = "mcp__nt4__"


class FakeValue:
    def __init__(self, raw):
        self._raw = raw

    def value(self):
        return self._raw


def tool_event(name, tool_id):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": PREFIX + name, "id": tool_id}]}}


def test_tokens_estimates_four_characters_per_token():
    assert benchmark_naive.tokens("") == 0
    assert benchmark_naive.tokens("abcdefgh") == 2
    assert benchmark_naive.tokens("abcdef") == 2  # rounds rather than truncates


def test_value_json_hex_encodes_bytes_and_passes_other_values_through():
    assert benchmark_naive.value_json(FakeValue(b"\x01\xff")) == "01ff"
    assert benchmark_naive.value_json(FakeValue(bytearray(b"\x10"))) == "10"
    assert benchmark_naive.value_json(FakeValue(3.5)) == 3.5
    assert benchmark_naive.value_json(FakeValue([1, 2])) == [1, 2]


def test_naive_dump_lists_every_topic_or_only_the_named_ones():
    class Topic:
        def __init__(self, name, type_str):
            self._name, self._type = name, type_str

        def getName(self):
            return self._name

        def getTypeString(self):
            return self._type

    class Entry:
        def __init__(self, raw):
            self._raw = raw

        def getValue(self):
            return FakeValue(self._raw)

    class Inst:
        def __init__(self):
            self.topics = {"/a": ("double", 1.5), "/b": ("raw", b"\x0a")}

        def getTopics(self):
            return [Topic(name, kind) for name, (kind, _) in self.topics.items()]

        def getEntry(self, name):
            return Entry(self.topics[name][1])

    everything = json.loads(benchmark_naive.naive_dump(Inst()))
    assert everything == [{"name": "/a", "type": "double", "value": 1.5}, {"name": "/b", "type": "raw", "value": "0a"}]
    assert [row["name"] for row in json.loads(benchmark_naive.naive_dump(Inst(), ["/b"]))] == ["/b"]


def test_parse_output_counts_each_tool_use_once():
    events = [tool_event("nt4_watch", "t1"), tool_event("nt4_watch", "t1"), tool_event("nt4_get_values", "t2"),
              {"type": "result", "result": "done"}]
    parsed, calls = eval_output.parse_output("\n".join(json.dumps(e) for e in events))
    assert parsed["result"] == "done"
    assert calls == {"nt4_watch": 1, "nt4_get_values": 1}


def test_parse_output_without_tool_events_reports_no_counts():
    parsed, calls = eval_output.parse_output(json.dumps({"type": "result", "result": "ok"}))
    assert parsed["result"] == "ok" and calls is None


def test_parse_output_requires_a_result_event():
    with pytest.raises(ValueError):
        eval_output.parse_output(json.dumps({"type": "assistant", "message": {"content": []}}))


def test_adapter_response_accepts_a_result_with_metrics():
    data = eval_output.parse_adapter_response(json.dumps(
        {"result": "ok", "usage": {"input_tokens": 3}, "num_turns": 2, "mcp_tool_calls": {"nt4_watch": 1}}))
    assert data["result"] == "ok"


@pytest.mark.parametrize("reply", [
    "[]",
    json.dumps({"usage": {}}),
    json.dumps({"result": "ok", "error": ""}),
    json.dumps({"result": "ok", "usage": [1]}),
    json.dumps({"result": "ok", "num_turns": -1}),
    json.dumps({"result": "ok", "usage": {"input_tokens": "many"}}),
    json.dumps({"result": "ok", "mcp_tool_calls": {"nt4_watch": -1}}),
    '{"result": "ok", "total_cost_usd": NaN}',
])
def test_adapter_response_rejects_malformed_replies(reply):
    with pytest.raises(ValueError):
        eval_output.parse_adapter_response(reply)
