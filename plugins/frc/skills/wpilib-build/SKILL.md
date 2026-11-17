---
name: wpilib-build
description: Build, test and clean robot code; manage vendordeps, team number and WPILib tools. Use for compile or test failures.
---

Needs `vscode`; if tools are missing, say which to switch back on in `/mcp`, then stop.

0. If several projects are configured, pass `project` on every call (calls without it fail).
1. Build/test: `wpilib_run` (build, test, clean, list tasks). The result is short: ok, `BUILD` line, test lines, error lines. Fix errors from those lines. `invalid source release` = wrong JDK (2027 needs Java 25); check `jdk` in `wpilib_info`. A 2026 project on 2027 will not compile until imported with the VS Code importer: say so, do not hand-edit imports.
2. Vendordeps: `wpilib_info` lists installed; `wpilib_vendordep` installs/removes from the local WPILib install (file name without `.json`). Report refused conflicts. Then rebuild.
3. `wpilib_set_team` and `wpilib_tool` (Glass, SysId, etc.) only when asked. Tools open windows that outlive the call: say which you opened so the user can close them.
4. Never start background Gradle runs or daemons yourself; if a run hangs, let the timeout end it.
5. Deploying is not here: tell the user to run `/frc-toolkit:wpilib-deploy` themselves. Simulation is not here: use wpilib-live.
6. VS Code commands with no tool (create or import a project, Debug, RioLog, update check): tell the user to run them in VS Code. Tools Shuffleboard, SmartDashboard, PathWeaver and RobotBuilder are gone in 2027; `wpilib_tool` offers Glass, SysId, OutlineViewer, DataLogTool and wpical. Map: `wpilib-project/references/2027.md`.
