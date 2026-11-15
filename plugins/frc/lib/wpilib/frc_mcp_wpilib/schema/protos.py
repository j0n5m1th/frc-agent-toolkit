"""Decode WPILib protobuf values (`proto:<package.Message>`) from the descriptors the server publishes.
Each `.proto` file is a serialized `FileDescriptorProto` on `/.schema/proto:<file>.proto`. We collect those, build a descriptor pool
on demand, and flatten a decoded message to dotted keys (`translation.x`) like `structs.py`. Anything we cannot decode stays hex.
"""

# protobuf types are annotations only: protobuf itself loads on the first descriptor (a server that never meets a proto topic never imports it)
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from google.protobuf import descriptor_pb2, descriptor_pool
    from google.protobuf.descriptor import Descriptor
    from google.protobuf.message import Message

from .structs import SchemaError, debug_once

MAX_REJECTED = 10  # undecodable schema topics remembered for error messages
SCHEMA_PREFIX = "/.schema/proto:"
MAX_DEPTH = 8
_MESSAGE_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")  # a dotted protobuf full name, no array suffix


class ProtoRegistry:
    """Descriptors by .proto file name, filled from `/.schema/proto:*` topics; the pool is built lazily."""

    def __init__(self) -> None:
        self._files: dict[str, descriptor_pb2.FileDescriptorProto] = {}
        self._pool: descriptor_pool.DescriptorPool | None = None
        self._rejected: list[str] = []  # schema topics whose bytes were not a descriptor, to explain a later miss

    def entries(self) -> list[tuple[str, bytes]]:
        """Every loaded descriptor as the (`/.schema/proto:<file>.proto`, serialized FileDescriptorProto) pair `add` took."""
        return [(f"/.schema/proto:{name}", proto.SerializeToString()) for name, proto in self._files.items()]

    def clear(self) -> None:
        self._files.clear()
        self._rejected.clear()
        self._pool = None

    def add(self, topic_name: str, data: bytes) -> None:
        """Remember one schema topic's value (ignored unless it is a `/.schema/proto:*` topic holding a descriptor)."""
        if not topic_name.startswith(SCHEMA_PREFIX):
            return
        from google.protobuf import descriptor_pb2
        from google.protobuf.message import DecodeError

        proto = descriptor_pb2.FileDescriptorProto()
        try:
            proto.ParseFromString(data)
        except DecodeError:
            self._reject(topic_name)
            return
        if proto.name:
            self._files[proto.name] = proto
            self._pool = None

    def _reject(self, topic_name: str) -> None:
        if topic_name not in self._rejected and len(self._rejected) < MAX_REJECTED:
            self._rejected.append(topic_name)

    def _get_pool(self) -> descriptor_pool.DescriptorPool:
        if self._pool is None:
            from google.protobuf import descriptor_pool

            pool = descriptor_pool.DescriptorPool()
            pending = dict(self._files)
            added: set[str] = set()
            while True:  # add files whose dependencies are in; files with missing dependencies stay out
                ready = [n for n, f in pending.items() if all(d in added for d in f.dependency)]
                if not ready:
                    break
                for name in ready:
                    try:
                        pool.AddSerializedFile(pending.pop(name).SerializeToString())
                        added.add(name)
                    except (TypeError, ValueError, KeyError):
                        pending.pop(name, None)
                        self._reject(SCHEMA_PREFIX + name)  # the pool refused this file; say so in the next miss
            for name in pending:  # never added: a dependency did not arrive (or never could be built)
                self._reject(SCHEMA_PREFIX + name)
            self._pool = pool
        return self._pool

    def _descriptor(self, full_name: str) -> Descriptor:
        try:
            return self._get_pool().FindMessageTypeByName(full_name)
        except KeyError as e:
            hint = f" (undecodable schema topics: {', '.join(self._rejected)})" if self._rejected else ""
            raise SchemaError(f"no descriptor for protobuf message {full_name!r}{hint}") from e

    def decode(self, type_str: str, data: bytes) -> dict[str, Any] | None:
        """`proto:<package.Message>` bytes -> flat dict, or None if the type or bytes cannot be decoded."""
        try:
            return self.decode_strict(type_str, data)
        except SchemaError as e:
            debug_once(type_str, e)
            return None

    def decode_strict(self, type_str: str, data: bytes) -> dict[str, Any]:
        """Like `decode`, but raises SchemaError saying why the type or the bytes cannot be decoded."""
        name = type_str.removeprefix("proto:")
        if not type_str.startswith("proto:") or not _MESSAGE_NAME.fullmatch(name):
            raise SchemaError(f"{type_str!r} is not a plain proto:<Message> type")
        from google.protobuf import message_factory
        from google.protobuf.message import DecodeError

        try:
            message = message_factory.GetMessageClass(self._descriptor(name))()
            message.ParseFromString(data)
        except DecodeError as e:
            raise SchemaError(f"malformed protobuf bytes for {type_str!r}: {e}") from e
        return _flatten(message)

    def field_names(self, name: str) -> list[str]:
        """Dotted names a decoded value of this message will have (singular nested messages flattened)."""
        return _names(self._descriptor(name), 0)


def _repeated(field: Any) -> bool:
    from google.protobuf.descriptor import FieldDescriptor

    return field.is_repeated if hasattr(field, "is_repeated") else field.label == FieldDescriptor.LABEL_REPEATED


def _names(descriptor: Descriptor, depth: int) -> list[str]:
    if depth > MAX_DEPTH:
        raise SchemaError(f"protobuf message {descriptor.full_name!r} nests too deeply")
    out: list[str] = []
    for field in descriptor.fields:
        if field.message_type is not None and not _repeated(field):
            out.extend(f"{field.name}.{k}" for k in _names(field.message_type, depth + 1))
        else:
            out.append(field.name)
    return out


def _scalar(field: Any, value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if field.enum_type is not None:
        entry = field.enum_type.values_by_number.get(value)
        return entry.name if entry is not None else value
    return value


def _value(field: Any, value: Any, depth: int) -> Any:
    if field.message_type is not None:
        return _flatten(value, depth + 1)
    return _scalar(field, value)


def _flatten(message: Message, depth: int = 0) -> dict[str, Any]:
    if depth > MAX_DEPTH:
        raise SchemaError("protobuf message nests too deeply")
    out: dict[str, Any] = {}
    for field in message.DESCRIPTOR.fields:
        value = getattr(message, field.name)
        if field.message_type is not None and field.message_type.GetOptions().map_entry:
            value_field = field.message_type.fields_by_name["value"]
            out[field.name] = {str(k): _value(value_field, v, depth) for k, v in value.items()}
        elif _repeated(field):
            out[field.name] = [_value(field, v, depth) for v in value]
        elif field.message_type is not None:
            out.update({f"{field.name}.{k}": v for k, v in _flatten(value, depth + 1).items()})
        else:
            out[field.name] = _scalar(field, value)
    return out


PROTOS = ProtoRegistry()  # process-wide like structs.REGISTRY; reached only via schema/state.py, cleared by state.forget
