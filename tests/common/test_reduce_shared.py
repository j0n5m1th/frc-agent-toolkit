"""`all`: fields every topic has with one value are said once, and never when any topic differs or lacks them."""

from frc_mcp_wpilib.reduce.shared import hoist_shared


def test_a_field_every_topic_has_with_the_same_value_moves_to_all():
    data = {"/a": {"n": 3, "capture_rate_hz": 10.0}, "/b": {"n": 4, "capture_rate_hz": 10.0}}
    assert hoist_shared(data) == {"capture_rate_hz": 10.0}
    assert data == {"/a": {"n": 3}, "/b": {"n": 4}}


def test_differing_values_stay_on_each_topic():
    data = {"/a": {"capture_rate_hz": 10.0}, "/b": {"capture_rate_hz": 2.0}}
    assert hoist_shared(data) == {} and data["/a"]["capture_rate_hz"] == 10.0


def test_a_topic_without_the_field_or_with_an_error_blocks_it():
    for other in ({"n": 1}, {"err": "not in this log"}):
        data = {"/a": {"capture_rate_hz": 10.0}, "/b": other, "/c": {"capture_rate_hz": 10.0}}
        assert hoist_shared(data) == {} and data["/a"] == {"capture_rate_hz": 10.0}


def test_one_topic_is_never_hoisted():
    data = {"/a": {"capture_rate_hz": 10.0}}
    assert hoist_shared(data) == {} and data == {"/a": {"capture_rate_hz": 10.0}}
