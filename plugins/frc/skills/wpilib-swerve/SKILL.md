---
name: wpilib-swerve
description: Find which swerve module misbehaves, from a log or live. Use for "swerve", "a module is off", "the drive is jittery" or "wheel slipping".
---

Needs `wpilog` and `nt4`; if tools are missing, say which to switch back on in `/mcp`, then stop.

Goal: find which module misbehaves, then test why. A flag is a lead, not a cause (`frc-reasoning`).

From a log:
1. `wpilog_open`, then `wpilog_swerve`. It names the topics it used. If it lists `candidates`, ask which array is measured and which is commanded, then pass `measured=` and `setpoint=`.
2. Read the lagging module's speed and angle error, and `worst_speed_err.at_s`.
3. Test a cause with a second signal then (`align=auto`): that module's current with `wpilog_when`, battery and CAN with `wpilog_scan`.

Live: `nt4_swerve` records the arrays for a few seconds and gives the same answer. The drivers must be moving the robot; never ask for motion yourself.

Causes to check, as hypotheses: a slipping belt or gear, a CAN device dropping out, a low current limit, a wheel slipping on carpet, a poorly tuned steer loop. A wrong encoder offset or a bad gyro does not show here: check that the robot drives straight and that heading and odometry agree. YAGSL's number arrays are not read.

Report: the module, how far off (speed, angle), when, what you tested, how sure you are, and the next test on the robot.

Cleanup: `wpilog_close` logs you opened; `nt4_connection action=disconnect` if you connected.
