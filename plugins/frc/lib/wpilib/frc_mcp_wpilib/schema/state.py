"""The one facade over this process's WPILib schema registries (struct text and protobuf descriptors).
One set of schemas per server process. They enter only through `add` and leave only through `forget` (the NT4 server's schema
listener and `Session.disconnect`); the log server reloads the schemas of the log it reads before each query. Readers use `decode`
and `field_names`, never the registries, so this is the only place that adds to or clears them.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .protos import PROTOS
from .structs import REGISTRY, SchemaError

_log = logging.getLogger(__name__)


@dataclass
class _Registries:
    """The one identity of the loaded schemas: whose they are (None when nothing is loaded), and how often they changed.

    A single mutable container keeps the module free of `global`; the two stores themselves are imported instances.
    """

    owner: str | None = None
    revision: int = 0  # bumped by every `add` and `forget`, so a reader can tell cheaply that the schemas changed


_STATE = _Registries()


def add(topic_name: str, raw: bytes) -> None:
    """Feed one `/.schema/` topic value: proto descriptors as bytes, struct schemas as UTF-8 text."""
    _STATE.revision += 1
    if topic_name.startswith("/.schema/proto:"):
        PROTOS.add(topic_name, raw)  # a serialized FileDescriptorProto, not text
    else:
        REGISTRY.add(topic_name, raw.decode("utf-8", "replace"))


def revision() -> int:
    """A number that changes whenever a schema is added or the schemas are dropped: compare it to skip needless work."""
    return _STATE.revision


def entries() -> list[tuple[str, bytes]]:
    """Every loaded schema as the (`/.schema/...` topic name, raw bytes) pairs `add` took; what `activate` loads back.

    This is what a recorded log needs to carry so a reader can decode the structs and protobuf messages in it."""
    return [*REGISTRY.entries(), *PROTOS.entries()]


def decode(type_str: str, data: bytes) -> Any:
    """The lenient form of `decode_strict`: the same decode, with SchemaError folded to None for a missing value."""
    try:
        return decode_strict(type_str, data)
    except SchemaError:
        return None


def decode_strict(type_str: str, data: bytes) -> Any:
    """Like `decode`, but raises SchemaError saying why: the type is not struct/proto, or the schema or bytes fail."""
    if type_str.startswith("struct:"):
        return REGISTRY.decode_strict(type_str, data)
    if type_str.startswith("proto:"):
        return PROTOS.decode_strict(type_str, data)
    raise SchemaError(f"{type_str!r} is not a struct or proto type, so it has no schema to decode with")


def field_names(type_str: str) -> list[str]:
    """Field names of `struct:Name` (an array suffix is allowed) or `proto:pkg.Message`.

    Raises SchemaError: when the schema has not been announced (yet) or cannot be parsed.
    """
    if type_str.startswith("struct:"):
        return REGISTRY.field_names(type_str[len("struct:"):].removesuffix("[]"))
    if type_str.startswith("proto:"):
        return PROTOS.field_names(type_str[len("proto:"):])
    raise SchemaError(f"{type_str!r} is not a struct or proto type, so it has no field names")


def forget(owner: str | None = None) -> None:
    """Drop every schema, and the owner tag. Called by `Session.disconnect`, the only unconditional clear.

    With `owner`, clear only when that owner's schemas are the loaded ones (what a WPILOG does when its log closes).
    """
    if owner is not None and _STATE.owner != owner:
        return
    _STATE.revision += 1
    REGISTRY.clear()
    PROTOS.clear()
    _STATE.owner = None


def activate(owner: str, entries: Iterable[tuple[str, bytes]]) -> bool:
    """(Re)load `entries` as the schemas of `owner`; False when `owner` is already loaded.
    Only the log server calls this (a WPILOG's owner is its own id), so a repeat of the same log is free. The NT4 server feeds
    `add` directly and clears with `forget`, which also resets the owner tag.
    """
    if _STATE.owner == owner:
        return False
    forget()
    for name, raw in entries:
        try:
            add(name, raw)
        except SchemaError as e:  # a bad schema only costs that type its decoding
            _log.debug("cannot load schema %s: %s", name, e)
    _STATE.owner = owner
    return True


def fields_by_type(types: Iterable[str]) -> dict[str, list[str]]:
    """{type: field names} for each distinct `struct:Name[]` or `proto:pkg.Message` in `types` whose schema we have.
    A type with a missing or unparseable schema is left out; the reason is only in the debug log.
    """
    out: dict[str, list[str]] = {}
    for type_str in sorted({t for t in types if t.startswith(("struct:", "proto:"))}):
        try:
            out[type_str] = field_names(type_str)
        except SchemaError as e:
            _log.debug("no field names for %s: %s", type_str, e)
    return out
