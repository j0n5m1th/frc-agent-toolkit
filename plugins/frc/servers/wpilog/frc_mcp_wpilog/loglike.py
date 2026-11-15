"""What the query and tool code reads from "a log": a `LogFile`, or a `SyncedLog` (several logs on one clock).

Neither class inherits from the other. Code that works on either is typed against `LogLike`."""

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

from .logfile import Topic, TopicFilter
from .rows import Rows


class LogLike(Protocol):
    path: Path  # a synced view's is just its id
    topics: dict[str, Topic]
    schemas: dict[str, bytes]
    derived: dict[str, Any]
    size_bytes: int
    records: int
    skipped_records: int
    skipped_reasons: dict[str, int]
    cut_short: bool
    start_us: int
    end_us: int

    @property
    def duration_s(self) -> float: ...

    def busiest(self, limit: int = 5) -> list[tuple[str, int]]: ...

    def topic_rows(self, criteria: TopicFilter) -> tuple[list[dict[str, Any]], list[str]]: ...

    def rows(self, names: Iterable[str]) -> dict[str, Rows]: ...

    def activate_schemas(self) -> None: ...

    def release(self) -> None: ...

    def is_current(self) -> bool: ...
