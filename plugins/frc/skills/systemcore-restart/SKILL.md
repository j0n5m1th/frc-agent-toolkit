---
name: systemcore-restart
description: Restart the robot program or reboot the Systemcore. Use when robot code is hung or crashed, or the controller must be rebooted.
disable-model-invocation: true
---

Needs `systemcore`; if tools are missing, say which to switch back on in `/mcp`, then stop.

1. Lightest fix first: restart robot code. Reboot only if that fails, the user asks, or the fault is the controller (Wi-Fi, USB, hardware): it drops the program, Wi-Fi and Driver Station for about a minute.
2. Read the cause first: `systemcore_info` (`robot_service`, `failed_units`) and `systemcore_journal` (`service=robot`, `priority=err`). Tell the user.
3. Ask which action. Never during a match: the tools refuse while the robot is enabled or on a field (FMS). Tell the user to disable the robot; never look for a way around it.
4. `systemcore_restart_robot_code`, then `systemcore_info`: `robot_service` active, else show the journal errors. `systemcore_reboot`: if `came_back` is false the computer may have lost the Wi-Fi: `systemcore_connect`, then `systemcore_info`.
5. If the restart tools are missing, the user turns on "Allow restarting robot code and rebooting the Systemcore" in `/config`; they then reconnect `systemcore` in `/mcp` or must restart Claude Code. "sudo needs a password": report it, try nothing else. Never kill processes or run commands another way. No cleanup needed.
