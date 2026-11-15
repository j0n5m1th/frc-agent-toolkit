"""Shared helpers for the session test files: the only place that touches `NT4Session`'s private state.

Like `capture_helpers`, every session test reaches the session internals through these named helpers,
so the private attribute names appear exactly once (here) instead of in every test file.
"""


def attach_fake_instance(session, inst):
    """Hand the session a stand-in NetworkTableInstance without connecting (the offline paths only need `_inst` set)."""
    session._inst = inst


def fire_idle(session):
    """Run the session's idle-timeout callback now, as if the idle timer had expired."""
    session._on_idle()
