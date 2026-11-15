"""The write path: publish a value, change topic properties, release publishers (the stateful half of `publishing.py`).

Every function takes the connection's ntcore instance and `PublisherRegistry`, so `NT4Session` stays the only owner of
connection state. TTL timers run on the event loop and release through `_release`; `disconnect()` cancels them all.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import ntcore

from .. import config
from ..clock import estimate_server_now_us
from ..errors import NT4Error
from .publisher_registry import PublisherRegistry
from .publishing import (
    PublishOptions,
    SetResult,
    check_ttl,
    convert_value,
    local_sample_time,
    normalize_properties,
)

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublishContext:
    """What `set_value` learned about the topic, shared by its helper steps."""

    name: str
    topic: ntcore.Topic
    exists: bool
    props: dict[str, Any]
    immutable: bool


def _check_writable(name: str) -> None:
    """`config.check_writable`, with each refusal logged so denied attempts leave a record."""
    try:
        config.check_writable(name)
    except NT4Error as err:
        _log.warning("refused write to %s: %s", name, err)
        raise


def _value_size(value: Any) -> str:
    """Length of a sized value, for the log line; the payload itself is never logged."""
    return str(len(value)) if isinstance(value, (str, bytes, list, tuple, dict)) else "scalar"


def set_value(inst: ntcore.NetworkTableInstance, publishers: PublisherRegistry, name: str, value: Any,
              type_str: str | None, opts: PublishOptions) -> SetResult:
    """Publish `value` and/or change topic properties.

    `opts` (`PublishOptions`): default=True is NT4's "set default", a weak write (timestamp 0) that never overrides a value
    set by anyone else. properties: merged in; null deletes a key. publisher_ttl_s: release the publisher after that long.
    timestamp_us: server-clock sample time.

    Raises NT4Error: writes disabled or `name` outside NT4_WRITE_PREFIXES; a bad option or unsynchronized clock; nothing to
    publish, or properties for a missing topic; `type_str` missing or different from the topic's; unconvertible value.
    """
    _check_writable(name)
    check_ttl(opts.publisher_ttl_s, value)
    props = normalize_properties(opts.properties)
    local_time_us = local_sample_time(opts.timestamp_us, value, opts.default,
                                      lambda: estimate_server_now_us(inst))
    topic = inst.getTopic(name)
    exists = topic.exists()
    # `mutable: false` is an app convention (seen on WPILib/DogLog choosers), not NT4 spec: warn, do not block
    immutable = exists and topic.getProperty("mutable") is False
    ctx = PublishContext(name, topic, exists, props, immutable)
    if value is None:  # properties-only change: any client may do this to an existing topic
        result = _change_properties(ctx)
        _log.info("write topic=%s properties-only", name)
        return result
    type_str = _publish_type(ctx, publishers, type_str)
    nt_value = convert_value(type_str, value, local_time_us)
    _send(ctx, publishers, type_str, nt_value, opts.default)
    if opts.publisher_ttl_s is not None:
        _arm_ttl(inst, publishers, name, opts.publisher_ttl_s)
    publishers.discard_ghost(name)  # live again
    result = _publish_result(ctx, inst, publishers, type_str, nt_value, opts)
    _log.info("write topic=%s type=%s value_size=%s", name, type_str, _value_size(value))
    return result


def unpublish(inst: ntcore.NetworkTableInstance, publishers: PublisherRegistry, name: str) -> bool:
    """Release our publisher for `name`; True if we held one.

    Raises NT4Error: when writes are disabled or `name` is outside NT4_WRITE_PREFIXES.
    """
    _check_writable(name)
    released = _release(inst, publishers, name)
    _log.info("write topic=%s unpublish released=%s", name, released)
    return released


def _release(inst: ntcore.NetworkTableInstance, publishers: PublisherRegistry, name: str) -> bool:
    """Let go of our publisher for `name`; a retained or persistent topic stays listable as a 'ghost'."""
    held = publishers.remove(name)
    if held is not None:
        raw = inst.getTopic(name).getProperties()  # typed as a JSON value; ntcore returns a dict
        props: dict[str, Any] = raw if isinstance(raw, dict) else {}
        if props.get("retained") or props.get("persistent"):
            publishers.add_ghost(name, held[0], props)
    return held is not None


def _change_properties(ctx: PublishContext) -> SetResult:
    if not ctx.props:
        raise NT4Error("Nothing to do: give a `value` and/or `properties`.")
    if not ctx.exists:
        raise NT4Error(f"Topic {ctx.name!r} does not exist; publish a value (with `type`) to create it, "
                       "passing `properties` in the same call.")
    ctx.topic.setProperties(ctx.props)
    return SetResult(ctx.name, None, properties=tuple(sorted(ctx.props)), immutable=ctx.immutable)


def _publish_type(ctx: PublishContext, publishers: PublisherRegistry, type_str: str | None) -> str:
    """The NT4 type to publish as: the caller's, else the topic's known one; they must agree."""
    known = ctx.topic.getTypeString() if ctx.exists else (publishers.ghost(ctx.name) or ("", {}))[0]
    type_str = type_str or known
    if not type_str:
        raise NT4Error(
            f"Topic {ctx.name!r} does not exist yet, so its type is unknown. Pass `type` "
            "(e.g. 'double', 'boolean', 'string', 'double[]')."
        )
    if known and known != type_str:
        raise NT4Error(f"Topic {ctx.name!r} is type {known!r}; cannot publish as {type_str!r}.")
    return type_str


def _send(ctx: PublishContext, publishers: PublisherRegistry, type_str: str, nt_value: ntcore.Value,
          default: bool) -> None:
    """Write through the publisher we hold for this topic, creating it first when needed."""
    name = ctx.name
    held = publishers.get(name)
    if held is None or held[0] != type_str:
        # initial properties travel with the publish (spec: "set on publish")
        pub = ctx.topic.genericPublishEx(type_str, ctx.props or {})
        publishers.add(name, type_str, pub)
        created = True
    else:
        pub = held[1]
        created = False
        if ctx.props:
            ctx.topic.setProperties(ctx.props)
    try:
        (pub.setDefault if default else pub.set)(nt_value)
    except Exception:
        if created:
            publishers.remove(name)
        raise


def _arm_ttl(inst: ntcore.NetworkTableInstance, publishers: PublisherRegistry, name: str, ttl_s: float) -> None:
    """(Re)start the countdown that releases this publisher."""
    deadline = time.monotonic() + ttl_s
    timer = asyncio.get_running_loop().call_later(ttl_s, _release, inst, publishers, name)
    publishers.arm(name, deadline, timer)


def _publish_result(ctx: PublishContext, inst: ntcore.NetworkTableInstance, publishers: PublisherRegistry,
                    type_str: str, nt_value: ntcore.Value, opts: PublishOptions) -> SetResult:
    name = ctx.name
    chooser = name.endswith("/selected/value") and inst.getTopic(name[: -len("value")] + "tune").exists()
    return SetResult(
        topic=name,
        type_str=type_str,
        written=nt_value.value(),
        default=opts.default,
        timestamp_us=opts.timestamp_us,
        properties=tuple(sorted(ctx.props)),
        publisher_expires_in_s=publishers.expires_in(name, time.monotonic()),
        selectable_chooser=chooser,
        immutable=ctx.immutable,
    )
