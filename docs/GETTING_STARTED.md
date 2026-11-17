# Getting started

This page takes you from nothing to a working assistant. No robot is needed. The steps are written for Windows first. On macOS or Linux they are the same in a normal terminal.

## 1. Install the tools

1. Install [Claude Code](https://claude.com/claude-code), version 2.1.295 or newer (check with `claude --version`). It needs a Claude account. An older version does not block a safety-hook failure, so gated tools could run without a prompt.
2. Install [uv](https://docs.astral.sh/uv/getting-started/installation/). On Windows, open **PowerShell** and use the command on that page.
3. Close PowerShell and open a **new** window. Check that `uv --version` prints a version.
4. Check that `git --version` prints a version. The practice robot command below needs `git` on your PATH. (Git for Windows adds it.)

If PowerShell refuses the uv installer, the cause is its execution policy. The command on uv's page already passes `-ExecutionPolicy ByPass` for that one run: use it exactly as written. The first `uv` run downloads Python 3.13 and the packages (about a minute, once). A school network may block that download; install Python 3.13 yourself, then `uv` uses it.

**No `uv`?** The servers cannot start and `/mcp` shows them as failed (open one to read its `Issue:` line). Claude Code skips a plugin server for 15 minutes after it failed, so a new window alone may not bring them back. Install `uv` (step 2), then reconnect them in `/mcp`.

## 2. Add the plugin

In Claude Code:

```text
/plugin marketplace add j0n5m1th/frc-agent-toolkit
/plugin install frc-toolkit@frc-toolkit
```

Open Claude Code **in your robot project folder**. In a terminal, `cd` to that folder and type `claude` (the desktop app has a folder picker). The log, simulation, VS Code and CTRE servers use that folder, and only that folder. To read a file from somewhere else (Driver Station logs, Downloads), copy it into the project folder first.

All eleven servers start on. Each one costs tokens in every session, so open `/mcp` and switch off the ones you do not need ([step 6](#6-which-servers-to-keep-on)).

**Servers show as failed the first time?** The first start builds the Python environment, and eleven servers ask at once. Wait a minute, then open `/mcp` and reconnect them.

## 3. Try the demos

**A practice robot.** Open a **second** terminal window (keep Claude Code open in the first). Start the robot there and leave it running. Replace `<tag>` with the version on the release page.

```powershell
uvx --python 3.13 --from "git+https://github.com/j0n5m1th/frc-agent-toolkit@<tag>#subdirectory=plugins/frc" frc-mcp demo robot
```

It listens on port 5810 on your computer. The plugin's `nt4_host` setting is `127.0.0.1` by default, so the `nt4` server finds it. In Claude Code, ask "what topics does the robot have?". (The **start here** prompt only shows what is set up; it does not connect.) Press Ctrl+C in the second terminal to stop the robot.

**A practice log.** In your project folder, run the same command with `demo log` at the end. It writes `practice.wpilog` in the folder you ran it from, so open Claude Code in that same folder. Ask "what happened in practice.wpilog?".

## 4. Connect a real robot

1. Open `/config` and set **NetworkTables host** (`nt4_host`) to the robot's address, then reconnect `nt4` in `/mcp`. Servers read settings only when they start. If a changed setting does not take effect, restart Claude Code. A roboRIO commonly uses `10.TE.AM.2`: team 1234 is `10.12.34.2`. Other setups: [WPILib's address guide](https://docs.wpilib.org/en/stable/docs/networking/networking-introduction/ip-configurations.html).
2. Leave **NetworkTables read-only** on. The assistant then cannot change anything on the robot.
3. Check the connection without the assistant:

   ```powershell
   uvx --python 3.13 --from "git+https://github.com/j0n5m1th/frc-agent-toolkit@<tag>#subdirectory=plugins/frc" frc-mcp nt4 check --host 10.12.34.2
   ```

The settings apply to every project. Read [Safety](SETTINGS.md#safety) before you turn on any "Allow" switch.

## 5. Other assistants

There is no PyPI package, so other assistants run the servers from a clone of the repository:

1. Clone the repository.
2. Copy the entries you want from [mcp.json.example](mcp.json.example) into your assistant's MCP settings.
3. Make the command use the clone: `uv run --project <clone>/plugins/frc --frozen --no-dev --python 3.13 frc-mcp <key>`. Write the clone path as an **absolute** path.
4. Replace every `REPLACE_WITH_ABSOLUTE_PATH...` value with a real, absolute folder.

Do not use `uv --directory`: it moves the working directory into the clone.

## 6. Which servers to keep on

Plugin servers start on. Each one that is on costs tokens in every session. A server that is off costs nothing. Use `/mcp` to switch off what you do not need.

| What you are doing | Keep on | Startup tokens | Definitions (full / core) |
|---|---|---:|---:|
| Writing code, no robot | `vscode` | 104 | 609 / 281 |
| Simulator | `sim`, `nt4` | 98 + 182 | 474 / 319 and 3,442 / 2,114 |
| On the bench with the robot | `nt4`, `systemcore`, `ctre-diag` | 182 + 203 + 141 | 3,442 / 2,114, 1,335 / 1,099 and 691 / 486 |
| After a match, with logs | `wpilog`, `ctre-owlet` | 174 + 107 | 2,653 / 2,171 and 531 / 531 |
| CTRE devices | `ctre-tools`, `ctre-diag`, `ctre-caniv`, `ctre-passerine` | 67 + 141 + 115 + 70 | 270, 691 / 486, 362 and 246 |
| Profiling Java | `jfr` | 135 | 1,143 / 985 |

"Startup" loads at the start of every session. "Definitions" load when the assistant first uses a tool.

Tokens are estimates (tiktoken) measured before the plugin was renamed `frc-toolkit` (names are now longer). With all eleven on (the default), startup is about 1,400 tokens, plus about 350 for the skills the assistant can start by itself. The per-server table, with memory and start time, is in the [benchmarks](../evaluations/README.md#benchmarks). A test fails if a server grows more than 5%.

**Smaller tool lists.** The `core` toolset hides the rarely used tools. It helps most in assistants with no tool search, which load every definition at the start. Set the server's toolset setting to `core` (for example `NT4_TOOLSET=core`; names in [SETTINGS](SETTINGS.md)). The three small CTRE servers have no `core` set. If `ENABLE_TOOL_SEARCH=false` is set, a server uses `core` on its own. See [SETTINGS](SETTINGS.md).

## 7. The `frc-mcp` command

One command runs everything. From a clone, put `uv run --project plugins/frc` in front of it. Without a clone (plugin only), use this in place of `frc-mcp` and pin a tag, so you never get a newer version by surprise. The `--python 3.13` matters: without it `uvx` may pick a newer Python the libraries do not support yet.

```powershell
uvx --python 3.13 --from "git+https://github.com/j0n5m1th/frc-agent-toolkit@<tag>#subdirectory=plugins/frc" frc-mcp
```

| Command | What it does |
|---|---|
| `frc-mcp <server>` | Runs one server: `nt4`, `wpilog`, `sim`, `vscode`, `systemcore`, `jfr`, `ctre-tools`, `ctre-owlet`, `ctre-passerine`, `ctre-caniv`, `ctre-diag`. |
| `frc-mcp <server> check` | That server's own check (`frc-mcp nt4 check --host x`). `--help` lists the options. |
| `frc-mcp doctor` | Checks that only look at your computer. Run it in your robot project folder. Optional CTRE tools you have not installed show as FAILED; that is normal. |
| `frc-mcp demo robot` / `demo log` | A practice robot (port 5810) and a practice log (`practice.wpilog`). |

`doctor` skips the checks that could reach outside your computer (`nt4`, `systemcore`, `ctre-diag`, `ctre-caniv`, `ctre-tools`) and says so. Run those yourself. 

## If something goes wrong

| You see | Do this |
|---|---|
| `uv` is not recognized | Open a new PowerShell window. If it still fails, install uv again. |
| Servers failed on the first start | Wait a minute, then reconnect in `/mcp`. |
| Nothing listens on port 5810 | Is `demo robot` still running? Is `nt4_host` `127.0.0.1`? |
| A log server finds no files | Open Claude Code in the folder that holds the logs. |
| Still stuck | See the [FAQ](FAQ.md). |
