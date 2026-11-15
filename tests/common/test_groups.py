import pytest

from frc_mcp_wpilib.groups import build_widget_group


@pytest.mark.parametrize(("selected", "active", "default", "expected"), [
    ("A", "B", "B", "A"),
    ("", "B", "A", "B"),
    ("", "", "A", "A"),
])
def test_chooser_current_is_a_listed_candidate(selected, active, default, expected):
    result = build_widget_group({".type": "String Chooser", "options": ["A", "B"],
                    "selected": selected, "active": active, "default": default})
    assert result["current"] == expected


@pytest.mark.parametrize(("selected", "active", "default", "invalid", "reported"), [
    ("bad", "A", "B", "selected", "bad"),
    (0, "A", "B", "selected", None),
    (False, "A", "B", "selected", None),
    ("", "bad", "B", "active", "bad"),
    ("", "", "bad", "default", "bad"),
])
def test_invalid_chooser_value_is_not_presented_as_the_robots_choice(selected, active, default, invalid, reported):
    result = build_widget_group({".type": "String Chooser", "options": ["A", "B"],
                    "selected": selected, "active": active, "default": default})
    # The resolved string is reported as `current` and the warning names where it came from; a non-string value has
    # no `current` at all, only the warning.
    assert result.get("current") == reported
    assert invalid in result["warning"]


@pytest.mark.parametrize(("options", "truncated"), [
    ({"len": 100, "head": ["A"], "truncated": True}, False),
    (["A", 2], False),
    (["A", "B"], True),
])
def test_incomplete_chooser_options_warns_but_keeps_the_resolved_choice(options, truncated):
    result = build_widget_group({".type": "Selectable", "options": options, "selected/value": "A"}, truncated)
    assert result["current"] == "A"  # the published choice survives an option list that cannot confirm it
    assert "Incomplete" in result["warning"]


@pytest.mark.parametrize(("values", "unavailable"), [
    ({".type": "String Chooser", "options": ["A"], "active": "", "default": "A"}, {"selected": {"err": "no value"}}),
    ({".type": "String Chooser", "options": ["A"], "active": "", "default": "A"}, {"selected/value": {"err": "no value"}}),
    ({".type": "String Chooser", "options": ["A"], "selected": "", "default": "A"}, {"active": {"err": "no value"}}),
    ({".type": "String Chooser", "options": ["A"], "selected": "", "active": ""}, {"default": {"err": "no value"}}),
    ({".type": "String Chooser", "options": ["A"], "selected/value": "A"}, {"selected": {"err": "no value"}}),
])
def test_chooser_does_not_fall_back_past_unavailable_value(values, unavailable):
    result = build_widget_group(values, unavailable=unavailable)
    assert "current" not in result and "unavailable" in result["warning"]
    assert result["unavailable"] == unavailable


def test_unavailable_lower_priority_values_do_not_hide_known_selection():
    result = build_widget_group({".type": "String Chooser", "options": ["A"], "selected": "A"},
                   unavailable={"active": {"err": "no value"}, "default": {"err": "no value"}})
    assert result["current"] == "A"


def test_chooser_with_unavailable_options_reports_the_choice_and_the_gap():
    result = build_widget_group({".type": "String Chooser", "selected": "A"}, unavailable={"options": {"err": "no value"}})
    assert result["current"] == "A" and "Incomplete" in result["warning"]


def test_nested_chooser_names_the_child_a_dashboard_writes():
    result = build_widget_group({".type": "Selectable", "options": ["A", "B"], "selected/value": "A", "selected/tune": "A"})
    assert result["input"] == "selected/tune" and result["current"] == "A"
    assert "input" not in build_widget_group({".type": "Selectable", "options": ["A"], "selected/value": "A"})
