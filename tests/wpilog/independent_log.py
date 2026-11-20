"""A second WPILOG reader, written from the format spec (wpiutil/doc/datalog.adoc, version 1.0), to check the log server against.

The server reads logs through wpiutil and its own `logfile.py`. A test that reads a log with the server and checks the answer with the server
proves little, because a misreading would repeat. The tests in `test_wpilog_independent.py` compare the server with this reader and with Python's
`statistics`. The bytes under test come from our `LogWriter`, whose own round trip through wpiutil is checked in `tests/common/test_wpilog_writer.py`;
a real robot log would add one more check.
"""

import struct
from typing import Any

SCALARS = {"boolean": ("?", 1), "int64": ("<q", 8), "float": ("<f", 4), "double": ("<d", 8)}


def _uint(data: bytes) -> int:
    return int.from_bytes(data, "little")


def _text(payload: bytes, at: int) -> tuple[str, int]:
    size = _uint(payload[at:at + 4])
    return payload[at + 4:at + 4 + size].decode("utf-8"), at + 4 + size


def _value(type_str: str, payload: bytes) -> Any:
    """A scalar or text decoded; anything else (arrays, structs) as its raw bytes, for the test to unpack."""
    if type_str == "string":
        return payload.decode("utf-8")
    if type_str in SCALARS:
        return struct.unpack(SCALARS[type_str][0], payload)[0]
    return payload


def parse(data: bytes) -> dict[str, list[tuple[int, Any]]]:
    """Every topic name -> its `(microseconds, value)` updates in file order, from a complete log."""
    assert data[:6] == b"WPILOG" and _uint(data[6:8]) == 0x0100, "not a version 1.0 WPILOG"
    at = 12 + _uint(data[8:12])
    entries: dict[int, tuple[str, str]] = {}  # entry id -> (name, type)
    samples: dict[str, list[tuple[int, Any]]] = {}
    while at < len(data):
        bits = data[at]
        id_len, size_len, ts_len = (bits & 3) + 1, ((bits >> 2) & 3) + 1, ((bits >> 4) & 7) + 1
        head = 1 + id_len + size_len + ts_len
        entry = _uint(data[at + 1:at + 1 + id_len])
        size = _uint(data[at + 1 + id_len:at + 1 + id_len + size_len])
        t_us = _uint(data[at + 1 + id_len + size_len:at + head])
        payload = data[at + head:at + head + size]
        assert len(payload) == size, "the log is cut short"
        at += head + size
        if entry != 0:
            name, type_str = entries[entry]
            samples[name].append((t_us, _value(type_str, payload)))
        elif payload[0] == 0:  # a start record; finish and metadata records change nothing here
            name, pos = _text(payload, 5)
            entries[_uint(payload[1:5])] = (name, _text(payload, pos)[0])
            samples.setdefault(name, [])
    return samples
