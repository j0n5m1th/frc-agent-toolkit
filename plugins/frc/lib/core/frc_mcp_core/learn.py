"""Learning mode: a short "tidbit" added to a tool's reply: context on why something works, or something interesting, to spark curiosity.

Off by default. The plugin setting `learning_mode` sets `FRC_LEARNING_MODE`; when it is on, a reply from a tool that has a tip in `learn_tips.py`
gets one extra key, `learn`. The tip is fixed text that ships with the toolkit (never robot or device text). It costs nothing when the mode is
off: tool descriptions and schemas do not change, so the token budget is untouched.
"""

import json
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.middleware import CallNext
from fastmcp.tools import ToolResult

from .learn_tips import TIPS
from .reply import compact_json, max_response_budget
from .settings import TRUE_WORDS, environ

LEARN_ENV = "FRC_LEARNING_MODE"
LEARN_KEY = "learn"
LEARN_LEAD = "Share with the student, in your own words: "  # tells the model what the tip is for, so no skill has to


def learning_mode(getenv=environ) -> bool:
    """True when learning mode is on. Anything but a true word (including a blank or unknown value) is off: a typo never changes what a tool does."""
    return (getenv(LEARN_ENV) or "").strip().lower() in TRUE_WORDS


def with_tip(result: ToolResult, tip: str, limit: int) -> ToolResult | None:
    """`result` with `learn` added, or None when that is not possible: it is not one JSON object in one text block, or the new text would pass `limit` characters."""
    text = getattr(result.content[0], "text", None) if len(result.content) == 1 else None  # only a text block has `text`
    if result.structured_content is not None or not isinstance(text, str):
        return None
    try:
        data: Any = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or LEARN_KEY in data:
        return None
    text = compact_json({**data, LEARN_KEY: LEARN_LEAD + tip})
    if len(text) > limit:  # `reply()` already filled its budget: the tip never pushes a reply over the cap
        return None
    return ToolResult(content=text, meta=result.meta, is_error=result.is_error)


class LearnTips(Middleware):
    """Add the tool's tip to its reply while learning mode is on, the first time that tool is called in this server's life (a repeat would only cost tokens)."""

    def __init__(self) -> None:
        self._shown: set[str] = set()

    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext) -> ToolResult:
        result = await call_next(context)
        name = context.message.name
        tip = TIPS.get(name)
        if not tip or name in self._shown or not isinstance(result, ToolResult) or not learning_mode():
            return result
        if tipped := with_tip(result, tip, max_response_budget()):
            self._shown.add(name)
            return tipped
        return result
