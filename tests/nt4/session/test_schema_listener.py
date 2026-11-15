"""`on_schema` routing of `/.schema/` topic values into the two registries (no network)."""

from types import SimpleNamespace

from frc_mcp_nt4.schema_listener import on_schema
from frc_mcp_wpilib.schema import state
from frc_mcp_wpilib.schema.protos import PROTOS
from frc_mcp_wpilib.schema.structs import REGISTRY


def event(name, raw):
    """Minimal ntcore event: only `data.value.value()` and `data.topic.getName()` are read."""
    return SimpleNamespace(data=SimpleNamespace(
        topic=SimpleNamespace(getName=lambda: name),
        value=SimpleNamespace(value=lambda: raw),
    ))


def recorders(monkeypatch):
    """Replace the process-wide registries' `add` with recorders so global state stays clean."""
    proto_calls, struct_calls = [], []
    monkeypatch.setattr(PROTOS, "add", lambda *args: proto_calls.append(args))
    monkeypatch.setattr(REGISTRY, "add", lambda *args: struct_calls.append(args))
    return proto_calls, struct_calls


def test_non_bytes_value_returns_without_touching_either_registry(monkeypatch):
    proto_calls, struct_calls = recorders(monkeypatch)
    for raw in ("double x;double y", 1.5, True, None, [1, 2]):
        assert on_schema(event("/.schema/struct:Vec2", raw)) is None
    assert proto_calls == []
    assert struct_calls == []


def test_proto_topic_goes_to_protos_as_bytes(monkeypatch):
    proto_calls, struct_calls = recorders(monkeypatch)
    on_schema(event("/.schema/proto:geometry2d.proto", b"\x0a\x0egeometry2d.proto"))
    assert proto_calls == [("/.schema/proto:geometry2d.proto", b"\x0a\x0egeometry2d.proto")]
    assert struct_calls == []


def test_other_schema_topics_go_to_the_struct_registry_as_text(monkeypatch):
    proto_calls, struct_calls = recorders(monkeypatch)
    on_schema(event("/.schema/struct:Pose2d", b"double x;double y"))
    assert struct_calls == [("/.schema/struct:Pose2d", "double x;double y")]
    assert proto_calls == []


def test_undecodable_bytes_use_the_replacement_character(monkeypatch):
    _proto_calls, struct_calls = recorders(monkeypatch)
    on_schema(event("/.schema/struct:Bad", b"\xff\xfe"))
    assert struct_calls == [("/.schema/struct:Bad", "\ufffd\ufffd")]


def test_bytearray_is_accepted_and_copied_as_bytes(monkeypatch):
    proto_calls, struct_calls = recorders(monkeypatch)
    on_schema(event("/.schema/proto:g.proto", bytearray(b"\x0a\x01g")))
    on_schema(event("/.schema/struct:G", bytearray(b"uint8 x")))
    assert proto_calls == [("/.schema/proto:g.proto", b"\x0a\x01g")]
    assert struct_calls == [("/.schema/struct:G", "uint8 x")]
    assert type(proto_calls[0][1]) is bytes  # a plain bytes, not the bytearray


def test_forget_empties_both_registries_through_the_state_facade():
    state.add("/.schema/struct:FacadePose", b"double x;double y")
    assert state.field_names("struct:FacadePose") == ["x", "y"]
    state.forget()
    assert state.decode("struct:FacadePose", bytes(16)) is None
