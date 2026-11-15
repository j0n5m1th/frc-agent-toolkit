"""SSH access: status of the key and host key, installing the key, forgetting a device. Trusting a host key is a terminal command (`trust`), not a tool."""

import asyncio
from typing import Any

from fastmcp.tools import ToolResult

from frc_mcp_core.reply import reply
from frc_mcp_core.toolset import gated_by

from .. import config, net, ssh, state
from ..app import FULL_ONLY_TAG, PROVISION, READ_ONLY, STATE, Host, mcp, tool_errors
from ..errors import NeedsProvisioning, SystemcoreError
from ..target import resolve


async def _current_os_hash(host: str) -> str | None:
    try:
        return (await net.os_info(host)).get("hash")
    except SystemcoreError:
        return None


@mcp.tool(annotations={**READ_ONLY, "title": "SSH access status"}, output_schema=None)
@tool_errors
async def systemcore_ssh_status(host: Host = None) -> ToolResult:
    """Can this server log in over SSH? Reports the host key (new, matches, changed) and whether the key is installed."""
    target = await resolve(host)
    os_hash = await _current_os_hash(target)
    presented = await asyncio.to_thread(ssh.probe_host_key, target)
    pinned = state.pinned(target)
    out: dict[str, Any] = {"host": target, "user": config.user(), "key": state.fingerprint_of_public_line(state.public_key_line()),
                           "presented_host_key": presented}
    if pinned is None:
        out["host_key"] = "unpinned (pinned on first login)"
    elif pinned["fingerprint"] == presented:
        out["host_key"] = "matches"
    else:
        out["host_key"] = "changed"
        out["login"] = "not tried"
        out["hint"] = ssh.host_key_message(target, pinned, presented, os_hash)
        return reply(out)
    try:
        await asyncio.to_thread(ssh.check_login, target, os_hash)
        out["login"] = "ok"
    except NeedsProvisioning as e:
        out["login"] = "needs_provisioning"
        out["hint"] = str(e)
    return reply(out)


@mcp.tool(annotations={**PROVISION, "title": "Install the SSH key"}, output_schema=None, tags=gated_by(config.provision))
@tool_errors
async def systemcore_provision(host: Host = None) -> ToolResult:
    """Install this server's SSH key with the default login password. Ask first. Never ask for or pass a password. A custom password or a changed host key is for the user's terminal. Off unless SYSTEMCORE_ALLOW_PROVISION=1."""
    config.provision.require()
    target = await resolve(host)
    os_hash = await _current_os_hash(target)
    if os_hash is None:
        raise SystemcoreError(f"{target} did not answer like a Systemcore (no OS hash), so no password was sent to it.")
    return reply(await asyncio.to_thread(ssh.provision_sync, target, os_hash))


@mcp.tool(annotations={**STATE, "title": "Forget a Systemcore"}, output_schema=None, tags={FULL_ONLY_TAG, *gated_by(config.provision)})
@tool_errors
async def systemcore_ssh_forget(host: Host = None) -> ToolResult:
    """Drop the pinned SSH host key of one Systemcore (this server's record only). Off unless SYSTEMCORE_ALLOW_PROVISION=1."""
    config.provision.require()
    target = await resolve(host)
    return reply({"host": target, "forgotten": state.forget(target)})
