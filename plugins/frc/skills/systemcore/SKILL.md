---
name: systemcore
description: Check the Systemcore controller and its logs. Use for "is the controller OK", a slow or crashing robot, a full disk, support bundles, or match logs.
---

Needs `systemcore`; if tools are missing, say which to switch back on in `/mcp`, then stop.

Options, hints and prune rules: `references/tools.md`.

**Find it.** Often already attached: try the tools first. `systemcore_discover` if no host is known (several: ask, pass `host`). Nothing found: ask, then `systemcore_connect` (needs the Wi-Fi setting in `/config`, then reconnect it in `/mcp` or restart Claude Code). Settings live in a `.env`; the user edits it, never take a password in chat. SSH refused, key changed, provisioning: the user runs `/frc-toolkit:systemcore-provision`.

**Health.** `systemcore_info`: read `warnings`, then `errors`; go deeper (`area`) only where a warning points. No memory or services means no SSH: `systemcore_ssh_status`; without SSH: `systemcore-live`. A problem service: `systemcore_journal` (`service=robot`, `priority=err`). Restart or reboot: the user runs `/frc-toolkit:systemcore-restart`.

**Logs.** `systemcore_bundle` (`symptoms`: one sentence; one-shot, not during a match) gives a zip path. `systemcore_logs` lists them; `systemcore_logs_download`, then wpilib-log-analysis (wpilog opens only project-folder files: ask the user to copy it there).

**Free space,** only on request: `systemcore_logs_prune` dry run first (prefer `keep_newest`; needs the "Allow deleting Systemcore logs" plugin setting). Show the list; only after they agree repeat with `dry_run=false` and `expect_count`. Never retry a refusal another way.

`systemcore_os_download` installs nothing: the user updates at robot.local/configure. Download logs first.

One request at a time. Never TCP 1740. Read-only unless pruning. No cleanup needed.
