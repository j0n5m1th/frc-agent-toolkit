"""Asking a log about the same struct topic again does not decode its values again."""

from frc_mcp_wpilib.schema.structs import Registry
from frc_mcp_wpilog import queries
from frc_mcp_wpilog.queries import QuerySpec, Range

POSE = "/Fake/Drive/Pose"


def ask(log, names, reduce="summary", **spec):
    return queries.query(log, names, QuerySpec(reduce=reduce, **spec), Range())


def count_decodes(monkeypatch):
    calls = {"n": 0}
    real = Registry._decode_one

    def counted(self, *args, **kwargs):
        calls["n"] += 1
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Registry, "_decode_one", counted)
    return calls


def test_repeated_queries_decode_each_pose_once(sample, monkeypatch):
    decodes = count_decodes(monkeypatch)
    first = ask(sample, [POSE], "summary", fields=["translation.x"])
    after_first = decodes["n"]
    assert after_first >= 31  # 31 pose updates, decoded at least once each
    assert ask(sample, [POSE], "summary", fields=["translation.x"]) == first
    assert ask(sample, [POSE], "percentiles", fields=["translation.x"])["data"][POSE]["fields"]["translation.x"]["max"] == 3.0
    first_when = queries.when(sample, POSE, ">=", 2.0, "translation.x", Range(), 20)
    assert decodes["n"] == after_first  # the later questions read the cache
    assert queries.when(sample, POSE, ">=", 2.0, "translation.x", Range(), 20) == first_when


def test_a_schema_added_later_is_used_not_the_cached_decode(sample):
    first = ask(sample, [POSE], "summary", fields=["translation.x"])["data"][POSE]
    assert first["fields"]["translation.x"]["max"] == 3.0
    sample.schemas["/.schema/struct:Pose2d"] = b"double a;double b;double c"  # the log's schema changed
    sample._schema_owner += "x"  # force `activate_schemas` to load it again
    changed = ask(sample, [POSE], "summary", fields=["a"])["data"][POSE]
    assert changed["fields"]["a"] == {"min": 0.0, "max": 3.0, "mean": 1.5}
