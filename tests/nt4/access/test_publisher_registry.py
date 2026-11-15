"""Unit tests for `PublisherRegistry`, the one place publisher bookkeeping lives."""

from types import SimpleNamespace

from frc_mcp_nt4.access.publisher_registry import PublisherRegistry


class _Timer:
    """Stand-in for asyncio.TimerHandle: records whether the registry cancelled it."""

    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


def test_add_get_contains_remove():
    r = PublisherRegistry()
    assert not r.is_busy() and "x" not in r and r.get("x") is None
    pub = SimpleNamespace()
    r.add("/t/x", "double", pub)
    assert r.is_busy() and "x" not in r and "/t/x" in r
    assert r.get("/t/x") == ("double", pub)
    assert r.names == ["/t/x"]
    assert r.remove("/t/x") == ("double", pub)
    assert not r.is_busy() and r.get("/t/x") is None
    assert r.remove("/t/x") is None  # idempotent


def test_arm_replaces_timer_and_deadline():
    r = PublisherRegistry()
    first, second = _Timer(), _Timer()
    r.arm("/t/x", 10.0, first)
    assert r.deadline("/t/x") == 10.0 and r.has_timer("/t/x")
    r.arm("/t/x", 20.0, second)
    assert first.cancelled and not second.cancelled
    assert r.deadline("/t/x") == 20.0
    r.cancel_timer("/t/x")
    assert second.cancelled and not r.has_timer("/t/x")
    r.cancel_timer("/t/x")  # idempotent


def test_expires_in_and_expiry_table_round_to_two_decimals():
    r = PublisherRegistry()
    assert r.expires_in("/t/a", 100.0) is None and r.expiry_table(100.0) == {}
    r.arm("/t/a", 101.239, _Timer())
    r.arm("/t/b", 100.0, _Timer())  # already elapsed -> clamped to 0
    assert r.expires_in("/t/a", 100.0) == 1.24
    assert r.expires_in("/t/a", 105.0) == 0.0
    assert r.expiry_table(100.0) == {"/t/a": 1.24, "/t/b": 0.0}


def test_remove_clears_timer_and_deadline():
    r = PublisherRegistry()
    t = _Timer()
    r.arm("/t/x", 5.0, t)
    pub = SimpleNamespace()
    r.add("/t/x", "int", pub)
    assert r.remove("/t/x") == ("int", pub)
    assert t.cancelled and r.deadline("/t/x") is None and not r.has_timer("/t/x")


def test_ghosts_are_added_read_and_discarded():
    r = PublisherRegistry()
    assert r.ghost("/t/x") is None and r.ghosts == []
    r.add_ghost("/t/x", "int", {"retained": True})
    assert r.ghost("/t/x") == ("int", {"retained": True})
    assert r.ghosts == [("/t/x", ("int", {"retained": True}))]
    r.discard_ghost("/t/x")
    assert r.ghosts == []
    r.discard_ghost("/t/x")  # idempotent


def test_close_cancels_every_timer_and_clears_everything():
    r = PublisherRegistry()
    t1, t2 = _Timer(), _Timer()
    r.add("/t/a", "double", SimpleNamespace())
    r.arm("/t/a", 1.0, t1)
    r.arm("/t/b", 2.0, t2)
    r.add_ghost("/t/a", "double", {"persistent": True})
    r.close()
    assert t1.cancelled and t2.cancelled
    assert not r.is_busy() and r.names == [] and r.ghosts == []
    assert r.deadline("/t/a") is None and r.expires_in("/t/b", 3.0) is None
    r.close()  # idempotent
