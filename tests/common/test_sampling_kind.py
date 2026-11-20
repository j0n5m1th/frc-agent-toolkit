"""`sampling_kind` says how a signal is logged (every loop, only on change, or as events) so a student knows what a statistic over it means."""

from frc_mcp_wpilib.reduce.stats import sampling_kind


def rows(times, values):
    return list(zip(times, values, strict=True))


def test_a_signal_logged_every_loop_is_periodic_even_with_a_stall_and_repeated_values():
    times = [n * 20.0 for n in range(60)]
    times[30] += 500  # one long stall is a minority of the gaps
    assert sampling_kind(rows(times, [1.0] * 60)) == "periodic"


IRREGULAR = [0, 20, 500, 520, 3000, 3040, 3041, 9000, 9100, 12000, 12500]


def test_irregular_gaps_with_a_new_value_every_time_is_change_only():
    assert sampling_kind(rows(IRREGULAR, [float(n) for n in range(len(IRREGULAR))])) == "change_only"


def test_irregular_gaps_with_repeated_values_is_event():
    assert sampling_kind(rows(IRREGULAR, [1.0, 1.0, *map(float, range(2, 11))])) == "event"


def test_too_few_updates_say_nothing():
    assert sampling_kind(rows([0, 10, 50], [1.0, 2.0, 3.0])) is None


def test_only_the_first_updates_of_a_long_signal_are_read():
    steady = [n * 20.0 for n in range(5000)]
    odd = [steady[-1] + 100_000.0 * (n + 1) for n in range(20000)]  # irregular, but past the cap
    assert sampling_kind(rows([*steady, *odd], [float(n) for n in range(25000)])) == "periodic"
