# frc-toolkit: FRC robot tools for Claude Code

This plugin helps you debug an FRC robot with an AI assistant. It reads live robot data, match logs and simulations. It also talks to the Systemcore and to CTRE devices.

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) installed. The servers start through it. You also need Claude Code 2.1.295 or newer.

## Install

In Claude Code, run `/plugin install frc-toolkit@frc-toolkit` (add the marketplace first with `/plugin marketplace add j0n5m1th/frc-agent-toolkit`).

## Settings

Claude Code asks for 14 settings when you enable the plugin:

- Three addresses: the robot (NetworkTables), the Systemcore, and the CTRE diagnostics controller.
- NetworkTables read-only. It is on, so the assistant cannot write to your robot. Turning it off is not enough to allow writes.
- Read logs from other folders: on, but Claude Code asks first, each time.
- Nine `allow_*` switches (deploy, restart, record, and so on). All start off. Each one applies to every project, so turn it off after you use it.

## Servers

All servers start on. Open `/mcp` in Claude Code and switch off the ones you do not need, because each costs tokens.

The simulation, VS Code and CTRE servers read only your project folder, and the log server does too unless you allow other folders (it asks first). Copy other files into it.

NetworkTables writes cannot be turned on from these settings: see the docs.

## More

Read the [docs](https://github.com/j0n5m1th/frc-agent-toolkit/tree/master/docs) for each server.

License: [AGPL-3.0](LICENSE).
