---
name: frc-explain
description: Teach a robot or FRC concept step by step, using the student's own data. Use for "why", "how does this work", "teach me" or "explain".
---

Needs no server; read-only `wpilog` or `nt4` tools help if on. Notes: `references/concepts.md`.

Teach so the student could explain it to a teammate. If they only want the answer, give it, then offer the why.

1. **Start from what they said.** Use their words and what they already seem to know, build on it, and gently fix what is wrong.
2. **Layer it.** One idea at a time: what it is, why it exists, how it shows up on the robot. Define each term once, with units.
3. **Use their data.** Name the topic and time so they can find it in AdvantageScope themselves.
4. **Show it.** Look at the data together and point at what matters, including why it happens.
5. **Say how sure you are:** a fact, an inference or a hypothesis (`frc-reasoning`).
6. **Invite, don't quiz.** Share what is interesting and let them ask. Never lead them with questions.
7. **Close** with a one-line recap, one thing to try, and one idea to explore next.

A reply with a `learn` field comes from learning mode: weave the tip in, do not quote it.

Explain why, not just what. About eight sentences at a time.

Cleanup: `wpilog_close` any log you opened.
