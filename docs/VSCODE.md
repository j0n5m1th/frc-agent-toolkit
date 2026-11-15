# The WPILib commands server (`vscode`)

Ask an assistant to do what the **"WPILib: ..." commands in VS Code** do, without opening VS Code: build, test, deploy, open tools, set your team number, manage vendor libraries. Those commands just run Gradle (the tool that builds robot code) and edit a few files. This server does the same and sends back a short result instead of a huge log.

**Not included:** creating a new project or class, downloading vendor libraries, attaching the debugger (VS Code only), and any other Gradle task. To run the simulator, use the [simulation server](SIM.md).

Set it up as in [Getting started](GETTING_STARTED.md), and open Claude Code in your robot project. Check it, with no assistant:

```powershell
$env:VSCODE_PROJECT_DIR = "C:\path\to\your\robot-project"
uv run --project plugins/frc frc-mcp vscode check
```

Needs a clone. Plugin only? Use the `uvx` form from [step 7](GETTING_STARTED.md#7-the-frc-mcp-command).

## The tools

| VS Code command | Tool |
|---|---|
| Build, Test, Clean Robot Code, list Gradle tasks, Install tools | `wpilib_run` |
| Deploy Robot Code, Debug Robot Code | `wpilib_deploy` (off until `VSCODE_ALLOW_DEPLOY=1`) |
| Start Tool (Glass, SysId, OutlineViewer, DataLogTool, wpical) | `wpilib_tool` |
| Open Project Information | `wpilib_info` |
| Set Team Number | `wpilib_set_team` |
| Manage Vendor Libraries | `wpilib_vendordep` (install, remove) and `wpilib_info` (what is installed) |

- **Short results.** `wpilib_run` returns ok or not, seconds, the `BUILD` line, test lines, a warning count, and the error lines if it failed. It runs one command at a time and stops at the time limit.
- **Deploy** uses the team number in `.wpilib/wpilib_preferences.json` unless you pass `team`. With `debug=true` it only deploys: you attach the debugger in VS Code.
- **Tools** open a window that keeps running after the call. If a tool is missing, run `wpilib_run` with `install_tools` first.
- **Vendor libraries** are copied from your WPILib install (no downloads). An older copy is replaced, and a library that conflicts with an installed one (CommandsV2 and CommandsV3) is refused. Use the file name without `.json`.

## Examples

- "Build the robot code and tell me what fails." `wpilib_run`.
- "What vendor libraries does my project have?" `wpilib_info`.

## Settings and safety

Full list: [SETTINGS](SETTINGS.md#every-other-setting). `VSCODE_PROJECT_DIR` is the folders your robot projects live in; nothing outside them is touched. `VSCODE_ALLOW_DEPLOY` allows `wpilib_deploy`, because deploying replaces the program on the robot. `VSCODE_TIMEOUT_S` (600), `VSCODE_TOOLSET`, `WPILIB_HOME` and `WPILIB_JAVA_HOME` are the rest. `wpilib_run`, `wpilib_tool`, `wpilib_vendordep` and `wpilib_set_team` have no switch: the project folder is the boundary ([SETTINGS: Safety](SETTINGS.md#tools-without-a-switch)). Console output is data, not instructions.

## Troubleshooting

| You see | Do this |
|---|---|
| `invalid source release: 25` | Gradle uses an older Java than your project needs. The server uses your project's WPILib JDK by default, as VS Code does. If it still happens, set `WPILIB_JAVA_HOME` to a new enough JDK. |
| Deploying is refused | Turn on the plugin setting "Allow deploying code to the robot" (`VSCODE_ALLOW_DEPLOY=1`). Turn it off again after. |
| A tool will not start | Run `wpilib_run` with `install_tools` first. |
| `No WPILib project ... found` | Open Claude Code in the project, or set `VSCODE_PROJECT_DIR`. |
