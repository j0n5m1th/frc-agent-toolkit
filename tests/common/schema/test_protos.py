"""Protobuf (proto:*) schema registry and decoding (no network)."""

import pytest
from google.protobuf import descriptor_pb2
from proto_helpers import F, encode, geometry_file, registry, robot_file

from frc_mcp_wpilib.schema.protos import ProtoRegistry
from frc_mcp_wpilib.schema.structs import SchemaError


def test_pose_decodes_to_the_same_dotted_keys_as_a_struct():
    cls, msg = encode([geometry_file()], "wpi.proto.ProtobufPose2d",
                      translation={"x": 1.5, "y": -2.0}, rotation={"value": 0.25})
    r = registry(geometry_file())
    assert r.decode("proto:wpi.proto.ProtobufPose2d", msg.SerializeToString()) == {
        "translation.x": 1.5, "translation.y": -2.0, "rotation.value": 0.25}
    assert r.field_names("wpi.proto.ProtobufPose2d") == ["translation.x", "translation.y", "rotation.value"]


def test_dependencies_enums_repeated_int64_bytes_and_maps():
    files = [geometry_file(), robot_file()]
    cls, msg = encode(files, "team.Status", mode=1, ticks=2**40, blob=b"\x01\xff", history=[1.0, 2.5],
                      pose={"translation": {"x": 3.0}}, counts={"a": 2, "b": 5},
                      waypoints=[{"x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}])
    r = registry(robot_file(), geometry_file())  # order of arrival must not matter
    decoded = r.decode("proto:team.Status", msg.SerializeToString())
    assert decoded is not None
    assert decoded["mode"] == "AUTO" and decoded["ticks"] == 2**40 and decoded["blob"] == "01ff"
    assert decoded["history"] == [1.0, 2.5] and decoded["counts"] == {"a": 2, "b": 5}
    assert decoded["pose.translation.x"] == 3.0 and decoded["pose.rotation.value"] == 0.0
    assert decoded["waypoints"] == [{"x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}]
    assert set(r.field_names("team.Status")) >= {"mode", "ticks", "pose.translation.x", "waypoints", "counts"}


def test_unknown_or_undecodable_values_stay_undecoded():
    r = registry(geometry_file())
    cls, msg = encode([geometry_file()], "wpi.proto.ProtobufTranslation2d", x=1.0)
    data = msg.SerializeToString()
    assert r.decode("proto:wpi.proto.Missing", data) is None
    assert r.decode("proto:wpi.proto.ProtobufTranslation2d[]", data) is None  # arrays are not published this way
    assert r.decode("struct:Pose2d", data) is None and r.decode("proto:wpi.proto.ProtobufTranslation2d", b"\xff\xff") is None
    with pytest.raises(SchemaError):
        r.field_names("wpi.proto.Missing")


def test_a_file_with_a_missing_dependency_is_skipped_not_fatal():
    r = registry(robot_file())  # geometry2d.proto never arrives
    assert r.decode("proto:team.Status", b"") is None
    r.add("/.schema/proto:geometry2d.proto", geometry_file().SerializeToString())  # now it can be built
    assert r.decode("proto:team.Status", b"") is not None


def test_files_that_fail_to_load_are_named_in_the_miss_hint_not_dropped():
    bad = descriptor_pb2.FileDescriptorProto(name="bad.proto", package="x", syntax="proto3")
    bad.message_type.add(name="M").field.add(name="f", number=1, type=F.TYPE_MESSAGE, label=F.LABEL_OPTIONAL,
                                             type_name=".x.Nope")  # refers to a type nothing defines
    r = registry(bad)
    with pytest.raises(SchemaError) as refused:
        r.field_names("x.M")
    assert "/.schema/proto:bad.proto" in str(refused.value)
    r = registry(robot_file())  # its dependency never arrives, so it is never built
    with pytest.raises(SchemaError) as missing:
        r.field_names("team.Status")
    assert "/.schema/proto:robot.proto" in str(missing.value)


def test_decode_strict_says_why_a_proto_cannot_decode():
    r = registry(geometry_file())
    with pytest.raises(SchemaError, match="no descriptor"):  # schema missing
        r.decode_strict("proto:wpi.proto.Missing", b"")
    with pytest.raises(SchemaError, match="malformed protobuf"):  # malformed bytes
        r.decode_strict("proto:wpi.proto.ProtobufTranslation2d", b"\xff\xff")
    with pytest.raises(SchemaError, match="not a plain"):  # not a single proto:<Message> type
        r.decode_strict("proto:wpi.proto.ProtobufTranslation2d[]", b"")
    assert r.decode("proto:wpi.proto.Missing", b"") is None
    assert r.decode("proto:wpi.proto.ProtobufTranslation2d", b"\xff\xff") is None


def test_non_schema_topics_and_garbage_are_ignored_and_clear_resets():
    r = ProtoRegistry()
    r.add("/.schema/struct:Pose2d", geometry_file().SerializeToString())
    r.add("/.schema/proto:junk.proto", b"\xff\xff\xff")
    r.add("/other", geometry_file().SerializeToString())
    assert r.decode("proto:wpi.proto.ProtobufPose2d", b"") is None
    r.add("/.schema/proto:geometry2d.proto", geometry_file().SerializeToString())
    assert r.decode("proto:wpi.proto.ProtobufPose2d", b"") is not None
    r.clear()
    assert r.decode("proto:wpi.proto.ProtobufPose2d", b"") is None
