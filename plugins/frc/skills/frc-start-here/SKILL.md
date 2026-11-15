---
name: frc-start-here
description: Welcome students and show what is set up. Use for "start here", "what can you do", "help me get started", or when unsure where to begin.
---

1. Check setup with quick, read-only calls: `wpilib_info` (`vscode`), `wpilog_list` (`wpilog`), `sim_status` (`sim`), `nt4_status` (`nt4`; do not connect), `ctre_status` (`ctre-tools`). Never reach out to a robot here. If a server's tools are missing, carry on with the rest.
2. Short table: ready, not set up (say the one setting to fix, such as `WPILOG_DIR` or the project folder), or server off.
3. All servers start on. Suggest which to switch off in `/mcp` (each one costs tokens in every session); name what they need for what they are doing now, and the skill:
   - **Writing code:** `vscode` (wpilib-project, wpilib-build; `/frc-toolkit:wpilib-deploy` is the user's to run).
   - **No robot:** `sim`, `nt4`, `vscode` (wpilib-live-check); `jfr` (wpilib-profile).
   - **Robot on the bench:** `nt4` (wpilib-live-check); `systemcore` (systemcore; the user runs `/frc-toolkit:systemcore-restart` or `/frc-toolkit:systemcore-provision`); `ctre-diag`, `ctre-tools` (ctre-diagnose); `ctre-caniv` (the user runs `/frc-toolkit:ctre-canivore`).
   - **After a match:** `wpilog` (wpilib-log-analysis); `ctre-tools`, `ctre-owlet` and `ctre-passerine` (ctre-convert).
4. Safety, one line: tools are read-only unless the team turns on a switch; anything that moves a robot needs a person watching.
5. Ask what they want to do, and offer one concrete first try based on what is ready.
6. Plain words, short sentences. Cleanup: nothing to stop.
