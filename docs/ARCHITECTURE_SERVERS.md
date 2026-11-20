# How each server is built

For contributors. The per-server notes: what each server is made of and why. The big picture, the layering rules and the shared machinery are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Live server (`frc_mcp_nt4`)

- **One session object.** NT4 state is per connection: close it and your published topics disappear. So the connection is one long-lived object (`session.py`) that tools borrow with `async with session.operation()`, which connects if needed, blocks a reconnect mid-call and counts the call. A tool that only reads local state passes `require_connection=False`.
- **One lock, one event loop.** ntcore has its own threads; Python has one event loop. Never block the loop (`await asyncio.sleep`), and every capture listener callback takes `Capture._lock`. A subscriber has no `close()`: it unsubscribes when its last reference goes away, so keep a reference to anything that must stay subscribed.
- **Schemas come from the robot.** Struct and protobuf layouts are parsed when they arrive (`schema_listener.py`), because field order changes between WPILib versions. Until a schema shows up, bytes stay hex.
- **The capture buffer is bounded three ways** (`capture/buffer.py`): samples per topic, samples per capture and estimated bytes. Eviction is global, oldest first, using a heap with lazy deletion (a topic whose oldest sample changed is queued and pushed only when the next eviction needs it). Evictions are counted in `dropped`.
- **Barriers mark what must not be bridged.** Unpublish and drop windows and suspect timestamps are recorded as positions in a topic's buffer, and a query reads a copy of the state (`BufferView`) so it never holds the capture's lock. Reducers split at a barrier: no invented crossing, no made-up duration, and the first value after a reconnect is a new baseline, not a change.
- **Sample times are the value's own timestamp, not arrival time.** ntcore delivers in batches. Measured once: a 0.4 s spike read as 0.325 s with arrival times. A regression test pins a 150 ms pulse.
- **Writes are opt-in twice and fail closed.** `NT4_ALLOW_WRITES`, a non-empty `NT4_WRITE_PREFIXES` (`/` is refused), and `NT4_READ_ONLY` beats both. The plugin dialog has only the read-only field, so writes from the plugin need your own server entry ([SAFETY.md](SAFETY.md#nt4-writes)). Inputs are validated at the edge (strict booleans, int64 range) and a wrong type on an existing topic is rejected first, because NT4 drops it silently.

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
- **Never port 1740, `/api/wifi`, `/api/disk/largest*` or `/api/logs/journal-stats`** ([SAFETY.md](SAFETY.md#systemcore) says why). Tools in `tools/` are thin; the rules live in the modules.

## Profiling server (`frc_mcp_jfr`)

- **Attach, do not launch.** `jcmd JFR.start` works for any running JVM, so the server never needs to know how a program was started. It uses the target JVM's own `java.home` so versions always match.
- **Secrets never leave the recording.** The events that hold the program's environment variables and system properties are disabled when recording and refused when reading, and the JVM's command-line arguments (they can carry passwords) are dropped (`SECRET_EVENTS`, `SECRET_FIELDS` in `jdk.py`).

## CTRE servers

- **Sidecar verification.** Each installed tool gets a `<file>.sha256` record next to it, checked before every run. A file with no record runs only if it matches a hash CTRE published, and a file that differs and has no record is never replaced silently (it may be the user's own). Limits are in [CTRE.md](CTRE.md).
- **CTRE code more than one CTRE server needs goes in `frc_mcp_ctre`.** Pick tool versions by the project's channel, not just the newest.

## Sim and commands servers (`frc_mcp_sim`, `frc_mcp_vscode`)

- **They run only fixed Gradle commands.** `sim_start` runs `simulate*` tasks with validated `-Pname=value` properties and no shell characters; `wpilib_run` takes a fixed list. A free-form task would be arbitrary code execution. Their boundary is the project-folder allowlist (`SIM_PROJECT_DIR`, `VSCODE_PROJECT_DIR`), not an `*_ALLOW_*` switch. That is the one deliberate exception to "risky means gated"; `wpilib_deploy` is gated.
- **Shared Gradle code lives in `frc_mcp_wpilib/gradle/`:** the project rules, one JDK choice for both servers (so they never disagree about Java), one child process and the bounded console.
