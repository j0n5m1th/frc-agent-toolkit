# Checks: the log and the live robot

The same checks run on a recorded log and on the live robot. Each pair of tools answers one question, with the same rules, over two sources. The log tools read the whole log (and the enabled periods); the live tools record a few seconds, while someone drives the robot, and cannot tell when the robot was enabled. Guides: [log server](WPILOG.md), [live server](NT4.md).

| Question | On a log | Live |
|---|---|---|
| What is the usual trouble? | `wpilog_scan` | `nt4_scan` |
| Which swerve module lags? | `wpilog_swerve` | `nt4_swerve` |
| Which device drew the current? | `wpilog_power` | `nt4_power` |
| How well did a mechanism follow its setpoint? | `wpilog_mechanism` | `nt4_mechanism` |
| Do two signals move together? | `wpilog_correlate` | `nt4_correlate` |
| Does the robot pose jump or drift from another estimate? | `wpilog_pose` | `nt4_pose` |

## The usual trouble

Ask: "Scan this log." or "Scan the robot." `wpilog_scan` runs the same checks every time and returns ranked `flags`: errors, loop overruns, battery below 10 V or below the brownout setting, brownouts, packet loss, CAN use, signals that stopped. It does not guess which topic is the battery: it uses one only when you name it (`battery=`) or its name follows a usual convention (`BatteryVoltage`, or `Voltage` under a PDH, PDP or Battery path). Other topics with a suggestive name are listed as `candidates` and not used, because a 5 V rail is not the battery. It reads loop time only from AdvantageKit's `LoggedRobot/FullCycleMS` (and `UserCodeMS`) and CAN utilization and error counters only by exact field name under a CAN path (`Utilization`, `TEC`, `REC`, `BusOffCount`, `TxFullCount` and their long forms), judged while the robot was enabled, because CAN errors while disabled are normal. The brownout setting is read from a `BrownoutVoltage` topic when the robot logs one, and otherwise is 6 V, the Systemcore's nominal setting (configurable; from the vendor specification, not seen on a robot here): the reply says which (`brownout_basis`). The reply says which topic each number came from (`chosen_by`, `link.from`).

## Swerve drivetrains

A swerve robot logs its module states twice: what each module **measured** and what the code **commanded**. `wpilog_swerve` compares them while the robot was enabled (or in a window you give), per module:

- **Speed error**: how far the module's speed was from the command (mean and p95, in m/s), and its top speed.
- **Angle error**: how far its angle was from the command (mean and p95, in degrees), counted only while the module was told to move (below 0.1 m/s the angle is not being asked to do anything).
- **Lagging**: a module whose p95 error is at least twice the typical module's, and not tiny (0.3 m/s or 10 degrees), gets a flag. That is a rule of thumb, not a verdict: a lagging module can be a slipping belt or gear, a CAN problem, a slipping wheel or a current limit. Check a second signal (`wpilog_when` on that module's current, `wpilog_scan` for brownouts and CAN) before naming a cause.

It uses a pair of arrays only when their names follow a convention: AdvantageKit's `SwerveStates/Measured` with `SwerveStates/SetpointsOptimized` (or `Setpoints`), or CTRE's `DriveState/ModuleStates` with `DriveState/ModuleTargets`. Both must be `SwerveModuleState[]`. Any other pair is listed as `candidates`; name them with `measured=` and `setpoint=`. The module numbers are the array positions (the common order is front left, front right, back left, back right: check your code). Compare against the optimized setpoints: against the raw ones a module that flips direction looks 180 degrees off. **Checked on synthetic logs only; the names come from AdvantageKit and CTRE documentation, not a real match log seen here.** This is a module-level check. A wrong encoder offset or a bad gyro is **not** visible here (the steer loop follows the biased reading, so the error stays small): look for a robot that does not drive straight, or for heading and odometry that disagree with the modules. YAGSL's plain-number arrays are not read. For live values use `nt4_swerve` (see the `wpilib-swerve` skill).

## Which device drew the current

`wpilog_power` and `nt4_power` answer "what pulled the battery down?". They report the battery's low point and when it happened, the brownouts, the devices with the highest current (with when, and the battery voltage then), and what each device was drawing at the moment the battery was lowest.

- **Brownout setting.** Read from the robot's `BrownoutVoltage` topic when it publishes one; otherwise 6 V, the Systemcore's nominal setting (configurable; the vendor specification also lists 6.3 V with a 7.5 V restoration voltage). The reply says which (`brownout_basis`).
- **Which currents.** Only by convention: a power distribution `ChannelCurrent` array (one number per channel) or `Chan<N>` signals, and motor-controller signals named `StatorCurrent`, `SupplyCurrent`, `TorqueCurrent`, `OutputCurrent` or `CurrentAmps`. Anything else that only has `current` or `amps` in its name is listed as `current_candidates`; name signals with `currents=` and the battery with `battery=`.
- **Names.** A channel is a number until the robot publishes a map of channel to device name: see [Robot info](ROBOT_INFO.md).

## One mechanism's tracking

`wpilog_mechanism` and `nt4_mechanism` judge one arm, elevator or flywheel. You name its signals: `setpoint` and `measurement` (in the same units), and optionally `velocity`, `current` and `temperature`. Nothing in a log says that a signal is a setpoint, so nothing is guessed. They report:

- **Following error**: the measurement minus the setpoint in force (RMSE, bias, the largest error and when).
- **Steps**: setpoint changes bigger than 5% of the previous setpoint; their overshoot and settling time (until the measurement stays within 5% of the step). Rules of thumb, not a controller analysis.
- **Stalls**: stopped (velocity under 0.01) while the current is over 30 A.
- **Temperature**: the peak and the last value.

## Do two signals move together?

`wpilog_correlate` and `nt4_correlate` take two numeric signals and report Pearson's r, how strong it is, and the lag (up to `max_lag_s`) at which they match best: "b follows a by 0.3 s". A high correlation shows two signals move together. It does not show that one causes the other, and two signals that both trend (a draining battery, a warming motor) correlate without being related. A live recording times updates by when they arrive, so a lag is good to a few tens of milliseconds.

## The robot pose

`wpilog_pose` and `nt4_pose` read a `Pose2d`. A pose is found only by a convention (`DriveState/Pose`, `Odometry/Robot`, `Drive/Pose`, `EstimatedPose`, `RobotPose`); other `Pose2d` topics are listed under `others`, or name one with `pose=`. They report **jumps**: a pose that moves faster than 8 m/s between two updates, which is a vision correction or an odometry reset, with when and how far. With `reference=` (a second `Pose2d`: the vision estimate, or odometry alone) they also report how far apart the two were in position and heading.

## On a log and live

On a log the default window is the enabled periods; give `since_ms` and `until_ms` for a window. A live tool records for `duration_s` seconds, and someone must be driving the robot: a live recording cannot tell when the robot was enabled, so it covers the whole recording. **Checked on synthetic logs and a local NetworkTables server, not on a real robot or a real match log. The topic names are AdvantageKit, WPILib and CTRE conventions from their documentation.**

