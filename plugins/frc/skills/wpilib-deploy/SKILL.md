---
name: wpilib-deploy
description: Deploy robot code to the robot. Use when the user asks to deploy or download code to the robot.
disable-model-invocation: true
---

Needs `vscode`; if tools are missing, say which to switch back on in `/mcp`, then stop.

0. If several projects are configured, pass `project` on every call (calls without it fail).
1. Deploy needs explicit user approval each time (it replaces the robot's program and may interrupt a running robot). Before `wpilib_deploy`: `wpilib_run` build/test must pass, and state the project and team number (from `wpilib_info`).
2. If `wpilib_deploy` is missing or refused, tell the user to turn on "Allow deploying code to the robot" in `/config`, then reconnect `vscode` in `/mcp`, or restart Claude Code; don't work around it.
3. Deploy options (`debug`, `offline`, `team`) only when asked.
4. Afterwards confirm the result. If the robot is reachable, use wpilib-live-check.
5. Never start background Gradle runs or daemons yourself; if a run hangs, let the timeout end it.
