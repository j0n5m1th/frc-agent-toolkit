"""One long-lived NT4 client connection shared by all tools (connect, lazy connect, reconnect wait, idle timeout).

It owns the ntcore instance and the per-connection bookkeeping (discovery, meta-topic and schema subscriptions, the
publisher registry, the capture registry); the work is in `access/`, `capture/` and `schema_listener.py`. Why one shared
connection: docs/ARCHITECTURE.md.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from typing import Any

import ntcore

from frc_mcp_wpilib.conditions import WaitOp
from frc_mcp_wpilib.reduce.models import Condition
from frc_mcp_wpilib.schema import state as schema_state
from frc_mcp_wpilib.values import unit_of

from . import clock, config, schema_listener
from .access import reading, topic_listing, waiting, writer
from .access.publisher_registry import PublisherRegistry
from .access.publishing import PublishOptions, SetResult
from .access.reading import ReadResult
from .access.topic_listing import Expansion, TopicFilter, TopicListing
from .capture import (
    Capture,
    CaptureRegistry,
    CaptureSpec,
    QuerySpec,
    new_log_path,
    wait_ended,
)
from .endpoint import check_connection
from .errors import NT4Error
from .gate import OperationGate

RECONNECT_WAIT_S = 3.0  # how long a tool waits for ntcore to reconnect before reporting the loss
AUTO_CONNECT_TIMEOUT_S = 5.0  # connect timeout for the lazy NT4_HOST connection
MAX_CAPTURES = 5  # oldest stopped capture is evicted first
_log = logging.getLogger(__name__)


class NT4Session:
    """The one NT4 connection shared by every tool.

    A sync method works only from state ntcore or our registries already hold and never waits, so the caller must be
    connected, normally inside `async with session.operation():`. An async method may wait on the network. Methods that
    need a connection raise NT4Error when not connected unless noted.

    | Method | Kind | Why |
    |---|---|---|
    | `list_topics`, `expand`, `topic_unit`, `field_names` | sync | read ntcore's cached topic table and the schema state |
    | `status`, `estimate_server_now_us`, `clock_synchronized`, `publisher_expiry`, `get_capture` | sync | read cached clock, publisher or capture state |
    | `set_value`, `unpublish` | sync | publish or release a local publisher; ntcore queues the write |
    | `capture_start`, `capture_stop`, `note_activity`, `disconnect` | sync | start, stop or forget a capture, or re-arm/tear down timers. `capture_start` still needs the running loop |
    | `get_values`, `inspect_meta`, `wait_for` | async | wait for values or a server meta-topic to arrive |
    | `announce_meta` | async | sleeps a grace period so the server can announce meta-topics |
    | `capture_wait`, `watch_once` | async | wait for a capture to end or for its duration to pass |
    | `connect`, `ensure_connected`, `operation`, `disconnect_safely` | async | connect, or wait for reconnect or the link to go quiet |

    Timeouts: `wait_for` returns `met=False`; `capture_wait` returns the capture (read its `active` flag); `connect` and
    `inspect_meta` (no value within 3 s) raise NT4Error, because neither has a partial result.
    """

    def __init__(self) -> None:
        self._inst: ntcore.NetworkTableInstance | None = None
        self._discovery: ntcore.MultiSubscriber | None = None  # keeps topic announcements flowing
        self._meta_listing: dict[str, ntcore.MultiSubscriber] = {}  # explicit `$` prefixes, kept until disconnect
        self._meta_ready: dict[str, asyncio.Event] = {}  # per `$` prefix: set once its announce grace has elapsed
        self._schemas: list[Any] = []  # keeps struct schema subscription + listener alive
        self._publishers = PublisherRegistry()  # our publishers, their TTL timers/deadlines, and ghost topics
        self.capture_registry = CaptureRegistry()  # our background captures, their ids and duration budgets
        self._idle: asyncio.TimerHandle | None = None
        self._gate = OperationGate()  # transition lock, idle event, active-operation counter
        self.host = ""
        self.port = config.DEFAULT_PORT
        self.identity = ""

    # -- connection ---------------------------------------------------------
    @property
    def inst(self) -> ntcore.NetworkTableInstance:
        """The live ntcore instance.

        Raises NT4Error: when there is no connection (call nt4_connection first, or set NT4_HOST).
        """
        if self._inst is None:
            raise NT4Error("Not connected. Call nt4_connection first.")
        return self._inst

    def note_activity(self) -> None:
        """Record activity: (re)arm the idle timer. Must run on the event loop."""
        if self._idle is not None:
            self._idle.cancel()
            self._idle = None
        secs = config.idle_seconds()
        if secs > 0 and self._inst is not None:
            self._idle = asyncio.get_running_loop().call_later(secs, self._on_idle)

    def _on_idle(self) -> None:
        self._idle = None
        if self._inst is None:
            return
        if self._gate.busy or self._publishers.is_busy() or self.capture_registry.is_busy():
            self.note_activity()  # we are still doing something: publishers/captures die with the connection
        else:
            self.disconnect()

    @property
    def active_operations(self) -> int:
        """Calls in flight (the gate owns the counter); read by tests to assert that a call has finished."""
        return self._gate.active_operations

    @asynccontextmanager
    async def operation(self, require_connection: bool = True) -> AsyncIterator[None]:
        """Protect the shared instance while allowing concurrent ordinary tool calls.

        Re-arms the idle timer once per call: `ensure_connected` does it when a connection is required, otherwise we do.
        `connect`, `disconnect_safely` and the same-endpoint shortcut in `nt4_connection` re-arm as their last step.
        Raises NT4Error: `require_connection` is set and the lazy connect fails.
        """
        async with self._gate.operation(
            prepare=self.ensure_connected if require_connection else None,
            is_connected=(lambda: self.connected) if require_connection else None,
        ):
            if not require_connection:
                self.note_activity()  # the connection path already re-armed inside ensure_connected
            yield

    async def ensure_connected(self) -> None:
        """Lazy connect: if NT4_HOST is configured and we have no connection, make one. A dropped connection gets up to
        RECONNECT_WAIT_S to come back first.
        Raises NT4Error: no connection and no NT4_HOST; still down after the wait; invalid NT4_* setting; no server answers.
        """
        await self._gate.pass_through()
        self.note_activity()
        if self._inst is not None and not self._inst.isConnected():
            await self._await_reconnect()
        if self._inst is None:
            host = config.default_host()
            if host is None:
                raise NT4Error("Not connected. Call nt4_connection first (or set NT4_HOST for auto-connect).")
            await self.connect(host, config.default_port(), config.default_identity(), AUTO_CONNECT_TIMEOUT_S)

    async def _await_reconnect(self) -> None:
        """Robot rebooting/redeploying: ntcore retries by itself, so give it a moment before failing."""
        deadline = time.monotonic() + RECONNECT_WAIT_S
        while self._inst is not None and not self._inst.isConnected() and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            await self._gate.pass_through()
        if self._inst is not None and not self._inst.isConnected():
            raise NT4Error(
                f"The connection to {self.host}:{self.port} was lost and is not back yet (ntcore keeps retrying). "
                "Retry in a few seconds; nt4_status shows the current state."
            )

    @property
    def connected(self) -> bool:
        return self._inst is not None and self._inst.isConnected()

    async def connect(self, host: str, port: int, identity: str, timeout_s: float) -> None:
        """Connect to host:port unless already connected there.
        Raises NT4Error: invalid host or client name; port 1740; a host that is not a robot-network address; calls running and the target differs; no server within timeout_s.
        """
        config.check_target(host, identity)
        await check_connection(host, port)  # every connect passes here: nt4_connection, the lazy connect of a read tool, and the diagnostic command
        lock, _ = self._gate.acquire_pair()
        async with lock:
            if self.connected and (self.host, self.port) == (host, port):
                return
            if self._gate.active_operations:
                raise NT4Error("Cannot reconnect while NT4 requests are running; wait for or cancel them first.")
            await self._connect(host, port, identity, timeout_s)

    async def _connect(self, host: str, port: int, identity: str, timeout_s: float) -> None:
        self.disconnect()
        inst = ntcore.NetworkTableInstance.create()
        self._inst, self.host, self.port, self.identity = inst, host, port, identity
        connected = False
        try:
            inst.startClient4(identity)
            inst.setServer(host, port)
            self._discovery = ntcore.MultiSubscriber(inst, [""], ntcore.PubSubOptions(topicsOnly=True))
            schema_sub = ntcore.MultiSubscriber(inst, ["/.schema/"])
            handle = inst.addListener(schema_sub, ntcore.EventFlags.kValueAll | ntcore.EventFlags.kImmediate,
                                      schema_listener.on_schema)
            self._schemas = [schema_sub, handle]
            deadline = time.monotonic() + timeout_s
            while not inst.isConnected():
                if time.monotonic() > deadline:
                    secure = (f" Port {config.SECURE_PORT} is NT4's secure (wss) port, which ntcore does not "
                              f"support; use {config.DEFAULT_PORT}.") if port == config.SECURE_PORT else ""
                    raise NT4Error(
                        f"No NT4 server answered at {host}:{port} within {timeout_s}s. Check the "
                        f"robot/sim is running and the address is reachable (sim: 127.0.0.1).{secure}"
                    )
                await asyncio.sleep(0.05)
            await self._wait_until_quiet(inst)
            connected = True
        finally:
            if not connected and self._inst is inst:
                self.disconnect()
        self.note_activity()  # the re-arm for this call, once the instance exists

    async def disconnect_safely(self, wait: bool = False) -> None:
        """Disconnect once running calls finish (`wait`), or refuse while they run.

        Raises NT4Error: when calls are running and `wait` is false.
        """
        lock, idle = self._gate.acquire_pair()
        async with lock:
            if self._gate.active_operations and not wait:
                raise NT4Error("Cannot disconnect while NT4 requests are running; wait for or cancel them first.")
            await idle.wait()
            self.disconnect()
            self.note_activity()  # the re-arm for this call (no instance left, so nothing is armed)

    @staticmethod
    async def _wait_until_quiet(inst: ntcore.NetworkTableInstance, quiet: float = 0.2, limit: float = 1.5) -> None:
        """Wait until topic announcements stop arriving, so a list right after connect is complete."""
        start = time.monotonic()
        last, stable_since = -1, start
        while (now := time.monotonic()) < start + limit:
            n = len(inst.getTopicInfo(""))
            if n != last:
                last, stable_since = n, now
            elif now - start >= 0.3 and now - stable_since >= quiet:
                return
            await asyncio.sleep(0.05)

    def disconnect(self) -> None:
        if self._idle is not None:
            self._idle.cancel()
            self._idle = None
        if self._inst is not None:
            self.capture_registry.close()
            self._publishers.close()
            self._discovery = None
            for ready in self._meta_ready.values():
                ready.set()  # release callers still in a grace period; their subscription is gone with the instance
            self._meta_ready.clear()
            self._meta_listing.clear()
            self._schemas = []
            schema_state.forget()
            ntcore.NetworkTableInstance.destroy(self._inst)
            self._inst = None

    def status(self) -> dict[str, Any]:
        try:
            restriction = config.write_restriction(port=self.port)
        except NT4Error as e:
            restriction = str(e)
        # One boolean from the write gate, named like the commands server's `deploy_allowed`, plus the sentence that
        # names the setting to change while writes are off.
        safety: dict[str, Any] = {"writes_allowed": restriction is None}
        if restriction is not None:
            safety["write_reason"] = restriction
        if self._inst is None:
            return {"connected": False, **safety, **({"auto_connect": config.default_host()} if config.default_host() else {})}
        out: dict[str, Any] = {"connected": self.connected, "server": f"{self.host}:{self.port}", **safety}
        if self._publishers.is_busy():
            out["published"] = self._publishers.names
        if (table := self._publishers.expiry_table(time.monotonic())):
            out["publisher_expiry_s"] = table
        if self.capture_registry:
            out["captures"] = [c.info() for c in self.capture_registry.all()]
        if self.connected:
            out["topic_count"] = len(self._inst.getTopicInfo(""))
            if (server_clock := self.estimate_server_now_us()) is not None:
                out["server_time_us"], out["clock_offset_us"] = server_clock
        return out

    # -- topics -------------------------------------------------------------
    def list_topics(self, f: TopicFilter) -> TopicListing:
        """See `topic_listing.list_topics`: (topics, hidden_names)."""
        return topic_listing.list_topics(self.inst, self._publishers, f)

    async def announce_meta(self, prefix: str, grace_s: float = 0.4) -> None:
        """Make server meta-topics listable. They are announced only to a subscription whose prefix starts with `$` (NT4 spec),
        so the standing `""` discovery subscription never sees them. Other prefixes never raise.

        The first caller for a `$` prefix subscribes and sleeps `grace_s`; every other caller returns only once that grace has
        elapsed (once per connection). A cancelled first caller still releases the others, and `disconnect` does too.
        """
        if not prefix.startswith("$"):
            return
        if (ready := self._meta_ready.get(prefix)) is not None:
            await ready.wait()
            return
        subscriber = ntcore.MultiSubscriber(self.inst, [prefix], ntcore.PubSubOptions(topicsOnly=True))
        ready = asyncio.Event()
        self._meta_listing[prefix] = subscriber
        self._meta_ready[prefix] = ready
        try:
            await asyncio.sleep(grace_s)
        finally:
            ready.set()

    def expand(self, base: str) -> Expansion:
        """See `topic_listing.expand`: (names, truncated_at_GROUP_LIMIT)."""
        return topic_listing.expand(self.inst, base)

    async def get_values(self, topics: list[str], timeout_s: float, grace_s: float = 0.3) -> ReadResult:
        """One-shot read; see `reading.get_values`."""
        return await reading.get_values(self.inst, topics, timeout_s, grace_s)

    def topic_unit(self, name: str) -> str | None:
        """The unit the publisher declared for this topic (`unit`/`units` property), if any."""
        return unit_of(self.inst.getTopic(name).getProperties())

    def field_names(self, types: Iterable[str]) -> dict[str, list[str]]:
        """{type: field names} for the struct and protobuf types in `types` (see `schema_state.fields_by_type`).

        Reads the schema state, so it needs no connection.
        """
        return schema_state.fields_by_type(types)

    async def inspect_meta(self, meta_topic: str) -> list[Any]:
        """A server meta-topic's decoded msgpack rows; raises NT4Error when the meta-topic is unknown."""
        return await topic_listing.inspect_meta(self.inst, meta_topic)

    async def wait_for(self, name: str, op: WaitOp, target: Any, field: str | None,
                       timeout_s: float, also: Sequence[Condition] = ()) -> waiting.WaitResult:
        """Block (async) until a condition on one topic (and every `also`) holds; WPILibMCPError on a bad target or field, met=False on a timeout."""
        return await waiting.wait_for(self.inst, name, op, target, field, timeout_s, also)

    # -- publishing ---------------------------------------------------------
    def set_value(self, name: str, value: Any, type_str: str | None,
                  options: PublishOptions | None = None) -> SetResult:
        """Publish `value` and/or change topic properties (option semantics: `writer.set_value`); raises NT4Error."""
        return writer.set_value(self.inst, self._publishers, name, value, type_str, options or PublishOptions(), self.port)

    def clock_synchronized(self) -> bool:
        """True once the NT4 clocks have synchronized, so server-time ages are meaningful."""
        return self.inst.getServerTimeOffset() is not None

    def estimate_server_now_us(self) -> tuple[int, int] | None:
        """(estimated server time now, offset) in microseconds, or None until the clocks have synchronized."""
        return clock.estimate_server_now_us(self.inst)

    def publisher_expiry(self, name: str) -> float | None:
        return self._publishers.expires_in(name, time.monotonic())

    def unpublish(self, name: str) -> bool:
        """Release our publisher for `name`; True if we held one (raises NT4Error). A retained or persistent topic stays listable."""
        return writer.unpublish(self.inst, self._publishers, name, self.port)

    # -- captures -----------------------------------------------------------
    def capture_start(self, spec: CaptureSpec, *, log: bool) -> Capture:
        """Start and register a background capture described by `spec`. Call it from the running event loop.
        Raises NT4Error: not connected; otherwise as `CaptureRegistry.start` does.
        """
        inst = self.inst
        return self.capture_registry.start(
            inst, spec, max_captures=MAX_CAPTURES, new_log_path=new_log_path if log else None,
        )

    def get_capture(self, cid: str) -> Capture:
        """The capture with id `cid`.

        Raises NT4Error: as `CaptureRegistry.get` does.
        """
        return self.capture_registry.get(cid)

    def capture_stop(self, cid: str, *, discard: bool) -> Capture:
        """Stop capture `cid`; `discard` also forgets it.

        Raises NT4Error: as `CaptureRegistry.stop` does.
        """
        return self.capture_registry.stop(cid, discard=discard)

    async def capture_wait(self, cid: str, timeout_s: float) -> Capture:
        """The capture `cid`, once it has stopped or `timeout_s` has passed (the query tool's `wait_s`).
        Returns on timeout too: check `cap.active`. Raises NT4Error as `get_capture` does.
        """
        cap = self.get_capture(cid)
        await wait_ended(cap, timeout_s)
        return cap

    async def watch_once(self, spec: CaptureSpec, query: QuerySpec) -> dict[str, Any]:
        """One-shot capture: record `spec.duration_s`, stop, return the result of `query`, free the capture (no `id` or `active` keys).
        Raises NT4Error as `CaptureRegistry.watch` does.
        """
        return await self.capture_registry.watch(self.inst, spec, query, max_captures=MAX_CAPTURES)


# One process = one robot connection: the MCP server is single-session, so tools share this instance by import.
# Tests and reconnects reset state through its own methods (disconnect forgets the schemas), never by rebinding.
session = NT4Session()
