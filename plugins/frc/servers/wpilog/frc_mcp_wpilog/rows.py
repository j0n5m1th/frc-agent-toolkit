"""How a topic's updates are held in memory: `(time, value)` rows, compact for number topics.

A list of `(int, float)` tuples costs about 120 bytes a row, two `array`s cost 16, and a match log has millions of rows. So
`double`, `float` and `int64` topics are `NumberRows` (a read-only sequence backed by arrays); every other type stays a list.
"""

import itertools
import math
import operator
from array import array
from collections.abc import Iterable, Iterator, Sequence
from typing import Any, overload

LogRow = tuple[int, Any]  # (timestamp in microseconds, value as the live server would see it)
Rows = Sequence[LogRow]  # what a topic's rows are: a list of rows, or `NumberRows`

# The array typecode for a topic whose every entry has one of these log types (None: keep a list). `float` entries
# decode to Python floats, so they share the `double` storage without changing a value.
_FLOAT_TYPES = frozenset({"double", "float"})
_INT_TYPES = frozenset({"int64"})


def finite_numbers(rows: Iterable[LogRow]) -> list[LogRow]:
    """The rows whose value is a finite number (no text, no NaN or infinity), which a sort, a max or a percentile can take."""
    return [r for r in rows if isinstance(r[1], (int, float)) and math.isfinite(float(r[1]))]


def compact_kind(log_types: Iterable[str]) -> str | None:
    """`'d'` (floats) or `'q'` (ints) when every log type given is one number kind, else None (a mix, or not a number)."""
    kinds = set(log_types)
    if kinds and kinds <= _FLOAT_TYPES:
        return "d"
    if kinds and kinds <= _INT_TYPES:
        return "q"
    return None


class NumberRows(Sequence[LogRow]):
    """The rows of a number topic: `array('q')` times beside an `array('d')` / `array('q')` of values."""

    __slots__ = ("_times", "_values")

    def __init__(self, times: "array[int]", values: "array[Any]") -> None:
        self._times = times
        self._values = values

    def __len__(self) -> int:
        return len(self._times)

    @overload
    def __getitem__(self, index: int) -> LogRow: ...

    @overload
    def __getitem__(self, index: slice) -> "NumberRows": ...

    def __getitem__(self, index: int | slice) -> "LogRow | NumberRows":
        if isinstance(index, slice):
            return NumberRows(self._times[index], self._values[index])
        return self._times[index], self._values[index]

    def __iter__(self) -> Iterator[LogRow]:
        return zip(self._times, self._values, strict=True)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, NumberRows):
            return self._times == other._times and self._values == other._values
        if isinstance(other, (list, tuple)):
            return len(other) == len(self) and all(a == b for a, b in zip(self, other, strict=True))
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]  # mutable-looking container, like a list

    def __repr__(self) -> str:
        return f"NumberRows({list(self)!r})" if len(self) <= 8 else f"NumberRows(<{len(self)} rows>)"


class ShiftedRows(Sequence[LogRow]):
    """Another log's rows with every time moved by `shift_us`, computed as they are read (a synced view copies nothing, so it
    costs no memory beyond the rows the log itself holds, which the row budget already counts)."""

    __slots__ = ("_rows", "_shift")

    def __init__(self, rows: Rows, shift_us: int) -> None:
        self._rows = rows
        self._shift = shift_us

    def __len__(self) -> int:
        return len(self._rows)

    @overload
    def __getitem__(self, index: int) -> LogRow: ...

    @overload
    def __getitem__(self, index: slice) -> "ShiftedRows": ...

    def __getitem__(self, index: int | slice) -> "LogRow | ShiftedRows":
        if isinstance(index, slice):
            return ShiftedRows(self._rows[index], self._shift)
        t, v = self._rows[index]
        return t + self._shift, v

    def __iter__(self) -> Iterator[LogRow]:
        shift = self._shift
        return ((t + shift, v) for t, v in self._rows)

    def __repr__(self) -> str:
        return f"ShiftedRows(<{len(self)} rows>, shift_us={self._shift})"


class RowBuilder:
    """Collects one topic's rows while the file is read: into arrays for a number topic, else into a list.

    `add(time, value)` appends one row. A timestamp or value that does not fit the arrays (a corrupt record's 64-bit
    time) turns the topic into a plain list on the spot, so no row is ever lost or altered."""

    def __init__(self, kind: str | None) -> None:
        self._times: array[int] | None = array("q") if kind else None
        self._values: array[Any] | None = array(kind) if kind else None
        self._list: list[LogRow] = []

    def add(self, t: int, v: Any) -> None:
        if self._times is None or self._values is None:
            self._list.append((t, v))
            return
        try:
            self._values.append(v)
            try:
                self._times.append(t)
            except OverflowError:
                self._values.pop()
                raise
        except OverflowError:
            self._list = list(zip(self._times, self._values, strict=True))
            self._times = self._values = None
            self._list.append((t, v))

    def finish(self) -> Rows:
        """The rows, oldest first (entries restarted under new ids can interleave, so they are sorted by time)."""
        if self._times is None or self._values is None:
            return _sorted_list(self._list)
        times, values = self._times, self._values
        if any(map(operator.gt, times, itertools.islice(times, 1, None))):
            order = sorted(range(len(times)), key=times.__getitem__)  # stable, like sorting the tuples by time
            times = array("q", (times[i] for i in order))
            values = array(values.typecode, (values[i] for i in order))
        return NumberRows(times, values)


def _sorted_list(rows: list[LogRow]) -> list[LogRow]:
    if any(a[0] > b[0] for a, b in zip(rows, rows[1:], strict=False)):
        rows.sort(key=lambda row: row[0])
    return rows


def from_pairs(kind: str | None, parts: list[list[LogRow]]) -> Rows:
    """The rows of one topic from the per-entry row lists read while indexing (several when an entry restarted)."""
    builder = RowBuilder(kind)
    for part in parts:
        for t, v in part:
            builder.add(t, v)
    return builder.finish()
