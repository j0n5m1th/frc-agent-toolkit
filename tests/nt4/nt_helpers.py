"""Shared helpers for the server test files (importable as `nt_helpers`; fixtures live in conftest.py)."""

import asyncio
import os
import subprocess
import sys

import ntcore
from envelope_helpers import j

from frc_mcp_nt4.errors import NT4Error
from frc_mcp_nt4.session import session

PORT = 5899


def publish(srv, name, type_str, value):
    pub = srv.getTopic(name).genericPublish(type_str)
    pub.set(value)
    return pub


async def wait_for_client_sync(srv, timeout=5.0):
    """Wait until the client has seen every topic (and value) the test server holds, instead of a fixed sleep."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            client_names = {t.getName() for t in session.inst.getTopics()}
            ready = all(
                t.getName() in client_names and (srv.getEntry(t.getName()).getValue().time() == 0
                                                 or session.inst.getEntry(t.getName()).getValue().time() != 0)
                for t in srv.getTopics() if not t.getName().startswith("$")
            )
        except NT4Error:  # not connected yet: keep waiting
            ready = False
        if ready or asyncio.get_running_loop().time() > deadline:
            return
        await asyncio.sleep(0.02)


SCHEMAS = {
    "Pose2d": "Translation2d translation;Rotation2d rotation",
    "Translation2d": "double x;double y",
    "Rotation2d": "double value",
    "ControlWord": "uint64 opModeHash:56;enum{unknown=0,autonomous=1,teleoperated=2,utility=3} "
                   "uint64 robotMode:2;bool enabled:1;bool eStop:1;bool fmsAttached:1;bool dsAttached:1;",
}


def publish_schemas(srv):
    return [publish(srv, f"/.schema/struct:{n}", "structschema", ntcore.Value.makeRaw(t.encode()))
            for n, t in SCHEMAS.items()]


async def drive(srv, name, values, interval):
    pub = srv.getTopic(name).genericPublish("double")
    for v in values:
        pub.set(ntcore.Value.makeDouble(v))
        await asyncio.sleep(interval)


def make_chooser(srv, base, typ, options, default, active, selected_path, selected):
    return [
        publish(srv, f"{base}/.type", "string", ntcore.Value.makeString(typ)),
        publish(srv, f"{base}/options", "string[]", ntcore.Value.makeStringArray(options)),
        publish(srv, f"{base}/default", "string", ntcore.Value.makeString(default)),
        publish(srv, f"{base}/active", "string", ntcore.Value.makeString(active)),
        publish(srv, f"{base}/{selected_path}", "string", ntcore.Value.makeString(selected)),
    ]


async def get_value(client, topic):
    return j(await client.call_tool("nt4_get_values", {"topics": [topic]}))[topic]


async def wait_for_capture_samples(capture_id, *topics, timeout=5.0):
    """Wait until a capture has recorded at least one sample for every topic (it subscribes after capture_start returns)."""
    cap = session.get_capture(capture_id)
    assert await wait_until(lambda: all(cap.snapshot(list(topics)).buffer.samples.get(t) for t in topics), timeout=timeout)


def captured(capture_id, topic):
    """Values a capture has recorded so far for `topic`, oldest first."""
    return [v for _, v, _ in session.get_capture(capture_id).buffer.samples.get(topic, [])]


async def wait_for_capture_tail(capture_id, topic, values, timeout=5.0):
    """Wait until the newest recorded samples for `topic` equal `values` (updates arrive batched, so this is when they are in)."""
    assert await wait_until(lambda: captured(capture_id, topic)[-len(values):] == values, timeout=timeout)


async def wait_for_tool(client, tool, args, ok, timeout=8.0, step=0.1):
    """Poll a read-only tool until ok(result) holds; returns the last result so the caller's assertions decide pass or fail."""
    end = asyncio.get_running_loop().time() + timeout
    while True:
        result = j(await client.call_tool(tool, args))
        if ok(result) or asyncio.get_running_loop().time() >= end:
            return result
        await asyncio.sleep(step)


async def read_and_discard(client, args):
    """Stop a capture, read it once more, then free it (what a caller does when finished with a capture)."""
    await client.call_tool("nt4_capture_stop", {"capture_id": args["capture_id"]})
    result = await client.call_tool("nt4_capture_query", args)
    await client.call_tool("nt4_capture_stop", {"capture_id": args["capture_id"], "discard": True})
    return result


def watch_idle(session) -> list[float]:
    """Count the real idle-callback runs, so a test can tell 'fired and refused' from 'never fired'.

    `count` grows only when the session's own timer fires, so an assertion on its length is discriminating.
    """
    count: list[float] = []
    original = session._on_idle

    def counted() -> None:
        count.append(1.0)
        original()

    session._on_idle = counted  # type: ignore[method-assign]
    return count


NT4_MAIN = "from frc_mcp_nt4.spec import main; main()"  # what `mcp-wpilib-nt4` runs: for `python -c`, there is no `python -m` any more


async def wait_until(predicate, timeout=8.0, step=0.1):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if predicate():
            return True
        await asyncio.sleep(step)
    return False


def toolset_listing(toolset: str) -> subprocess.CompletedProcess:
    code = (
        "import asyncio\n"
        "from fastmcp import Client\n"
        "from frc_mcp_nt4.spec import SPEC\n"
        "mcp, apply_toolset = SPEC.load()\n"
        "apply_toolset()\n"
        "async def main():\n"
        "    async with Client(mcp) as c:\n"
        "        tools = await c.list_tools()\n"
        "        print(','.join(sorted(t.name for t in tools)))\n"
        "        print(next(t.description for t in tools if t.name == 'nt4_watch'))\n"
        "asyncio.run(main())\n"
    )
    env = {**os.environ, "NT4_TOOLSET": toolset}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60, check=False)
