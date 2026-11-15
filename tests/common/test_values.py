import pytest

from frc_mcp_nt4.access.publishing import make_value
from frc_mcp_wpilib.conditions import ConditionIndeterminate, holds, values_equal
from frc_mcp_wpilib.errors import WPILibMCPError
from frc_mcp_wpilib.values import to_bool


@pytest.mark.parametrize(("value", "expected"), [
    (True, True), (False, False), (1, True), (0, False),
    (1.0, True), (0.0, False), ("yes", True), (" OFF ", False),
])
def test_boolean_values_accept_unambiguous_representations(value, expected):
    assert to_bool(value) is expected
    assert make_value("boolean", value).value() is expected


@pytest.mark.parametrize("value", [2, -1, 0.5, float("nan"), [], {}, None, "maybe", "2"])
def test_boolean_values_reject_ambiguous_representations(value):
    with pytest.raises(ValueError, match="not a boolean"):
        make_value("boolean", value)


@pytest.mark.parametrize(("value", "expected"), [
    (-(2**63), -(2**63)), (2**63 - 1, 2**63 - 1), ("42", 42), (2.0, 2),
])
def test_integer_values_accept_exact_values(value, expected):
    assert make_value("int", value).value() == expected


@pytest.mark.parametrize("value", [1.5, -1.5, True, [], {}, float("inf"), 2**63, -(2**63) - 1])
def test_integer_values_reject_truncation_and_invalid_input(value):
    with pytest.raises(ValueError):
        make_value("int", value)


@pytest.mark.parametrize("type_str", ["boolean[]", "int[]", "double[]", "float[]", "string[]"])
@pytest.mark.parametrize("value", [{"1": 2}, 1, '"text"'])
def test_array_types_require_json_arrays(type_str, value):
    with pytest.raises(ValueError, match="JSON arrays"):
        make_value(type_str, value)


def test_array_elements_are_checked_and_valid_json_text_arrays_work():
    assert make_value("boolean[]", '[true, 0, "yes"]').value() == [True, False, True]
    with pytest.raises(ValueError, match="not a boolean"):
        make_value("boolean[]", [False, 2])
    with pytest.raises(ValueError, match="not an integer"):
        make_value("int[]", [1, 2.5])
    with pytest.raises(ValueError, match="not a number"):
        make_value("double[]", [1.0, True])


def test_invalid_boolean_condition_is_an_actionable_error():
    with pytest.raises(WPILibMCPError, match="not a boolean"):
        holds("==", True, 2, None)


@pytest.mark.parametrize(("op", "current", "target", "expected"), [
    ("==", 2**53, 2**53 + 1, False),
    ("!=", 2**53, 2**53 + 1, True),
    (">", 2**53 + 1, 2**53, True),
    (">=", 2**53, 2**53 + 1, False),
    ("<", 2**53, str(2**53 + 1), True),
    ("<=", -(2**63), -(2**63) + 1, True),
    (">", 2**63 - 1, str(2**63 - 2), True),
    ("<", 2**53, "9007199254740992.5", True),
    ("==", 2**53 + 1, float(2**53), False),
    ("==", 4.0, "4", True),
    (">", True, 0, True),
])
def test_condition_comparison_preserves_integer_precision(op, current, target, expected):
    assert holds(op, current, target, None) is expected


def test_invalid_integer_ordering_target_reports_an_error():
    with pytest.raises(WPILibMCPError, match="Cannot compare"):
        holds(">", 2**53, "not a number", None)
    with pytest.raises(WPILibMCPError, match="NaN comparison"):
        holds(">", 1, "NaN", None)


@pytest.mark.parametrize("op", ["==", "!=", ">", ">=", "<", "<="])
def test_nan_does_not_establish_a_comparison(op):
    with pytest.raises(ConditionIndeterminate, match="NaN"):
        holds(op, float("nan"), 40, None)
    with pytest.raises(WPILibMCPError, match="NaN comparison"):
        holds(op, 40.0, float("nan"), None)


@pytest.mark.parametrize("value", [[0.0, float("nan")], {"x": [float("nan")]}, (1.0, (float("nan"),))])
def test_nested_nan_does_not_establish_a_comparison(value):
    with pytest.raises(ConditionIndeterminate, match="NaN"):
        holds("!=", value, value, None)
    with pytest.raises(WPILibMCPError, match="NaN comparison"):
        holds("==", [0.0], value, None)
    assert holds("changed", value, None, value) is False


@pytest.mark.parametrize(("left", "right", "equal"), [
    (float("nan"), float("nan"), True),
    (float("nan"), 1.0, False),
    ([float("nan"), [1.0]], [float("nan"), [1.0]], True),
    ([float("nan")], [float("nan"), 1.0], False),
    ({"value": [float("nan")]}, {"value": [float("nan")]}, True),
    ({"value": float("nan")}, {"other": float("nan")}, False),
    ([1], (1,), True),  # one container rule: the sequence kind is not part of the comparison, as in _contains_nan
    ((1, [2]), [1, (2,)], True),
    ([1, 2], (1,), False),
    ((1,), [1, 2], False),
])
def test_observed_value_equality_handles_nan_without_hiding_changes(left, right, equal):
    assert values_equal(left, right) is equal
    assert holds("changed", left, None, right) is (not equal)


def test_unit_of_reads_a_declared_unit_only():
    from frc_mcp_wpilib.values import unit_of

    assert unit_of({"unit": " deg "}) == "deg" and unit_of({"units": "m/s"}) == "m/s"
    assert unit_of({"unit": ""}) is None and unit_of({"unit": 5}) is None and unit_of({"source": "x"}) is None
    assert unit_of(None) is None and unit_of({"unit": "x" * 50}) == "x" * 20
