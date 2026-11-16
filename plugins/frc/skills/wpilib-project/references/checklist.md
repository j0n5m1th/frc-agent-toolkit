# Robot code review checklist (Java)

Rules taken from the vendors' own docs and examples, each with its source so a student can read more. Format:
**rule**: why it matters / what to look for. `[src]` is the source.

What to check:
- **WPILib:** always.
- **CTRE:** when `vendordeps/Phoenix6*.json` exists.
- **AdvantageKit:** when `vendordeps/AdvantageKit.json` exists, or `Robot extends LoggedRobot` / `LoggedOpModeRobot`.
- **(2027) rules:** only when `build.gradle` uses a 2027 GradleRIO.
- An item marked *example* comes from a vendor example project, not a stated rule. Say so when you cite it.

Sources:
- `W` = https://docs.wpilib.org/en/stable/docs/software
- `WL` = https://docs.wpilib.org/en/latest/docs (2027)
- `C` = https://v6.docs.ctr-electronics.com/en/stable/docs
- `CE` = https://github.com/CrossTheRoadElec/Phoenix6-Examples/tree/main/java
- `A` = https://docs.advantagekit.org

## Contents
1. WPILib: command-based
2. WPILib: loop timing, units, controllers, Driver Station, safety and logging
3. CTRE Phoenix 6
4. AdvantageKit
5. 2027 changes

## 1. WPILib: command-based

- **Every command that drives a subsystem requires it, and a default command requires its own subsystem.** Without the requirement, two commands can drive the same motor at once. Look for missing `addRequirements`, and `setDefaultCommand` given a command that does not require that subsystem. The subsystem's own `run`/`runOnce`/`startEnd` add the requirement automatically. [W/commandbased/subsystems.html]
- **Hardware fields are `private`.** Code outside the subsystem can't put the hardware in a bad state. Look for `public` motor or sensor fields, or `subsystem.motor` used elsewhere. [same]
- **Subsystems extend `SubsystemBase` (or register themselves).** Otherwise `periodic()` never runs, and nothing warns you. [allwpilib CommandScheduler.java]
- **`CommandScheduler.getInstance().run()` is called in `robotPeriodic()`.** Without it, no commands run. Look for it missing, or only in `teleopPeriodic`. [W/commandbased/command-scheduler.html]
- **Factories return a new command each call.** A command object can belong to only one group or binding. Look for `Command` fields reused in several places. [W/commandbased/organizing-command-based.html]
- **A command is never reused after it is decorated or put in a group.** Doing so throws an exception and crashes the robot program. Look for `cmd.withTimeout(...)` followed by `cmd` used again, or the same object in two `sequence`/`parallel` calls. [W/commandbased/command-compositions.html]
- **Commands that use several subsystems live outside any one subsystem.** Passing one subsystem into another can cause race conditions and circular dependencies. [W/commandbased/organizing-command-based.html]
- **Each step in a sequence can finish.** Otherwise auto stalls on that step. Look for `run(...)`, or `isFinished` that is always `false`, inside `sequence`/`andThen` with no `until`/`withTimeout`. [W/commandbased/command-compositions.html]
- **Stopping and cleanup go in `end(interrupted)`, or use `startEnd`/`runEnd`.** `end` runs whether the command finished or was interrupted. Look for stop code in `execute`/`isFinished`. [W/commandbased/commands.html]
- **Avoid `asProxy()`/`ProxyCommand`.** Proxying spreads: every command on that subsystem then needs it too. [W/commandbased/command-compositions.html]
- **Bind triggers once, at setup, with the right binding type.** `whileTrue` cancels on release, while `onTrue` runs to the end. The docs advise against toggles for driver controls. Look for bindings made in periodic code. [W/commandbased/binding-commands-to-triggers.html]
- **No `ignoringDisable(true)` on commands that move motors.** [W/commandbased/commands.html]

## 2. WPILib: loop timing, units, controllers, Driver Station, safety and logging

**Loop timing**
- **Never block the loop.** A command that loops or sleeps in `execute` stalls the whole robot program and causes loop overruns. Look for `while` waiting on a sensor, `Thread.sleep`, `Timer.delay`, or file or network work in `execute`/`periodic`/`*Init`. Use `addPeriodic` or a `Notifier` to run something at another rate. [allwpilib design-docs/commands-v3.md; W/convenience-features/scheduling-functions.html]

**Units and coordinates**
- **WPILib axes:** +X forward, +Y left, counter-clockwise positive. Joystick Y is negative when pushed forward, so negate it. Some gyros, such as the NavX, read clockwise positive and must be inverted. [W/basic-programming/coordinate-system.html]
- **Keep `Measure` values (the units library) until a non-units API needs a number, then convert with `.in(Unit)`.** A bare `double` hides unit mix-ups. Look for degrees vs radians, rotations vs meters, and gear ratios applied twice or never. [W/basic-programming/java-units.html]

**PID, profiles and pose estimation**
- **`PIDController.calculate()` is called once per loop at its period.** It is not thread-safe. [W/advanced-controls/controllers/pidcontroller.html]
- **`setTolerance` is set before `atSetpoint()` is used.** [same]
- **`enableContinuousInput` only on mechanisms that can spin all the way around.** Never on an arm, or on a turret with hard stops. [same]
- **`reset()` the controller when re-enabling.** Avoid `kI`; if you use it, add `setIntegratorRange` or an IZone. [same]
- **With `ProfiledPIDController`, compute feedforward from `getSetpoint()`, not from the goal.** [W/advanced-controls/controllers/profiled-pidcontroller.html]
- **The pose estimator's `update()` runs every loop.** `addVisionMeasurement` uses the camera's capture timestamp, not "now". Standard deviations grow with tag distance, and heading trust is very low. [W/advanced-controls/state-space/state-space-pose-estimators.html]

**Driver Station**
- **The alliance is an `Optional`.** Never read it in a constructor or store it once. Check it every loop and handle it being empty. Look for `getAlliance().get()` with no presence check. [W/basic-programming/coordinate-system.html]

**Safety, faults and logging**
- **Motor Safety stays on for drivetrains and arms.** Look for `setSafetyEnabled(false)` on a drive class. [W/hardware-apis/motors/wpi-drive-classes.html]
- **Each `Alert` is a field, updated with `set(condition)` every loop.** Not `new Alert` in periodic code. [WL/software/telemetry/persistent-alerts.html]
- **Logging is started.** Nothing is saved by default. Look for `DataLogManager.start()`, plus `DriverStation.startDataLog(...)` if joystick data matters. [WL/software/telemetry/datalog.html]
- **Epilogue `@Logged`: no null or expensive getters.** Logged methods that read CAN cause loop overruns. Use `@NotLogged` on noisy members. [WL/software/telemetry/robot-telemetry-with-annotations.html]
- **Preferences: write a default only when the key is missing (`containsKey`), and keep keys in constants.** Otherwise a tuned value is overwritten on every boot. [W/basic-programming/robot-preferences.html]
- **Simulation-only physics goes in `simulationPeriodic()`.** [W/commandbased/subsystems.html]

## 3. CTRE Phoenix 6

**Configuration**
- **Apply one full `TalonFXConfiguration` at startup.** Settings the code doesn't set go back to factory defaults, so no factory-default step is needed. Look for single-group applies (`apply(cfg.Slot0)`) as the only setup. [C/api-reference/api-usage/configuration.html]
- **Never `apply()` or `refresh()` a configuration in periodic code.** Both block the loop. At runtime, apply one group on an event only. [same; *example* CE/CurrentLimits]
- **Retry `apply` (for example 5 times) and report a failed `StatusCode`.** A failed apply leaves the motor running wrong settings without anyone knowing. *Example*. [CE/MotionMagic, CE/ControlRequestLimits]

**Current limits**
- **Set a stator limit, and add a supply limit to protect against brownouts.** Each limit has its own `...Enable = true`, so a value without the enable does nothing. The docs give example values, not rules: drive 120 A stator / 70 A supply, steer 60 A, elevator 80/30 A, intake 20 A. The docs say to measure each mechanism. [C/hardware-reference/talonfx/improving-performance-with-current-limits.html]

**Direction and followers**
- **Set inversion with `MotorOutput.Inverted = InvertedValue...`, not by negating outputs.** *Example*. [CE/ArcadeDrive]
- **Create followers once with `new Follower(leaderId, MotorAlignmentValue...)`.** Since 2026, a follower also copies the leader's brake/coast setting. [CE/VelocityClosedLoop; C/yearly-changes/yearly-changelog.html]

**Control requests**
- **Store each request in a field and reuse it with `withX(...)`.** Making a new request every loop creates garbage, which can cause loop overruns. Look for `setControl(new ...)` in periodic code. [C/api-reference/api-usage/control-requests.html]
- **A request with `withUpdateFreqHz(0)` is sent only once,** so `setControl` must be called at least every 50 ms. [same]
- **Stop with `NeutralOut`.** Prefer Voltage or TorqueCurrentFOC over DutyCycle for repeatable motion, since DutyCycle changes as the battery drains. [C/api-reference/device-specific/talonfx/closed-loop-requests.html]
- **FOC, Dynamic Motion Magic and fused sensors need Pro.** Without a license the motor disables its output with `UnlicensedFeatureInUse`. Dynamic Motion Magic also needs a CANivore. [C/api-reference/device-specific/talonfx/motion-magic.html]

**Status signals**
- **Keep signals in fields and refresh them together once per loop with `BaseStatusSignal.refreshAll`.** A cached signal is stale until it is refreshed. [C/api-reference/api-usage/status-signals.html]
- **Refresh before `getLatencyCompensatedValue`.** [same; *example* CE/BasicLatencyCompensation]
- **Check the status from `waitForAll`/`getStatus()` to notice a device dropping off CAN.** Don't mix signals from different buses in one call. [same; *example* CE/WaitForAll]
- **Set update frequencies once, at setup.** The docs warn that above 90% bus use, behavior gets unpredictable. When using `optimizeBusUtilization()`, set the rates you need before calling it. [same]
- **Avoid `getDataCopy()` in loops.** It makes a new object on every call. [same]

**CAN bus and IDs**
- **Construct devices with a `CANBus` object: `new TalonFX(id, canBus)`.** The `(id)` and `(id, "name")` constructors are deprecated and removed in 2027. Use one shared `CANBus` constant. [C/yearly-changes/yearly-changelog.html]
- **No two devices of the same model share an ID on a bus.** In simulation, IDs must be unique across all buses. [C/troubleshooting/canbus-troubleshooting.html; C/api-reference/simulation/simulation-intro.html]

**Sensors and ratios**
- **Use `Feedback.SensorToMechanismRatio` instead of scaling by hand.** Doing both applies the ratio twice. [CE/MotionMagic]
- **A FusedCANcoder needs `FeedbackRemoteSensorID`, `FusedCANcoder` as the source, and `RotorToSensorRatio`.** Wrong ratios cause `Fault_FusedSensorOutOfSync`. [C/api-reference/device-specific/talonfx/remote-sensors.html]

**Closed loop and Motion Magic**
- **The request names its gain slot with `.withSlot(n)`,** and that slot's gains are configured. [C/.../closed-loop-requests.html]
- **Arms and elevators set `kG` with the right `GravityType`.**
  - Arm: `Arm_Cosine`, with a sensor where 0 means horizontal.
  - Elevator: `Elevator_Static`.

  [same]
- **Motion Magic configs set cruise velocity and acceleration** in mechanism units. [C/.../motion-magic.html]
- **Swerve steer and turrets that can spin freely use `ContinuousWrap`.** [C/.../closed-loop-requests.html]
- **A Kraken X60 has no limit switch input.** Use a remote limit source or `withLimitForwardMotion`. [C/api-reference/api-usage/actuator-limits.html]

**Simulation, logging, generated code**
- **Only sim code uses `getSimState()`.** Set `Orientation` to match the real mechanism (sim ignores the invert setting), and set the supply voltage. [C/api-reference/simulation/simulation-intro.html]
- **SignalLogger starts on its own** on a roboRIO 2 or with a USB drive, and stops when 5 MB of free space is left. Check what `setPath()` returns. [C/api-reference/api-usage/signal-logging.html]
- **`TunerConstants` is generated.** Regenerate it from the saved Tuner X project instead of editing it by hand, but do tune its gains. [C/tuner/tuner-swerve/generating-running-project.html]

## 4. AdvantageKit

- **`Robot extends LoggedRobot` and does not use `addPeriodic`.** [A/getting-started/installation/existing-projects]
- **In the `Robot` constructor: data receivers, replay source and metadata first, then `Logger.start()`, then create `RobotContainer`.**
  - Real mode: `WPILOGWriter` + `NT4Publisher`.
  - Replay mode: `setUseTiming(false)` + `WPILOGReader`.
  - Look for subsystems created as `Robot` field initializers, which run before logging starts.

  [same; A/getting-started/common-issues/uninitialized-inputs]
- **Subsystem constructors don't read `inputs.*`.** Inputs update only in `periodic`, so the constructor sees default values. [A/getting-started/common-issues/uninitialized-inputs]
- **No `DriverStation.waitForDsConnection`.** [A/data-flow/built-in-logging]
- **All hardware goes through an IO interface.** `periodic` calls `io.updateInputs(inputs)`, then `Logger.processInputs("Name", inputs)`. Logic reads only `inputs`, and subsystems never touch motors or sensors directly. [A/data-flow/recording-inputs/io-interfaces]
- **Code uses the generated `XInputsAutoLogged`, not plain `XInputs`.** The plain class can't be logged. [A/data-flow/recording-inputs/annotation-logging]
- **Replay-safe code:**
  - Use `Timer.getTimestamp()`, not FPGA time.
  - Read NetworkTables and vision (Limelight, PhotonVision) only inside an IO layer.
  - No `Math.random`, no loops over a `HashMap`/`HashSet` that change behavior, and file reads count as inputs.

  [A/theory/deterministic-timestamps; A/getting-started/common-issues/non-deterministic-data-sources]
- **Hardware libraries that bypass the IO layer break replay.** The docs name YAGSL and CTRE's generated swerve and suggest AdvantageKit's swerve template. CTRE recommends its generated swerve, so present this as a trade-off for the team, not as a bug. [same]
- **Main robot code is single-threaded.** Extra threads live inside IO implementations, and `Logger.*` is not thread-safe. [A/getting-started/common-issues/multithreading]
- **Dashboard inputs use `LoggedNetworkNumber`/`LoggedDashboardChooser`, not `SmartDashboard.getNumber` or `SendableChooser`.** Those don't work in replay. [A/data-flow/recording-inputs/dashboard-inputs]
- **Outputs use `Logger.recordOutput` or `@AutoLogOutput`.** `@AutoLogOutput` works only in classes reachable from `Robot`. [A/data-flow/recording-outputs]
- **Geometry is logged as structs, units are attached, and `LoggedMechanism2d` is used instead of `Mechanism2d`.** [A/data-flow/supported-types]
- **A protobuf or record type is first logged while disabled.** Its first log can take over 100 ms. [same]
- **`build.gradle` has `annotationProcessor "org.littletonrobotics.akit:akit-autolog:..."`.** Without it, no `*AutoLogged` classes are generated. [A/getting-started/installation/existing-projects]

## 5. 2027 changes

These are silent breaking changes or new rules, for projects on the 2027 alphas.

**WPILib, OpModes, Commands v3, telemetry, simulation:** see `references/2027.md` (checked against alpha-8). Read it first for any 2027 project: packages, `robotInit`, `simulateJava` and Java 25 break old code before any rule here applies.

**CTRE**
- The CAN constructors without a `CANBus` object are removed.

**AdvantageKit**
- `LoggedDashboardChooser` is replaced by `LoggedNetworkChooser`.
- Java 25 is required.
- `LoggedOpModeRobot` exists for OpMode robots.
- Logs are saved to `/home/systemcore/logs`.
- AdvantageKit says only teams in the Systemcore alpha should use the 2027 builds.

[github.com/wpilibsuite/SystemCoreTesting AdvantageKit.md]
