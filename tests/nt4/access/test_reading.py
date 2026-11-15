"""reading.collect_values: polling behavior with fake ntcore subscriptions."""

import time

from frc_mcp_nt4.access import reading


class Val:
    def __init__(self, valid=True, t=5, value=1.0):
        self._valid, self._t, self._value = valid, t, value

    def isValid(self):
        return self._valid

    def time(self):
        return self._t

    def value(self):
        return self._value


class Sub:
    def __init__(self, *values):
        self.values = list(values)

    def get(self):
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


class CountingSub(Sub):
    """A Sub that counts how many times the poller read it."""

    def __init__(self, *values):
        super().__init__(*values)
        self.calls = 0

    def get(self):
        self.calls += 1
        return super().get()


class Topic:
    def __init__(self, exists=True, type_str="double"):
        self._exists, self._type = exists, type_str

    def exists(self):
        return self._exists

    def getTypeString(self):
        return self._type


class Inst:
    def __init__(self, **topics):
        self.topics = topics

    def getTopic(self, name):
        return self.topics[name]


def test_still_waiting_false_for_valid_real_value():
    assert not reading._still_waiting(Inst(a=Topic()), {"a": Sub(Val())}, 0.0, 1.0, 1.0)


def test_still_waiting_waits_for_existing_topic_without_value():
    assert reading._still_waiting(Inst(a=Topic()), {"a": Sub(Val(valid=False))}, 5.0, 1.0, 1.0)


def test_still_waiting_unannounced_topic_only_during_grace():
    inst, subs = Inst(a=Topic(exists=False)), {"a": Sub(Val(valid=False))}
    assert reading._still_waiting(inst, subs, 0.1, 0.5, 0.5)
    assert not reading._still_waiting(inst, subs, 0.6, 0.5, 0.5)


def test_still_waiting_weak_value_only_during_weak_grace():
    inst, subs = Inst(a=Topic()), {"a": Sub(Val(t=0))}
    assert reading._still_waiting(inst, subs, 0.1, 0, 0.4)
    assert not reading._still_waiting(inst, subs, 0.5, 0, 0.4)


async def test_collect_values_returns_type_and_value_immediately():
    inst = Inst(a=Topic(type_str="int"))
    start = time.monotonic()
    out = await reading.collect_values(inst, {"a": Sub(Val(value=7))}, timeout_s=5, grace_s=1)
    assert out["a"].type_string == "int" and out["a"].value.value() == 7
    assert time.monotonic() - start < 1


async def test_collect_values_reports_hints_for_missing_topics():
    inst = Inst(gone=Topic(exists=False), empty=Topic(exists=True))
    subs = {"gone": Sub(Val(valid=False)), "empty": Sub(Val(valid=False))}
    out = await reading.collect_values(inst, subs, timeout_s=0.1, grace_s=0.05)
    assert out == {"gone": reading.ReadMiss(reading.NOT_ANNOUNCED), "empty": reading.ReadMiss(reading.NO_VALUE_YET)}


async def test_collect_values_polls_until_value_arrives():
    inst = Inst(a=Topic())
    sub = Sub(Val(valid=False), Val(valid=False), Val(value=3.5))
    out = await reading.collect_values(inst, {"a": sub}, timeout_s=2, grace_s=1)
    assert out["a"].value.value() == 3.5


async def test_collect_values_zero_wait_does_not_sleep():
    inst = Inst(a=Topic())
    start = time.monotonic()
    out = await reading.collect_values(inst, {"a": Sub(Val(valid=False))}, timeout_s=0, grace_s=1)
    assert out == {"a": reading.ReadMiss(reading.NO_VALUE_YET)} and time.monotonic() - start < 0.5


async def test_collect_values_reads_each_subscription_once_per_pass():
    inst = Inst(a=Topic())
    sub = CountingSub(Val(valid=False), Val(value=3.5))
    out = await reading.collect_values(inst, {"a": sub}, timeout_s=2, grace_s=1)
    assert out["a"].value.value() == 3.5
    # pass 1 reads the invalid value (keep waiting), pass 2 reads the real one (stop);
    # the final report reuses pass 2's read instead of calling get() again
    assert sub.calls == 2


async def test_collect_values_falls_back_to_one_read_when_no_pass_ran():
    inst = Inst(a=Topic())
    sub = CountingSub(Val(valid=False))
    out = await reading.collect_values(inst, {"a": sub}, timeout_s=0, grace_s=1)
    assert out == {"a": reading.ReadMiss(reading.NO_VALUE_YET)} and sub.calls == 1
