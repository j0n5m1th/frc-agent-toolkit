# The live server: more

Recording in the background, writing (off by default), smaller tool lists and troubleshooting. Setting up and the everyday tools are in [NT4.md](NT4.md).

## Captures and watches

A **watch** records for a few seconds and gives one answer. A **capture** keeps recording so you can ask several questions about the same data, and it stays until you stop it.

- Without `all_changes=true`, a capture stores about 10 samples a second. Use `all_changes=true` for fast signals. Even then, updates lost before they reached the server cannot be recovered.
- A capture started with `log=true` is also saved as a `.wpilog` file, the format WPILib writes on a robot, so the [log server](WPILOG.md) can open it later. The reply gives the path as `log_file`. Files go to `logs/` (where that is: [SETTINGS](SETTINGS.md#every-other-setting), `NT4_LOG_DIR`). Each file stops at 16 MiB, and at 20 files the server refuses a new log instead of deleting old ones. Times in the file start near zero at the capture's start. To open it with the log server, `WPILOG_DIR` must include the folder in `log_file`. In the plugin that is your project folder, so copy the file there if it is somewhere else.
- A capture is evidence, not a full recording. When evidence is missing, the result says so and never bridges the gap: `gap_affected` (connection dropped), `timestamp_affected`, `drop_affected` (an oversized update was dropped), `topic_affected` (a topic was unpublished) and `evicted_oldest` (the oldest samples were pushed out to fit memory).
- A capture keeps only the newest 256 events (all its rules together). `timing_uncertain` marks events whose time may be off.

## Writing (off by default)

Writing can move a real robot, so the server only reads until you turn it on, and only for the topic paths you list. The rules are in [SETTINGS: Safety](SAFETY.md#safety). Use the simulation. **The plugin cannot turn writes on:** the dialog has no field for `NT4_ALLOW_WRITES` or `NT4_WRITE_PREFIXES`, so you need your own server entry ([SETTINGS](SETTINGS.md#how-to-set-the-ones-that-are-not-in-the-dialog)). A few facts about writes:

- `nt4_set_value` with `default=true` only sets a value nobody else has set. The result says whether yours stuck (`default_applied`).
- `publisher_ttl_s` (up to 3600) releases this server's publisher after a while. It is best effort, not a safety interlock. `read_back=true` shows this client's own view, which is not proof the robot acted.
- The server holds a publisher until you release it or disconnect. Persistent topics can outlive the publisher.
- **Port 6810 is never written.** It is the Systemcore's own internal server (health, CAN, alerts). The server refuses `nt4_set_value` and `nt4_release_publisher` there, whatever the write settings say, and `nt4_status` shows `writes_allowed: false`. Use port 5810 for the robot program's topics.

## Smaller tool lists

`NT4_TOOLSET=core` hides the capture tools and the two write tools (`nt4_watch` stays): 10 tools, about 2.2k tokens of definitions instead of about 3.8k for all 15. Server costs: [GETTING_STARTED](GETTING_STARTED.md#6-which-servers-to-keep-on).

## Troubleshooting

| What you see | What it usually means |
|---|---|
| `No NT4 server answered at host:5810` | The robot or sim is not running, the address is wrong, or a firewall is in the way |
| `The connection ... was lost` | The robot is rebooting or redeploying. Wait a few seconds and try again |
| A topic is `not announced` | The name is wrong (try `nt4_list_topics` with `name_contains`) or nothing publishes it yet |
| `Port 1740 ...` or `not a robot-network address` | `nt4_connection` never uses the Driver Station's port 1740 or a public IP. Use port 5810 and a robot or simulator address |
| Port 5811 or a `wss://` address | Secure NT4 is not supported. Use plain port 5810 |
| Writes refused | Writing is off by default. See [SETTINGS: Safety](SAFETY.md#safety) |
| A capture shows about 10 Hz | You asked for a rate. Use `all_changes=true` for fast signals |

How the server is built: [ARCHITECTURE](ARCHITECTURE.md).
