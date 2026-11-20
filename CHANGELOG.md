# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The version is the same in `plugins/frc/.claude-plugin/plugin.json` and in every `pyproject.toml` under `plugins/frc/`.

## [Unreleased]

Nothing yet.

## [0.1.0] - not yet released

First release: eleven small MCP servers for FRC robot teams and 21 Agent Skills, in one plugin.

Unless a line says otherwise, a feature was tried on synthetic logs, a practice robot and a local NetworkTables server, not on a real robot or a real match log. Topic names are conventions from the AdvantageKit, WPILib and CTRE documentation, and thresholds are rules of thumb.

### Live robot (NetworkTables 4)

- Connect, list topics, read and write values (writes are off by default), watch and capture signals, and decode struct, msgpack and protobuf values. Publishers with properties and set-default writes, `periodic`, `all` and `prefix` subscriptions, meta-topics (`nt4_inspect`, which with no arguments lists the clients), triggers and an event inbox, conditions on several topics (`also`), widget groups, units on values, and honest handling of connection gaps and dropped history.
- Reducers: `summary`, `time_weighted`, `changes`, `samples`, `edges`, `durations`, `percentiles`, `rate`, `step_response`, `peaks` (spikes and dips, one per event, with the median) and `buckets` (min, max and mean per time slice, so spikes survive).
- `nt4_health` reads a Systemcore's own health (port 6810) in one call, with ranked `flags` and what it could not check. **Seen on a real Systemcore** (alpha18, over Wi-Fi, healthy): the topic names and types, alert paths and CAN utilization. Fault and brownout paths were tested only locally. The server never writes to that port, even with the write settings on.
- Not planned: secure `wss:` on port 5811 (pyntcore has no TLS; the server explains this) and a raw protocol inspector.

### Logs

- The log server reads `.wpilog` files and Driver Station logs (`.dslog` and `.dsevents`; the format is taken from AdvantageScope's reader, not checked against a real Driver Station) with the same reducers, finds when a condition held, compares logs (`wpilog_diff`), and lines them up on one clock (`wpilog_sync`, tested on generated logs only).
- `wpilog_query by_mode=true` answers per enabled period, `wpilog_when sample=[...]` reads other topics at the start of each stretch, and `a|b` in `name_contains` works on the NT4, log, simulation and VS Code servers alike.
- `wpilog_open` shows which robot code made a log (`code`, from AdvantageKit's `BuildConstants` topics) and warns when `/RealMetadata` and `/ReplayMetadata` disagree. `wpilog_diff` warns when two logs come from different code.
- A log that is replaced or shortened after it was opened fails every query with "changed on disk ... call wpilog_open on it again". A log that only grows keeps answering from what it was opened with, and reopening it warns once.
- It can read logs outside the project folder when "Read logs from other folders" (`WPILOG_ALLOW_OUTSIDE`) is on, and Claude Code asks before each such `wpilog_open` or `wpilog_list folder=`.
- `summary` says how a signal is logged (`periodic`, `change_only` or `event`), because a mean over a `change_only` signal counts updates, not seconds. Topics that share one value get it once.

### Checks

Each check is a log tool with a live twin that runs the same shared code (`frc_mcp_wpilib`). `docs/CHECKS.md` covers all of them, and `tests/common/test_parity.py` fails when a tool has no partner and no stated reason.

- **Scan:** `wpilog_scan` / `nt4_scan` check for the usual trouble: errors, loop time (median, p95 and worst while enabled; flags p95 over 20 ms), battery sag and brownouts, packet loss, CAN buses (utilization, error levels, bus-off counts) and stopped signals. They return ranked `flags` and say what they could not check. Many signals stopping together is one low flag, not one fault each. A live scan has no log text, no enabled periods and no stopped-signal check, and says so. Not checked on a real match log.
- **Power, mechanism, correlation and pose** (full toolset only): `wpilog_power` / `nt4_power` (battery low point, brownouts, the devices with the highest current and what each drew when the battery was lowest), `wpilog_mechanism` / `nt4_mechanism` (following error, step overshoot and settling, stalls, temperature; you name the signals), `wpilog_correlate` / `nt4_correlate` (Pearson's r and the best lag) and `wpilog_pose` / `nt4_pose` (pose jumps, distance from a reference pose). They count every update, so a one-update worst moment is never thinned away.
- **Swerve:** `wpilog_swerve` / `nt4_swerve` compare each module's measured state with its command and flag a module whose p95 error is at least twice the typical one. Arrays are found only by naming convention (AdvantageKit, CTRE); other `SwerveModuleState[]` topics are listed as candidates. YAGSL's plain-number arrays are not read, and a wrong encoder offset or bad gyro is not visible.
- **No guessing:** a topic is used for a role only when you name it or its name follows a convention. For example the battery is `battery=` or `BatteryVoltage` (or `Voltage` under a power distribution or battery path). A topic with only a suggestive name is listed as a candidate, and the reply says how each choice was made.
- **Brownout:** the scans use the robot's own `BrownoutVoltage` topic when it publishes one, otherwise 6 V, the Systemcore's nominal setting. The reply says which (`brownout_v`, `brownout_basis`). **From the vendor specification; not seen on a real Systemcore.**
- **Robot info:** a robot can publish `/RobotInfo/PdhChannels` (a JSON map of power channel to device name) so power replies show names instead of channel numbers. `docs/ROBOT_INFO.md` has an example, not run here.
- **Status:** a reply that is not a full answer says `status: no_match` or `partial`, with a hint that finding nothing is not proof of no problem. It is on `wpilog_query`, `wpilog_get_values`, `wpilog_text`, `nt4_get_values` and `nt4_get_widgets`, and in the checks; not yet on the other tools.

### Skills and teaching

- 21 skills for students, from `frc-start-here` to task skills such as `wpilib-project`, `wpilib-live`, `wpilib-log-analysis`, `wpilib-prematch` (read-only pit check), `wpilib-triage` (log to a one-page "fix first" report), `wpilib-tune`, `wpilib-compare` (two matches or practice runs), `wpilib-swerve`, `systemcore`, `systemcore-live` and `ctre-diagnose`. Skills that change things (`systemcore-restart`, `systemcore-provision`, `ctre-canivore`, `wpilib-deploy`) run only when you ask. Descriptions are short, so less is loaded in every session.
- `frc-reasoning`: nine rules for explaining a cause without overclaiming (with `references/why.md`); other skills and the `match-log-analyst` subagent point to it. Not tried in a live headless run.
- `frc-explain` is a step-by-step teaching guide (explain why, show it in their data, invite curiosity without quizzing). **Learning mode** (the `learning_mode` setting, off by default) adds one short `learn` statement to read-only replies, once per tool per session; it explains why or shares something interesting, and never asks a question. Not tried with real students.
- Subagent `match-log-analyst` reads `.wpilog`, `.hoot` and JFR files with read-only tools in its own context and returns a short summary: what happened, when, the likely cause and the evidence.
- Practice robot and practice log (`frc-mcp demo`) for learning without hardware.

### WPILib, simulation and CTRE

- WPILib 2027 (alpha-8) support: `wpilib-project/references/2027.md` (renames and removals, OpModes, Commands v3, Telemetry and Tunables, gamepads, simulation); `wpilib-build` and `wpilib-live` know the Java 25 and import rules.
- `sim_start` uses the Gradle `run` task by default for a 2027 project and refuses `simulateJava` there, because GradleRIO 2027 made it fail on purpose (checked on a real alpha-8 project). `sim_stop` and the time limit mark a non-zero exit as expected and ignore shutdown error lines.
- CTRE servers: tool version manager, Owlet (`.hoot` to `.wpilog`), passerine (MIDI to CHRP), caniv (CANivore) and the Phoenix Diagnostic Server reader. Opening `.hoot` straight from `wpilog_open` is not done: a read tool would have to run Owlet, and servers must not import each other.

### Safety

- Read-only by default. Risky tools (deploy, restart, record, delete logs, install, flash) are off until you turn on an `*_ALLOW_*` setting. The 11 tools behind a setting are hidden while it is off, so the assistant does not try what it cannot use and about 1,600 fewer tokens load in every session; each tool still refuses on its own.
- Confirm first: a `PreToolUse` hook makes Claude Code ask a person before each call of a tool that changes something (NT4 writes, deploy, changing vendor libraries or team number, JFR recording, installing a CTRE tool, configuring or flashing a CANivore, restarting robot code, rebooting, provisioning or forgetting the SSH key, deleting logs, the support bundle, Wi-Fi connect and disconnect). The `*_ALLOW_*` switches stay the real boundary.
- Host keys are trusted only by a person at a terminal: `frc-mcp systemcore trust <host>` prints the fingerprint, says whether the OS image changed, and pins the key only after you type the first 8 characters of it. `systemcore_provision` uses only the default login password; with your own, run `frc-mcp systemcore provision`.
- The robot-network check resolves IPv4 and IPv6 names, needs every address to be private, and connects to the checked address instead of looking the name up again.
- `systemcore_connect` joins no Wi-Fi when a password is saved but `SYSTEMCORE_WIFI_SSID` is not. Windows join uses a private temporary profile that is removed afterwards. The SSH host-key probe has a connect timeout, the support bundle download stops at `SYSTEMCORE_MAX_DOWNLOAD_MB` (default 200), and an OS image whose SHA-256 does not match is re-downloaded.
- The SSH key is owner-only from the start, JFR recordings keep secrets out, and a restart is refused when the robot state is unknown.

### Size and startup

- Short tool descriptions, a core toolset, and shared parameter types with one wording each (a test checks it). Server instructions total under 2,800 characters; a test caps each at 250. The reply ceiling is 12000 characters (re-checked on a 570-topic log).
- If `ENABLE_TOOL_SEARCH=false` is set, a server with no `*_TOOLSET` setting uses `core` instead of `full`.
- Startup is lighter (paramiko, ntcore and protobuf load only when used), and a `SessionStart` hook runs `uv sync` quietly so the servers find their Python environment ready.

### Setup, docs and tests

- One uv workspace under `plugins/frc/` (`servers/` and `lib/`). One command, `frc-mcp`, starts any server (`frc-mcp nt4`), checks your setup (`frc-mcp doctor`) and runs the demos. The plugin asks for 13 settings; folders follow your project and on/off settings are real switches.
- License AGPL-3.0, with `THIRD_PARTY_NOTICES.md`. `SECURITY.md`, a pull request template and issue forms. The marketplace entry is pinned to the release tag; the repository is `frc-agent-toolkit`, and the plugin and marketplace are `frc-toolkit`.
- Docs: one guide per server, the biggest split so each file stays under 2,000 words (a test enforces it), and `docs/README.md` as a map.
- Tests, mypy and ruff run on Linux, Windows and macOS. The log server is also checked against a second WPILOG reader written from the format spec (`tests/wpilog/independent_log.py`) and Python's `statistics`. `evaluations/make_sim_eval.py` writes a read-only question set, with computed answers, from a simulation you start yourself; no question set for a real robot ships.
