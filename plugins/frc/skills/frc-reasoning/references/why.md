# Why each rule exists

A model, like a person, can find an explanation that fits the data even when the data cannot support it. Each rule closes one way that goes wrong. The examples are illustrations of what can happen, not cases seen with this toolkit.

- **Answer first, and only returned numbers.** Students in the pit have little time, and a number no tool returned is a guess that looks like data.
- **Empty is not clean.** A tool that returns an empty success for a window in which the robot clearly moved looks like an answer, and it is not one. Finding nothing is not evidence that nothing is wrong.
- **A name is not proof.** A tool that takes the first entry with "voltage" in its name as the battery may pick the 5 V rail and report a brownout risk that is not there. A name can hide the unit, the mechanism, and whether the value is measured or commanded. The robot code that logs it settles that.
- **Check the event happened,** or you may explain a brownout that never occurred.
- **Three tiers.** A logged brownout flag is a fact and needs no statistical caveat. A mean or correlation is an inference. A signal logged only on change has few samples even over a long time, so a statistic over it counts changes, not seconds. A cause outside the telemetry is a hypothesis: say so, and say what to check on the robot.
- **Test the cause.** Two signals moving together may share a cause or be coincidence. A second signal, or a rival explanation, can show the idea is wrong. One match is one sample.
- **No hand statistics:** the tools count and reduce the same way every time.
- **Cut-short logs.** Most robot logs end inside their last record because the robot is switched off while logging. That alone is a note, not an alarm. A log that lost more than that deserves a warning.
