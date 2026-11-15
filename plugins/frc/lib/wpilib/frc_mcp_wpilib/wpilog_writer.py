"""A small WPILOG writer: the live server's capture logs, the practice log (`frc-mcp demo log`) and test logs. It exists because
`wpiutil` exposes a reader but no writer in Python. It only builds bytes (`to_bytes`, `drain`) or saves a whole log (`save`).
Format: https://github.com/wpilibsuite/allwpilib/blob/main/wpiutil/doc/datalog.adoc (version 1.0).
"""

import struct
from pathlib import Path
from typing import Any

from .files import write_atomic

MAGIC = b"WPILOG"
VERSION = 0x0100
SUFFIX = ".wpilog"  # the one file name extension a WPILOG carries, for writer and readers alike
SCHEMA_PREFIX = "/.schema/"  # the topic prefix WPILib gives a schema record
_START, _FINISH, _SET_METADATA = 0, 1, 2

_PACK = {"boolean": "?", "int64": "<q", "float": "<f", "double": "<d"}  # scalar type -> struct format
_TEXT_TYPES = ("string", "json")  # the two types whose payload is their UTF-8 text (json[] uses the array rule)


def _le(value: int, size: int) -> bytes:
    return value.to_bytes(size, "little")


def _size_needed(value: int) -> int:
    return max(1, (value.bit_length() + 7) // 8)


def _text(s: str) -> bytes:
    raw = s.encode("utf-8")
    return _le(len(raw), 4) + raw


class LogWriter:
    """Builds a WPILOG in memory; `save(path)` writes it all, or `drain()` hands over what is new for streaming into a file.
    Timestamps are microseconds. Declare entries with `start`, feed them with `append`; `schema` registers a struct or protobuf
    schema the way WPILib's loggers do (a `/.schema/<name>` entry with one record).
    """

    def __init__(self, extra_header: str = "") -> None:
        self._buf = bytearray(MAGIC + _le(VERSION, 2) + _le(len(extra_header.encode()), 4) + extra_header.encode())
        self._types: dict[int, str] = {}
        self._next_id = 1
        self._schemas: set[str] = set()

    # -- records ---------------------------------------------------------------
    def _record(self, entry: int, t_us: int, payload: bytes) -> None:
        id_len, size_len, ts_len = _size_needed(entry), _size_needed(len(payload)), _size_needed(t_us)
        if t_us < 0 or id_len > 4 or size_len > 4 or ts_len > 8:  # the header's length fields would wrap and corrupt the record
            raise ValueError(f"a WPILOG record cannot hold entry {entry}, {len(payload)} bytes and time {t_us} us")
        self._buf.append((id_len - 1) | ((size_len - 1) << 2) | ((ts_len - 1) << 4))
        self._buf += _le(entry, id_len) + _le(len(payload), size_len) + _le(t_us, ts_len) + payload

    def start(self, name: str, type_str: str, metadata: str = "", t_us: int = 0) -> int:
        """Declare an entry and return its id."""
        entry = self._next_id
        self._next_id += 1
        self._types[entry] = type_str
        self._record(0, t_us, bytes([_START]) + _le(entry, 4) + _text(name) + _text(type_str) + _text(metadata))
        return entry

    def finish(self, entry: int, t_us: int) -> None:
        self._record(0, t_us, bytes([_FINISH]) + _le(entry, 4))

    def set_metadata(self, entry: int, metadata: str, t_us: int) -> None:
        self._record(0, t_us, bytes([_SET_METADATA]) + _le(entry, 4) + _text(metadata))

    def append(self, entry: int, value: Any, t_us: int) -> None:
        """Add one data record; `value` is encoded according to the type the entry was started with."""
        self._record(entry, t_us, encode(self._types[entry], value))

    def schema(self, type_name: str, schema: bytes | str, kind: str = "structschema", t_us: int = 0) -> None:
        """Register `struct:<type_name>` (kind `structschema`) or a protobuf file (kind `proto:FileDescriptorProto`)."""
        if type_name in self._schemas:
            return
        self._schemas.add(type_name)
        entry = self.start(f"/.schema/{type_name}", kind, "", t_us)
        self._record(entry, t_us, schema.encode() if isinstance(schema, str) else schema)

    # -- output ----------------------------------------------------------------
    def to_bytes(self) -> bytes:
        return bytes(self._buf)

    def drain(self) -> bytes:
        """Everything written since the last `drain` (the first call includes the file header), then forget it.

        Streaming and `to_bytes`/`save` do not mix: after a drain, those return only what is still held."""
        out = bytes(self._buf)
        self._buf.clear()
        return out

    def save(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(out, self.to_bytes())  # atomic: a reader never sees a half-written log
        return out


def encode(type_str: str, value: Any) -> bytes:
    """The WPILOG payload for `value` of type `type_str` (raw/struct/proto values pass through as bytes). Mirrors the reader's
    table in `frc_mcp_wpilog.logfile`; `json` shares the string encoder and `json[]` the array rule.
    Raises struct.error, TypeError, ValueError or OverflowError for a value that does not fit its declared type.
    """
    if type_str in _PACK:
        return struct.pack(_PACK[type_str], value)
    if type_str in _TEXT_TYPES:
        return str(value).encode("utf-8")
    if type_str.endswith("[]"):
        base = type_str[:-2]
        if base in _TEXT_TYPES:  # string[] and json[]: a count, then each value's UTF-8 text
            return _le(len(value), 4) + b"".join(_text(v) for v in value)
        if base in _PACK and not type_str.startswith("struct:"):
            fmt = _PACK[base]
            return b"".join(struct.pack(fmt, v) for v in value)
    return bytes(value)  # raw, msgpack, struct:*, proto:*


__all__ = ["MAGIC", "VERSION", "LogWriter", "encode"]
