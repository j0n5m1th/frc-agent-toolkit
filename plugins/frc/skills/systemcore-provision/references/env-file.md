# The Systemcore `.env` file and host keys

Read this when the user asks what goes in the `.env`, why it was refused, or why a host key changed. The rules about passwords stay in SKILL.md.

## The file
- It is `~/.mcp-systemcore/.env`. The user edits it, then restarts the server.
- One `NAME=value` per line.
- An unquoted value loses a trailing ` # comment`.
- In double quotes, `\"`, `\` and `\n` mean a quote, a backslash and a new line.
- In single quotes, everything is literal.
- After a closing quote only spaces or a `# comment` may follow, or the file is refused. The message gives the line number, never the value.
- The file must be UTF-8, or UTF-16 with a byte-order mark.
- A saved Wi-Fi password needs `SYSTEMCORE_WIFI_SSID` too. Without it `systemcore_connect` joins nothing.

## Host keys
- The key is pinned on the first login.
- A changed key stops everything. After an OS update that is normal. Without one, it is suspicious.
- `systemcore_ssh_status` shows `host_key` as unpinned, `matches` or `changed`.
- The user trusts a new key at a terminal with `frc-mcp systemcore trust <host>`, by typing part of the fingerprint. The command is in SKILL.md.
- Dropping the pin with `systemcore_ssh_forget` needs the "Allow installing the SSH key on the Systemcore" plugin setting, and the next login pins the new key with no check. Prefer `trust`.
- A login password the user chose is sent only after the user confirms the host key at a terminal (`provision`, or `trust` first). The assistant may use only the public default login.
