"""Validation and conversion helpers for publishing a topic value (the stateless half of the write path; the
connection-bound half is writer.py)."""

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import ntcore

from frc_mcp_wpilib.values import RAW_PREFIXES, JsonValue, to_bool

from ..errors import NT4Error

# type string -> Value factory. Scalars take one python value, arrays a list. These are the only ntcore
# constructions in the shared surface, so they live with the nt4 write path that uses them.
_MAKERS: dict[str, Callable[[Any, int], ntcore.Value]] = {
    "boolean": lambda v, t: ntcore.Value.makeBoolean(to_bool(v), t),
    "double": lambda v, t: ntcore.Value.makeDouble(_float(v), t),
    "float": lambda v, t: ntcore.Value.makeFloat(_float(v), t),
    "int": lambda v, t: ntcore.Value.makeInteger(_int64(v), t),
    "string": lambda v, t: ntcore.Value.makeString(json.dumps(v) if isinstance(v, (dict, list)) else str(v), t),
    "json": lambda v, t: ntcore.Value.makeString(v if isinstance(v, str) else json.dumps(v), t),
    "raw": lambda v, t: ntcore.Value.makeRaw(bytes.fromhex(v), t),
    "boolean[]": lambda v, t: ntcore.Value.makeBooleanArray([to_bool(x) for x in v], t),
    "double[]": lambda v, t: ntcore.Value.makeDoubleArray([_float(x) for x in v], t),
    "float[]": lambda v, t: ntcore.Value.makeFloatArray([_float(x) for x in v], t),
    "int[]": lambda v, t: ntcore.Value.makeIntegerArray([_int64(x) for x in v], t),
    "string[]": lambda v, t: ntcore.Value.makeStringArray([str(x) for x in v], t),
}

SUPPORTED_TYPES = sorted(_MAKERS)  # the type strings nt4_set_value accepts, for the types resource
# Types whose value is opaque bytes given as hex, on top of SUPPORTED_TYPES.
_RAW_TYPES = ("msgpack", "rpc")


def _int64(v: Any) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ValueError(f"{v!r} is not an integer")
    if isinstance(v, float) and not v.is_integer():
        raise ValueError(f"{v!r} is not an integer")
    n = int(v)
    if not -(2**63) <= n < 2**63:
        raise ValueError(f"{n} does not fit in a 64-bit integer")
    return n


def _float(v: Any) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ValueError(f"{v!r} is not a number")
    try:
        return float(v)
    except OverflowError as e:
        raise ValueError(f"{v!r} is too large for a floating-point number") from e


def make_value(type_str: str, value: JsonValue, time_us: int = 0) -> ntcore.Value:
    """Build an ntcore.Value. struct:*/proto:*/msgpack/rpc types are raw bytes given as hex; other unknown types are rejected.

    `time_us` is the sample's time on the *local* NT clock (0 = now). Callers convert server time first."""
    if type_str not in _MAKERS and not type_str.startswith(RAW_PREFIXES) and type_str not in _RAW_TYPES:
        raise ValueError(
            f"unknown NT4 type; use one of {', '.join(SUPPORTED_TYPES)}, or struct:<Name>/proto:<Name>/msgpack with a hex value"
        )
    if isinstance(value, str) and type_str.endswith("[]") and type_str in _MAKERS:
        value = json.loads(value)  # tolerate '["a","b"]' passed as a string
    if type_str.endswith("[]") and type_str in _MAKERS and not isinstance(value, list):
        raise ValueError("array values must be JSON arrays")
    return _MAKERS.get(type_str, _MAKERS["raw"])(value, time_us)


MAX_TTL_S = 3600  # longest publisher time limit a caller may ask for
MAX_FUTURE_US = 1_000_000  # a sample time may lead the server clock by this much (estimation error)
_BOOL_PROPS = ("persistent", "retained", "cached")  # NT4-defined; must be true/false (null deletes)


@dataclass(frozen=True)
class PublishOptions:
    """How to publish: `default` is NT4's "set default" (a weak write, timestamp 0, that never overrides a value
    set by anyone else); `properties` are merged into the topic's properties (null deletes a key); `publisher_ttl_s` releases
    the publisher after that many seconds; `timestamp_us` stamps the sample with a server-clock time."""

    default: bool = False
    properties: dict[str, Any] | None = None
    publisher_ttl_s: float | None = None
    timestamp_us: int | None = None


@dataclass(frozen=True)
class SetResult:
    """What `NT4Session.set_value` did, as facts about the topic (`tools/write_tools.py` renders it).

    `type_str` is None for a properties-only change; `written` is the raw value sent; `properties` are the merged keys,
    sorted; `publisher_expires_in_s` is the TTL left (None without one); `selectable_chooser` means the topic is a
    chooser's `selected/value` (write `selected/tune`); `immutable` means mutable=false, so the write may be ignored.
    """

    topic: str
    type_str: str | None
    written: Any = None
    default: bool = False
    timestamp_us: int | None = None
    properties: tuple[str, ...] = ()
    publisher_expires_in_s: float | None = None
    selectable_chooser: bool = False
    immutable: bool = False


def normalize_properties(props: dict[str, Any] | None) -> dict[str, Any]:
    if not props:
        return {}
    for k in _BOOL_PROPS:
        if k in props and props[k] is not None and not isinstance(props[k], bool):
            raise NT4Error(f"Property {k!r} must be true or false (or null to delete), got {props[k]!r}.")
    try:
        json.dumps(props)
    except TypeError as e:
        raise NT4Error(f"`properties` must be JSON-serializable: {e}") from e
    return dict(props)


# Naming convention (see config.py): `check_*` validate and return None; `checked_*` return the normalized value.
def check_ttl(publisher_ttl_s: float | None, value: Any) -> None:
    if publisher_ttl_s is None:
        return
    if isinstance(publisher_ttl_s, bool) or not math.isfinite(publisher_ttl_s) or not 0 < publisher_ttl_s <= MAX_TTL_S:
        raise NT4Error(f"publisher_ttl_s must be greater than 0 and at most {MAX_TTL_S} seconds.")
    if value is None:
        raise NT4Error("publisher_ttl_s requires a value; it cannot time-limit a properties-only change.")


def local_sample_time(timestamp_us: int | None, value: Any, default: bool,
                      server_time: Callable[[], tuple[int, int] | None]) -> int:
    """Server-clock sample time -> local NT clock microseconds (0 = stamp with the current time).

    `server_time` returns (estimated server time now, offset) or None while the clocks are unsynchronized."""
    if timestamp_us is None:
        return 0
    if value is None:
        raise NT4Error("timestamp_us needs a value to stamp; it cannot go with a properties-only change.")
    if default:
        raise NT4Error("timestamp_us cannot be combined with default=true: a set-default is always time 0.")
    if isinstance(timestamp_us, bool) or not isinstance(timestamp_us, int) or timestamp_us <= 0:
        raise NT4Error("timestamp_us must be a positive whole number of microseconds on the server clock.")
    clock = server_time()
    if clock is None:
        raise NT4Error("The NT4 clock is not synchronized yet, so a server timestamp cannot be converted. "
                       "Wait a moment and retry, or omit timestamp_us.")
    server_now, offset = clock
    if timestamp_us > server_now + MAX_FUTURE_US:
        raise NT4Error(f"timestamp_us is in the future (server time now is about {server_now}).")
    local = timestamp_us - offset
    if local <= 0:
        raise NT4Error("timestamp_us is before this client's clock started; use a more recent time.")
    return local


def convert_value(type_str: str, value: Any, local_time_us: int) -> Any:
    """Build the ntcore value to publish, turning conversion failures into a one-line NT4Error."""
    try:
        return make_value(type_str, value, local_time_us)
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        reason = str(e).splitlines()[0][:200]  # pybind errors are multi-line
        raise NT4Error(f"Cannot convert {value!r} to NT4 type {type_str!r}: {reason}") from e
