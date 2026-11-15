---
name: frc-start-here
description: Welcome students and show what is set up. Use for "start here", "what can you do", "help me get started", or when unsure where to begin.
---

1. Check setup with quick, read-only calls: `wpilib_info` (`vscode`), `wpilog_list` (`wpilog`), `sim_status` (`sim`), `nt4_status` (`nt4`; do not connect), `ctre_status` (`ctre-tools`). Never reach out to a robot here. If a server's tools are missing, carry on with the rest.
2. Short table: ready, not set up (say the one setting to fix, such as opening Claude Code in the robot project folder, or a `/config` switch), or server off.
3. All servers start on. Suggest which to switch off in `/mcp` (each one costs tokens in every session) and the skill each serves: writing code (`vscode`: wpilib-project, wpilib-build); no robot (`sim`, `nt4`, `vscode`, `jfr`); robot on the bench (`nt4`, `systemcore`, `ctre-diag`, `ctre-tools`, `ctre-caniv`); after a match (`wpilog`, `ctre-tools`, `ctre-owlet`, `ctre-passerine`). `references/server-choices.md` has the full table with skills and slash commands (those are the user's to run).
4. Safety, one line: tools are read-only unless the team turns on a switch; anything that moves a robot needs a person watching.
5. Ask what they want to do, and offer one concrete first try based on what is ready.
6. Plain words, short sentences. Cleanup: nothing to stop.
