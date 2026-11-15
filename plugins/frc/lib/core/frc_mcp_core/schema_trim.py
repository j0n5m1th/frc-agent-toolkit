"""Leaner JSON schemas for advertised tool parameters (validation still uses the full ones)."""

from typing import Any

# Validation keywords stay enforced server-side (with clear errors); advertising them costs tokens on every session.
_BOUNDS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength", "minItems",
           "maxItems", "title", "additionalProperties"}
_NULL = {"type": "null"}


def slim_schema(node: Any) -> Any:
    """Drop JSON-schema noise that costs tokens every session but tells a model nothing (pydantic's titles,
    `additionalProperties: false`, `default: null`, `anyOf: [X, null]`). Validation is unaffected: FastMCP validates from the
    function signature; this dict is only what clients show the model.
    """
    if isinstance(node, list):
        return [slim_schema(v) for v in node]
    if not isinstance(node, dict):
        return node
    options = node.get("anyOf")
    if isinstance(options, list) and len(options) == 2 and _NULL in options:
        # Optional[X]: an omitted argument is already null, so advertise plain X (null stays accepted)
        inner = next(o for o in options if o != _NULL)
        node = {**slim_schema(inner), **{k: v for k, v in node.items() if k != "anyOf"}}  # the wrapper's description wins
    trimmed: dict[str, Any] = {}
    for key, value in node.items():
        # absent `default: null|false` reads as null/false anyway, so it costs a token for nothing
        if (key not in _BOUNDS or isinstance(value, dict)) and (key != "default" or (value is not None and value is not False)):
            trimmed[key] = slim_schema(value)
    return trimmed
