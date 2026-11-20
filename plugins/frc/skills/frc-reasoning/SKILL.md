---
name: frc-reasoning
description: Rules for explaining a cause from robot data without overclaiming. Use before saying why something happened, or when a result looks too clean.
---

Needs no server. Why each rule exists: `references/why.md`.

1. Answer the question asked first, then the evidence. A lookup gets a direct answer.
2. Quote only numbers a tool returned; name only topics you saw in a tool result.
3. Empty is not clean. `no_match`, `checked: false` or zero means not found: say what was searched.
4. A name is not proof of what a topic measures. Read the robot code that logs it (mechanism, units, measured or commanded), or call the mapping an assumption.
5. Check the event happened (enabled period, brownout flag) before explaining it.
6. Three tiers: a logged event is a fact; a statistic is an inference, limited by sample count and how the signal is logged; a cause outside the data (wiring, wear) is a hypothesis needing a physical check.
7. For "why", test the cause against a second signal or a rival explanation. One match is one sample.
8. Never compute statistics by hand: use `wpilog_query`.
9. Say when a log was cut short.

Cleanup: nothing to stop.
