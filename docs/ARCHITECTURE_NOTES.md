# Design notes

For contributors. Reasons that used to live in code comments, kept here so the code stays short. The big picture is in [ARCHITECTURE.md](ARCHITECTURE.md); the per-server notes are in [ARCHITECTURE_SERVERS.md](ARCHITECTURE_SERVERS.md).

## Design notes moved from code comments

Long "why" text that used to sit in code comments. The code keeps one-line summaries; the reasons are here.

**Live server (`frc_mcp_nt4`)**

- *One shared connection.* NT4 state belongs to the connection: a topic you publish disappears when you disconnect, and subscriptions deliver data only while they exist. MCP tool calls are short, so the connection (with our publishers and captures) must outlive any one call. ntcore runs its own network threads; we only poll.
- *Few subscriptions.* The only standing subscription is a topics-only discovery one. Values are subscribed briefly for a read, or for the life of a capture. This keeps robot bandwidth and token use low.
- *Why captures exist.* Streaming samples through the model burns tokens. A capture records into a bounded in-memory buffer (optionally a `.wpilog` file) and the model asks for a reduced view (latest, summary, changes, samples) when it needs one. Only the named topics (or, with `prefix`, those under the prefixes) are subscribed.
- *`rate_hz` is enforced locally too.* It is sent to the server (NT4 `periodic`), but some servers ignore it (a WPILib 2027 sim sent 250 Hz regardless), so we keep only the latest sample per 1/`rate_hz` window. `all_changes` (NT4 `all`) is opt-in and turns this off.
- *Sample times.* `t_ms` comes from each value's own timestamp, not its arrival time: ntcore delivers in batches (every `periodic`, 100 ms by default), so arrival times would blur event timing. Snapshot values older than the capture are clamped to 0.
- *Triggers.* A capture can watch one topic for a false-to-true crossing (not "already true"), record `after_s` more seconds, then stop, so the buffer holds the lead-up and the aftermath. Queries can be windowed relative to the trigger.
- *How `Capture` is layered.* So no file is a wall of methods: `state.py` (options, clocks, buffers), then `rule_events.py` (triggers and event rules), then `listeners.py` (ntcore callbacks), then `recorder.py` (lifecycle and queries). Helpers: `buffer.py` (samples), `log.py` (the WPILOG file), `capture_clock.py` (NT clock), `admission.py` (connection gaps), `rules.py` (rule definitions), `query.py` (reply assembly).
- *`access/` holds no code in its `__init__`.* Import a name from the module that owns it. `session.py` runs the operations; `tools/` also imports the plain value records and pure helpers (`Observed`, `ReadMiss`, `WaitResult`, `SetResult`, `TopicFilter`, ...), which carry no state, so no layer is crossed.
- *Module layout.* Entry point: `spec.py` (the `SPEC`, instructions, lifespan) and `diagnostics.py` (`check`). Surface: `app.py`, `prompts_resources.py`, `tools/`. Shared infrastructure: `session.py`, `gate.py` (keeps connect/disconnect away from running calls), `clock.py`, `config.py`, `errors.py`. Shared with the log server: `frc_mcp_wpilib`.

**Log server (`frc_mcp_wpilog`)**

- *Logs are complete.* Unlike a live capture there are no lost updates or connection gaps, so results carry no uncertainty flags (a log still holds only what the robot logged). The reducers are the live server's.
- *Aligned times.* A log's clock starts when the robot program started, so "12.3 s" means little to a student. Aligning to the start of autonomous (or teleop) gives times that match what they saw on the field.
- *Syncing logs (`wpilog_sync`).* Every log records the same enable and disable moments, while wall clocks on different devices (a laptop, a controller without a battery clock) need not agree, so `auto` prefers edges. Each log notices an edge a little late (its logging period and network delay), hence tens of milliseconds of error, which the reply reports. The wall clock is the fallback, or a cross-check (`clock_differs_ms`). A Driver Station log knows the wall time from its header; a robot log knows it when `DataLogManager` wrote `systemTime` (only after the Driver Station set the robot's clock) or AdvantageKit wrote `/SystemStats/EpochTimeMicros` while `EpochTimeValid` was true.
- *Memory.* A list of `(int, float)` tuples costs about 120 bytes a row, two `array`s cost 16, and a match log has millions of rows, so number topics are `NumberRows`.
- *Questions builders* carry `@serialized` (from `logfile`) so one index is read per reply.

**Systemcore and CTRE servers**

- *CTRE version choice.* A channel covers the compliancy levels from its season's first beta or alpha up to its own level. A log needs the tool of exactly its compliancy (seen on a real compliancy 19 log: Owlet 21 and 20 refused it, 19 read it, 18 listed 36 signals instead of 2,124). A pre-release tool is chosen only for a pre-release channel.
- *Tool trust.* A file installed by `ctre_install` has a `.sha256` record next to it. The record cannot stop someone who can edit both the file and the record; it catches a damaged, swapped or mistakenly edited file, and one that never came from CTRE.
- *Child processes.* On Windows every tracked child joins one Job Object that kills its members when the handle closes, so a crashed server leaves nothing behind. On POSIX a child starts in its own session so the whole group can be killed.

**Shared code**

- *Lean schemas.* `slim_schema` drops pydantic's titles, `additionalProperties: false`, `default: null` and `anyOf: [X, null]`. This cut the advertised tool schemas by about a fifth; validation is unaffected because FastMCP validates from the function signature.
- *Reply keys.* List replies use `total`, `omitted` and `next_offset` everywhere; time-windowed pages use `next_after`; several counters in one reply add the section name (`omitted_topics`).
- *Class and field docstrings* of pydantic models that become tool parameters are sent in every tool schema, so they are short or absent on purpose.
