"""One word, one description: a NT4 or wpilog tool parameter whose name is in the shared vocabulary must carry the shared type's description,
so the wording (and its tokens, paid in every session) cannot drift. A parameter that deliberately differs is listed in `EXCEPTIONS` with the reason."""

from typing import Any, get_args

import pytest

from frc_mcp_core.toolset import set_gate_visibility
from frc_mcp_nt4.spec import SPEC as NT4
from frc_mcp_wpilib.tooling import topic_params as tp
from frc_mcp_wpilog.spec import SPEC as WPILOG


def describe(annotated: Any) -> str:
    return next(m.description for m in get_args(annotated)[1:] if hasattr(m, "description"))


# parameter name -> the descriptions the shared types allow for it (several only where one name has several sizes of the same idea)
VOCABULARY: dict[str, set[str]] = {
    "reduce": {describe(tp.ReduceParam)},
    "fields": {describe(tp.Fields)},
    "field": {describe(tp.StructField)},
    "threshold": {describe(tp.Threshold)},
    "since_ms": {describe(tp.SinceMs)},
    "until_ms": {describe(tp.UntilMs)},
    "also": {describe(tp.Also)},
    "depth": {describe(tp.Depth)},
    "name_contains": {describe(tp.NameContains)},
    "struct_fields": {describe(tp.StructFields)},
    "include_hidden": {describe(tp.IncludeHidden)},
    "max_samples": {describe(tp.MaxSamples)},
    "max_topics": {describe(tp.MaxTopics)},
    "offset": {describe(tp.PageOffset)},
    "limit": {describe(tp.Limit), describe(tp.PageLimit), describe(tp.MaxRows)},
    "prefix": {describe(tp.Prefix)},
    "topics": {describe(tp.Topics)},
}

# (server, tool, parameter) -> why it keeps its own description
EXCEPTIONS: dict[tuple[str, str, str], str] = {
    ("nt4", "nt4_list_topics", "prefix"): "also takes '$' (server meta-topics), which a log has not",
    ("nt4", "nt4_capture_start", "prefix"): "a flag here (treat topics as prefixes), not a name filter",
    ("nt4", "nt4_watch", "prefix"): "a flag here (treat topics as prefixes), not a name filter",
    ("nt4", "nt4_capture_query", "topics"): "a subset of the capture (default all), not a required list",
    ("wpilog", "wpilog_query", "topics"): "names may end in * (a prefix)",
    ("wpilog", "wpilog_text", "topics"): "text topics only, default every string topic",
}


async def advertised(spec) -> dict[str, dict[str, str]]:
    mcp, _ = spec.load()
    set_gate_visibility(mcp, dict.fromkeys(spec.gates, True), core=False)
    return {t.name: {p: v.get("description", "") for p, v in t.parameters["properties"].items()} for t in await mcp.list_tools()}


@pytest.mark.parametrize(("key", "spec"), [("nt4", NT4), ("wpilog", WPILOG)])
async def test_shared_vocabulary_keeps_the_shared_description(key, spec):
    seen_exceptions = set()
    for tool, params in (await advertised(spec)).items():
        for name, description in params.items():
            if name not in VOCABULARY:
                continue
            if (key, tool, name) in EXCEPTIONS:
                seen_exceptions.add((key, tool, name))
                continue
            assert description in VOCABULARY[name], f"{tool}.{name}: {description!r} is not the shared description; use the type from topic_params"
    assert seen_exceptions == {e for e in EXCEPTIONS if e[0] == key}, "an exception no longer matches a real parameter: remove it"
