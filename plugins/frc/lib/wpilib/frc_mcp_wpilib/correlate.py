"""Do two signals move together? Shared by the log server and the live server.

Both signals are put on one clock by holding each value until its next update, sampled on an even grid, and compared with Pearson's r. The lag scan
also asks whether one signal follows the other a moment later (a motor current rising, then the voltage sagging). A high correlation shows two signals
move together; it does not show that one causes the other, and two signals that both trend (a draining battery, a warming motor) correlate without
being related. The caller names both signals.
"""

import math
from bisect import bisect_right
from collections.abc import Sequence
from typing import Any

from .reduce.stats import US_PER_S
from .scan_rows import Window, everywhere, finite_numbers, value_at
from .tracking import MIN_COMPARED, NothingToCompare, Samples

MAX_POINTS = 2000  # grid points per comparison
MIN_STEP_US = 10_000
LAGS = 40  # lag steps scanned on each side, at most
TIE = 0.005  # matches this close to the strongest count as equal; the one nearest zero lag wins
STRENGTHS = ((0.7, "strong"), (0.4, "moderate"), (0.2, "weak"))
NOTE = "Moving together is not one causing the other; signals that both trend (a draining battery, a warming motor) correlate without being related."


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return None if sx == 0 or sy == 0 else sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / (sx * sy)


def _strength(r: float) -> str:
    return next((label for floor, label in STRENGTHS if abs(r) >= floor), "none")


def analyze_correlation(a: Samples, b: Samples, windows: Sequence[Window], max_lag_s: float = 1.0) -> dict[str, Any]:
    """Pearson's r between `a` and `b` over the windows at zero lag, and the lag (within `max_lag_s`) at which they match best.
    A positive lag means `b` follows `a`. Raises NothingToCompare when a signal never changes or the overlap is too short."""
    a_rows, b_rows = finite_numbers(a), finite_numbers(b)
    if not a_rows or not b_rows:
        raise NothingToCompare("one of the signals has no readings")
    start, end = max(a_rows[0][0], b_rows[0][0]), min(a_rows[-1][0], b_rows[-1][0])
    spans = [(max(lo, start), min(hi, end)) for lo, hi in everywhere(windows) if min(hi, end) > max(lo, start)]
    total = sum(hi - lo for lo, hi in spans)
    if total <= 0:
        raise NothingToCompare("the two signals do not overlap in the time checked")
    step = max(MIN_STEP_US, total // MAX_POINTS)
    grid = [t for lo, hi in spans for t in range(lo, hi, step)]
    xs = [float(value_at(a_rows, t)) for t in grid]
    if len(grid) < MIN_COMPARED:
        raise NothingToCompare(f"only {len(grid)} points overlap (at least {MIN_COMPARED} are needed)")
    lag_step = max(step, round(max_lag_s * US_PER_S / LAGS))
    b_times = [t for t, _ in b_rows]

    def b_at(t: int) -> float | None:
        i = bisect_right(b_times, t) - 1
        return float(b_rows[i][1]) if i >= 0 else None

    reach = round(max_lag_s * US_PER_S)
    lags = sorted({0, *range(-reach, reach + 1, lag_step)})  # zero lag is always scanned: it is the headline number
    scanned: list[tuple[float, int]] = []  # (r, lag) at each lag with enough points
    zero: float | None = None
    for lag in lags:
        kept = [(x, y) for x, t in zip(xs, grid, strict=True) if (y := b_at(t + lag)) is not None]
        if len(kept) < MIN_COMPARED or (r := _pearson([x for x, _ in kept], [y for _, y in kept])) is None:
            continue
        if lag == 0:
            zero = r
        scanned.append((r, lag))
    if zero is None:
        raise NothingToCompare("one of the signals never changes in the time checked, so there is nothing to correlate")
    # the best match is the strongest |r|; among matches within `TIE` of it, the one nearest zero lag (a periodic signal matches at many lags)
    best = min(((r, lag) for r, lag in scanned if abs(r) >= max(abs(r) for r, _ in scanned) - TIE), key=lambda p: abs(p[1])) if scanned else None
    out: dict[str, Any] = {"n": len(grid), "r": round(zero, 3), "strength": _strength(zero), "direction": "together" if zero >= 0 else "opposite"}
    if best is not None and best[1] != 0 and abs(best[0]) >= abs(zero) + 0.05:
        out["best_lag"] = {"lag_s": round(best[1] / US_PER_S, 2), "r": round(best[0], 3), "meaning": "b follows a" if best[1] > 0 else "a follows b"}
    out["note"] = NOTE
    return out
