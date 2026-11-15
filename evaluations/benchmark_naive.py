"""The "obvious alternative" the payload benchmark compares against: raw ntcore dumps and token estimates."""

import json
import time

import ntcore

CHARS_PER_TOKEN = 4
QUEUE_POLL_S = 0.01


def tokens(text: str) -> int:
    return round(len(text) / CHARS_PER_TOKEN)


def value_json(v) -> object:
    raw = v.value()
    return raw.hex() if isinstance(raw, (bytes, bytearray)) else raw


def naive_dump(inst: ntcore.NetworkTableInstance, names: list[str] | None = None) -> str:
    """What a script without this server would print: every topic with type and current value."""
    rows = []
    for t in inst.getTopics():
        name = t.getName()
        if names is not None and name not in names:
            continue
        rows.append({"name": name, "type": t.getTypeString(), "value": value_json(inst.getEntry(name).getValue())})
    return json.dumps(rows)


def naive_recording(inst: ntcore.NetworkTableInstance, names: list[str], seconds: float) -> str:
    """Every update of the given topics as [topic, time_us, value] rows (what hand-rolled logging prints)."""
    rows: list[list[object]] = []
    poller = ntcore.NetworkTableListenerPoller(inst)
    sub = ntcore.MultiSubscriber(inst, names, ntcore.PubSubOptions(sendAll=True))  # exact names, prefix-matched
    poller.addListener(sub, ntcore.EventFlags.kValueAll | ntcore.EventFlags.kImmediate)
    end = time.time() + seconds
    while time.time() < end:
        for ev in poller.readQueue():
            d = ev.data
            rows.append([d.topic.getName(), d.value.time(), value_json(d.value)])
        time.sleep(QUEUE_POLL_S)
    return json.dumps(rows)
