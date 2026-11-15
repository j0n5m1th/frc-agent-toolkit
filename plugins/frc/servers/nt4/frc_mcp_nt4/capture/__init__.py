"""Capture subpackage: the recorder (listener/lifecycle), the capture registry, plus the buffer, WPILOG file, query assembly, rules, stats and shared types."""

from frc_mcp_wpilib.reduce.types import Reduce

from .log import new_log_path
from .query import QuerySpec, make_spec
from .recorder import Capture, CaptureSpec
from .registry import CaptureRegistry, wait_ended
from .rules import EventComparison, EventRule, Trigger

__all__ = [
    "Capture",
    "CaptureRegistry",
    "CaptureSpec",
    "EventComparison",
    "EventRule",
    "QuerySpec",
    "make_spec",
    "Reduce",
    "Trigger",
    "new_log_path",
    "wait_ended",
]
