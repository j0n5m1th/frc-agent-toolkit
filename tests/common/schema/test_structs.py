"""Struct schema parsing/decoding edge cases (no network)."""

import struct
import time

import msgpack
import ntcore
import pytest

from frc_mcp_wpilib.schema import state as schema_state
from frc_mcp_wpilib.schema.structs import Registry, SchemaError, parse_schema
from frc_mcp_wpilib.values import MAX_REASON, MAX_TEXT, shape


def registry(**schemas: str) -> Registry:
    r = Registry()
    for name, text in schemas.items():
        r.add(f"/.schema/struct:{name}", text)
    return r


def test_fixed_arrays_chars_and_nested_arrays():
    r = registry(
        Vec2="double x;double y",
        Thing="char name[4];double w[2];Vec2 pts[2];uint8 flag",
    )
    data = b"ab\0\0" + struct.pack("<2d", 1.5, 2.5) + struct.pack("<4d", 1, 2, 3, 4) + b"\x07"
    out = r.decode("struct:Thing", data)
    assert out == {"name": "ab", "w": [1.5, 2.5], "pts[0].x": 1.0, "pts[0].y": 2.0,
                   "pts[1].x": 3.0, "pts[1].y": 4.0, "flag": 7}
    assert r.size("Thing") == 4 + 16 + 32 + 1


def test_struct_arrays_decode_to_lists():
    r = registry(Vec2="double x;double y")
    data = struct.pack("<4d", 1, 2, 3, 4)
    assert r.decode("struct:Vec2[]", data) == [{"x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}]
    assert r.decode("struct:Vec2[]", struct.pack("<2d", 1, 2)) == [{"x": 1.0, "y": 2.0}]  # one element stays a list


def test_signed_bitfields_and_enums():
    r = registry(Packed="int8 a:3;int8 b:4;bool flag:1", Mode="enum{off=0,on=1} uint8 mode:2;bool armed:1")
    # a=-2 (0b110, bits 0-2), b=5 (0b0101, bits 3-6), flag=1 (bit 7)
    assert r.size("Packed") == 1
    assert r.decode("struct:Packed", bytes([0b10101110])) == {"a": -2, "b": 5, "flag": True}
    assert r.decode("struct:Mode", bytes([0b00000101])) == {"mode": "on", "armed": True}


def test_bitfield_overflow_starts_a_new_unit():
    r = registry(Two="uint8 a:6;uint8 b:6")  # 6+6 > 8: two units
    assert r.size("Two") == 2
    assert r.decode("struct:Two", bytes([0b00101010, 0b00010101])) == {"a": 42, "b": 21}


@pytest.mark.parametrize("text", ["double", "double x y", "uint8 x:", "enum{a=} uint8 x",
                                 "enum{=1} uint8 x", "enum{bad} uint8 x", "int8 x:0",
                                 "uint8 x:9", "double x:1", "bool x:2"])
def test_unparseable_schemas_raise(text):
    with pytest.raises(SchemaError):
        parse_schema(text)
    assert registry(Bad=text).decode("struct:Bad", b"\x00") is None


def test_undecodable_inputs_return_none_not_exceptions():
    r = registry(Vec2="double x;double y", Bad="Missing m")
    assert r.decode("struct:Nope", b"\0" * 8) is None  # no schema
    assert r.decode("struct:Vec2", b"\0" * 15) is None  # wrong length
    assert r.decode("struct:Vec2", b"") is None
    assert r.decode("struct:Bad", b"\0" * 8) is None  # references an unknown struct


def test_recursive_schema_does_not_blow_up():
    r = registry(Loop="Loop again;uint8 x")
    assert r.decode("struct:Loop", b"\0") is None


def test_shape_decodes_msgpack_values():
    blob = msgpack.packb([{"id": "robot@1", "conn": "127.0.0.1:5810", "raw": b"\x01"}])
    out = shape(blob, "msgpack")
    assert out == [{"id": "robot@1", "conn": "127.0.0.1:5810", "raw": "01"}]
    # invalid msgpack falls back to hex, and the reply says why it did not decode
    assert shape(b"\xc1\xc1", "msgpack") == {"hex": "c1c1", "decode_error": "FormatError"}
    # a plain `raw` topic has no schema to fail against, so its hex stays a bare string
    assert shape(ntcore.Value.makeRaw(b"\x0a\x0b").value(), "raw") == "0a0b"


def test_shape_tags_undecodable_bytes_with_the_reason():
    """The hex fallback of a schema type carries `decode_error`; a schema-less type does not."""
    schema_state.add("/.schema/struct:Vec2", b"double x;double y")
    try:
        assert shape(struct.pack("<2d", 1, 2), "struct:Vec2") == {"x": 1.0, "y": 2.0}  # decoded, so no hex
        missing = shape(b"\x01\x02", "struct:Nope")  # the server never announced this schema
        assert missing["hex"] == "0102" and "not announced" in missing["decode_error"]
        wrong_len = shape(b"\x01\x02", "struct:Vec2")  # 2 bytes for a 16-byte struct
        assert wrong_len["hex"] == "0102" and "wrong length" in wrong_len["decode_error"]
        no_bytes = shape(b"", "struct:Vec2")
        assert no_bytes["hex"] == "" and "no bytes" in no_bytes["decode_error"]
        no_descriptor = shape(b"\x08\x01", "proto:nope.Missing")  # proto type, no descriptor either
        assert no_descriptor["hex"] == "0801" and "no descriptor" in no_descriptor["decode_error"]
        # long hex keeps the usual truncation marker, now beside the reason
        long_hex = shape(b"\xff" * 300, "struct:Nope")
        assert long_hex["truncated"] and long_hex["len"] == 300 and len(long_hex["head"]) == MAX_TEXT
        assert all(len(v["decode_error"]) <= MAX_REASON
                   for v in (missing, wrong_len, no_bytes, no_descriptor, long_hex))
        # types without a schema are unchanged: a bare hex string
        assert shape(b"\xff" * 200, "rpc") == "ff" * 200
    finally:
        schema_state.forget()


def test_field_names_match_the_keys_a_decode_produces():
    r = registry(Vec2="double x;double y", Thing="char name[4];double w[2];Vec2 pts[2];uint8 flag")
    names = r.field_names("Thing")
    assert names == ["name", "w", "pts[0].x", "pts[0].y", "pts[1].x", "pts[1].y", "flag"]
    data = b"ab\0\0" + struct.pack("<2d", 1.5, 2.5) + struct.pack("<4d", 1, 2, 3, 4) + b"\x07"
    assert list(r.decode("struct:Thing", data)) == names
    with pytest.raises(SchemaError):
        r.field_names("Missing")
    with pytest.raises(SchemaError, match="nests too deeply"):
        registry(Loop="Loop inner").field_names("Loop")


def test_field_names_of_a_huge_struct_array_is_an_error_not_a_hang():
    hostile = registry(Inner="double a;double b;double c", Outer="Inner a[100000000]",
                       Wide="Inner x[100000];uint8 y", Nest1="Inner a[1000]", Nest2="Nest1 b[1000]")
    for name in ("Outer", "Wide", "Nest2"):  # none of them may build a list (a size-only check, so this is instant)
        with pytest.raises(SchemaError, match="field names once its arrays are expanded"):
            hostile.field_names(name)
    assert registry(Ok="double a[3]").field_names("Ok") == ["a"]  # a normal array still lists


def test_decode_strict_says_why_a_struct_cannot_decode():
    r = registry(Vec2="double x;double y", Bad="Missing m", Loop="Loop again;uint8 x")
    with pytest.raises(SchemaError, match="not announced"):  # schema missing
        r.decode_strict("struct:Nope", b"\0" * 16)
    with pytest.raises(SchemaError, match="wrong length"):  # malformed bytes
        r.decode_strict("struct:Vec2", b"\0" * 15)
    with pytest.raises(SchemaError, match="no bytes"):
        r.decode_strict("struct:Vec2", b"")
    with pytest.raises(SchemaError, match="no schema for struct 'Missing'"):  # nested schema missing
        r.decode_strict("struct:Bad", b"\0" * 8)
    with pytest.raises(SchemaError, match="nests too deeply"):
        r.decode_strict("struct:Loop", b"\0")
    assert r.decode_strict("struct:Vec2", struct.pack("<2d", 1, 2)) == {"x": 1.0, "y": 2.0}


def test_decode_stays_lenient_for_the_same_inputs():
    r = registry(Vec2="double x;double y", Bad="Missing m", Loop="Loop again;uint8 x")
    assert r.decode("struct:Nope", b"\0" * 16) is None
    assert r.decode("struct:Vec2", b"\0" * 15) is None
    assert r.decode("struct:Vec2", b"") is None
    assert r.decode("struct:Bad", b"\0" * 8) is None
    assert r.decode("struct:Loop", b"\0") is None


def nested_empties(levels: int = 4, width: int = 60) -> Registry:
    """The reviewer's hostile schema: an empty struct nested `levels` deep, `width` fields at each level."""
    r = registry(E="")
    child = "E"
    for level in range(1, levels + 1):
        r.add(f"/.schema/struct:L{level}", ";".join(f"{child} f{i}" for i in range(width)))
        child = f"L{level}"
    return r


def test_nested_empty_structs_fail_fast_instead_of_multiplying_the_work():
    r = nested_empties()
    started = time.perf_counter()
    with pytest.raises(SchemaError, match="field names once its arrays are expanded"):
        r.field_names("L4")  # 60**4 units: used to take 16 s
    r.add("/.schema/struct:X", "L4 a;double x")
    with pytest.raises(SchemaError, match="would decode to"):
        r.decode_strict("struct:X", b"\0" * 8)  # used to take 10 s per value
    assert time.perf_counter() - started < 2
    assert r.field_names("L2") == []  # 3,600 empty units is under the limit, and an empty struct lists no names


def test_a_struct_nested_many_times_is_counted_once_per_struct(monkeypatch):
    r = nested_empties(levels=3, width=20)  # 20**3 = 8,000 units: allowed
    assert r.field_names("L3") == []
    monkeypatch.setattr(Registry, "fields", lambda self, name: pytest.fail("the counts were not remembered"))
    assert r._shape("L3").names == 8000
    monkeypatch.undo()
    r.add("/.schema/struct:E", "double v")  # a schema change forgets the counts and they follow the new schema
    assert r._shape("L3").names == 8000 and len(r.field_names("L3")) == 8000


def test_an_array_of_a_wide_struct_is_refused_by_the_work_it_would_do():
    r = registry(Wide=";".join(f"double f{i}" for i in range(1000)))
    assert len(r.decode_strict("struct:Wide[]", b"\0" * 8000 * 30)) == 30  # 30,000 values: fine
    with pytest.raises(SchemaError, match="would decode to"):
        r.decode_strict("struct:Wide[]", b"\0" * 8000 * 400)  # 400,000 values
