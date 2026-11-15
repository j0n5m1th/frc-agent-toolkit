"""The struct registry caches encoded sizes and decoded values, and drops both when a schema is added."""

import struct

import pytest

from frc_mcp_wpilib.schema.structs import MAX_DECODED, Registry, SchemaError

DATA = struct.pack("<5d", 1, 2, 3, 4, 5)


def pose_registry() -> Registry:
    r = Registry()
    r.add("/.schema/struct:Vec", "double x;double y")
    r.add("/.schema/struct:Pose", "Vec p;Vec q;double theta")
    return r


def count_calls(monkeypatch, cls, name):
    calls = {"n": 0}
    real = getattr(cls, name)

    def counted(self, *args, **kwargs):
        calls["n"] += 1
        return real(self, *args, **kwargs)

    monkeypatch.setattr(cls, name, counted)
    return calls


def test_size_walks_the_fields_once_until_a_schema_is_added(monkeypatch):
    r = pose_registry()
    walks = count_calls(monkeypatch, Registry, "fields")
    assert r.size("Pose") == 40
    first = walks["n"]
    assert first > 0
    assert r.size("Pose") == 40 and r.size("Vec") == 16
    assert walks["n"] == first  # both came from the cache
    r.add("/.schema/struct:Pose", "Vec p;double theta")  # a changed schema must not keep the old size
    assert r.size("Pose") == 24


def test_a_repeated_decode_is_served_from_the_cache(monkeypatch):
    r = pose_registry()
    decodes = count_calls(monkeypatch, Registry, "_decode_one")
    first = r.decode_strict("struct:Pose", DATA)
    again = r.decode_strict("struct:Pose", DATA)
    assert first == again == {"p.x": 1.0, "p.y": 2.0, "q.x": 3.0, "q.y": 4.0, "theta": 5.0}
    assert decodes["n"] == 3  # Pose, Vec p, Vec q: once, not twice


def test_callers_cannot_change_what_the_cache_holds():
    r = Registry()
    r.add("/.schema/struct:Arr", "double a[2]")
    got = r.decode_strict("struct:Arr", struct.pack("<2d", 1, 2))
    got["a"].append(99)
    got["extra"] = 1
    assert r.decode_strict("struct:Arr", struct.pack("<2d", 1, 2)) == {"a": [1.0, 2.0]}
    many = r.decode_strict("struct:Arr[]", struct.pack("<4d", 1, 2, 3, 4))
    many[0]["a"].clear()
    assert r.decode_strict("struct:Arr[]", struct.pack("<4d", 1, 2, 3, 4)) == [{"a": [1.0, 2.0]}, {"a": [3.0, 4.0]}]


def test_adding_a_schema_invalidates_the_decoded_values(monkeypatch):
    r = pose_registry()
    assert r.decode_strict("struct:Pose", DATA)["theta"] == 5.0
    r.add("/.schema/struct:Pose", "double a;double b;double c;double d;double e")
    assert r.decode_strict("struct:Pose", DATA) == {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0, "e": 5.0}
    r.clear()
    with pytest.raises(SchemaError):
        r.decode_strict("struct:Pose", DATA)


def test_failures_are_not_cached():
    r = Registry()
    with pytest.raises(SchemaError):
        r.decode_strict("struct:Late", bytes(8))
    r.add("/.schema/struct:Late", "double x")
    assert r.decode_strict("struct:Late", bytes(8)) == {"x": 0.0}


def test_a_full_cache_starts_over_and_still_answers_correctly():
    r = Registry()
    r.add("/.schema/struct:One", "uint32 v")
    for i in range(MAX_DECODED + 5):
        assert r.decode_strict("struct:One", struct.pack("<I", i)) == {"v": i}
    assert len(r._decoded) <= MAX_DECODED
