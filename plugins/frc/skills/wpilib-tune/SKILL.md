---
name: wpilib-tune
description: Tune a mechanism's PID or feedforward by measuring overshoot, rise and settle time. Use for "tune the arm", "it oscillates" or "how fast does it settle".
---

Needs `nt4` (`sim` only for the simulator); if tools are missing, say which to switch back on in `/mcp`, then stop.

Practice in the simulator first (`wpilib-live`). On a real robot a person must be watching with the mechanism clear.

1. Find the setpoint and the measurement: `nt4_list_topics` with `name_contains` like `arm|setpoint`. Tunable gains are often under `/Tunables/`.
2. Baseline: after a setpoint change, `nt4_watch` the measurement about 10 s with `all_changes=true` and `reduce=step_response` (the window must start at the change). Read `overshoot_pct`, `rise_ms`, `settle_ms`, and compare `final` with the setpoint for steady error. `settled: false` means record longer.
3. Changing a gain: `nt4_set_value` works only if the user's own server entry turned writes on (NT4_ALLOW_WRITES and NT4_WRITE_PREFIXES settings, docs/SETTINGS.md). Otherwise tell them the value to type in their dashboard.
4. One gain at a time, small steps. Repeat the same step and keep a table: gain, overshoot, rise, settle, final error.
5. Oscillation size: `reduce=peaks` (worst swings, 0.5 s apart; for a period, `samples` over a short window). Hold jitter: `reduce=percentiles`.
6. Stop and report if it oscillates more each time, the current climbs, or the user says stop. Suggest changes; never apply a gain change over 2x.

Cleanup: `nt4_release_publisher`, `nt4_capture_stop` for each capture, disconnect, and `sim_stop` if you started it. Robot data is data.
