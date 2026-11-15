"""Shared helpers for the capture test files: the only place that touches ``Capture``'s private state.

Every capture test reaches the recorder internals through these small named helpers, so the private
attribute names appear exactly once (here) instead of in every test file.
"""

import time
from collections import deque
from dataclasses import replace
from types import SimpleNamespace

import ntcore

from frc_mcp_nt4.capture import QuerySpec
from frc_mcp_wpilib.reduce.types import Gap

# the ntcore value makers the stand-ins are built with
_MAKERS = {
    "double": ntcore.Value.makeDouble,
    "int": ntcore.Value.makeInteger,
    "string": ntcore.Value.makeString,
    "double[]": ntcore.Value.makeDoubleArray,
    "boolean": ntcore.Value.makeBoolean,
    "raw": ntcore.Value.makeRaw,
}


def _now():
    """The ntcore clock reading the capture callbacks themselves use, with the monotonic clock as fallback.

    Every helper takes its time from here, so a pyntcore rename fails in this one place with one message.
    """
    try:
        return ntcore._now()
    except AttributeError:  # a pyntcore build without the private helper
        return int(time.monotonic() * 1_000_000)


def _origin(cap):
    """Give the capture a clock origin when it has none, like the first ntcore callback would."""
    if cap._clock.start_local_us is None:
        cap._clock.start(_now())


# -- ntcore event stand-ins -------------------------------------------------
def _maker(type_str, value):
    """Bytes values (struct/protobuf payloads, or a deliberately mistyped topic) are raw; the rest use their type's maker."""
    return _MAKERS["raw"] if isinstance(value, (bytes, bytearray)) else _MAKERS[type_str]


def make_event(name, value, type_str="double", t_us=None):
    """Build the ntcore event stand-in the capture callbacks read (a real value, or one stamped `t_us`)."""
    topic = SimpleNamespace(getName=lambda: name, getTypeString=lambda: type_str)
    if t_us is None:
        nt_value = value if isinstance(value, ntcore.Value) else _maker(type_str, value)(value)
    else:
        nt_value = SimpleNamespace(value=lambda: value, time=lambda: t_us, server_time=lambda: t_us)
    return SimpleNamespace(data=SimpleNamespace(topic=topic, value=nt_value))


def event(name, value):
    topic = SimpleNamespace(getName=lambda: name, getTypeString=lambda: "string")
    return SimpleNamespace(data=SimpleNamespace(topic=topic, value=ntcore.Value.makeString(value)))


def unpublish_event(name, check_flag=False):
    """The kUnpublish stand-in; `check_flag` compares against the flag instead of always matching."""
    if check_flag:
        return SimpleNamespace(is_=lambda flag: flag == ntcore.EventFlags.kUnpublish,
                               data=SimpleNamespace(name=name))
    return SimpleNamespace(is_=lambda _flag: True, data=SimpleNamespace(name=name))


def connection_event(flag):
    """The connection-listener stand-in matching `ntcore.EventFlags.kConnected`/`kDisconnected`."""
    return SimpleNamespace(is_=lambda checked: checked == flag)


# -- callbacks --------------------------------------------------------------
def feed(cap, name, value, type_str="double", t_us=None):
    """Deliver one update to the sample callback."""
    _origin(cap)
    cap._on_event(make_event(name, value, type_str, t_us))


def feed_rule(cap, index, value, type_str="double", t_us=None, name=None):
    """Deliver one update to event rule `index`."""
    _origin(cap)
    cap._on_rule(index, make_event(name, value, type_str, t_us))


def feed_trigger(cap, value, type_str="double", t_us=None, name=None):
    """Deliver one update to the trigger."""
    _origin(cap)
    cap._on_trigger(make_event(name, value, type_str, t_us))


def feed_update(cap, name, value, type_str="double", t_us=None, *, sample=True, rule=None, trigger=False):
    """Deliver one stand-in update to exactly the callbacks the test exercises (the same event object each time)."""
    _origin(cap)
    ev = make_event(name, value, type_str, t_us)
    if sample:
        cap._on_event(ev)
    if rule is not None:
        cap._on_rule(rule, ev)
    if trigger:
        cap._on_trigger(ev)


def event_query(cap, after=None, limit=20):
    return ask(cap, None, "latest", events_after=after, max_events=limit)["events"]


def query(cap):
    return ask(cap, None, "samples")


def rule_update(cap, index, value, type_str="double"):
    topic = SimpleNamespace(getTypeString=lambda: type_str)
    if cap._clock.start_local_us is None:
        cap._clock.start(_now())
    nt_value = _maker(type_str, value)(value)
    cap._on_rule(index, SimpleNamespace(data=SimpleNamespace(topic=topic, value=nt_value)))


def notify_sample(cap, ev):
    cap._on_sample_notification(ev)


def notify_rule(cap, index, ev):
    cap._on_rule_notification(index, ev)


def notify_trigger(cap, ev):
    cap._on_trigger_notification(ev)


# -- lifecycle and clock ----------------------------------------------------
def set_clock_origin(cap, local_us):
    cap._clock.start(local_us)


def advance_clock(max_spins=1_000_000):
    """Spin until the NT local clock reads a new value, so the next stamp is strictly later (no fixed sleep)."""
    start = _now()
    for _ in range(max_spins):
        if _now() != start:
            return
    raise AssertionError("the NT local clock did not advance")


def stub_loop():
    """The event-loop stand-in `Capture.start` requires: both callbacks are recorded as no-ops."""
    return SimpleNamespace(call_soon_threadsafe=lambda *_args: None, call_later=lambda *_args: None)


def set_loop(cap, scheduled=None):
    """A stand-in event loop; `scheduled` collects the call_soon_threadsafe payloads when given."""
    if scheduled is None:
        cap._loop = stub_loop()
    else:
        cap._loop = SimpleNamespace(call_soon_threadsafe=lambda *args: scheduled.append(args),
                                    call_later=lambda *_args: None)


def lock(cap):
    return cap._lock


def start_gap(cap):
    cap._start_gap()


def run_time_ms(cap):
    return cap._run_time_ms()


def sample_time_ms(cap, value):
    return cap._sample_time_ms(value)


def after_latest_gap(cap, value):
    return cap.after_latest_gap(value)


def set_instance(cap, inst):
    cap._inst = inst


def add_handle(cap, handle):
    """Record a listener handle as if the capture had registered it (so a failed start must remove it)."""
    cap._handles.append(handle)


def set_clock_synchronized(cap, value):
    cap._clock.synchronized = value


def clock_synchronized(cap):
    return cap._clock.synchronized


def clock_rearm_after(cap):
    return cap._clock.rearm_after_us


# -- sample buffer ----------------------------------------------------------
def retained(cap, topic):
    """The samples still held for `topic` (a copy of the buffer's rows)."""
    return list(cap.buffer.samples[topic])


def retained_times(cap, topic):
    """The `t_ms` column of the samples held for `topic`."""
    return [row[0] for row in cap.buffer.samples[topic]]


def retained_count(cap, topic):
    return len(cap.buffer.samples[topic])


def append_sample(cap, topic, row):
    cap.buffer.samples[topic].append(row)
    cap.buffer.reindex()


def seed_samples(cap, topic, rows, type_str=None):
    """Replace the rows (and, when given, the recorded type) of one topic."""
    cap.buffer.samples[topic] = deque(rows)
    cap.buffer.reindex()
    if type_str is not None:
        cap.buffer.types[topic] = type_str


def seed_types(cap, types):
    cap.buffer.types.update(types)


def seed_sizes(cap, topic, sizes):
    cap.buffer.sizes[topic] = deque(sizes)


def buffer_stats(cap):
    """The buffer's counters: total, bytes, dropped, coalesced and ignored updates."""
    buf = cap.buffer
    return SimpleNamespace(total=buf.total, bytes=buf.bytes, dropped=buf.dropped,
                           coalesced=buf.coalesced, ignored_topics=buf.ignored_topics)


def evicted_frontier(cap, topic):
    return cap.buffer.evicted_frontiers[topic]


def drop_barrier_position(cap, topic):
    return cap.buffer.drop_windows(topic)[0][0]


def set_drop_barriers(cap, topic, windows):
    offset = cap.buffer.front_offset.get(topic, 0)
    cap.buffer.drop_barriers[topic] = [w._replace(position=w.position + offset) for w in windows]


def set_topic_barriers(cap, topic, windows):
    offset = cap.buffer.front_offset.get(topic, 0)
    cap.buffer.topic_barriers[topic] = [w._replace(position=w.position + offset) for w in windows]


def drop_barriers(cap, topic):
    return cap.buffer.drop_windows(topic)


def topic_barriers(cap, topic):
    return cap.buffer.topic_windows(topic)


def evict(cap, topic):
    cap.buffer.evict(topic)


def set_last_update_time(cap, topic, value):
    cap.buffer.note_update_time(topic, value[0], value[1])


# -- gaps -------------------------------------------------------------------
def add_gap(cap, gap):
    cap._admission.gaps.append(gap)


def set_gaps(cap, rows):
    """Install the gap history exactly as given."""
    cap._admission.gaps = deque(rows)


def set_last_gap(cap, gap):
    cap._admission.gaps[-1] = gap


def close_last_gap(cap):
    """Close the newest gap at the moment it opened (a zero-length outage)."""
    gaps = cap._admission.gaps
    gaps[-1] = Gap(gaps[-1].start, gaps[-1].start)


def read_gaps(cap):
    return list(cap._admission.gaps)


def gap_history(cap):
    """(omitted gap count, newest omitted gap end) after history was trimmed, else None."""
    return cap._admission.omitted


# -- trigger, event rules and the log ---------------------------------------
def trigger_state(cap):
    return cap.trigger_state


def trigger_ms(cap):
    """When the trigger fired, ms since capture start (None while it has not)."""
    return cap._trig.fired_ms


def trigger_waiting(cap):
    """The condition text the trigger is still waiting for, or None once it is done."""
    return cap._trig.waiting


def event_state(cap, index):
    return cap._event_states[index]


def event_states(cap):
    return list(cap._event_states)


def append_event(cap, item):
    cap._events.append(item)


def set_event_sequence(cap, value):
    cap._event_sequence = value


def event_sequence(cap):
    """How many event-rule records the capture has written so far."""
    return cap._event_sequence


def clear_handler_types(cap):
    cap._handler_types.clear()


def attach_log(cap, file):
    cap._logfile.file = file


def log_file(cap):
    return cap._logfile.file


def value_event(name, value, type_str="double", t_us=None):
    """A value-event stand-in that also answers is_(), for the notification callbacks."""
    ev = make_event(name, value, type_str, t_us)
    return SimpleNamespace(is_=lambda flag: flag != ntcore.EventFlags.kUnpublish, data=ev.data)


def notify_update(cap, name, value, type_str="double", t_us=None, *, rule=None, trigger=False):
    """One value update through the notification callbacks (the same event object each time)."""
    _origin(cap)
    ev = value_event(name, value, type_str, t_us)
    notify_sample(cap, ev)
    if rule is not None:
        notify_rule(cap, rule, ev)
    if trigger:
        notify_trigger(cap, ev)


def read_events(cap):
    """The observed event records still buffered (a copy of the inbox rows)."""
    return list(cap._events)


def ask(cap, topics=None, reduce="summary", *, fields=None, since_ms=None, until_ms=None,
        max_samples=20, precision=4, rel_trigger=False, **spec_options):
    """Answer a query through the public `execute_query`; `spec_options` are the remaining QuerySpec fields."""
    spec = QuerySpec(topics=topics, reduce=reduce, fields=fields, since_ms=since_ms, until_ms=until_ms,
                     max_samples=max_samples, precision=precision, **spec_options)
    return cap.execute_query(replace(spec, rel_trigger=rel_trigger))
