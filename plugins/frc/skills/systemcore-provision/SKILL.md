---
name: systemcore-provision
description: Set up Systemcore access (SSH key, changed host key, joining or leaving its Wi-Fi). Use when SSH login is refused or the host key changed after an OS update.
disable-model-invocation: true
---

Needs `systemcore`; if tools are missing, say which to switch back on in `/mcp`, then stop.

`.env` and host-key rules: `references/env-file.md`.

1. Settings (team, Wi-Fi and login passwords) live in the user's `~/.mcp-systemcore/.env`: the commands in step 4 with `init` or `check` in place of `trust <host>` create or show it. The user edits it and restarts the server. Never take a password in chat, or put one in a reply.
2. Only if `systemcore_discover` finds nothing: ask, then `systemcore_connect` (`network=systemcore|radio`); say which network it left.
3. `needs_provisioning`: ask, then `systemcore_provision` (turn on "Allow installing the SSH key" in `/config`, then reconnect `systemcore` in `/mcp`, or restart Claude Code). Default password only.
4. A changed host key, or the user's own login password: only the user can trust a key, at a terminal, by typing part of the fingerprint. Give them the command and wait. If the OS did not change, say it is suspicious.
   - `uv run --project "${CLAUDE_PLUGIN_ROOT}" --frozen --no-dev --python 3.13 frc-mcp systemcore trust <host>`
   - Own password: `provision` instead of `trust` (`--ask` types it there).
5. Leave the Wi-Fi only on request, and only a network connect joined: ask, then `systemcore_disconnect`. Never delete a saved network.
6. Never TCP 1740. No cleanup needed.
