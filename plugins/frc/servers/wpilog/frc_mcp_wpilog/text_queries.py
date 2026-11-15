"""`search_text`: find messages, console output and faults by their text."""

from typing import Any

from .logfile import serialized
from .loglike import LogLike
from .timeline import origin_us
from .windows import Range, _at_ms, _in_range

_MESSAGE_WORDS = ("message", "console", "log", "error", "warn", "fault", "alert", "events")


def _text_topics(log: LogLike) -> list[str]:
    """Where a student's "what went wrong" lives: topics named like messages, else every text topic."""
    strings = sorted(t.name for t in log.topics.values() if t.type_str == "string" and not t.hidden)
    messages = [n for n in strings if any(word in n.lower() for word in _MESSAGE_WORDS)]
    return messages or strings


@serialized
def search_text(log: LogLike, topics: list[str] | None, contains: str | None, window: Range, limit: int) -> dict[str, Any]:
    """Text entries (messages, console output, faults) whose value contains `contains`, oldest first.

    With no `topics`, the topics named like messages or console output are searched (else every text topic).
    """
    origin, _ = origin_us(log, window.align)
    names = topics or _text_topics(log)
    missing = [n for n in names if n not in log.topics]
    names = [n for n in names if n in log.topics]
    needle = (contains or "").lower()
    hits: list[tuple[float, str, str]] = []
    for name, rows in log.rows(names).items():
        for t, v in rows:
            text = v if isinstance(v, str) else str(v)  # a name restarted with another type holds both
            if text.strip() and needle in text.lower():  # blank values carry nothing worth listing
                hits.append((_at_ms(t, origin), name, text))
    hits.sort(key=lambda h: h[0])
    hits = [h for h in hits if _in_range(h[0], window)]
    items = [{"t_ms": round(t, 1), "topic": n, "v": text[:400]} for t, n, text in hits[:limit]]
    out: dict[str, Any] = {"n": len(hits), "searched": len(names), "items": items}
    if len(hits) > limit:
        out["omitted"] = len(hits) - limit
    if missing:
        out["not_in_log"] = missing[:5]
    return out
