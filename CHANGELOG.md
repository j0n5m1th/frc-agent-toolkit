# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The version is the same in `plugins/frc/.claude-plugin/plugin.json` and in every `pyproject.toml` under `plugins/frc/`.

## [Unreleased]

## [0.1.0]

First release.

- License: AGPL-3.0 (`LICENSE`), with `THIRD_PARTY_NOTICES.md` listing what the servers use and the trademarks involved.
- Evaluations: no question set for a real robot ships any more. `evaluations/make_sim_eval.py` writes a read-only question set, with computed answers, from any robot simulation you start yourself.
What it can do:

- Live NetworkTables 4 server: connect, list topics, read and write values (writes are off by default), watch and capture signals, reduce them to numbers (summary, percentiles, step response and more), triggers and events, and struct and protobuf decoding.
- Safety and size: read-only by default, short tool descriptions, a core toolset, a `check` command, prompts and resources.
- Log server: reads `.wpilog` files and Driver Station logs with the same reducers, finds when a condition held, compares logs, and lines up several logs on one clock.
- Practice robot and practice log (`demo robot`, `demo log`) for learning without hardware.
- Skills for students: `frc-start-here`, `wpilib-code-review` and `wpilib-explain-project`.
- CTRE servers: tool version manager, Owlet (`.hoot` to `.wpilog`), passerine (MIDI to CHRP), caniv (CANivore) and the Phoenix Diagnostic Server reader.
- Tests, mypy and ruff on Linux, Windows and macOS, plus evaluations with a model.
- Live server details: WebSocket connect and clock sync through ntcore, struct, msgpack and protobuf decoding, publishers with properties and set-default writes, `periodic`, `all` and `prefix` subscriptions, and meta-topics (`nt4_inspect`). Reducers: `summary`, `time_weighted`, `changes`, `samples`, `edges`, `durations`, `percentiles`, `rate` and `step_response`. Triggers and an event inbox, conditions on several topics (`also`), widget groups, honest handling of connection gaps and dropped history, and units on values.
- Log server extras: `wpilog_sync` (matches enable and disable edges, falls back to wall clock; tested on generated logs only), Driver Station logs (`.dslog` and `.dsevents`; format taken from AdvantageScope's reader, not checked against a real Driver Station), and the prompts `review_match`, `find_problems` and `scan_match`.
- Not planned: secure `wss:` on port 5811 (pyntcore has no TLS; the server explains this) and a raw protocol inspector.

Changes and fixes made while getting ready for this release:

- Open-source basics: `SECURITY.md` (private reporting), a pull request template, and issue forms for bugs, feature requests and safety concerns. The marketplace entry is pinned to the release tag, and the repository is named `frc-agent-toolkit`, while the plugin and its marketplace are named `frc-toolkit` (the `frc-mcp` command is unchanged).
- Docs: one guide per server. `CTRE_DIAG.md` is part of `CTRE.md`; `ROADMAP.md`, `LEARNING.md` and `evaluations/BENCHMARKS.md` are gone (open ideas are in CONTRIBUTING, benchmarks in `evaluations/README.md`).
- Host keys are trusted only by a person at a terminal. **Removed the `systemcore_ssh_trust` tool**; the new command `frc-mcp systemcore trust <host>` prints the fingerprint, says whether the OS image changed, and pins the key only after you type the first 8 characters of the fingerprint (it refuses when input is not a terminal). `systemcore_provision` no longer takes `fingerprint` and uses only the default login password; with your own password, run `frc-mcp systemcore provision`, which asks for the same confirmation. A changed-key message now names that command.
- The robot-network check resolves names for IPv4 and IPv6, needs every address to be private, and the servers (Systemcore, Phoenix Diagnostic Server) then connect to the checked address instead of looking the name up again.
- `systemcore_connect` joins no Wi-Fi when a password is saved but `SYSTEMCORE_WIFI_SSID` is not (an impostor `SYSTEMCORE...` network could take the password). Windows join: the temporary profile file is in a private folder removed on exit, and a profile added by a failed join is deleted.
- Systemcore: the SSH host-key probe has a connect timeout; the support bundle download stops at `SYSTEMCORE_MAX_DOWNLOAD_MB` (default 200) and deletes the partial file; an OS image of the right size is re-downloaded when its SHA-256 does not match the release list.
- Risky tools (deploy, restart, record, delete logs, install, flash) are off until you turn on an `*_ALLOW_*` setting.
- Tools behind an `*_ALLOW_*` setting are hidden while it is off (11 tools: `nt4_set_value`, `nt4_release_publisher`, `wpilib_deploy`, `jfr_record`, `ctre_install`, `caniv_configure`, `caniv_flash` and four Systemcore restart and SSH tools), so the assistant does not try what it cannot use and about 1,800 fewer tokens load in every session. They appear when you turn the setting on. Each tool still refuses on its own. `systemcore_logs_prune` (its dry run works without the setting) and the Wi-Fi tools stay listed.
- Security fixes: the SSH key is made owner-only from the start, JFR recordings keep secrets out, and a restart is refused when the robot state is unknown.
- Closing `.wpilog` logs no longer blocks the server.
- Everything lives in one uv workspace under `plugins/frc/`: `servers/` for the eleven servers and `lib/` for shared code.
- One command, `frc-mcp`, starts any server (`frc-mcp nt4`), checks your setup (`frc-mcp doctor`) and runs the practice robot and log (`frc-mcp demo`).
- Servers have short keys such as `nt4`, `wpilog` and `ctre-diag`.
- The plugin asks for 13 settings. Folders follow your project, and on/off settings are real switches.
- Skills: 18 became 13 (`systemcore`, `systemcore-provision`, `ctre-diagnose`, `ctre-convert`, `wpilib-project` and `wpilib-live-check` are merges; the build and deploy skill is split into `wpilib-build` and `wpilib-deploy`). Skills that change things (`systemcore-restart`, `systemcore-provision`, `ctre-canivore`, `wpilib-deploy`) run only when you ask. Descriptions are shorter, so less is loaded in every session.
- The NT4 and log servers describe their shared parameters (`since_ms`, `until_ms`, `field`, `also`, `include_hidden`, `prefix`) with one shared type each, so the wording cannot drift; a test checks it. About 40 fewer tokens load in every session.
- Server instructions are shorter and say what each server is for and which sibling to use instead (the live-values and log servers, simulation and VS Code, the Systemcore and its logs, the two CTRE device servers). All eleven together are under 2,800 characters (about 540 tokens instead of about 820); a test caps each at 250. Several tool descriptions now start with the words a student would search (for example "Robot values now", "Compare two logs", "Robot console output"), with no more description tokens in total. A test checks that no tool asks to be always loaded.
- If `ENABLE_TOOL_SEARCH=false` is set where your assistant starts, a server with no `*_TOOLSET` setting uses `core` instead of `full`; an explicit `*_TOOLSET` still wins.
- Startup is lighter: paramiko, ntcore and protobuf load only when used.
- First start is safer: a `SessionStart` hook runs `uv sync` quietly so the servers find their Python environment ready (it prints nothing; if it is slow, the servers still set themselves up).
- Confirm first: a `PreToolUse` hook makes Claude Code ask a person before each call of a tool that changes something (deploy, restart, reboot, provision, SSH trust, flash, configure, install, record, NT4 writes, log pruning, Wi-Fi connect and disconnect). Read-only tools are not affected; the `*_ALLOW_*` switches stay the real boundary. New command `frc-mcp hook ask "<reason>"` prints the answer.
- Plugin metadata, version checks and this changelog were added.
