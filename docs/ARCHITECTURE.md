# Architecture

For contributors. This file explains why the code is shaped the way it is. How to use the servers is in the guides ([NT4](NT4.md), [WPILOG](WPILOG.md), [CTRE](CTRE.md), [SYSTEMCORE](SYSTEMCORE.md), [SIM](SIM.md), [VSCODE](VSCODE.md), [JFR](JFR.md)); every setting is in [SETTINGS.md](SETTINGS.md); how to make a change is in [CONTRIBUTING.md](../CONTRIBUTING.md#where-do-i-change-x).

More: [how each server is built](ARCHITECTURE_SERVERS.md) and [design notes](ARCHITECTURE_NOTES.md).

## The big picture

- **Eleven small servers, not one big one.** A tool's schema is paid for in every session that loads it. A person reading last night's log should not pay for write tools, and a person on the bench should not pay for CTRE tools.
- **One repository, one plugin folder.** Everything that ships is under `plugins/frc/`: `servers/<key>/` (one package per server) and `lib/<name>/` (code the servers share).  Tests, docs and evaluations stay outside.

## Live and log parity

What you can ask of a recorded log you can ask of the live robot, and the other way round. `tests/common/test_parity.py` lists the pairs (`wpilog_topics` and `nt4_list_topics`, `wpilog_scan` and `nt4_scan`, `wpilog_swerve` and `nt4_swerve`, and so on) and fails when a tool has neither a partner nor a stated reason to have none (a log has no console text to write to, the Systemcore's own status topics are not in a log). Each tool does one job: `wpilog_scan` looks for usual trouble, `wpilog_swerve` checks the swerve modules, `wpilog_query` reduces any signal. A generic tool beside a specialized one is fine; copying one tool's checks into another is not. The rules both sides share (the scan, the swerve check, the reductions, the topic naming conventions) live in `frc_mcp_wpilib`, and a server only supplies its source: rows, topics and a clock.

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

## The plugin (`plugins/frc/`)

- **`.mcp.json` runs the same command as `docs/mcp.json.example`,** with `--project ${CLAUDE_PLUGIN_ROOT}` and the flags `--frozen --no-dev --python 3.13`. The environment values come from the 15 dialog fields in `plugin.json` (`userConfig`); `tests/test_plugin_flags.py` checks every switch the plugin sets.
- **The `SessionStart` hook pre-syncs the environment** with the same flags, so eleven servers do not all download Python and packages at once. If you change the flags, change both (`tests/test_plugin_hooks.py` checks they match). If a cold start always works without it, remove it.
- **The `PreToolUse` hook is a second lock, not the first.** Its matcher is an anchored regex over full plugin tool names (Claude Code regexes are not anchored, and a bare server key never matches). It runs `frc-mcp hook ask`, which prints one JSON line asking a person first. The switches stay the real boundary. Which modes honour it is partly not verified ([SAFETY.md](SAFETY.md#the-confirm-first-hook)).
- **Agents.** `match-log-analyst` reads logs in its own context. Its `tools:` list is read-only: nothing that records, converts or needs an `*_ALLOW_*` switch (`tests/test_plugin_agents.py`).
- **Skills.** A skill that changes something (restart, provision, flash, deploy) sets `disable-model-invocation: true`. `tests/test_plugin_skills.py` checks that every tool a skill names exists, that the files it names exist, and that a skill naming a gated tool says which setting turns it on.

## Tests

- **Prefer the real thing.** The NT4 tests start a real `ntcore` server in the test process on port 5899 and drive the live server through FastMCP's in-memory `Client`, so the JSON asserted is what the model would see. Unit-test pure logic (reducers) directly.
- **Tests pass on Windows, Linux and macOS.** CI runs all three. Symlink tests skip on Windows; the POSIX process-kill paths are tested in CI on Linux and macOS.
