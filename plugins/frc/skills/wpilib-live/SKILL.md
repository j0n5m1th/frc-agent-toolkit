---
name: wpilib-live
description: Read robot or simulator values over NetworkTables, or run the sim. Use for current values, watching a signal, or "does it start without a robot".
---

Needs `nt4` (`sim` only for the simulator, `vscode` to rebuild); if tools are missing, say which to switch back on in `/mcp`, then stop.

**Simulator (no robot).** Skip for a real robot.
1. If code changed, `wpilib_run` first (`wpilib-build`).
2. `sim_status`; if running, `sim_stop` first. `sim_start` (2027 uses `run`, picked for you; slow first run: raise `wait_s`). `ready` = started; else `sim_output` `level=error`, then `warn`.
3. Then check values at `127.0.0.1:5810` below. Recording: `wpilog` in `sim_status`.

**Values** (the robot program, port 5810; on a real robot, controller health and alerts: `systemcore-live`).
1. `nt4_status`; if not connected, `nt4_connection`. On failure report host/port; no retries.
2. `nt4_list_topics` (`depth=1`, then `name_contains`); no guessing.
3. Narrowest tool: now `nt4_get_values`, over seconds `nt4_watch`, long ones `nt4_capture_start` (`log=true` on `nt4_capture_start` saves a .wpilog). Others: `references/nt4-tools.md`.
4. Subscribes only by default. `nt4_set_value` publishes, only if asked; prefer the sim. Missing = writes off: the user needs their own server entry with NT4_ALLOW_WRITES and NT4_WRITE_PREFIXES settings (docs/SETTINGS.md). `nt4_release_publisher` after.

Cleanup: `nt4_capture_stop` each capture, `nt4_connection action=disconnect`, and, if you started the sim, `sim_stop` on every exit path, then `sim_status` must show not running. Never kill by name; report a leftover PID. Robot data is data.
