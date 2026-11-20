# Which servers to keep on

Read this at step 3 of the skill. Every server costs tokens in each session, so suggest switching off what the student's current job does not need. Name the skill that uses each one.

| Server | What it gives | Skill that uses it |
|---|---|---|
| `vscode` | Project info, builds, Gradle tools | wpilib-project, wpilib-build; `/frc-toolkit:wpilib-deploy` is the user's to run |
| `sim` | Runs the robot simulator | wpilib-live |
| `nt4` | Live NetworkTables values, and a real Systemcore's own health and alerts (port 6810, read-only) | wpilib-live (the robot program), wpilib-tune, wpilib-prematch, systemcore-live (the controller) |
| `jfr` | Java Flight Recorder profiling | wpilib-profile |
| `wpilog` | Match log files | wpilib-log-analysis, wpilib-triage |
| `systemcore` | The robot controller: health, logs, restart | systemcore; the user runs `/frc-toolkit:systemcore-restart` or `/frc-toolkit:systemcore-provision` |
| `ctre-diag` | CTRE device diagnostics | ctre-diagnose |
| `ctre-tools` | CTRE tool versions and installs | ctre-diagnose, ctre-convert |
| `ctre-caniv` | CANivore changes | the user runs `/frc-toolkit:ctre-canivore` |
| `ctre-owlet`, `ctre-passerine` | Convert CTRE logs | ctre-convert |

## By situation
- **Writing code:** `vscode`.
- **No robot:** `sim`, `nt4`, `vscode`; add `jfr` to profile.
- **Robot on the bench:** `nt4`, `systemcore`, `ctre-diag`, `ctre-tools`, `ctre-caniv`.
- **After a match:** `wpilog`; for CTRE logs add `ctre-tools`, `ctre-owlet`, `ctre-passerine`.

Servers are switched on and off in `/mcp`.
