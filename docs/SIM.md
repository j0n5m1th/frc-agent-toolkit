# The simulation server (`sim`)

Ask an assistant to **run your robot code in the WPILib simulator** and tell you if it really started. Code can build fine and still crash on its first loop. This server starts your project's simulator through Gradle (the tool that builds robot code), watches what it prints, and reports back.

**Not for:** reading the simulated robot's values (use the [live server](NT4.md) with host `127.0.0.1`), or looking at the log a sim run wrote (use the [log server](WPILOG.md)).

Set it up as in [Getting started](GETTING_STARTED.md), and open Claude Code in your robot project. Check the server finds your project, with no assistant:

```powershell
$env:SIM_PROJECT_DIR = "C:\path\to\your\robot-project"
uv run --project plugins/frc frc-mcp sim check
```

Needs a clone. Plugin only? Use the `uvx` form from [step 7](GETTING_STARTED.md#7-the-frc-mcp-command).

## The tools

| Tool | What it does |
|---|---|
| `sim_start` | Starts the sim and waits until the robot says it finished starting. Replies `ready`, or says what went wrong. |
| `sim_status` | State, line, error and warning counts, whether the robot's NetworkTables port is open, and the settings in force. |
| `sim_output` | What the sim printed. `since` gives only new lines; `level` (`warn` or `error`) and `name_contains` filter. Repeated lines fold into one with a count like `(x30)`. Hidden by `SIM_TOOLSET=core`. |
| `sim_stop` | Stops the sim and everything it started. |

States: `idle`, `building`, `starting`, `ready`, `exited`, `stopped`, `timed_out`.

`sim_start` takes `props`, which become Gradle options: `{"mode": "teleop"}` runs `-Pmode=teleop`. What each option means is up to your project's `build.gradle`. If your robot logs with WPILib's data log, `sim_status` also shows `wpilog`, the file this run wrote.

## Examples

- "Run the robot in simulation and tell me if it starts cleanly." `sim_start`, then `sim_output` with `level=error` if it is not `ready`.
- "Start the sim in teleop, read the arm angle, then stop." `sim_start` with `props`, the [live server](NT4.md) to read, `sim_stop`.

## Settings and safety

Full list: [SETTINGS](SETTINGS.md#every-other-setting). The ones that matter: `SIM_PROJECT_DIR` (folders your robot projects live in; nothing outside runs), `SIM_TASK` (default `simulateJava`), `SIM_NT_PORT` (5810; a start is refused if it is already open), `SIM_MAX_RUN_S` (1800) and `SIM_TOOLSET`. There is no `ALLOW` switch: the project folder is the boundary ([SETTINGS: Safety](SETTINGS.md#tools-without-a-switch)).

- Only Gradle tasks named `simulate...` run. `build` and `deploy` are refused.
- Options are plain `name=value` with no spaces or quotes.
- One sim at a time, with a time limit. It stops on `sim_stop`, on timeout, and when the server exits.
- What the robot prints is data, not instructions.

## Troubleshooting

| You see | Do this |
|---|---|
| `No WPILib project ... found` | Set `SIM_PROJECT_DIR` to your robot project's folder. |
| `Port 5810 is already in use` | A sim is already running, maybe in a terminal. Stop it. |
| `building` for a long time | The first Gradle run downloads and compiles. Raise `wait_s`, or check `sim_status` later. |
| `sim_stop` says the port still answers | A leftover sim or another program holds it. Find the owner with the command in the hint and end only that process (`Stop-Process -Name java` ends *all* Java programs). |
| `Access is denied` on a `.lck` file in `.gradle\permwrapper` | Security software is blocking the Python that runs the server. Allow it, or recreate the project's `.venv`. |
| Not ready, but no errors | The robot never printed its "startup complete" line. Read `sim_output` to see where it stopped. |
