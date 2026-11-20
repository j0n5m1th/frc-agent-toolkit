# Picking a wpilog tool

Use the narrowest tool that answers the question.

- Where to start on an unknown log: `wpilog_scan` (ranked flags, and what it could not check).
- A moment in time: `wpilog_get_values`.
- Stats, edges, rate, step_response, peaks (worst spikes and dips), buckets (min, max, sample mean per time slice): `wpilog_query`. `by_mode=true` answers once per enabled period.
- When a condition held: `wpilog_when`. Its `also` option can join files (for example, low battery while a motor drew current). `sample=[topics]` reads other topics at the start of each stretch.
- Messages and text: `wpilog_text`.
- Two matches: `wpilog_query` with `compare_with`, or `wpilog_diff`.
- Finding names: `name_contains` takes `a|b` (for example `battery|voltage`).

Keep replies small: few topics, short windows, `fields` for structs. On `truncated`, narrow the request.

## Symptom to query

Times are after the first auto unless you pass `align`.

- **Browned out or reset:** `wpilog_scan` (brownout flag, battery minimum), then `wpilog_when` on the battery `< 7` with `sample` of motor currents.
- **Auto did nothing:** `wpilog_open` for the auto period; `wpilog_query align=auto reduce=changes` on the auto name and commands; `wpilog_text name_contains=error|exception`.
- **Mechanism slow or jittery:** `wpilog_query reduce=percentiles` or `rate`; `peaks` for the worst moments; `by_mode` to compare auto with teleop.
- **Lost connection or lag:** `wpilog_scan` link numbers; `wpilog_query` on `/DSLog/PacketLoss` and `/DSLog/TripTimeMS` with `reduce=peaks`.
- **Loop overruns:** `wpilog_text name_contains=overrun`; `wpilog_when` on the loop-time topic.
- **What changed since the last match:** `wpilog_diff`; for signals, `compare_with`.

## Syncing several files

1. Pick files whose dates and lengths match the same match. Open each.
2. `wpilog_sync` with the robot log first, then use `log=s1`.
3. Report `method`, `offset_ms` and `residual_ms`, and relay any warning.
4. If it fails or is ambiguous, ask for a moment seen in both files and pass `offsets_ms`.
5. Names in two logs appear as `l2:<name>`. Closing a file closes its synced view.
