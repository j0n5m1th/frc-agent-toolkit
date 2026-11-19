# The live server (`frc-mcp nt4`)

Ask an AI assistant about what your robot is doing **right now**. This server talks to [NetworkTables 4](https://docs.wpilib.org/en/stable/docs/software/networktables/networktables-intro.html), the system your robot uses to share numbers like arm angle and motor current. Connect it to a robot or a simulation. Your assistant can then find topics, read values, watch signals over time, and (only if you allow it) change values.

| You want to... | Use |
|---|---|
| See what the robot is doing **right now** | this server, `frc-mcp nt4` |
| Look back at a match or a test run | the log server, `frc-mcp wpilog` ([WPILOG.md](WPILOG.md)) |

It is not a dashboard and it is not a robot controller. NetworkTables can drop updates, and nothing can bring them back. When the connection drops or updates are lost, the answer says so instead of guessing.

## Set it up

Install and the practice robot are in [GETTING_STARTED](GETTING_STARTED.md#3-try-the-demos). In short: run `frc-mcp demo robot` in a second terminal (the plugin's `nt4` server is already on), and ask "what topics does the robot have?". To check the connection without an assistant, run `frc-mcp nt4 check --host localhost --port 5810`.

The server needs a host and a port:

- **A simulation or the practice robot:** host `localhost`, port `5810`.
- **A real robot:** a roboRIO commonly uses `10.TE.AM.2` on port 5810 (team 1234 is `10.12.34.2`, team 732 is `10.7.32.2`). See [WPILib's address guide](https://docs.wpilib.org/en/stable/docs/networking/networking-introduction/ip-configurations.html) for other setups. Use your team's real address. In the plugin, set `nt4_host` in `/config`. Without the plugin, set `NT4_HOST` and `NT4_PORT` and restart the server. With `NT4_HOST` set, tools connect on first use. If it is unset, ask the assistant to connect to a host and port.
- Keep the robot in read-only mode while you are just looking. See [Writing](#writing-off-by-default).
- After you change `nt4_host`, reconnect `nt4` in `/mcp`.

Connection details:

- `host` is only a name or IP. A URL (`ws://...`), a path, or `host:port` is rejected with a hint. IPv6 addresses work.
- **Secure NT4 (`wss`, port 5811) is not supported.** The NetworkTables library the server uses has no TLS, so it speaks plain `ws` only. WPILib robots do not serve TLS on 5810 either.

## The tools

You do not need to name tools. Describe what you want and the assistant picks one. Start small: one level deep, a few seconds, a few topics.

| Tool | What it answers | Example |
|---|---|---|
| `nt4_connection` | Connect to the robot, or leave it (`action=disconnect`) | "Connect to my simulation." |
| `nt4_status` | Connection state, without connecting | "Is anything recording?" |
| `nt4_list_topics` | What topics exist. `depth=1` gives an overview; `name_contains` filters | "Find topics with 'elevator' in the name." |
| `nt4_get_values` | The value of some topics right now | "What is the arm angle?" |
| `nt4_get_widgets` | Dashboard widgets such as autonomous choosers, one object each | "What does the Auto Chooser say?" |
| `nt4_watch` | Records for a few seconds and returns one summary | "Watch the arm angle for 10 seconds." |
| `nt4_wait_for` | Waits until a value meets a condition | "Tell me when the current goes above 40 A." |
| `nt4_capture_start`, `nt4_capture_query`, `nt4_capture_stop` | Record in the background, ask many questions, then free it | "Record the drive speed for 30 seconds." |
| `nt4_inspect` | Who is connected, and who publishes or reads a topic | "Who publishes /Tunables/ShooterRPS?" |
| `nt4_set_value`, `nt4_release_publisher` | Write a value, or release what you published (off by default) | "Set /Tunables/Testing/ShooterRPS to 50." |

Prompts you can pick in your assistant, if it supports them: `explore_robot`, `diagnose_mechanism`, `compare_robot_signals`, `check_signal_steadiness`, `diagnose_connection`. They only read.

Things that trip people up:

- **Topic names** look like folders: `/Telemetry/A_Turret/HOOD_ANGLE/Position`. A big robot has hundreds. Ask for one level first.
- **Values.** Struct and protobuf values such as `Pose2d` are shown as readable fields (`translation.x`). Ask for the ones you want with `fields`. A value that cannot be decoded is shown as hex with a `decode_error`. Replies leave out `type` for plain `double`, `int`, `boolean` and `string` values, because the JSON already shows it (`5` is an int, `5.0` a double).
- **Choosers.** A chooser is shown as one object: options, default and current choice. To change a `Selectable` chooser, write `selected/tune`, not `selected/value`.
- **Big answers** are cut to fit and say what was left out. Narrow the request (fewer topics, a shorter window).

### Wait for a condition

`nt4_wait_for` takes a topic, a comparison (`==`, `!=`, `>`, `>=`, `<`, `<=`, `changed` or `exists`) and a value. `also` adds up to 4 more conditions on other topics that must be true at the same moment, for example "the elevator current is over 40 A while the mode is `auto`". A value is assumed to hold until its next update. If the time runs out, `met` is `false` and the answer shows what the other topics last were. Short pulses can be missed, so use a capture trigger for brief events.

### Ways to summarize a recording (`reduce`)

`nt4_watch` and `nt4_capture_query` do the math, so the assistant does not read hundreds of raw numbers.

| `reduce` | Good for | You get back |
|---|---|---|
| `summary` (default) | "What did it do?" | first, last, min, max, mean |
| `latest` | "What is it right now?" | the newest value and when it arrived |
| `changes` | "When did the mode switch?" | each change, with its time |
| `samples` | "Show me the readings" | evenly spaced samples |
| `edges` | "When did the current go above 40 A?" | times it rose above or fell below `threshold` |
| `durations` | "How long in each state?" | how long each value lasted (complete runs only) |
| `time_weighted` | "What was the fair average?" | a mean that counts how long each value was held |
| `percentiles` | "How steady is it? Worst case?" | `min`, `p50`, `p95`, `p99`, `max` |
| `rate` | "How fast did it move?" | units per second, and the fastest rise and fall with when |
| `step_response` | "Did it overshoot after the setpoint changed?" | `overshoot_pct`, `rise_ms`, `settle_ms` |
| `peaks` | "Worst spikes and dips?" | `median`, up to 5 `high` peaks above it and 5 `low` valleys below it (a reverse spin is a valley), as `[ms, value]` |
| `buckets` | "Shape of the whole run?" | min, max, sample mean per time slice; spikes are kept |

`p50` is the typical value. `p95` and `p99` are the bad moments. `percentiles`, `rate`, `step_response`, `peaks` and `buckets` need number topics; for a struct, name the field (`fields=["vx"]`).

For `step_response`, ask about the **measurement** (arm angle, shooter speed), over a window that **starts at the setpoint change**. It measures against where the signal settled, not where it was told to go. To check that, compare `final` with the setpoint topic. If the signal is still moving at the end, you get `settled: false`: record longer.

## Worked examples

**Wait for an event.** "Wait up to 20 seconds for the match mode to become teleop." Or with two conditions: "Wait until the elevator current goes above 40 A while the match mode is auto."

**Measure a spike.** "Watch the elevator current for 10 seconds. How long does each spike last?" (`edges` or `durations`.) Times like `1250.4` are milliseconds since the recording started.

**Tune a loop.** "I changed the arm setpoint 2 seconds ago. How much did the angle overshoot, and how long did it take to settle?" That is `step_response`.

**Catch a brief moment.** A capture can stop shortly after a condition happens and keep the lead-up (a *trigger*), or count each time something happens without keeping every sample (an *event rule*): "Record the elevator current. When it goes above 40 A, keep recording 2 more seconds, then show me the data around that moment." "Count every time the intake sensor turns true in the next 60 seconds."

**Debug a mechanism.** "The elevator isn't moving. Find its topics and tell me what you can and can't tell." Or: "Who publishes /Tunables/Testing/ShooterRPS and who reads it?"

## Captures and watches

A **watch** records for a few seconds and gives one answer. A **capture** keeps recording so you can ask several questions about the same data, and it stays until you stop it.

- Without `all_changes=true`, a capture stores about 10 samples a second. Use `all_changes=true` for fast signals. Even then, updates lost before they reached the server cannot be recovered.
- A capture started with `log=true` is also saved as a `.wpilog` file, the format WPILib writes on a robot, so the [log server](WPILOG.md) can open it later. The reply gives the path as `log_file`. Files go to `logs/` (where that is: [SETTINGS](SETTINGS.md#every-other-setting), `NT4_LOG_DIR`). Each file stops at 16 MiB, and at 20 files the server refuses a new log instead of deleting old ones. Times in the file start near zero at the capture's start. To open it with the log server, `WPILOG_DIR` must include the folder in `log_file`. In the plugin that is your project folder, so copy the file there if it is somewhere else.
- A capture is evidence, not a full recording. When evidence is missing, the result says so and never bridges the gap: `gap_affected` (connection dropped), `timestamp_affected`, `drop_affected` (an oversized update was dropped), `topic_affected` (a topic was unpublished) and `evicted_oldest` (the oldest samples were pushed out to fit memory).
- A capture keeps only the newest 256 events (all its rules together). `timing_uncertain` marks events whose time may be off.

## Writing (off by default)

Writing can move a real robot, so the server only reads until you turn it on, and only for the topic paths you list. The rules are in [SETTINGS: Safety](SETTINGS.md#safety). Use the simulation. **The plugin cannot turn writes on:** the dialog has no field for `NT4_ALLOW_WRITES` or `NT4_WRITE_PREFIXES`, so you need your own server entry ([SETTINGS](SETTINGS.md#how-to-set-the-ones-that-are-not-in-the-dialog)). A few facts about writes:

- `nt4_set_value` with `default=true` only sets a value nobody else has set. The result says whether yours stuck (`default_applied`).
- `publisher_ttl_s` (up to 3600) releases this server's publisher after a while. It is best effort, not a safety interlock. `read_back=true` shows this client's own view, which is not proof the robot acted.
- The server holds a publisher until you release it or disconnect. Persistent topics can outlive the publisher.
- **Port 6810 is never written.** It is the Systemcore's own internal server (health, CAN, alerts). The server refuses `nt4_set_value` and `nt4_release_publisher` there, whatever the write settings say, and `nt4_status` shows `writes_allowed: false`. Use port 5810 for the robot program's topics.

## Smaller tool lists

`NT4_TOOLSET=core` hides the capture tools and the two write tools (`nt4_watch` stays): 8 tools, about 2.1k tokens of tool definitions instead of about 3.4k for all 13. See [GETTING_STARTED](GETTING_STARTED.md#6-which-servers-to-keep-on) for what each server costs.

## Troubleshooting

| What you see | What it usually means |
|---|---|
| `No NT4 server answered at host:5810` | The robot or sim is not running, the address is wrong, or a firewall is in the way |
| `The connection ... was lost` | The robot is rebooting or redeploying. Wait a few seconds and try again |
| A topic is `not announced` | The name is wrong (try `nt4_list_topics` with `name_contains`) or nothing publishes it yet |
| `Port 1740 ...` or `not a robot-network address` | `nt4_connection` never uses the Driver Station's port 1740 or a public IP. Use port 5810 and a robot or simulator address |
| Port 5811 or a `wss://` address | Secure NT4 is not supported. Use plain port 5810 |
| Writes refused | Writing is off by default. See [SETTINGS: Safety](SETTINGS.md#safety) |
| A capture shows about 10 Hz | You asked for a rate. Use `all_changes=true` for fast signals |

How the server is built: [ARCHITECTURE](ARCHITECTURE.md).
