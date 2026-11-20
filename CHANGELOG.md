# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The version is the same in `plugins/frc/.claude-plugin/plugin.json` and in every `pyproject.toml` under `plugins/frc/`.

## [Unreleased]

Unless a line says otherwise, a new check was tried on synthetic logs and a local NetworkTables server, not on a real robot or a real match log. Topic names are conventions from the AdvantageKit, WPILib and CTRE documentation, and thresholds are rules of thumb.

### Checks

Each check is a log tool with a live twin that runs the same shared code (`frc_mcp_wpilib`). `docs/CHECKS.md` covers all of them, and `tests/common/test_parity.py` fails when a tool has no partner and no stated reason.

- **Power, mechanism, correlation and pose:** `wpilog_power` / `nt4_power` (battery low point, brownouts, the devices with the highest current and what each drew when the battery was lowest), `wpilog_mechanism` / `nt4_mechanism` (following error, step overshoot and settling, stalls, temperature; you name the signals), `wpilog_correlate` / `nt4_correlate` (Pearson's r and the best lag) and `wpilog_pose` / `nt4_pose` (pose jumps, distance from a reference pose). Full toolset only. They count every update, so a one-update worst moment is never thinned away.
- **Swerve:** `wpilog_swerve` / `nt4_swerve` compare each module's measured state with its command and flag a module whose p95 error is at least twice the typical one. The arrays are found only by naming convention (AdvantageKit, CTRE); other `SwerveModuleState[]` topics are listed as candidates. YAGSL's plain-number arrays are not read, and a wrong encoder offset or bad gyro is not visible. New skill `wpilib-swerve`.
- **Scan:** `nt4_scan` runs on a few seconds of the live robot the same usual-trouble checks `wpilog_scan` runs on a log. `wpilog_scan` also checks loop time (median, p95 and worst while enabled; flags p95 over 20 ms) and CAN buses (utilization, error levels, bus-off counts), and no longer carries a swerve block. A live scan has no log text, no enabled periods and no stopped-signal check, and says so.
- **No guessing:** the scan uses a battery topic only when you pass `battery=` or its name follows a convention (`BatteryVoltage`, or `Voltage` under a power distribution or battery path). A topic with only a suggestive name is listed as a candidate, and the reply says how each choice was made.
- **Brownout:** no longer assumed to be 7 V. The scans use the robot's own `BrownoutVoltage` topic when it publishes one, otherwise 6 V, the Systemcore's nominal setting. The reply says which (`brownout_v`, `brownout_basis`); `below_7v_s` is now `below_brownout_s`. roboRIO wording was dropped. **From the vendor specification; not seen on a real Systemcore.**
- **Robot info:** a robot can publish `/RobotInfo/PdhChannels` (a JSON map of power channel to device name) so power replies show names instead of channel numbers. `docs/ROBOT_INFO.md` has an example, not run here.
- **Live health:** `nt4_health` reads a Systemcore's own health (port 6810) in one call, with ranked `flags`. **Seen on a real Systemcore** (alpha18, over Wi-Fi, healthy): the topic names and types, alert paths and CAN utilization. Fault and brownout paths were tested only locally.

### Logs and replies

- **Replaced logs:** a log that is replaced or shortened after it was opened now fails every query with "changed on disk ... call wpilog_open on it again". A log that only grows keeps answering from what it was opened with, and reopening it warns once.
- **Robot code version:** `wpilog_open` shows which code made a log (`code`, from AdvantageKit's `BuildConstants` topics) and warns when `/RealMetadata` and `/ReplayMetadata` disagree. `wpilog_diff` adds `code` and a warning when two logs come from different code.
- **Status:** a reply that is not a full answer says `status: no_match` or `partial`, with a hint that finding nothing is not proof of no problem. On `wpilog_query`, `wpilog_get_values`, `wpilog_text`, `nt4_get_values` and `nt4_get_widgets`; not yet on the other tools.
- **Sampling:** `summary` says how a signal is logged (`periodic`, `change_only` or `event`), because a mean over a `change_only` signal counts updates, not seconds. Topics that share one value get it once.
- **Reduce and search:** new `reduce` values `peaks` (spikes and dips, one per event, with the `median`, `high` and `low`) and `buckets` (min, max, mean per slice). Also `wpilog_query by_mode=true`, `wpilog_when sample=[...]`, `a|b` in `name_contains` on every server, and `nt4_inspect` with no arguments lists the clients.
- **`wpilog_scan`:** one call that checks a log for the usual trouble (errors, loop overruns, battery sag, brownouts, packet loss, CAN use, stopped signals) and returns ranked `flags`. Many signals stopping together is one low flag, not one fault each. Not checked on a real match log.
- **Other folders:** the log server can read logs outside the project when "Read logs from other folders" (`WPILOG_ALLOW_OUTSIDE`) is on, and Claude Code asks before each such `wpilog_open` or `wpilog_list folder=`.
- **Simulation:** `sim_stop` and the time limit mark a non-zero exit as expected and ignore shutdown error lines.
- The reply ceiling stays at 12000 characters (re-checked on a 570-topic log).

### Skills and teaching

- **`wpilib-compare`:** compare two matches or practice runs. `wpilib-tune` now uses `nt4_mechanism`.
- **`frc-reasoning`:** nine rules for explaining a cause without overclaiming, with `references/why.md`. Other skills and `match-log-analyst` point to it. Not tried in a live headless run.
- **Learning mode:** a setting (`learning_mode`, off by default) that adds one short `learn` statement to read-only replies, once per tool per session. It explains why or shares something interesting, and never asks a question. `frc-explain` is now a step-by-step teaching guide. Not tried with real students.

### Docs and tests

- The four biggest guides were split so each file stays under 2,000 words (a test enforces it); `docs/README.md` is a map.
- The log server is also checked against a second WPILOG reader written from the format spec (`tests/wpilog/independent_log.py`) and Python's `statistics`.

Not done: opening `.hoot` straight from `wpilog_open`, because a read tool would have to run Owlet and servers must not import each other.

## [0.1.0] - not yet released

First release.

What it can do:

- Live NetworkTables 4 server: connect, list topics, read and write values (writes are off by default), watch and capture signals, reduce them to numbers (summary, percentiles, step response and more), triggers and events, and struct and protobuf decoding.
- Safety and size: read-only by default, short tool descriptions, a core toolset, a `check` command, prompts and resources.
- Log server: reads `.wpilog` files and Driver Station logs with the same reducers, finds when a condition held, compares logs, and lines up several logs on one clock.
- Practice robot and practice log (`demo robot`, `demo log`) for learning without hardware.
- Eighteen skills for students, from `frc-start-here` (where to begin) to task skills such as `wpilib-project`, `wpilib-live`, `wpilib-log-analysis`, `systemcore`, `systemcore-live` and `ctre-diagnose`. New since the first draft: `wpilib-prematch` (read-only pit check), `wpilib-triage` (log to a one-page "fix first" report), `wpilib-tune` (measure a loop with `step_response` and `peaks`) and `frc-explain` (explains a result in plain words and asks one question).
- WPILib 2027 (alpha-8) support: `wpilib-project/references/2027.md` (renames and removals, OpModes, Commands v3, Telemetry and Tunables, gamepads, simulation); `wpilib-build` and `wpilib-live` know the Java 25 and import rules; the WPILib install folder on Linux also looks in `$XDG_DATA_HOME/wpilib`
- `sim_start` uses the Gradle `run` task by default for a 2027 project and refuses `simulateJava` there, because GradleRIO 2027 made `simulateJava` fail on purpose (checked on a real alpha-8 project)
- `systemcore-live` reads a real Systemcore's health (`/sys`), CAN buses, robot-code state and alerts from its private NT4 server on port 6810, with no SSH (seen on a real Systemcore). The NT4 server never writes to that port, even with the write settings on
- Subagent `match-log-analyst`: reads `.wpilog`, `.hoot` and JFR files with read-only tools in its own context and returns a short summary of what happened, when, the likely cause and the evidence
- CTRE servers: tool version manager, Owlet (`.hoot` to `.wpilog`), passerine (MIDI to CHRP), caniv (CANivore) and the Phoenix Diagnostic Server reader.
- Tests, mypy and ruff on Linux, Windows and macOS, plus evaluations with a model.
- Live server details: WebSocket connect and clock sync through ntcore, struct, msgpack and protobuf decoding, publishers with properties and set-default writes, `periodic`, `all` and `prefix` subscriptions, and meta-topics (`nt4_inspect`). Reducers: `summary`, `time_weighted`, `changes`, `samples`, `edges`, `durations`, `percentiles`, `rate` and `step_response`. Triggers and an event inbox, conditions on several topics (`also`), widget groups, honest handling of connection gaps and dropped history, and units on values.
- Log server extras: `wpilog_sync` (matches enable and disable edges, falls back to wall clock; tested on generated logs only), Driver Station logs (`.dslog` and `.dsevents`; format taken from AdvantageScope's reader, not checked against a real Driver Station), and the prompts `review_match`, `find_problems` and `scan_match`.
- Not planned: secure `wss:` on port 5811 (pyntcore has no TLS; the server explains this) and a raw protocol inspector.

Changes and fixes made while getting ready for this release:

- License: AGPL-3.0 (`LICENSE`), with `THIRD_PARTY_NOTICES.md` listing what the servers use and the trademarks involved.
- Evaluations: no question set for a real robot ships any more. `evaluations/make_sim_eval.py` writes a read-only question set, with computed answers, from any robot simulation you start yourself.
- Open-source basics: `SECURITY.md` (private reporting), a pull request template, and issue forms for bugs, feature requests and safety concerns. The marketplace entry is pinned to the release tag, and the repository is named `frc-agent-toolkit`, while the plugin and its marketplace are named `frc-toolkit` (the `frc-mcp` command is unchanged).
- Docs: one guide per server. `CTRE_DIAG.md` is part of `CTRE.md`; `ROADMAP.md`, `LEARNING.md` and `evaluations/BENCHMARKS.md` are gone (open ideas are in CONTRIBUTING, benchmarks in `evaluations/README.md`).
- Host keys are trusted only by a person at a terminal. **Removed the `systemcore_ssh_trust` tool**; the new command `frc-mcp systemcore trust <host>` prints the fingerprint, says whether the OS image changed, and pins the key only after you type the first 8 characters of the fingerprint (it refuses when input is not a terminal). `systemcore_provision` no longer takes `fingerprint` and uses only the default login password; with your own password, run `frc-mcp systemcore provision`, which asks for the same confirmation. A changed-key message now names that command.
- The robot-network check resolves names for IPv4 and IPv6, needs every address to be private, and the servers (Systemcore, Phoenix Diagnostic Server) then connect to the checked address instead of looking the name up again.
- `systemcore_connect` joins no Wi-Fi when a password is saved but `SYSTEMCORE_WIFI_SSID` is not (an impostor `SYSTEMCORE...` network could take the password). Windows join: the temporary profile file is in a private folder removed on exit, and a profile added by a failed join is deleted.
- Systemcore: the SSH host-key probe has a connect timeout; the support bundle download stops at `SYSTEMCORE_MAX_DOWNLOAD_MB` (default 200) and deletes the partial file; an OS image of the right size is re-downloaded when its SHA-256 does not match the release list.
- Risky tools (deploy, restart, record, delete logs, install, flash) are off until you turn on an `*_ALLOW_*` setting.
- Tools behind an `*_ALLOW_*` setting are hidden while it is off (11 tools: `nt4_set_value`, `nt4_release_publisher`, `wpilib_deploy`, `jfr_record`, `ctre_install`, `caniv_configure`, `caniv_flash` and four Systemcore restart and SSH tools), so the assistant does not try what it cannot use and about 1,600 fewer tokens load in every session. They appear when you turn the setting on. Each tool still refuses on its own. `systemcore_logs_prune` (its dry run works without the setting) and the Wi-Fi tools stay listed.
- Security fixes: the SSH key is made owner-only from the start, JFR recordings keep secrets out, and a restart is refused when the robot state is unknown.
- Closing `.wpilog` logs no longer blocks the server.
- Everything lives in one uv workspace under `plugins/frc/`: `servers/` for the eleven servers and `lib/` for shared code.
- One command, `frc-mcp`, starts any server (`frc-mcp nt4`), checks your setup (`frc-mcp doctor`) and runs the practice robot and log (`frc-mcp demo`).
- Servers have short keys such as `nt4`, `wpilog` and `ctre-diag`.
- The plugin asks for 13 settings. Folders follow your project, and on/off settings are real switches.
- Skills: 18 became 14 (`systemcore`, `systemcore-provision`, `ctre-diagnose`, `ctre-convert`, `wpilib-project` and `wpilib-live` are merges; the build and deploy skill is split into `wpilib-build` and `wpilib-deploy`). Skills that change things (`systemcore-restart`, `systemcore-provision`, `ctre-canivore`, `wpilib-deploy`) run only when you ask. Descriptions are shorter, so less is loaded in every session.
- The NT4 and log servers describe their shared parameters (`since_ms`, `until_ms`, `field`, `also`, `include_hidden`, `prefix`) with one shared type each, so the wording cannot drift; a test checks it. About 40 fewer tokens load in every session.
- Server instructions are shorter and say what each server is for and which sibling to use instead (the live-values and log servers, simulation and VS Code, the Systemcore and its logs, the two CTRE device servers). All eleven together are under 2,800 characters (about 540 tokens instead of about 820); a test caps each at 250. Several tool descriptions now start with the words a student would search (for example "Robot values now", "Compare two logs", "Robot console output"), with no more description tokens in total. A test checks that no tool asks to be always loaded.
- If `ENABLE_TOOL_SEARCH=false` is set where your assistant starts, a server with no `*_TOOLSET` setting uses `core` instead of `full`; an explicit `*_TOOLSET` still wins.
- Startup is lighter: paramiko, ntcore and protobuf load only when used.
- First start is safer: a `SessionStart` hook runs `uv sync` quietly so the servers find their Python environment ready (it prints nothing; if it is slow, the servers still set themselves up).
- Confirm first: a `PreToolUse` hook makes Claude Code ask a person before each call of a tool that changes something (NT4 writes and releasing a publisher, deploy, changing the project's vendor libraries or team number, recording with JFR, live JFR attach, installing a CTRE tool, configuring or flashing a CANivore, restarting robot code, rebooting, provisioning or forgetting the SSH key, deleting logs, the support bundle, and Wi-Fi connect and disconnect). Read-only tools are not affected; the `*_ALLOW_*` switches stay the real boundary. New command `frc-mcp hook ask "<reason>"` prints the answer.
- Plugin metadata, version checks and this changelog were added.
