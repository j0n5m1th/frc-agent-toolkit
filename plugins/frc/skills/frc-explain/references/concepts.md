# Concept notes

General background to teach from, not facts about this team's robot. Check a number against the student's own data or the WPILib docs before you state it. Each note ends with a thread to offer if the student is curious; do not quiz with it.

**NetworkTables (NT).** A shared table of named values. The robot code publishes values (sensors, state, tuning numbers) and tools such as AdvantageScope subscribe. It shows live data only: once a value changes, the old one is gone unless something records it. Worth exploring: which values are worth watching live and which belong in a log.

**Log files (.wpilog).** A time-stamped record of values the code chose to log. If it was not logged, it is not in the file. Match logs from the robot and the Driver Station use different clocks, so they must be lined up. Worth exploring: what extra logging would make the next failure easier to explain.

**Loop time and overruns.** Robot code runs its periodic methods in a loop, 20 ms by default. An overrun means one pass took longer than its slot, so everything late in that pass is late too. Common causes: slow work in the loop, printing too much, waiting on a device. Worth exploring: what in a loop can take a long or uneven time.

**Battery, voltage sag and brownout.** Motors draw current, and the battery's voltage drops as the current rises. If it drops too low, the controller browns out and cuts motor output to protect itself. Looking at voltage next to current or mechanism activity shows what pulled it down. Worth exploring: what the robot was doing a moment before a dip.

**Setpoint, measurement, error.** A controller compares where something should be (setpoint) with where it is (measurement); the gap is the error. Seeing all three over time tells you what a controller is doing. Worth exploring: an error that shrinks, stays flat or swings each says something different.

**PID.** P pushes in proportion to the error (too high: overshoot and shaking; too low: slow). I adds up error over time to remove a steady gap (can build up and overshoot). D reacts to how fast the error changes and damps motion (noisy sensors make it jumpy). Change one gain at a time. Worth exploring: how too much P looks in the data.

**Feedforward.** A guess of the output a mechanism needs for a wanted motion, made before any error shows up (for example, the voltage that holds an arm up). It does most of the work, and PID cleans up the rest. Worth exploring: how much of a motion can be predicted without any sensor.

**CAN bus.** One shared pair of wires that motor controllers and sensors use to talk. Every device needs the right ID, and two of the same type must not share one. A busy or noisy bus slows or drops messages. Worth exploring: how a missing device shows up on the bus.

**Signal rates and units.** Every value has units (volts, amps, degrees, rotations) and an update rate. Reading a value with the wrong unit, or looking at a slowly updated value as if it were live, gives wrong conclusions. Worth exploring: checking the units and update rate before trusting a number.

**Simulation.** Your real robot code running on a computer with pretend hardware. Good for logic and wiring mistakes; it cannot show real friction, battery sag, CAN problems or timing on the real controller. Worth exploring: which kinds of problems simulation can and cannot show.

**Profiling.** Measuring where a program spends its time and memory instead of guessing. A sample-based profile shows where time goes, not why. Make one change, measure again. Worth exploring: measuring before and after a change.

**Correlation and cause.** Two signals moving together may share a cause or be coincidence. To name a cause, find a second signal, or change one thing and watch. Worth exploring: designing a test that could prove an idea wrong.
