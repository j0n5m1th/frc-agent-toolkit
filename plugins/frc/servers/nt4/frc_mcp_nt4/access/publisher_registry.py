"""What `NT4Session` holds for the publishers it owns: handles, TTL timers and deadlines, and 'ghost' entries of released
retained/persistent topics, all keyed by topic name. `close()` is what `disconnect()` does to publishers.
"""

import asyncio
from typing import Any

import ntcore


def _remaining(deadline: float, now: float) -> float:
    return round(max(0.0, deadline - now), 2)


class PublisherRegistry:
    """Publisher handles, their TTL timer handles and deadlines, plus ghost entries, keyed by topic name."""

    def __init__(self) -> None:
        self._pubs: dict[str, tuple[str, ntcore.GenericPublisher]] = {}  # topic -> (type_str, publisher)
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._deadlines: dict[str, float] = {}
        self._ghosts: dict[str, tuple[str, dict[str, Any]]] = {}  # released retained/persistent: topic -> (type, props)

    def is_busy(self) -> bool:
        """True while we hold a publisher, i.e. the connection still has something to lose."""
        return bool(self._pubs)

    def close(self) -> None:
        """Cancel every TTL timer and forget everything (what disconnect does)."""
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
        self._deadlines.clear()
        self._pubs.clear()
        self._ghosts.clear()

    def __contains__(self, name: str) -> bool:
        return name in self._pubs

    @property
    def names(self) -> list[str]:
        """The topics we currently hold a publisher for."""
        return sorted(self._pubs)

    def get(self, name: str) -> tuple[str, ntcore.GenericPublisher] | None:
        """The (type_str, publisher) pair held for `name`, or None."""
        return self._pubs.get(name)

    def add(self, name: str, type_str: str, publisher: ntcore.GenericPublisher) -> None:
        self._pubs[name] = (type_str, publisher)

    def remove(self, name: str) -> tuple[str, ntcore.GenericPublisher] | None:
        """Drop the publisher for `name` (timer and deadline too) and return its record."""
        self.cancel_timer(name)
        self._deadlines.pop(name, None)
        return self._pubs.pop(name, None)

    def arm(self, name: str, deadline: float, timer: asyncio.TimerHandle) -> None:
        """(Re)start the countdown for `name`: the previous timer is cancelled first."""
        self.cancel_timer(name)
        self._deadlines[name] = deadline
        self._timers[name] = timer

    def cancel_timer(self, name: str) -> None:
        timer = self._timers.pop(name, None)
        if timer is not None:
            timer.cancel()

    def deadline(self, name: str) -> float | None:
        return self._deadlines.get(name)

    def has_timer(self, name: str) -> bool:
        """Whether a TTL timer handle is still registered for `name`."""
        return name in self._timers

    def expires_in(self, name: str, now: float) -> float | None:
        """Seconds left on this publisher's TTL, or None when it has no deadline. `now` is a `time.monotonic()`
        reading; the result is rounded to 2 decimals, which is what both nt4_status and the set_value reply show."""
        deadline = self._deadlines.get(name)
        return None if deadline is None else _remaining(deadline, now)

    def expiry_table(self, now: float) -> dict[str, float]:
        """name -> seconds left, for every publisher that has a deadline."""
        return {name: _remaining(deadline, now) for name, deadline in self._deadlines.items()}

    def add_ghost(self, name: str, type_str: str, props: dict[str, Any]) -> None:
        self._ghosts[name] = (type_str, dict(props))

    def discard_ghost(self, name: str) -> None:
        self._ghosts.pop(name, None)

    def ghost(self, name: str) -> tuple[str, dict[str, Any]] | None:
        return self._ghosts.get(name)

    @property
    def ghosts(self) -> list[tuple[str, tuple[str, dict[str, Any]]]]:
        return list(self._ghosts.items())
