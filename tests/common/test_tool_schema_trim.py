"""slim_schema: drop advertised noise, keep everything a model needs."""

import pytest
from pydantic import BaseModel, Field, ValidationError

from frc_mcp_core.schema_trim import slim_schema


def test_optional_parameter_collapses_to_its_inner_type_and_keeps_the_description():
    schema = {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "description": "Client name"}
    assert slim_schema(schema) == {"type": "string", "description": "Client name"}


def test_union_of_more_than_one_real_type_is_kept():
    schema = {"anyOf": [{"type": "string"}, {"type": "array"}, {"type": "null"}]}
    assert slim_schema(schema) == schema


def test_wrapper_description_wins_over_the_inner_one():
    schema = {"anyOf": [{"type": "integer", "description": "inner"}, {"type": "null"}], "description": "outer"}
    assert slim_schema(schema) == {"type": "integer", "description": "outer"}


def test_only_null_and_false_defaults_are_removed():
    assert slim_schema({"type": "boolean", "default": False}) == {"type": "boolean"}
    assert slim_schema({"type": "string", "default": None}) == {"type": "string"}
    assert slim_schema({"type": "integer", "default": 0}) == {"type": "integer", "default": 0}
    assert slim_schema({"type": "boolean", "default": True}) == {"type": "boolean", "default": True}
    assert slim_schema({"type": "string", "default": ""}) == {"type": "string", "default": ""}


def test_bounds_titles_and_additional_properties_are_dropped():
    schema = {
        "type": "number", "minimum": 0, "maximum": 5, "exclusiveMinimum": 0, "exclusiveMaximum": 9,
        "minLength": 1, "maxLength": 2, "minItems": 1, "maxItems": 3, "title": "Wait S",
        "additionalProperties": False, "description": "Seconds",
    }
    assert slim_schema(schema) == {"type": "number", "description": "Seconds"}


def test_a_property_named_like_a_dropped_keyword_survives():
    schema = {"type": "object", "properties": {"title": {"type": "string"}, "maximum": {"type": "number"}}}
    assert slim_schema(schema) == schema


def test_nested_objects_arrays_and_lists_are_slimmed_recursively():
    schema = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"anyOf": [{"type": "string", "maxLength": 4}, {"type": "null"}]}, "maxItems": 5},
            "rule": {"$ref": "#/$defs/Rule"},
        },
        "$defs": {"Rule": {"type": "object", "title": "Rule", "properties": {"n": {"type": "integer", "minimum": 1, "default": None}}}},
        "required": ["items"],
        "additionalProperties": False,
    }
    assert slim_schema(schema) == {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"type": "string"}},
            "rule": {"$ref": "#/$defs/Rule"},
        },
        "$defs": {"Rule": {"type": "object", "properties": {"n": {"type": "integer"}}}},
        "required": ["items"],
    }


def test_scalars_and_inputs_are_not_mutated():
    assert slim_schema("text") == "text" and slim_schema(3) == 3 and slim_schema(None) is None
    schema = {"anyOf": [{"type": "string", "maxLength": 3}, {"type": "null"}], "default": None}
    slim_schema(schema)
    assert schema == {"anyOf": [{"type": "string", "maxLength": 3}, {"type": "null"}], "default": None}


def test_real_pydantic_schema_is_slimmed_but_validation_still_uses_the_full_model():
    class Params(BaseModel):
        name: str | None = Field(default=None, description="Who", max_length=3)
        wait_s: float = Field(default=2.0, ge=0, le=30)

    slim = slim_schema(Params.model_json_schema())
    assert slim["properties"]["name"] == {"type": "string", "description": "Who"}
    assert slim["properties"]["wait_s"] == {"type": "number", "default": 2.0}
    assert "title" not in slim
    assert Params(wait_s=1).wait_s == 1
    with pytest.raises(ValidationError):
        Params(wait_s=99)
