# Architecture

For contributors. This file explains why the code is shaped the way it is. How to use the servers is in the guides ([NT4](NT4.md), [WPILOG](WPILOG.md), [CTRE](CTRE.md), [SYSTEMCORE](SYSTEMCORE.md), [SIM](SIM.md), [VSCODE](VSCODE.md), [JFR](JFR.md)); every setting is in [SETTINGS.md](SETTINGS.md); how to make a change is in [CONTRIBUTING.md](../CONTRIBUTING.md#where-do-i-change-x).

## The big picture

- **Eleven small servers, not one big one.** A tool's schema is paid for in every session that loads it. A person reading last night's log should not pay for write tools, and a person on the bench should not pay for CTRE tools.
- **One repository, one plugin folder.** Everything that ships is under `plugins/frc/`: `servers/<key>/` (one package per server) and `lib/<name>/` (code the servers share).  Tests, docs and evaluations stay outside.

## Layering

- **`frc_mcp_core` imports nothing from this repo.** It holds what every server does the same way: the `ServerSpec`, lean schemas, the core/full switch, the command line, the size-bounded `reply()`, the settings readers, the allowed-folders rule and the process rules.
- **`frc_mcp_wpilib` is shared by the WPILib servers** (`nt4`, `wpilog`, `sim`, `vscode`, and also used by `systemcore` and the CTRE packages): value decoding, schema registries, conditions, reducers, the Gradle runner. It needs no robot and no file. It imports only `frc_mcp_core`.
- **Servers never import each other.** If two servers need something, move it down into a shared package. Sharing means moving code, never copying it, so one fix reaches every server.
- **The other shared packages follow the same rule.** `frc_mcp_ctre` may import `frc_mcp_wpilib`; the five CTRE servers may import both and `frc_mcp_ctre`. `frc_mcp_systemcore_common` (the robot-network address check) imports nothing from the repo, and only `frc_mcp_systemcore` and `frc_mcp_ctre_diag` use it. `frc_mcp_jfr` needs only a JDK, so it imports only `frc_mcp_core` and could leave this repo with that one package.
- **Declared dependencies are checked.** Every package has its own `pyproject.toml` listing exactly what it imports. `tests/common/test_layering.py` fails when an import is not declared, when something declared is unused, or when an import breaks the rules above. `plugins/frc/uv.lock` is the one lockfile.

## One declaration per server

- **`ServerSpec` (`frc_mcp_core/spec.py`) is the whole server.** A server's `spec.py` holds its `SPEC`: name, instructions, toolset setting, `check`, the tool modules as strings, and optional commands, extra options and gates. The spec builds the FastMCP object, serves it over stdio and gives it its command line, so a new server's `spec.py` and `app.py` stay a few lines each.
- **Importing a spec is cheap.** It imports no tool. `SPEC.load()` imports the tool modules, and each registers itself on the server. That keeps `check`, `--help` and `frc-mcp doctor` fast, and it is why `app.py` never imports `tools/` while `tools/` import `app.py` (for the server object and the shared parameter types).
- **Expected failures are errors with a fix in them.** Each server has its own error type that extends `FrcMcpError`. Shared code raises the generic one, because it cannot know which server runs. FastMCP shows the model the text of a `ToolError` and nothing from other exceptions.

## Toolsets and gates

- **`core` and `full`.** Tools tagged `full-only` are hidden when a server's `*_TOOLSET` is `core`. This is for assistants with no tool search, where every definition loads up front (the default is `core` when `ENABLE_TOOL_SEARCH` is false; see [SETTINGS.md](SETTINGS.md)). Each server reads its variable once at import, so one session never mixes toolsets.
- **Gated tools are hidden, not just refused.** A tool that always refuses while its `*_ALLOW_*` switch is off carries the `gated` tag and a `gate:<SETTING>` tag. `ServerSpec.gates` maps each tag to a function that says whether the switch is on now, and serving hides the tools of every switch that is off. The tool still checks the switch itself, so hiding is a saving (the assistant does not try what it cannot do), never the safety boundary.
- **One `Capability` per switch.** It is the single rule for both the tool that refuses and the tool that reports (`wpilib_info` shows the same flag from the same object). `tests/common/test_gated_tools.py` checks that a tool is tagged `gated` exactly when it always refuses with its switch off.
- **NT4 is the odd one.** Its two write tools are gated on `writes_possible()`: writes opted in, at least one allowed prefix, and read-only off. A bad prefix list only hides the tools; a bad switch value stops the server.

## Reply budgets

- **A reply that is not a full answer says so (`with_status`).** `status` is `no_match` when nothing was found (every topic failed, or there was nothing to search) and `partial` when some topics failed. A good reply gets no `status`, so it costs no tokens. An empty success looks like "no problem", which is the most damaging thing a tool can say, so the `hint` says that finding nothing is not proof.
- **Tools return `reply()`, never a `dict`.** FastMCP sends a dict twice (structured and as text), which doubles the tokens. `reply()` prints compact JSON, and when it is too big it returns a valid `{"partial": ..., "omitted_items": n}` instead of a chopped string.
- **The budget is adaptive:** 4,000 characters plus 500 per topic, up to a ceiling of 12,000. `MCP_WPILIB_MAX_CHARS` replaces it with a fixed cap.
- **The ceiling of 12,000 is kept on purpose.** The benchmark replies were tiny (the largest was 451 characters), so that data cannot justify paging earlier. Re-check with a real match log before lowering it.
- **There is no per-call size parameter.** It would cost schema tokens on every tool. Narrowing the request (fewer topics, a shorter window) is what the truncation hint asks for.

## The token budget guard

- **What each server costs is checked in.** `tests/common/token_baseline.json` holds the measured tool names, definitions and instructions per server, in both toolsets and with switches on and off. `tests/common/test_token_budget.py` fails if a server grows by more than 5% (and 20 tokens). It also checks the startup total against `STARTUP_TARGET`. Tokens are characters divided by 4: a proxy, so trust ratios, not absolute numbers.
- **Growth is a decision.** When you change a tool name, description or parameter on purpose, regenerate the baseline with `uv run --project plugins/frc --group dev python tests/common/update_token_baseline.py` and say in the commit why the cost is worth it. The numbers live in the baseline, not in the docs.

## Settings and folders

- **A setting is an environment variable.** The readers in `frc_mcp_core/settings.py` (`env_str`, `env_flag`, `env_number`, `env_int`, `env_megabytes`) treat a blank text or number as not set. A blank or unknown flag is an error, never "on", so safety switches fail closed. The plugin writes `true` and `false`, and `tests/test_plugin_flags.py` checks the switches read both.
- **`EnvFile` is for secrets only.** Only the Systemcore server layers a `.env` file behind the real environment. The real environment wins, only names with the server's prefix are read, and nothing from the file is ever put into `os.environ`, so programs the server starts never inherit it.
- **One folder rule for every server (`frc_mcp_core/folders.py`).** A server names a setting (`WPILOG_DIR`, `JFR_DIR`, `SIM_PROJECT_DIR`, ...) that holds folders. A path is resolved (symlinks followed, `..` removed) and then checked to be inside one of them. A bare name is looked up under them.
- **The strictest reading wins.** If any way of reading a name leads outside the folders, it is refused, even when another way stays inside. A name that exists both inside and outside is a refusal, not a guess.
- **No broad default.** If the setting is unset and the working directory is a drive root or the home folder, the server asks you to set the folder instead of searching it. A setting that names such a folder is the owner's choice and stays allowed.

## Child processes

- **Nothing is left running (`frc_mcp_core/processes.py`).** On Windows every tracked child joins one Job Object set to kill its members when the handle closes. The handle lives as long as the server, so a crash or a kill still ends the children (`uv run` stays a parent process on Windows, which is why the manual release check looks for leftovers). On POSIX a child starts in its own session so the whole group can be killed at once. SIGTERM and SIGHUP end the server with its own handler: it kills every child process tree, then exits at once. (A plain exit is not enough: MCP's input thread waits on stdin and would keep the server and its children alive.) Windows has no such handler; the Job Object ends the children.
- **Gradle and CTRE tools share it.** Both run with no shell, a time limit, and bounded output, and are killed when the call is cancelled or times out. The POSIX branches run in CI on Linux and macOS, where the tests pass; nobody has tried them by hand yet (see [known gaps](../CONTRIBUTING.md#ideas-and-known-gaps)).

## Live server (`frc_mcp_nt4`)

- **One session object.** NT4 state is per connection: close it and your published topics disappear. So the connection is one long-lived object (`session.py`) that tools borrow with `async with session.operation()`, which connects if needed, blocks a reconnect mid-call and counts the call. A tool that only reads local state passes `require_connection=False`.
- **One lock, one event loop.** ntcore has its own threads; Python has one event loop. Never block the loop (`await asyncio.sleep`), and every capture listener callback takes `Capture._lock`. A subscriber has no `close()`: it unsubscribes when its last reference goes away, so keep a reference to anything that must stay subscribed.
- **Schemas come from the robot.** Struct and protobuf layouts are parsed when they arrive (`schema_listener.py`), because field order changes between WPILib versions. Until a schema shows up, bytes stay hex.
- **The capture buffer is bounded three ways** (`capture/buffer.py`): samples per topic, samples per capture and estimated bytes. Eviction is global, oldest first, using a heap with lazy deletion (a topic whose oldest sample changed is queued and pushed only when the next eviction needs it). Evictions are counted in `dropped`.
- **Barriers mark what must not be bridged.** Unpublish and drop windows and suspect timestamps are recorded as positions in a topic's buffer, and a query reads a copy of the state (`BufferView`) so it never holds the capture's lock. Reducers split at a barrier: no invented crossing, no made-up duration, and the first value after a reconnect is a new baseline, not a change.
- **Sample times are the value's own timestamp, not arrival time.** ntcore delivers in batches. Measured once: a 0.4 s spike read as 0.325 s with arrival times. A regression test pins a 150 ms pulse.
- **Writes are opt-in twice and fail closed.** `NT4_ALLOW_WRITES`, a non-empty `NT4_WRITE_PREFIXES` (`/` is refused), and `NT4_READ_ONLY` beats both. The plugin dialog has only the read-only field, so writes from the plugin need your own server entry ([SETTINGS.md](SETTINGS.md#nt4-writes)). Inputs are validated at the edge (strict booleans, int64 range) and a wrong type on an existing topic is rejected first, because NT4 drops it silently.

## Log server (`frc_mcp_wpilog`)

- **A separate server from the live one,** so reading a log never pays for capture and write tools, and the reverse. It shares the reducers: a record becomes the same `(time, value)` rows the live server buffers and goes through the same `reduce_rows`, so `percentiles` means the same in both.
- **`LogFile` indexes in one pass, then keeps a row cache.** Opening records every topic, the schemas and the time span. Topics with few updates are decoded in that pass (`PRELOAD_ROWS_PER_TOPIC`, `PRELOAD_TOTAL_ROWS`); busy ones are read on demand. The cache has one global row budget across all open logs (`MAX_CACHED_ROWS`), and the least recently used topic of any log goes first. The topics just asked for stay, even when they alone are over the limit.
- **`NumberRows` keeps match logs small.** A list of `(int, float)` tuples costs about 120 bytes a row; two `array`s cost 16. Number topics are a read-only sequence built on the fly over arrays; everything else stays a list. Callers only index, slice, iterate and compare.
- **One lock for shared state.** The row caches and the process-wide schema registries are shared between tool threads, so one re-entrant `logfile.LOCK` serializes that work (it is CPU-bound anyway). **Lock order: `LOCK`, then `OpenLogs._lock`, never the other way round.** Nothing in `store.py` calls `release` or `make` while holding its own lock.
- **`wpilog_sync` makes a view, not a copy.** A `SyncedLog` puts several logs on one clock and holds no rows of its own, so views do not count toward the open-log limit. Neither `LogFile` nor `SyncedLog` inherits from the other: query code is typed against the `LogLike` protocol.
- **Read-only, inside `WPILOG_DIR`.** The path must resolve inside an allowed folder and end in `.wpilog`, `.dslog` or `.dsevents`, and a size limit stops a huge file opening by accident.

## Systemcore (`frc_mcp_systemcore`)

- **Connect to the checked address, not the name.** `net.connect_address` resolves once, requires every address to be private, and connections go to that address, so a DNS answer that changes between the check and the connection cannot redirect them. Host-key pins stay keyed by the name the user gave.
- **SSH host keys are pinned on first use, and trusted only at a terminal.** A mismatch stops with `HostKeyChanged`. No tool accepts a key; `frc-mcp systemcore trust <host>` pins it after a person types part of the fingerprint, and refuses when input is not a terminal. The model cannot do it for the user.
- **Only the managed key is offered, and no password is ever used here.** If the controller refuses it, the error points at provisioning. Passwords come only from the user's `.env` or `provision --ask`.
- **The restart guard cannot be forced.** Restart and reboot read the robot's control word and refuse while it is enabled or on a field. If the state cannot be read they refuse too, unless the robot service is stopped or `force=True`.
- **Never port 1740, `/api/wifi`, `/api/disk/largest*` or `/api/logs/journal-stats`** ([SETTINGS.md](SETTINGS.md#systemcore) says why). Tools in `tools/` are thin; the rules live in the modules.

## Profiling server (`frc_mcp_jfr`)

- **Attach, do not launch.** `jcmd JFR.start` works for any running JVM, so the server never needs to know how a program was started. It uses the target JVM's own `java.home` so versions always match.
- **Secrets never leave the recording.** The events that hold the program's environment variables and system properties are disabled when recording and refused when reading, and the JVM's command-line arguments (they can carry passwords) are dropped (`SECRET_EVENTS`, `SECRET_FIELDS` in `jdk.py`).

## CTRE servers

- **Sidecar verification.** Each installed tool gets a `<file>.sha256` record next to it, checked before every run. A file with no record runs only if it matches a hash CTRE published, and a file that differs and has no record is never replaced silently (it may be the user's own). Limits are in [CTRE.md](CTRE.md).
- **CTRE code more than one CTRE server needs goes in `frc_mcp_ctre`.** Pick tool versions by the project's channel, not just the newest.

## Sim and commands servers (`frc_mcp_sim`, `frc_mcp_vscode`)

- **They run only fixed Gradle commands.** `sim_start` runs `simulate*` tasks with validated `-Pname=value` properties and no shell characters; `wpilib_run` takes a fixed list. A free-form task would be arbitrary code execution. Their boundary is the project-folder allowlist (`SIM_PROJECT_DIR`, `VSCODE_PROJECT_DIR`), not an `*_ALLOW_*` switch. That is the one deliberate exception to "risky means gated"; `wpilib_deploy` is gated.
- **Shared Gradle code lives in `frc_mcp_wpilib/gradle/`:** the project rules, one JDK choice for both servers (so they never disagree about Java), one child process and the bounded console.

## The plugin (`plugins/frc/`)

- **`.mcp.json` runs the same command as `docs/mcp.json.example`,** with `--project ${CLAUDE_PLUGIN_ROOT}` and the flags `--frozen --no-dev --python 3.13`. The environment values come from the 15 dialog fields in `plugin.json` (`userConfig`); `tests/test_plugin_flags.py` checks every switch the plugin sets.
- **The `SessionStart` hook pre-syncs the environment** with the same flags, so eleven servers do not all download Python and packages at once. If you change the flags, change both (`tests/test_plugin_hooks.py` checks they match). If a cold start always works without it, remove it.
- **The `PreToolUse` hook is a second lock, not the first.** Its matcher is an anchored regex over full plugin tool names (Claude Code regexes are not anchored, and a bare server key never matches). It runs `frc-mcp hook ask`, which prints one JSON line asking a person first. The switches stay the real boundary. Which modes honour it is partly not verified ([SETTINGS.md](SETTINGS.md#the-confirm-first-hook)).
- **Agents.** `match-log-analyst` reads logs in its own context. Its `tools:` list is read-only: nothing that records, converts or needs an `*_ALLOW_*` switch (`tests/test_plugin_agents.py`).
- **Skills.** A skill that changes something (restart, provision, flash, deploy) sets `disable-model-invocation: true`. `tests/test_plugin_skills.py` checks that every tool a skill names exists, that the files it names exist, and that a skill naming a gated tool says which setting turns it on.

## Tests

- **Prefer the real thing.** The NT4 tests start a real `ntcore` server in the test process on port 5899 and drive the live server through FastMCP's in-memory `Client`, so the JSON asserted is what the model would see. Unit-test pure logic (reducers) directly.
- **Tests pass on Windows, Linux and macOS.** CI runs all three. Symlink tests skip on Windows; the POSIX process-kill paths are tested in CI on Linux and macOS.

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

