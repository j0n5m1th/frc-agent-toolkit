"""Several open logs put on one clock and read as one `SyncedLog` (a robot `.wpilog`, a Driver Station log, a converted `.hoot`).

`wpilog_sync` finds the offset that turns each log's time into the first log's time. Methods: `enable` (line up the
enable/disable edges, error is tens of ms), `clock` (wall-clock times, a fallback or a cross-check), `manual` (the caller
gives each offset). Why edges come first: docs/ARCHITECTURE.md.
"""

import bisect
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from frc_mcp_wpilib.schema import state as schema_state

from . import timeline
from .errors import WPILogError
from .logfile import (
    WALL_OFFSET,
    Topic,
    TopicFilter,
    busiest_topics,
    serialized,
    topic_listing,
)
from .loglike import LogLike
from .rows import LogRow, Rows, ShiftedRows

Method = Literal["auto", "clock", "enable"]
EDGE_TOLERANCE_US = 250_000  # edges this close after shifting count as the same moment
CANDIDATE_EDGES = 10  # offsets are tried from this many of the other log's first rising edges (they must match some edge)
CLOCK_DISAGREE_US = 1_000_000  # clock and edges further apart than this: trust the edges and say so
_UNIX_2020_US = 1_577_836_800_000_000  # wall-clock values before 2020 are a clock that was never set


@dataclass(frozen=True)
class Edge:
    t_us: int
    rising: bool


def enable_edges(log: LogLike) -> list[Edge]:
    """When the robot was enabled (rising) and disabled (falling), in log time, from the log's enabled periods (every
    source `timeline` reads, Phoenix `RobotEnable` included). Only edges the log saw happen count: a log that starts
    enabled has no first rising edge, and one that ends enabled no last falling edge."""
    merged: list[list[int]] = []
    starts_enabled = False
    for p in timeline.phases(log):  # auto straight into teleop is one enabled stretch, not a disable and an enable
        if merged and p.start_us <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], p.end_us)
        else:
            merged.append([p.start_us, p.end_us])
            starts_enabled = starts_enabled or (len(merged) == 1 and timeline.already_enabled(log, p))
    edges = []
    for i, (start, end) in enumerate(merged):
        if not (i == 0 and starts_enabled):
            edges.append(Edge(start, True))
        if end < log.end_us:
            edges.append(Edge(end, False))
    return edges


def epoch_diffs(epoch_rows: Sequence[LogRow], flag_rows: Sequence[LogRow]) -> list[int]:
    """Unix microseconds minus log microseconds for each epoch update that is a real time while the validity flag (its
    latest update at or before that moment) was true. `flag_rows` are oldest first; each lookup is a binary search, so a
    30-minute log is not a quadratic scan."""
    flag_times = [ft for ft, _ in flag_rows]
    diffs = []
    for t, v in epoch_rows:
        i = bisect.bisect_right(flag_times, t)
        if i and flag_rows[i - 1][1] and isinstance(v, int) and v > _UNIX_2020_US:
            diffs.append(v - t)
    return diffs


def wall_offset(log: LogLike) -> int | None:
    """Unix microseconds minus log microseconds, when the log knows the real time; else None."""
    if WALL_OFFSET in log.derived:
        return int(log.derived[WALL_OFFSET])
    if "systemTime" in log.topics:
        diffs = [int(v) - t for t, v in log.rows(["systemTime"])["systemTime"] if isinstance(v, int) and v > _UNIX_2020_US]
        if diffs:
            return round(statistics.median(diffs))
    epoch, valid = "/SystemStats/EpochTimeMicros", "/SystemStats/EpochTimeValid"
    if epoch in log.topics and valid in log.topics:
        rows = log.rows([epoch, valid])
        diffs = epoch_diffs(rows[epoch], rows[valid])
        if diffs:
            return round(statistics.median(diffs))
    return None


@dataclass
class EdgeMatch:
    offset_us: int
    matched: int
    total: int
    residual_us: int
    ambiguous: bool


def match_edges(reference: list[Edge], other: list[Edge]) -> EdgeMatch | None:
    """The offset (added to `other`'s times) that lines up the most edges of the same direction; None without edges.
    Candidates come only from the other log's first few rising edges (falling if none) against every reference edge, so about
    CANDIDATE_EDGES x len(reference) offsets are scored with binary searches.
    """
    times = {rising: sorted(e.t_us for e in reference if e.rising == rising) for rising in (True, False)}
    seeds = [e for e in other if e.rising][:CANDIDATE_EDGES] or other[:CANDIDATE_EDGES]
    guesses = sorted({ref - oth.t_us for oth in seeds for ref in times[oth.rising]})
    best: list[tuple[int, int, int]] = []  # (matched, -residual, offset)
    for guess in guesses:
        gaps = _gaps(times, other, guess)
        if gaps:
            refined = guess + round(statistics.median(gaps))
            gaps = _gaps(times, other, refined)
            best.append((len(gaps), -round(statistics.median(abs(g) for g in gaps)) if gaps else 0, refined))
    if not best:
        return None
    best.sort(reverse=True)
    matched, neg_residual, offset = best[0]
    rivals = [b for b in best if b[0] == matched and abs(b[2] - offset) > EDGE_TOLERANCE_US]
    return EdgeMatch(offset, matched, len(other), -neg_residual, ambiguous=bool(rivals))


def _gaps(times: dict[bool, list[int]], other: list[Edge], offset: int) -> list[int]:
    """For each edge of `other` with a same-direction reference edge within tolerance after shifting, the time between
    them (`times`: the reference edge times by direction, sorted)."""
    out = []
    for oth in other:
        shifted = oth.t_us + offset
        row = times[oth.rising]
        i = bisect.bisect_left(row, shifted)
        near = [row[j] - shifted for j in (i - 1, i) if 0 <= j < len(row)]
        closest = min(near, key=abs, default=None)
        if closest is not None and abs(closest) <= EDGE_TOLERANCE_US:
            out.append(closest)
    return out


@dataclass
class Placement:
    """How one log was put on the reference clock."""

    log_id: str
    offset_us: int
    method: str  # clock | enable | manual | reference
    notes: dict[str, Any] = field(default_factory=dict)


def place(reference: LogLike, other: LogLike, log_id: str, method: Method) -> Placement:
    """The offset for `other`. Raises WPILogError when the chosen method has nothing to work with."""
    ref_clock, oth_clock = wall_offset(reference), wall_offset(other)
    clock = oth_clock - ref_clock if ref_clock is not None and oth_clock is not None else None
    edges = None
    if method in ("auto", "enable"):
        edges = match_edges(enable_edges(reference), enable_edges(other))
    if method == "clock" or (method == "auto" and clock is not None and edges is None):
        if clock is None:
            raise WPILogError(f"{log_id} or the first log has no valid wall-clock time; use method='enable' or offsets_ms.")
        return Placement(log_id, clock, "clock")
    if edges is None or (edges.ambiguous and clock is None):
        why = "the enable/disable edges fit more than one offset" if edges else "one of the logs has no enable/disable edges"
        raise WPILogError(f"Cannot sync {log_id}: {why}, and the logs share no valid wall-clock time. Give offsets_ms "
                          "(ms to add to that log's times), e.g. from a moment you can see in both.")
    notes: dict[str, Any] = {"edges_matched": f"{edges.matched}/{edges.total}", "residual_ms": round(edges.residual_us / 1000, 1)}
    if edges.matched == 1:
        notes["warning"] = "only one edge matched; check the result against a moment you can see in both logs"
    if clock is not None:
        disagree = clock - edges.offset_us
        if edges.ambiguous or abs(disagree) <= CLOCK_DISAGREE_US:
            if edges.ambiguous:
                return Placement(log_id, clock, "clock", {"note": "the edges fit more than one offset; the wall clock picked"})
            notes["clock_differs_ms"] = round(disagree / 1000, 1)
        else:
            notes["warning"] = f"the wall clocks disagree with the edges by {disagree / 1e6:.1f} s; the edges were used"
    return Placement(log_id, edges.offset_us, "enable", notes)


class SyncedLog:
    """Open logs shifted onto the first one's clock and offered as one log.
    A topic name that more than one log has is shown as `<log id>:<name>` for every log but the first. Rows are read from
    the logs themselves; this view keeps only the name map and the offsets.
    """

    def __init__(self, view_id: str, parts: dict[str, LogLike], offsets_us: dict[str, int]) -> None:
        # There is no file to index: this implements `LogLike` from the parts' indexes.
        self.path = Path(view_id)
        self.parts = parts
        self.offsets_us = offsets_us
        self.size_bytes = sum(p.size_bytes for p in parts.values())
        self.records = sum(p.records for p in parts.values())
        self.skipped_records = sum(p.skipped_records for p in parts.values())
        self.skipped_reasons = {k: sum(p.skipped_reasons.get(k, 0) for p in parts.values()) for k in ("unknown_entry", "decode")}
        self.cut_short = any(p.cut_short for p in parts.values())
        self.derived: dict[str, Any] = {}
        self.schemas: dict[str, bytes] = {}
        for part in parts.values():
            for name, raw in part.schemas.items():
                self.schemas.setdefault(name, raw)  # the first log wins if two define a type differently
        self._schema_owner = f"synced:{view_id}:{id(self)}"
        self.topics: dict[str, Topic] = {}
        self._source: dict[str, tuple[str, str]] = {}  # shown name -> (log id, name in that log)
        self.renamed: dict[str, int] = {}
        for log_id, part in parts.items():
            shift = offsets_us[log_id]
            for name, topic in part.topics.items():
                shown = name if name not in self.topics else f"{log_id}:{name}"
                if shown != name:
                    self.renamed[log_id] = self.renamed.get(log_id, 0) + 1
                self._source[shown] = (log_id, name)
                self.topics[shown] = replace(topic, name=shown,
                                             first_us=None if topic.first_us is None else topic.first_us + shift,
                                             last_us=None if topic.last_us is None else topic.last_us + shift)
        self.start_us = min(p.start_us + offsets_us[i] for i, p in parts.items())
        self.end_us = max(p.end_us + offsets_us[i] for i, p in parts.items())

    def rows(self, names: Iterable[str]) -> dict[str, Rows]:
        wanted = [n for n in dict.fromkeys(names) if n in self._source]
        by_log: dict[str, list[str]] = {}
        for shown in wanted:
            log_id, name = self._source[shown]
            by_log.setdefault(log_id, []).append(name)
        read = {log_id: self.parts[log_id].rows(inner) for log_id, inner in by_log.items()}
        out = {}
        for shown in wanted:
            log_id, name = self._source[shown]
            shift = self.offsets_us[log_id]
            rows = read[log_id][name]
            out[shown] = ShiftedRows(rows, shift) if shift else rows
        return out

    def activate_schemas(self) -> None:
        schema_state.activate(self._schema_owner, self.schemas.items())

    @property
    def duration_s(self) -> float:
        return (self.end_us - self.start_us) / 1e6

    def busiest(self, limit: int = 5) -> list[tuple[str, int]]:
        return busiest_topics(self.topics, limit)

    def topic_rows(self, criteria: TopicFilter) -> tuple[list[dict[str, Any]], list[str]]:
        return topic_listing(self.topics, criteria)

    @serialized
    def release(self) -> None:
        schema_state.forget(self._schema_owner)  # the parts keep their own caches until they are closed

    def is_current(self) -> bool:
        return all(p.is_current() for p in self.parts.values())

    def is_replaced(self) -> bool:
        return any(p.is_replaced() for p in self.parts.values())


def build(view_id: str, parts: dict[str, LogLike], method: Method, offsets_ms: list[float] | None) -> tuple[SyncedLog, list[Placement]]:
    """Sync `parts` (the first sets the clock) and return the view and how each log was placed.

    Raises WPILogError: for a wrong number of offsets, or a log that cannot be placed.
    """
    ids = list(parts)
    reference = parts[ids[0]]
    placements = [Placement(ids[0], 0, "reference")]
    if offsets_ms is not None:
        if len(offsets_ms) != len(ids) - 1:
            raise WPILogError(f"Give one offset per log after the first ({len(ids) - 1}), not {len(offsets_ms)}.")
        placements += [Placement(i, round(ms * 1000), "manual") for i, ms in zip(ids[1:], offsets_ms, strict=True)]
    else:
        placements += [place(reference, parts[i], i, method) for i in ids[1:]]
    view = SyncedLog(view_id, parts, {p.log_id: p.offset_us for p in placements})
    return view, placements
