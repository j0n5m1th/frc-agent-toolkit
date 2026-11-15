# Picking a wpilog tool

Use the narrowest tool that answers the question.

- A moment in time: `wpilog_get_values`.
- Stats, edges, rate, step_response: `wpilog_query`.
- When a condition held: `wpilog_when`. Its `also` option can join files (for example, low battery while a motor drew current).
- Messages and text: `wpilog_text`.
- Two matches: `wpilog_query` with `compare_with`, or `wpilog_diff`.

Keep replies small: few topics, short windows, `fields` for structs. On `truncated`, narrow the request.

## Syncing several files

1. Pick files whose dates and lengths match the same match. Open each.
2. `wpilog_sync` with the robot log first, then use `log=s1`.
3. Report `method`, `offset_ms` and `residual_ms`, and relay any warning.
4. If it fails or is ambiguous, ask for a moment seen in both files and pass `offsets_ms`.
5. Names in two logs appear as `l2:<name>`. Closing a file closes its synced view.
