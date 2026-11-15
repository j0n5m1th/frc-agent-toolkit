"""Convert between JSON-friendly Python values and NT4 typed values. NT4 type strings (spec "Supported Data Types") are the
contract; anything unknown (``struct:*``, ``proto:*``, ``msgpack``, ``rpc``) travels as raw bytes, shown or accepted as hex.
"""

import logging
import math
from typing import Any, Final

import msgpack

from .schema import state as schema_state
from .schema.structs import SchemaError, debug_once

_log = logging.getLogger(__name__)


UNIT_PROPS = ("unit", "units")  # not defined by NT4; a convention some publishers follow

JsonValue = None | bool | int | float | str | list[Any] | dict[str, Any]  # the JSON-friendly form `shape` returns


class Unset:
    """The type of the one marker value; "nothing here" (`None` is a real value): no `changed` baseline, or an
    unpublished chooser part. Both servers share this class, so one identity check covers them."""

    __slots__ = ()


UNSET: Final = Unset()  # compared by identity (`is`), never serialized


def unit_of(props: Any) -> str | None:
    """The unit a publisher declared in a topic's properties, if any (a short non-empty string)."""
    if not isinstance(props, dict):
        return None
    for key in UNIT_PROPS:
        value = props.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:20]
    return None


def to_bool(v: Any) -> bool:
    """Accept booleans and unambiguous 0/1 representations."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        word = v.strip().lower()
        if word in ("1", "true", "yes", "on"):
            return True
        if word in ("0", "false", "no", "off"):
            return False
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    raise ValueError(f"{v!r} is not a boolean (use true/false, 1/0, yes/no or on/off)")


# Type strings that carry opaque bytes (given as hex): WPILib struct/protobuf types and the spec's raw-like types.
RAW_PREFIXES = ("struct:", "proto:")  # the two prefixes whose value travels as hex


def decode_struct_strict(type_str: str, data: bytes) -> Any:
    """The one schema decode: raises SchemaError saying why the bytes cannot be decoded (see schema.state)."""
    return schema_state.decode_strict(type_str, data)


# --- output shaping (token budget) ----------------------------------------
MAX_ITEMS = 50  # array elements shown before truncating
MAX_TEXT = 400  # characters of string/hex shown before truncating
MAX_REASON = 160  # characters of a decode failure's message carried beside its hex


# NT types whose JSON form already tells them apart (`5` int, `5.0` double, `true`, `"x"`), so replies leave `type` out.
SELF_DESCRIBING_TYPES = frozenset({"double", "int", "boolean", "string"})


def type_field(type_str: str) -> dict[str, str]:
    """`{"type": type_str}` for a value whose JSON does not show its type, else `{}` (saves tokens on every scalar)."""
    return {} if type_str in SELF_DESCRIBING_TYPES else {"type": type_str}


def _brief_reason(e: BaseException) -> str:
    """One short line saying why a value did not decode (the exception's own message, or its class)."""
    return (str(e).strip() or type(e).__name__)[:MAX_REASON]


def _truncated(total: int, head: Any) -> dict[str, Any]:
    """The marker every long value is cut down to: its full length plus a leading slice."""
    return {"len": total, "head": head, "truncated": True}


def _hex_value(data: bytes, reason: str | None) -> Any:
    """Hex for undecodable bytes: a bare string, or a truncated marker dict; `reason` tags a failed schema decode."""
    text = data.hex()
    if len(text) > MAX_TEXT:
        out = _truncated(len(data), text[:MAX_TEXT])
    else:
        if reason is None:
            return text
        out = {"hex": text}
    if reason is not None:
        out["decode_error"] = reason
    return out


def _round(v: Any, precision: int) -> Any:
    if isinstance(v, float):
        return round(v, precision) if math.isfinite(v) else str(v)
    if isinstance(v, dict):
        return {k: _round(x, precision) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_round(x, precision) for x in v]
    return v


def _project(decoded: Any, fields: list[str] | None) -> Any:
    if not fields:
        return decoded
    if isinstance(decoded, list):
        return [_project(d, fields) for d in decoded]
    # a field matches by full dotted key, leaf name ('x'), or parent ('translation')
    def keep(k: str) -> bool:
        return any(k == f or k.rsplit(".", 1)[-1] == f or k.startswith(f + ".") for f in fields)

    return {k: v for k, v in decoded.items() if keep(k)}


DEFAULT_PRECISION = 4  # decimal places for floats in every reply


def shape(raw: Any, type_str: str, fields: list[str] | None = None, precision: int = DEFAULT_PRECISION) -> Any:
    """Raw ntcore value -> slim JSON value.
    Decoded structs replace their hex, `fields` projects struct fields, floats are rounded, and long arrays/strings/hex are
    truncated with a marker. Bytes whose schema decode failed come back as hex tagged `decode_error`, so "undecodable" differs
    from an ordinary `raw`/`rpc` value.
    """
    if isinstance(raw, (bytes, bytearray)):
        data = bytes(raw)
        reason: str | None = None
        if type_str == "msgpack":
            try:  # the spec's nested-MessagePack type (e.g. the server's $clients meta-topic)
                return _round(_msgpack_jsonable(msgpack.unpackb(data)), precision)
            except (ValueError, msgpack.UnpackException) as e:
                _log.debug("msgpack topic value did not decode; falling back to hex: %s", e)
                reason = _brief_reason(e)
        elif type_str.startswith(RAW_PREFIXES):
            try:  # the strict decode says exactly why the schema or the bytes did not work
                decoded = decode_struct_strict(type_str, data)
            except SchemaError as e:
                debug_once(type_str, e)
                reason = _brief_reason(e)
            else:
                return _round(_project(decoded, fields), precision)
        return _hex_value(data, reason)
    if isinstance(raw, str):
        return raw if len(raw) <= MAX_TEXT else _truncated(len(raw), raw[:MAX_TEXT])
    if isinstance(raw, (list, tuple)):
        if len(raw) > MAX_ITEMS:
            return _truncated(len(raw), _round(list(raw[:MAX_ITEMS]), precision))
        return _round(list(raw), precision)
    return _round(raw, precision)


def _msgpack_jsonable(v: Any) -> Any:
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).hex()
    if isinstance(v, dict):
        return {str(k): _msgpack_jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_msgpack_jsonable(x) for x in v]
    return v


def is_numeric(type_str: str) -> bool:
    return type_str in ("boolean", "double", "float", "int")
