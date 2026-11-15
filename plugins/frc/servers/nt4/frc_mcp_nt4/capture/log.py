"""Optional WPILOG file of every update a capture receives, bounded in size, and where those files are kept.
It is an ordinary `.wpilog`, so the log server (`mcp-wpilib-wpilog`) opens it with the same tools as any match log.
"""

import struct
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any, BinaryIO

from frc_mcp_wpilib.schema import state as schema_state
from frc_mcp_wpilib.wpilog_writer import SCHEMA_PREFIX, SUFFIX, LogWriter

from .. import config
from ..errors import NT4Error
from .types import MIB

MAX_LOG_BYTES = 16 * MIB  # maximum bytes written to one log file
KEEP_LOGS = 20  # new logs are refused once this many exist in the log directory
_LOG_TYPES = {"int": "int64", "int[]": "int64[]"}  # NT4 spells these differently from WPILOG; every other type is the same
_ENCODE_ERRORS = (struct.error, TypeError, ValueError, OverflowError)  # a value that does not fit its declared type


def _is_schema_carrier(type_str: str) -> bool:
    """Whether values of this type can only be decoded with a schema, so the log must carry the schemas."""
    return type_str.startswith(("struct:", "proto:"))


class CaptureLog:
    """Writes a capture's updates to the WPILOG at `path`; does nothing without one. Callers hold the capture's lock.

    Topics are logged as `NT:<name>` (as WPILib does on a robot) at their time on the capture's timeline. Struct and
    protobuf schemas are written as `/.schema/...` entries so the log server can decode them.
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.file: BinaryIO | None = None
        self.bytes = 0
        self.truncated = False
        self.error: str | None = None
        self.dropped_nonfinite_time = 0  # updates not written because they have no time on the log's timeline
        self._writer = LogWriter()
        self._entries: dict[str, tuple[str, int]] = {}  # topic name -> (its NT4 type, WPILOG entry id)
        self._needs_schemas = False
        self._schema_revision = -1  # the schema_state.revision() whose schemas are in the log

    def open(self) -> None:
        if not self.path:
            return
        try:
            self.file = self.path.open("xb")
        except OSError as e:
            raise NT4Error(f"Cannot create capture log {self.path}: {e}") from e

    def write(self, name: str, type_str: str, t_us: int | None, raw: Any) -> None:
        """Log one update of topic `name` (NT4 type `type_str`) at `t_us` microseconds into the capture. An update with no time
        (None) is not written, since any time given to it would be made up; it is counted in `dropped_nonfinite_time`."""
        if self.file is None:
            return
        if t_us is None:
            self.dropped_nonfinite_time += 1
            return
        try:
            if name not in self._entries or self._entries[name][0] != type_str:
                self._start_entry(name, type_str, t_us)
            self._writer.append(self._entries[name][1], raw, t_us)
            self._flush()
        except _ENCODE_ERRORS as e:
            self._fail(f"cannot encode a {type_str} value of {name} for the log: {e}")
        except OSError as e:
            self._fail(str(e))

    def _start_entry(self, name: str, type_str: str, t_us: int) -> None:
        if _is_schema_carrier(type_str):
            self._needs_schemas = True
            self._write_schemas()  # before the first struct value, so even a log cut short can decode what it holds
        if name in self._entries:  # the topic came back with another type: end the old entry, as a robot's logger does
            self._writer.finish(self._entries[name][1], t_us)
        entry = self._writer.start(f"NT:{name}", _LOG_TYPES.get(type_str, type_str), "", t_us)
        self._entries[name] = (type_str, entry)

    def _write_schemas(self) -> None:
        """Add every schema loaded so far that the log does not have yet (the writer skips repeats); nothing to do when
        none was added since the last call."""
        revision = schema_state.revision()
        if revision == self._schema_revision:
            return
        self._schema_revision = revision
        for topic, raw in schema_state.entries():
            type_name = topic.removeprefix(SCHEMA_PREFIX)
            kind = "structschema" if type_name.startswith("struct:") else "proto:FileDescriptorProto"
            self._writer.schema(type_name, raw, kind)

    def _flush(self) -> None:
        """Move what the writer holds into the file, unless that would pass the size cap (then logging stops)."""
        data = self._writer.drain()
        if self.file is None:
            return
        if self.bytes + len(data) > MAX_LOG_BYTES:
            self.truncated = True
            self._close_file()
            return
        self.file.write(data)
        self.bytes += len(data)

    def _fail(self, message: str) -> None:
        self.error = message
        self._close_file()

    def _close_file(self) -> None:
        with suppress(OSError):
            if self.file is not None:
                self.file.close()
        self.file = None

    def close(self) -> None:
        if self.file is None:
            return
        try:
            if self._needs_schemas:
                self._write_schemas()  # schemas announced after the first struct value
                self._flush()
        except OSError as e:
            self.error = str(e)
        if self.file is not None:
            try:
                self.file.close()
            except OSError as e:
                self.error = str(e)
            self.file = None


def new_log_path(cid: str) -> Path:
    """A fresh log file path for capture `cid` in `config.log_dir()`.

    Raises NT4Error: when the log directory cannot be prepared, or when KEEP_LOGS logs already exist.
    """
    log_dir = config.log_dir()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        if sum(1 for _ in log_dir.glob(f"nt4-capture-*{SUFFIX}")) >= KEEP_LOGS:
            raise NT4Error(f"Capture log limit ({KEEP_LOGS}) reached in {log_dir}; move or delete old logs before recording.")
    except OSError as e:
        raise NT4Error(f"Cannot prepare capture log directory {log_dir}: {e}") from e
    return log_dir / f"nt4-capture-{time.strftime('%Y%m%d-%H%M%S')}-{cid}-{uuid.uuid4().hex}{SUFFIX}"
