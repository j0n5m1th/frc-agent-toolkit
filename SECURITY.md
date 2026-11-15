# Security

These tools let an AI assistant read from, and (when you allow it) change, FRC robots and the computers next to them. A mistake can cost a team a match, so we take reports seriously.

## Report a vulnerability (privately)

Please **do not open a public issue** for a vulnerability.

1. Go to the **Security** tab of this repository and choose **Report a vulnerability** ([direct link](https://github.com/j0n5m1th/frc-agent-toolkit/security/advisories/new)).
2. Say what you found, how to repeat it, and what it could do. Do not include real passwords, keys or private team data.

This is a small, volunteer-run project. We aim to answer within a week and to fix real problems quickly, but we cannot promise dates. We will credit you in the fix unless you ask us not to.

If the problem is a design worry rather than something an attacker could use (for example, "this switch should block more"), open a **Safety concern** issue instead.

## What we consider a vulnerability

- A tool that has an `*_ALLOW_*` setting (deploy, restart, record, delete logs, install, flash, NetworkTables writes) and does its job while that setting is off. A few tools work without a switch and stay inside the project folder; they are listed in [docs/SETTINGS.md](docs/SETTINGS.md#tools-without-a-switch).
- A way to make the servers connect somewhere they must not (a non-robot-network address, TCP 1740 on the Systemcore, a download host other than CTRE's for the CTRE tools).
- A password, key or token that leaks into a reply, log, error message or file with open permissions.
- Text from a robot, device or file that the servers act on as if it were an instruction.
- A path trick that reads or writes outside the project folder the servers are limited to.

## The safety model in short

- **Read-only by default.** Anything that deletes, deploys, restarts, installs, flashes or writes to a robot is behind an explicit `*_ALLOW_*` setting. The few tools without a switch (for example the project's own Gradle commands and file conversions) are limited to the project folder. There is no free-form command or shell tool.
- **Ask first.** The Claude Code plugin also makes Claude Code ask a person before each risky call. That needs Claude Code 2.1.295 or newer, and is not yet checked in `bypassPermissions` mode, so the `*_ALLOW_*` settings are the real boundary.
- **Robot and device text is data, not instructions.**
- **Small, bounded replies,** so a huge or hostile log cannot flood the assistant.
- **Systemcore:** the servers never connect to TCP 1740 and avoid a few risky endpoints. Passwords come only from your own `.env` file or a prompt in your terminal, never from chat, a tool argument or a reply. SSH host keys are trusted only by a person at a terminal.
- **CTRE tools:** only `redist.ctr-electronics.com` over https is fetched, downloads must match CTRE's published hashes, and installing is behind `CTRE_ALLOW_INSTALL`. Anything that changes a CANivore is behind `CANIV_ALLOW_*`.
- **Project folders:** build, simulation and log tools only work inside the project folder you configure.

Details for each setting are in [docs/SETTINGS.md](docs/SETTINGS.md).

## Supported versions

Only the newest release gets fixes. This is an early (alpha) project.

## Using it safely

- Keep the `*_ALLOW_*` settings off unless you need one, and turn them off again afterwards.
- Do not connect the tools to a robot that is enabled on a field.
- Keep your `.env` files out of git (this repository ignores them).
