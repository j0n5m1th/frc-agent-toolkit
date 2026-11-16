"""One WPILOG file: its topics, schemas and (on demand) the rows of the topics a question needs.

Opening scans the file once for an index (topics, update counts, time span, schemas). Values are read later, only for the topics
asked about, and cached. WPILOG timestamps are microseconds on the robot's clock; the query code turns them into milliseconds
since the aligned origin (see `timeline.py`).
"""

import functools
import itertools
import json
import logging
import threading
import weakref
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wpiutil.log import DataLogReader, DataLogRecord

from frc_mcp_wpilib.schema import state as schema_state
from frc_mcp_wpilib.tooling.query_bases import TopicMatch
from frc_mcp_wpilib.values import unit_of
from frc_mcp_wpilib.wpilog_writer import SCHEMA_PREFIX

from . import dslog
from .errors import WPILogError
from .rows import LogRow, RowBuilder, Rows, compact_kind, from_pairs

_log = logging.getLogger(__name__)

NT_PREFIX = "NT:"  # WPILib's NetworkTables logger prefixes the topics it mirrors; we show them as the live server does
MAX_CACHED_ROWS = 4_000_000  # rows kept across questions, over all open logs together; the least recently used topics are dropped beyond this
PRELOAD_ROWS_PER_TOPIC = 2000  # topics with at most this many updates are read while indexing, so questions about them need no second pass
PRELOAD_TOTAL_ROWS = 600_000  # and at most this many rows in all
_HEADER_BYTES = 12  # "WPILOG", the 2-byte version and the 4-byte length of the extra header text
_NUMBER_TYPES = frozenset({"double", "float", "int64"})  # a topic restarted among these still reads as numbers
SCHEMA_TYPES = ("structschema",)  # plus protobuf descriptors, whose type starts with "proto:"

WALL_OFFSET = "wall_offset_us"  # `LogFile.derived` key: unix microseconds = log time + this, when the log knows it
__all__ = ["LogRow"]  # defined in `rows.py`; most of the package imports it from here

# Tools run their work in threads, and two things are shared between them: a log's row cache, and the process-wide schema
# registries (`activate_schemas` swaps them). One re-entrant lock serializes that work; the work is CPU-bound anyway.
LOCK = threading.RLock()
# wpiutil's DataLogReader crashes the process (an access violation inside the iteration) when two threads read logs at once, even
# different files, so every pass over a reader holds this lock. It is a leaf: while holding it nothing takes LOCK or any other
# lock (the order is LOCK, then READ_LOCK).
READ_LOCK = threading.Lock()
_OWNER_IDS = itertools.count(1)
_USES = itertools.count(1)  # a clock for "least recently used", shared by every log so topics of different logs can be ranked
_OPEN_FILES: "weakref.WeakSet[LogFile]" = weakref.WeakSet()  # the logs that may hold cached rows (guarded by LOCK)


def serialized[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Run `fn` while holding the lock that protects the shared log state."""
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with LOCK:
            return fn(*args, **kwargs)
    return wrapper


def nt_type(log_type: str) -> str:
    """The NetworkTables spelling of a WPILOG type (`int64` is `int`), which the shared value code expects."""
    if log_type == "int64":
        return "int"
    if log_type == "int64[]":
        return "int[]"
    return log_type


_DECODERS: dict[str, Callable[[DataLogRecord], Any]] = {
    "boolean": DataLogRecord.getBoolean, "int64": DataLogRecord.getInteger, "float": DataLogRecord.getFloat,
    "double": DataLogRecord.getDouble, "string": DataLogRecord.getString, "json": DataLogRecord.getString,
    "boolean[]": DataLogRecord.getBooleanArray, "int64[]": DataLogRecord.getIntegerArray,
    "float[]": DataLogRecord.getFloatArray, "double[]": DataLogRecord.getDoubleArray,
    "string[]": DataLogRecord.getStringArray,
}


def _decoder(log_type: str) -> Callable[[DataLogRecord], Any]:
    """How to read a record's payload for `log_type`; raises on a payload of the wrong size."""
    return _DECODERS.get(log_type) or (lambda rec: bytes(rec.getRaw()))  # raw, msgpack, struct:*, proto:*, ...


@dataclass
class Topic:
    """One named signal. A topic restarted under a new entry id is still one topic here."""

    name: str
    type_str: str  # NetworkTables spelling
    log_type: str
    metadata: str = ""
    count: int = 0
    first_us: int | None = None
    last_us: int | None = None
    mixed_types: tuple[str, ...] = ()  # every log type the name was started with, when they are not one number kind

    def unusable_for(self, what: str) -> str | None:
        """Why `what` cannot be computed from this topic's rows, or None: a name restarted with another type holds values of
        several types under one type string, which no reduction can read."""
        if not self.mixed_types:
            return None
        return f"{self.name!r} changed type in this log ({', '.join(self.mixed_types)}), so {what} cannot read it."

    @property
    def unit(self) -> str | None:
        """The unit a publisher put in the topic's metadata (a JSON object such as `{"unit": "m"}`), if any."""
        try:
            return unit_of(json.loads(self.metadata)) if self.metadata else None
        except ValueError:
            return None

    @property
    def hidden(self) -> bool:
        return self.name.startswith(SCHEMA_PREFIX) or "/." in self.name


@dataclass
class _IndexState:
    """The running totals of one `_index` pass, kept together so the per-record helpers stay small."""

    raw_entries: dict[int, tuple[str, str, str]] = field(default_factory=dict)  # serial -> (raw name, log type, metadata)
    counts: dict[int, list[int]] = field(default_factory=dict)  # serial -> [count, first_us, last_us]
    live: dict[int, int] = field(default_factory=dict)  # entry id -> serial of the Start record it currently refers to
    schema_entries: set[int] = field(default_factory=set)
    preload: dict[int, list[LogRow] | None] = field(default_factory=dict)  # entry -> rows so far, or None if too big to keep
    decoders: dict[int, Callable[[DataLogRecord], Any]] = field(default_factory=dict)
    start_times: list[int] = field(default_factory=list)
    decode_failures: dict[int, int] = field(default_factory=dict)  # serial -> payloads that did not decode while indexing
    preloaded: int = 0
    low: int | None = None
    high: int = 0


@dataclass(frozen=True)
class TopicFilter(TopicMatch):
    """What `LogFile.topic_rows` should list: the shared filters (`TopicMatch`) and one display switch, `counts`."""

    counts: bool = False


def _stamp_of(paths: list[Path]) -> tuple[tuple[int, int], ...]:
    """Size and modification time of each file, to notice a file changing after it was indexed."""
    return tuple((info.st_size, info.st_mtime_ns) for info in (p.stat() for p in paths))


def busiest_topics(topics: dict[str, Topic], limit: int = 5) -> list[tuple[str, int]]:
    """The visible topics with the most updates, as (name, count), most first."""
    visible = [t for t in topics.values() if not t.hidden]
    return [(t.name, t.count) for t in sorted(visible, key=lambda t: t.count, reverse=True)[:limit]]


def topic_listing(topics: dict[str, Topic], criteria: TopicFilter) -> tuple[list[dict[str, Any]], list[str]]:
    """Matching topics as `{"name", "type", "props"}` rows, and the names of matches left out for being hidden."""
    rows: list[dict[str, Any]] = []
    hidden: list[str] = []
    for topic in sorted(topics.values(), key=lambda t: t.name):
        if not topic.name.startswith(criteria.prefix) or not criteria.name_matches(topic.name):
            continue
        if not criteria.type_matches(topic.type_str):
            continue
        if topic.hidden and not criteria.include_hidden:
            hidden.append(topic.name)
            continue
        props: dict[str, Any] = {}
        if criteria.counts:
            props["n"] = topic.count
        if topic.unit:
            props["unit"] = topic.unit
        rows.append({"name": topic.name, "type": topic.type_str, "props": props})
    return rows, hidden


class LogFile:
    """A WPILOG opened for reading. Raises WPILogError if the file is not a valid log.
    `data` holds the bytes when they do not come from `path` (a Driver Station log converted in memory); `sources` are then
    the files they were made from, which `is_current` watches.
    """

    def __init__(self, path: Path, data: bytes | None = None, sources: list[Path] | None = None) -> None:
        self.path = path
        self._data = data
        self._sources = sources or [path]
        self.size_bytes = sum(p.stat().st_size for p in self._sources)
        self._data_bytes = len(data) if data is not None else self.size_bytes
        self._stamp = _stamp_of(self._sources)
        self.topics: dict[str, Topic] = {}
        self.schemas: dict[str, bytes] = {}  # "/.schema/struct:Pose2d" -> schema text or descriptor bytes
        self.records = 0
        self.skipped_records = 0  # data records for an unknown entry, or whose payload could not be read
        # the same count split by cause, so a note can say which gap occurred: `unknown_entry` (no Start record for the
        # entry id) and `decode` (ntcore rejected the payload for its declared type)
        self.skipped_reasons: dict[str, int] = {"unknown_entry": 0, "decode": 0}
        self._decode_by_topic: dict[str, int] = {}  # topic -> payloads already counted as `decode` skips (a reload must not recount)
        self._record_limit = 0  # records (control and data) `_index` used; `_load` reads the same number
        self.cut_short = False  # the file ends in the middle of a record (the robot lost power while logging)
        self.start_us = 0
        self.end_us = 0
        self._entry_topic: dict[int, str] = {}  # Start-record serial (see `_index`) -> topic name
        self._display_name: dict[str, str] = {}  # raw name in the file -> topic name shown
        self._cache: OrderedDict[str, Rows] = OrderedDict()  # topic -> rows, least recently used first
        self._used: dict[str, int] = {}  # topic -> `_USES` tick of its last use, to rank topics across logs
        self._cached_rows = 0
        self._row_kind: dict[str, str | None] = {}  # topic -> array typecode when all its entries are one number type
        self._schema_owner = f"wpilog:{next(_OWNER_IDS)}"  # one tag per opened instance, so a reopened path is never mistaken for it
        self.derived: dict[str, Any] = {}  # facts computed from the index or rows once (e.g. the enabled phases)
        self._index()

    # -- index ---------------------------------------------------------------------------------------------
    def _reader(self) -> DataLogReader:
        reader = DataLogReader(self._data, self.path.name) if self._data is not None else DataLogReader(str(self.path))
        if not reader.isValid():
            raise WPILogError(f"{self.path.name} is not a valid WPILOG file (bad header).")
        return reader

    def _index(self) -> None:
        with READ_LOCK:
            header_len = len(self._reader().getExtraHeader().encode())
        if self._data_bytes <= _HEADER_BYTES + header_len:
            self._record_limit = 0
            return  # no records (the reader would invent one)
        # One pass over the file. When the file ends in the middle of a record, the reader repeats the record before it, so
        # the last record it returns is not in the file; that is checked once, at the end. A cut-short file (rare) is then
        # indexed again, stopping before the repeat, and `_record_limit` makes `_load` stop at the same record.
        state, total, ends_cleanly = self._scan(None)
        self.cut_short = not ends_cleanly
        self._record_limit = total - 1 if self.cut_short else total
        if self.cut_short:
            state, _, _ = self._scan(self._record_limit)  # only one native reader is ever alive at a time (two crashed)
        self._build_topics(state.raw_entries, state.counts)
        for entry, n in state.decode_failures.items():
            name = self._entry_topic[entry]
            self._decode_by_topic[name] = self._decode_by_topic.get(name, 0) + n
        self._cache_preloaded(state.preload)
        self.start_us = state.low if state.low is not None else min(state.start_times, default=0)
        self.end_us = state.high if state.low is not None else self.start_us

    def _scan(self, limit: int | None) -> tuple[_IndexState, int, bool]:
        """Fold up to `limit` records (all when None) into a fresh index. Returns it, the number of records read, and whether
        the last of them is the file's final bytes (see `_ends_with`: the reader and its record are gone when this returns)."""
        # An entry id can be reused after its Finish record, so everything is keyed by a serial number per Start record.
        state = _IndexState()
        self.records = self.skipped_records = 0
        self.skipped_reasons = {"unknown_entry": 0, "decode": 0}
        self.schemas.clear()
        total = 0
        last = None
        with READ_LOCK:
            for rec in itertools.islice(self._reader(), limit):
                total, last = total + 1, rec
                if rec.isControl():
                    self._index_control(rec, state)
                else:
                    self._index_data(rec, state)
            return state, total, last is None or self._ends_with(last)

    def _index_control(self, rec: DataLogRecord, state: _IndexState) -> None:
        """Fold one control record (Start / Finish / SetMetadata) into the index being built."""
        if rec.isStart():
            data = rec.getStartData()
            serial = len(state.raw_entries)
            state.live[data.entry] = serial
            state.raw_entries[serial] = (data.name, data.type, data.metadata)
            state.start_times.append(rec.getTimestamp())
            if data.type in SCHEMA_TYPES or (data.type.startswith("proto:") and SCHEMA_PREFIX in data.name):
                state.schema_entries.add(serial)
            else:
                state.preload[serial] = []
                state.decoders[serial] = _decoder(data.type)
        elif rec.isFinish():
            state.live.pop(rec.getFinishEntry(), None)
        elif rec.isSetMetadata():
            meta = rec.getSetMetadataData()
            started = state.live.get(meta.entry)
            if started is not None:
                name, type_str, _ = state.raw_entries[started]
                state.raw_entries[started] = (name, type_str, meta.metadata)

    def _skip(self, reason: str) -> None:
        """Count one unreadable data record once, in the total and under its cause."""
        self.skipped_records += 1
        self.skipped_reasons[reason] += 1

    def _index_data(self, rec: DataLogRecord, state: _IndexState) -> None:
        """Fold one data record into the index: its count, the time span, and its row while preloading lasts."""
        self.records += 1
        entry = state.live.get(rec.getEntry())
        if entry is None:
            self._skip("unknown_entry")
            return
        t = rec.getTimestamp()
        seen = state.counts.get(entry)
        if seen is None:
            state.counts[entry] = [1, t, t]
        else:
            seen[0] += 1
            seen[1] = min(seen[1], t)
            seen[2] = max(seen[2], t)
        state.low = t if state.low is None else min(state.low, t)
        state.high = max(state.high, t)
        if entry in state.schema_entries:
            self._keep_schema(state.raw_entries[entry][0], rec)
            return
        kept = state.preload.get(entry)
        if kept is None:
            return
        if len(kept) >= PRELOAD_ROWS_PER_TOPIC or state.preloaded >= PRELOAD_TOTAL_ROWS:
            state.preloaded -= len(kept)
            state.preload[entry] = None  # too big to hold: `rows` will read it from the file later
        else:
            try:
                kept.append((t, state.decoders[entry](rec)))
                state.preloaded += 1
            except (TypeError, ValueError):  # ntcore raises these when a payload does not match its declared type
                self._skip("decode")
                state.decode_failures[entry] = state.decode_failures.get(entry, 0) + 1

    def _ends_with(self, last: DataLogRecord) -> bool:
        """Whether the file's final bytes are the last record the reader returned. A power loss leaves a partial record, and
        the reader then repeats the record before it, so the file's final bytes are not that record's. The header's field
        widths are not known (a writer may use wider fields than it needs), so every width that can hold the values is tried."""
        entry = 0 if last.isControl() else last.getEntry()
        mask = (1 << 64) - 1  # the reader's numbers are signed; a corrupt timestamp of 2**63 or more reads as negative
        values = (entry & mask, last.getSize() & mask, last.getTimestamp() & mask)
        size = values[1]
        if size > self._data_bytes:
            return False
        minimal = [max(1, (value.bit_length() + 7) // 8) for value in values]
        tail = self._tail(min(self._data_bytes, size + 1 + 4 + 4 + 8))
        if len(tail) < size:
            return False
        for widths in itertools.product(range(minimal[0], 5), range(minimal[1], 5), range(minimal[2], 9)):
            header = bytes([(widths[0] - 1) | ((widths[1] - 1) << 2) | ((widths[2] - 1) << 4)])
            for value, width in zip(values, widths, strict=True):
                header += value.to_bytes(width, "little")
            if len(header) + size <= len(tail) and tail[len(tail) - size - len(header):len(tail) - size] == header:
                return True
        return False

    def _tail(self, count: int) -> bytes:
        """The last `count` bytes of the file (of the converted bytes for an in-memory log)."""
        if self._data is not None:
            return bytes(self._data[len(self._data) - count:])
        with self.path.open("rb") as f:
            f.seek(-count, 2)
            return f.read(count)

    def _keep_schema(self, raw_name: str, rec: DataLogRecord) -> None:
        name = raw_name.removeprefix(NT_PREFIX)
        self.schemas[name] = bytes(rec.getRaw())

    def _build_topics(self, raw_entries: dict[int, tuple[str, str, str]], counts: dict[int, list[int]]) -> None:
        names = {raw for raw, _, _ in raw_entries.values()}

        def display(raw: str) -> str:
            stripped = raw.removeprefix(NT_PREFIX)
            return stripped if stripped == raw or stripped not in names else raw  # keep NT: only to avoid a name clash

        types: dict[str, set[str]] = {}  # topic -> every log type it was started with
        for entry, (raw, log_type, metadata) in raw_entries.items():
            name = display(raw)
            types.setdefault(name, set()).add(log_type)
            self._entry_topic[entry] = name
            self._display_name[raw] = name
            topic = self.topics.setdefault(name, Topic(name, nt_type(log_type), log_type, metadata))
            if metadata and not topic.metadata:
                topic.metadata = metadata
            if entry in counts:
                count, first, last = counts[entry]
                topic.count += count
                topic.first_us = first if topic.first_us is None else min(topic.first_us, first)
                topic.last_us = last if topic.last_us is None else max(topic.last_us, last)
        self._row_kind = {name: compact_kind(kinds) for name, kinds in types.items()}
        for name, kinds in types.items():
            if len(kinds) > 1 and not kinds <= _NUMBER_TYPES:
                self.topics[name].mixed_types = tuple(sorted(kinds))

    def _cache_preloaded(self, preload: dict[int, list[LogRow] | None]) -> None:
        """Keep the rows read while indexing for every topic whose entries were all small enough to keep whole.

        Runs on the opening thread outside every other lock, while another thread may be evicting over every open log's cache,
        so the rows are built first and published under LOCK (the global lock may be held while taking `OpenLogs._lock`, never
        the reverse; `load` runs outside both)."""
        by_topic: dict[str, list[list[LogRow] | None]] = {}
        for entry, part_rows in preload.items():
            by_topic.setdefault(self._entry_topic[entry], []).append(part_rows)
        built = {
            name: from_pairs(self._row_kind.get(name), [part for part in parts if part is not None])
            for name, parts in by_topic.items() if all(part is not None for part in parts)
        }
        with LOCK:
            for name, built_rows in built.items():
                self._store(name, built_rows)

    def is_current(self) -> bool:
        """Whether the file is still the one that was indexed (a log being written grows; a replaced one differs)."""
        try:
            return _stamp_of(self._sources) == self._stamp
        except OSError:
            return False

    # -- facts ---------------------------------------------------------------------------------------------
    @property
    def duration_s(self) -> float:
        return (self.end_us - self.start_us) / 1e6

    def busiest(self, limit: int = 5) -> list[tuple[str, int]]:
        return busiest_topics(self.topics, limit)

    # -- topic listing (the shape `topic_pages` wants) -----------------------------------------------------
    def topic_rows(self, criteria: TopicFilter) -> tuple[list[dict[str, Any]], list[str]]:
        """Matching topics as `{"name", "type", "props"}` rows, and the names of matches left out for being hidden."""
        return topic_listing(self.topics, criteria)

    # -- schemas -------------------------------------------------------------------------------------------
    def activate_schemas(self) -> None:
        """Make this log's struct and protobuf schemas the ones values are decoded with (one log at a time)."""
        schema_state.activate(self._schema_owner, self.schemas.items())

    @serialized
    def release(self) -> None:
        """Drop the cached rows, and the loaded schemas when they are this log's (called when the log is closed)."""
        self._cache.clear()
        self._used.clear()
        self._cached_rows = 0
        _OPEN_FILES.discard(self)
        schema_state.forget(self._schema_owner)  # only clears when these are still this log's schemas

    # -- rows ----------------------------------------------------------------------------------------------
    @serialized
    def rows(self, names: Iterable[str]) -> dict[str, Rows]:
        """The updates of each named topic, oldest first (unknown names are left out). Reads the file for new topics only,
        and all of them in one pass: ask for every topic a question needs in one call."""
        wanted = [n for n in dict.fromkeys(names) if n in self.topics]
        missing = [n for n in wanted if n not in self._cache]
        if missing:
            if self._data is None and not self.is_current():  # a read of the file would not match the index made from it
                raise WPILogError(f"{self.path.name} changed on disk since it was opened; call wpilog_open on it again.")
            self._load(missing)
        for name in wanted:
            self._cache.move_to_end(name)
            self._used[name] = next(_USES)
        _evict(self, keep=set(wanted))
        return {name: self._cache[name] for name in wanted}

    def _store(self, name: str, rows: Rows) -> None:
        """Cache one topic's rows as the most recently used."""
        self._cache[name] = rows
        self._cached_rows += len(rows)
        self._used[name] = next(_USES)
        _OPEN_FILES.add(self)

    def _drop(self, name: str) -> None:
        self._cached_rows -= len(self._cache.pop(name))
        self._used.pop(name, None)

    def _load(self, names: list[str]) -> None:
        targets = set(names)
        builders = {name: RowBuilder(self._row_kind.get(name)) for name in names}
        bad: dict[str, int] = {}
        live: dict[int, tuple[str, Callable[[DataLogRecord], Any]]] = {}  # entry id -> (topic, decoder) for wanted entries
        with READ_LOCK:  # see READ_LOCK: one pass over a native reader at a time
            for rec in itertools.islice(self._reader(), self._record_limit):  # the records `_index` used, not a cut-short repeat
                if rec.isControl():
                    if rec.isStart():
                        data = rec.getStartData()
                        topic = self._display_name.get(data.name)
                        if topic in targets:
                            live[data.entry] = (topic, _decoder(data.type))
                        else:
                            live.pop(data.entry, None)
                    elif rec.isFinish():
                        live.pop(rec.getFinishEntry(), None)
                    continue
                target = live.get(rec.getEntry())
                if target is None:
                    continue
                topic, decode = target
                try:
                    builders[topic].add(rec.getTimestamp(), decode(rec))
                except (TypeError, ValueError):  # ntcore raises these when a payload does not match its declared type
                    bad[topic] = bad.get(topic, 0) + 1
        for topic, n in bad.items():  # count only payloads not counted before: a topic evicted and re-read has the same bad ones
            for _ in range(n - self._decode_by_topic.get(topic, 0)):
                self._skip("decode")
            self._decode_by_topic[topic] = max(n, self._decode_by_topic.get(topic, 0))
        for name, builder in builders.items():
            self._store(name, builder.finish())


def _evict(current: LogFile, keep: set[str]) -> None:
    """Drop the least recently used cached topics, of any open log, until the rows held are within MAX_CACHED_ROWS.

    The topics just asked for (`keep`, in `current`) stay, even when they alone are over the limit. Call under LOCK."""
    logs = list(_OPEN_FILES)
    while sum(log._cached_rows for log in logs) > MAX_CACHED_ROWS:
        oldest: tuple[int, LogFile, str] | None = None
        for log in logs:
            name = next((n for n in log._cache if log is not current or n not in keep), None)  # its LRU topic that may go
            if name is not None and (oldest is None or log._used.get(name, 0) < oldest[0]):
                oldest = (log._used.get(name, 0), log, name)
        if oldest is None:
            return
        oldest[1]._drop(oldest[2])


def load(path: Path) -> LogFile:
    """Open `path`: a `.wpilog` as it is, or a Driver Station log (`.dslog`/`.dsevents`) converted in memory.

    Raises WPILogError: when the file is not a valid log.
    """
    if not dslog.is_ds(path):
        return LogFile(path)
    converted = dslog.convert(path)
    log = LogFile(dslog.canonical(path), converted.data, converted.sources)
    log.cut_short |= converted.cut_short
    log.derived[WALL_OFFSET] = converted.start_unix_us  # the Driver Station laptop's clock at the log's time zero
    return log
