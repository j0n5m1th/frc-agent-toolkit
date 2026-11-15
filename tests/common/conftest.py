"""Shared fixtures for the common tests."""

import signal

import pytest


@pytest.fixture(autouse=True)
def _keep_signal_handlers():
    """`ServerSpec.serve()` takes over SIGTERM and SIGHUP; put the test runner's handlers back after each test."""
    names = [n for n in ("SIGTERM", "SIGHUP") if hasattr(signal, n)]
    before = {n: signal.getsignal(getattr(signal, n)) for n in names}
    yield
    for name, handler in before.items():
        signal.signal(getattr(signal, name), handler)
