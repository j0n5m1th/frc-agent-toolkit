"""The logs a server process has open (a few at a time; opening more closes nothing, it refuses)."""

import threading
from collections.abc import Callable
from pathlib import Path

from .errors import WPILogError
from .logfile import LOCK, load
from .loglike import LogLike

MAX_OPEN = 4
MAX_VIEWS = 2  # synced views (wpilog_sync) hold no rows of their own, so they do not count toward MAX_OPEN
NONE_OPEN = "No log is open. Call wpilog_list, then wpilog_open."


class OpenLogs:
    """Ids (`l1`, `l2`, ...) for opened logs. The newest one is the default when a tool is not given an id."""

    def __init__(self) -> None:
        self._logs: dict[str, LogLike] = {}
        self._next = 1
        self._views: dict[str, list[str]] = {}  # synced view id -> the ids of the logs it is made of
        self._next_view = 1
        self._views_building = 0  # views being built right now, counted against MAX_VIEWS
        self._epoch = 0  # bumped by `reset`: a view still being built from before it must not touch the new counters
        self._lock = threading.RLock()  # guards the id table for every access, mutating or reading; reentrant so `get` and
        # `pick` can call `resolve` inside it. Indexing a big file happens outside it, which is what must not block.
        # Lock order: `logfile.LOCK` (row caches, schemas) may be held while taking `_lock`, never the reverse, so nothing here
        # calls `release` or `make` while holding `_lock`.

    def _find(self, path: Path, stale: list[LogLike]) -> tuple[str, LogLike] | None:
        """The open log for `path` (made the newest), or None when it is not open.

        A stale index is dropped from the table and added to `stale`: release those after leaving the lock, because
        `release` waits for any running query."""
        for log_id, log in self._logs.items():
            if log.path == path:
                if not log.is_current():  # changed on disk since it was opened: index it afresh
                    stale.extend(self._remove(log_id))  # and drop the synced views made from it, as close() does
                    break
                self._logs[log_id] = self._logs.pop(log_id)  # make it the newest
                return log_id, log
        return None

    def _remove(self, log_id: str) -> list[LogLike]:
        """Take the log `log_id` and every synced view that depends on it out of the id table; return them for the caller
        to release after leaving the lock. Call while holding the lock, with `log_id` in the table."""
        gone = [self._logs.pop(log_id)]
        self._views.pop(log_id, None)
        for view_id in [v for v, parts in self._views.items() if log_id in parts]:  # a view cannot outlive its logs
            del self._views[view_id]
            gone.append(self._logs.pop(view_id))
        return gone

    def _room_for_more(self) -> None:
        """Raise WPILogError when the id table is full. Call while holding the lock."""
        files = [i for i in self._logs if i not in self._views]
        if len(files) >= MAX_OPEN:
            raise WPILogError(f"{MAX_OPEN} logs are open already ({', '.join(files)}); close one with wpilog_close.")

    def open(self, path: Path) -> tuple[str, LogLike]:
        """The id and log for `path`, opening it if it is not open already.

        Raises WPILogError: when MAX_OPEN logs are already open, or the file is not a valid log.
        """
        stale: list[LogLike] = []
        try:
            with self._lock:
                found = self._find(path, stale)
                if found is None:
                    self._room_for_more()  # under the lock: the slot count is what it guards
            if found:
                return found
            log = load(path)  # reads the whole file, so not under the lock
            with self._lock:
                found = self._find(path, stale)  # another call may have opened it, or filled the last slot, meanwhile
                if found:
                    return found
                self._room_for_more()
                log_id = f"l{self._next}"
                self._next += 1
                self._logs[log_id] = log
                return log_id, log
        finally:
            for old in stale:  # outside the lock: release waits for a running query
                old.release()

    def resolve(self, log_id: str | None = None) -> str:
        """This id if such a log is open, or the newest log's id when None.

        Raises WPILogError: when none is open or the id is unknown.
        """
        with self._lock:
            if not self._logs:
                raise WPILogError(NONE_OPEN)
            if log_id is None:
                return next(reversed(self._logs))
            if log_id not in self._logs:
                raise WPILogError(f"No open log {log_id!r}. Open logs: {', '.join(self._logs)}.")
            return log_id

    def get(self, log_id: str | None = None) -> LogLike:
        """The log with this id, or the newest one. Raises WPILogError as `resolve` does."""
        with self._lock:
            return self._logs[self.resolve(log_id)]

    def pick(self, log_ids: list[str]) -> dict[str, LogLike]:
        """The logs with these ids (in the order given).

        Raises WPILogError: when an id is unknown or repeated.
        """
        with self._lock:
            if len(set(log_ids)) != len(log_ids):
                raise WPILogError("Give each log id once.")
            return {log_id: self.get(log_id) for log_id in log_ids}

    def add_view(self, part_ids: list[str], make: Callable[[str, dict[str, LogLike]], LogLike]) -> tuple[str, LogLike]:
        """Register the view `make(view_id, parts)` builds over the open logs `part_ids`; it becomes the newest log.

        Raises WPILogError: for unknown, repeated or synced ids, or when MAX_VIEWS views are open already.
        """
        with self._lock:
            parts = self.pick(part_ids)
            if any(i in self._views for i in part_ids):
                raise WPILogError("Sync the logs themselves (l ids), not a synced view.")
            if len(self._views) + self._views_building >= MAX_VIEWS:
                raise WPILogError(f"{MAX_VIEWS} synced views are open already ({', '.join(self._views)}); close one with wpilog_close.")
            view_id = f"s{self._next_view}"
            self._next_view += 1
            self._views_building += 1  # holds the slot while the view is built outside the lock
            epoch = self._epoch
        try:
            with LOCK:  # `make` reads rows and activates schemas, as a query does; not under `_lock` (see the order above)
                view = make(view_id, parts)
        except BaseException:
            with self._lock:
                if epoch == self._epoch:
                    self._views_building -= 1
                if epoch == self._epoch and self._next_view == int(view_id[1:]) + 1:  # no other view took an id meanwhile: give this one back
                    self._next_view -= 1
            raise
        with self._lock:
            if epoch == self._epoch:
                self._views_building -= 1
            closed = epoch != self._epoch or any(self._logs.get(i) is not log for i, log in parts.items())  # closed, reopened or reset meanwhile
            if not closed:
                self._views[view_id] = list(part_ids)
                self._logs[view_id] = view
        if closed:
            view.release()  # outside the lock
            raise WPILogError(f"A log was closed while {view_id} was being synced; open it again and sync again.")
        return view_id, view

    def close(self, log_id: str | None = None) -> str:
        """Close the log with this id (default: the newest) and return its id.

        Raises WPILogError: when none is open or the id is unknown.
        """
        with self._lock:
            if log_id is None:
                if not self._logs:
                    raise WPILogError(NONE_OPEN)
                log_id = next(reversed(self._logs))
            if log_id not in self._logs:
                raise WPILogError(f"No open log {log_id!r}. Open logs: {', '.join(self._logs) or 'none'}.")
            gone = self._remove(log_id)
        for old in gone:  # outside the lock: release waits for a running query
            old.release()
        return log_id

    @property
    def ids(self) -> list[str]:
        with self._lock:
            return list(self._logs)

    def reset(self) -> None:
        """Forget every open log (the published way to empty the store; the benchmark harness calls it).

        Releases each log like `close()` does, so the two ways of emptying the store leave the same state: an empty
        id table and no cached rows or schemas left behind by the logs that were in it."""
        with self._lock:
            held = list(self._logs.values())
            self._logs.clear()
            self._views.clear()
            self._views_building = 0
            self._epoch += 1
            self._next = 1
            self._next_view = 1
        for log in held:  # outside the lock, as in close()
            log.release()


logs = OpenLogs()
