"""`schema.state.entries` is the inverse of `add`: what a recorded log needs to carry to be decodable later."""

from google.protobuf import descriptor_pb2

from frc_mcp_wpilib.schema import state


def test_entries_gives_back_what_add_was_given():
    state.forget()
    state.add("/.schema/struct:Point", b"double x;double y")
    descriptor = descriptor_pb2.FileDescriptorProto(name="point.proto", package="demo")
    state.add("/.schema/proto:point.proto", descriptor.SerializeToString())
    assert dict(state.entries()) == {
        "/.schema/struct:Point": b"double x;double y",
        "/.schema/proto:point.proto": descriptor.SerializeToString(),
    }
    state.forget()
    assert state.entries() == []


def test_entries_can_be_loaded_straight_back():
    state.forget()
    state.add("/.schema/struct:Point", b"double x;double y")
    loaded = state.entries()
    state.forget()
    assert state.activate("copy", loaded) is True
    assert state.decode("struct:Point", bytes(16)) is not None
    state.forget()


def test_revision_changes_when_a_schema_is_added_or_the_schemas_are_dropped():
    state.forget()
    start = state.revision()
    state.add("/.schema/struct:Point", b"double x;double y")
    added = state.revision()
    assert added != start
    assert state.revision() == added  # reading does not change it
    state.forget()
    assert state.revision() != added
    state.forget("someone-else")  # not the loaded owner: nothing is dropped, so nothing changed
    dropped = state.revision()
    state.forget("another")
    assert state.revision() == dropped
