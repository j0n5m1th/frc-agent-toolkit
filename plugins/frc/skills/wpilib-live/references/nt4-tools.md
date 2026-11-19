# Picking an NT4 tool

Use the narrowest tool that answers the question.

- Value right now: `nt4_get_values`. For choosers (autonomous picker), use `nt4_get_widgets`.
- A signal over a few seconds: `nt4_watch`.
- Wait for one event: `nt4_wait_for`.
- Long recordings: `nt4_capture_start`, then `nt4_capture_query`. Pass `log=true` to `nt4_capture_start` to also save a .wpilog.
- Who owns a topic: `nt4_inspect`.
- Fast signals: pass `all_changes=true` and relay any gap warnings.
- Worst moments: `reduce=peaks`. Shape of a long run: `reduce=buckets`. Tuning a loop: `wpilib-tune`.

## Writes

Read-only by default. Use `nt4_set_value` only if the user asks, and prefer the simulator. If the tool is missing, writes are off. The plugin cannot turn them on: the user needs their own server entry with the NT4_ALLOW_WRITES and NT4_WRITE_PREFIXES settings (docs/SETTINGS.md). Call `nt4_release_publisher` when done.
