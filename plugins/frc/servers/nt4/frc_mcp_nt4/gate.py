"""The operation gate: the transition lock, the idle event, and the count of calls inside them.

It keeps a connect/disconnect from running under ordinary tool calls. The lock and event belong to one event loop, so
`acquire_pair()` rebuilds them when the loop changes.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from .errors import NT4Error


class OperationGate:
    """Per-event-loop (lock, idle event) pair plus the active-operation counter."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._idle: asyncio.Event | None = None
        self._active = 0

    @property
    def active_operations(self) -> int:
        """Calls currently inside `operation()`."""
        return self._active

    @property
    def busy(self) -> bool:
        """True while an operation is in flight or a transition holds the lock."""
        return bool(self._active) or (self._lock is not None and self._lock.locked())

    def acquire_pair(self) -> tuple[asyncio.Lock, asyncio.Event]:
        """The (lock, idle) pair of the running loop, created on first use and after a loop change."""
        loop = asyncio.get_running_loop()
        lock, idle = self._lock, self._idle
        if self._loop is not loop or lock is None or idle is None:
            if self.busy:
                raise NT4Error("The NT4 session is in use by another event loop.")
            self._loop = loop
            lock = self._lock = asyncio.Lock()
            idle = self._idle = asyncio.Event()
            idle.set()
        return lock, idle

    @asynccontextmanager
    async def operation(
        self,
        prepare: Callable[[], Awaitable[None]] | None = None,
        is_connected: Callable[[], bool] | None = None,
    ) -> AsyncIterator[None]:
        """Count one ordinary call: (re)connect, take the lock, clear the idle event while it runs.
        `prepare` runs before each attempt; `is_connected` is re-checked under the lock, and the attempt restarts if a
        transition finished in between.
        """
        while True:
            if prepare is not None:
                await prepare()
            lock, idle = self.acquire_pair()
            async with lock:
                if is_connected is not None and not is_connected():
                    continue
                self._active += 1
                idle.clear()
                break
        try:
            yield
        finally:
            self._active -= 1
            if not self._active:
                idle.set()

    async def pass_through(self) -> None:
        """Wait out a connect/disconnect in progress without holding the lock afterwards."""
        lock, _ = self.acquire_pair()
        if lock.locked():
            async with lock:
                pass
