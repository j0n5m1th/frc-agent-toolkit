"""Unit tests for the extracted operation gate (`frc_mcp_nt4.gate`)."""

import asyncio

import pytest

from frc_mcp_nt4.errors import NT4Error
from frc_mcp_nt4.gate import OperationGate


async def test_operation_counts_calls_and_sets_idle_when_the_last_one_finishes():
    gate = OperationGate()
    _lock, idle = gate.acquire_pair()
    assert idle.is_set() and gate.active_operations == 0 and not gate.busy
    async with gate.operation():
        assert gate.active_operations == 1 and not idle.is_set() and gate.busy
        async with gate.operation():  # nested/parallel calls share one counter
            assert gate.active_operations == 2 and not idle.is_set()
        assert gate.active_operations == 1 and not idle.is_set()  # still not idle
    assert gate.active_operations == 0 and idle.is_set() and not gate.busy


async def test_busy_reports_a_held_transition_lock_without_any_operation():
    gate = OperationGate()
    lock, _idle = gate.acquire_pair()
    assert not gate.busy
    await lock.acquire()
    try:
        assert gate.busy and gate.active_operations == 0
    finally:
        lock.release()
    assert not gate.busy


async def test_operation_needs_no_connection_check_or_prepare_step():
    gate = OperationGate()  # the `require_connection=False` path
    async with gate.operation():
        assert gate.active_operations == 1
    assert gate.active_operations == 0


async def test_operation_retries_the_lock_until_connected_and_counts_once():
    gate = OperationGate()
    prepared: list[int] = []

    async def prepare() -> None:
        prepared.append(1)

    connected = iter((False, True))

    async with gate.operation(prepare=prepare, is_connected=lambda: next(connected)):
        assert gate.active_operations == 1
    assert len(prepared) == 2 and gate.active_operations == 0


async def test_pass_through_waits_for_an_in_progress_transition():
    gate = OperationGate()
    lock, _idle = gate.acquire_pair()
    await lock.acquire()
    passed = asyncio.Event()

    async def waiter() -> None:
        await gate.pass_through()
        passed.set()

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    assert not passed.is_set()
    lock.release()
    await task
    assert passed.is_set()


async def test_pass_through_is_a_no_op_when_the_lock_is_free():
    gate = OperationGate()
    gate.acquire_pair()
    await gate.pass_through()
    assert gate.active_operations == 0


async def test_a_new_loop_gets_a_fresh_pair_when_the_old_one_is_idle():
    gate = OperationGate()
    first_lock, first_idle = gate.acquire_pair()
    assert first_idle.is_set()
    second_lock, second_idle = await _on_second_loop(gate)  # a different loop, still idle
    assert (second_lock, second_idle) != (first_lock, first_idle)
    assert second_idle.is_set() and not gate.busy
    third_lock, third_idle = gate.acquire_pair()  # back on this loop: a fresh pair again
    assert third_lock is not second_lock and third_idle.is_set() and not gate.busy


async def test_a_busy_pair_from_another_loop_is_reported():
    gate = OperationGate()
    lock, _idle = gate.acquire_pair()
    await lock.acquire()  # a transition in flight on this loop
    with pytest.raises(NT4Error, match="in use by another event loop"):
        await _on_second_loop(gate)
    assert gate.busy  # the held lock is what made the other loop refuse
    lock.release()
    assert not gate.busy


async def _on_second_loop(gate: OperationGate) -> tuple[asyncio.Lock, asyncio.Event]:
    """`acquire_pair()` on a second, short-lived loop (each thread gets its own running loop)."""
    return await asyncio.get_running_loop().run_in_executor(None, _run_second_loop, gate)


def _run_second_loop(gate: OperationGate) -> tuple[asyncio.Lock, asyncio.Event]:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_acquire(gate))
    finally:
        loop.close()


async def _acquire(gate: OperationGate) -> tuple[asyncio.Lock, asyncio.Event]:
    return gate.acquire_pair()
