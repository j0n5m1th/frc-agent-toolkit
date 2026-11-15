"""`schema.state.decode` is the lenient form of `decode_strict`: anything undecodable is None, never an exception."""

import pytest

from frc_mcp_wpilib.schema import state
from frc_mcp_wpilib.schema.structs import SchemaError


def test_a_type_that_is_not_struct_or_proto_decodes_to_none_and_strictly_raises():
    assert state.decode("double", b"\x00" * 8) is None
    with pytest.raises(SchemaError):
        state.decode_strict("double", b"\x00" * 8)


def test_an_unknown_struct_decodes_to_none():
    state.forget()
    assert state.decode("struct:Nope", b"\x00") is None


def test_activate_reloads_for_a_new_owner_and_is_free_for_the_same_one():
    state.forget()
    assert state.activate("a", []) is True
    assert state.activate("a", []) is False
    assert state.activate("b", []) is True
    state.forget("a")  # not the loaded owner: nothing happens
    assert state.activate("b", []) is False
    state.forget("b")
    assert state.activate("b", []) is True
    state.forget()
