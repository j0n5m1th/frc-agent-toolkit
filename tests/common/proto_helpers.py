"""Hand-built protobuf descriptors and encoders shared by the proto tests (no network)."""

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from frc_mcp_wpilib.schema.protos import ProtoRegistry

F = descriptor_pb2.FieldDescriptorProto


def geometry_file() -> descriptor_pb2.FileDescriptorProto:
    file = descriptor_pb2.FileDescriptorProto(name="geometry2d.proto", package="wpi.proto", syntax="proto3")
    translation = file.message_type.add(name="ProtobufTranslation2d")
    translation.field.add(name="x", number=1, type=F.TYPE_DOUBLE, label=F.LABEL_OPTIONAL)
    translation.field.add(name="y", number=2, type=F.TYPE_DOUBLE, label=F.LABEL_OPTIONAL)
    rotation = file.message_type.add(name="ProtobufRotation2d")
    rotation.field.add(name="value", number=1, type=F.TYPE_DOUBLE, label=F.LABEL_OPTIONAL)
    pose = file.message_type.add(name="ProtobufPose2d")
    pose.field.add(name="translation", number=1, type=F.TYPE_MESSAGE, label=F.LABEL_OPTIONAL,
                   type_name=".wpi.proto.ProtobufTranslation2d")
    pose.field.add(name="rotation", number=2, type=F.TYPE_MESSAGE, label=F.LABEL_OPTIONAL,
                   type_name=".wpi.proto.ProtobufRotation2d")
    return file


def robot_file() -> descriptor_pb2.FileDescriptorProto:
    """A second file that depends on the first, with an enum, repeated, int64, bytes and map fields."""
    file = descriptor_pb2.FileDescriptorProto(name="robot.proto", package="team", syntax="proto3",
                                              dependency=["geometry2d.proto"])
    enum = file.enum_type.add(name="Mode")
    enum.value.add(name="DISABLED", number=0)
    enum.value.add(name="AUTO", number=1)
    status = file.message_type.add(name="Status")
    status.field.add(name="mode", number=1, type=F.TYPE_ENUM, label=F.LABEL_OPTIONAL, type_name=".team.Mode")
    status.field.add(name="ticks", number=2, type=F.TYPE_INT64, label=F.LABEL_OPTIONAL)
    status.field.add(name="blob", number=3, type=F.TYPE_BYTES, label=F.LABEL_OPTIONAL)
    status.field.add(name="history", number=4, type=F.TYPE_DOUBLE, label=F.LABEL_REPEATED)
    status.field.add(name="pose", number=5, type=F.TYPE_MESSAGE, label=F.LABEL_OPTIONAL,
                     type_name=".wpi.proto.ProtobufPose2d")
    status.field.add(name="waypoints", number=7, type=F.TYPE_MESSAGE, label=F.LABEL_REPEATED,
                     type_name=".wpi.proto.ProtobufTranslation2d")
    entry = status.nested_type.add(name="CountsEntry")
    entry.field.add(name="key", number=1, type=F.TYPE_STRING, label=F.LABEL_OPTIONAL)
    entry.field.add(name="value", number=2, type=F.TYPE_INT32, label=F.LABEL_OPTIONAL)
    entry.options.map_entry = True
    status.field.add(name="counts", number=6, type=F.TYPE_MESSAGE, label=F.LABEL_REPEATED,
                     type_name=".team.Status.CountsEntry")
    return file


def encode(files: list[descriptor_pb2.FileDescriptorProto], full_name: str, **values):
    pool = descriptor_pool.DescriptorPool()
    for file in files:
        pool.AddSerializedFile(file.SerializeToString())
    cls = message_factory.GetMessageClass(pool.FindMessageTypeByName(full_name))
    return cls, cls(**values)


def registry(*files: descriptor_pb2.FileDescriptorProto) -> ProtoRegistry:
    r = ProtoRegistry()
    for file in files:
        r.add(f"/.schema/proto:{file.name}", file.SerializeToString())
    return r
