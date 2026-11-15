"""Six-way open / query / close / reset races over cut-short and whole logs, with a hard deadline each round.

A stall must never be a silent hang: a round that runs past its deadline fails with every thread's stack.
(A reviewer saw rounds that never finished, with no thread holding a lock. The cause was native: wpiutil's DataLogReader
crashes or hangs when two threads read at once, so `logfile.READ_LOCK` now allows one pass at a time. Without it this test
died with an access violation in about half of its runs.)
"""

import random
import sys
import threading
import time
import traceback

import pytest

from frc_mcp_wpilib.wpilog_writer import LogWriter
from frc_mcp_wpilog import logfile
from frc_mcp_wpilog.errors import WPILogError
from frc_mcp_wpilog.store import OpenLogs

ROUNDS = 30
THREADS = 6
STEPS = 25
DEADLINE_S = 30.0
EXPECTED = ("logs are open already", "No open log", "No log is open")


def build_logs(tmp_path):
    paths = []
    for k in range(5):
        w = LogWriter()
        ids = [w.start(f"/t{j}", "double", "", 0) for j in range(3)]
        for i in range(300):
            w.append(ids[i % 3], float(i), 100 * (i + 1))
        data = w.to_bytes()
        path = tmp_path / f"s{k}.wpilog"
        path.write_bytes(data[:-3] if k % 2 else data)  # the odd ones are cut short
        paths.append(path)
    return paths


def one_round(paths, seed):
    store = OpenLogs()
    errors: list[str] = []

    def worker(n):
        rnd = random.Random(seed * 100 + n)
        for _ in range(STEPS):
            try:
                op = rnd.random()
                if op < 0.3:
                    store.open(rnd.choice(paths))
                elif op < 0.8:
                    log = store.get()
                    names = rnd.sample(sorted(log.topics), 2)
                    got = log.rows(names)
                    for name in names:
                        if len(got[name]) != log.topics[name].count:
                            errors.append(f"{log.path.name} {name}: {len(got[name])} rows, count {log.topics[name].count}")
                elif op < 0.95:
                    ids = store.ids
                    store.close(rnd.choice(ids) if ids else None)
                else:
                    store.reset()
            except WPILogError as e:
                if not any(text in str(e) for text in EXPECTED):
                    errors.append(repr(e))
            except Exception:  # noqa: BLE001 - reported below
                errors.append(traceback.format_exc())

    threads = [threading.Thread(target=worker, args=(n,), daemon=True) for n in range(THREADS)]
    for t in threads:
        t.start()
    end = time.monotonic() + DEADLINE_S
    for t in threads:
        t.join(max(0.0, end - time.monotonic()))
    stuck = [t for t in threads if t.is_alive()]
    if stuck:
        frames = sys._current_frames()
        dump = "\n".join("".join(traceback.format_stack(frames[t.ident])) for t in stuck if t.ident in frames)
        pytest.fail(f"round {seed}: {len(stuck)} threads still running after {DEADLINE_S:.0f} s (stacks follow)\n{dump}")
    return errors


def test_the_open_query_close_reset_race_finishes_30_rounds_in_a_row_without_error(tmp_path, monkeypatch):
    monkeypatch.setattr(logfile, "MAX_CACHED_ROWS", 400)  # every query evicts
    monkeypatch.setattr(logfile, "PRELOAD_ROWS_PER_TOPIC", 50)  # topics are read by `_load`, not kept while indexing
    paths = build_logs(tmp_path)
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-4)  # more thread switches, to give a race its chance
    try:
        for seed in range(ROUNDS):
            assert one_round(paths, seed) == [], seed
    finally:
        sys.setswitchinterval(old)
