---
name: wpilib-build
description: Build, test and clean robot code; manage vendordeps, team number and WPILib tools. Use for compile or test failures.
---

Needs `vscode`; if tools are missing, say which to switch back on in `/mcp`, then stop.

0. If several projects are configured, pass `project` on every call (calls without it fail).
1. Build/test: `wpilib_run` (build, test, clean, list tasks). The result is short: ok, `BUILD` line, test lines, error lines. Fix errors from those lines. `invalid source release` = wrong JDK; check `jdk` in `wpilib_info`.
2. Vendordeps: `wpilib_info` lists installed; `wpilib_vendordep` installs/removes from the local WPILib install (file name without `.json`). Report refused conflicts. Then rebuild.
3. `wpilib_set_team` and `wpilib_tool` (Glass, SysId, etc.) only when asked. Tools open windows that outlive the call: say which you opened so the user can close them.
4. Never start background Gradle runs or daemons yourself; if a run hangs, let the timeout end it.
5. Deploying is not here: tell the user to run `/frc-toolkit:wpilib-deploy` themselves. Simulation is not here: use wpilib-live-check.
