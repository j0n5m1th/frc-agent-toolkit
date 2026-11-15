# frc-agent-toolkit: let an assistant help with your robot

[![Tests](https://github.com/j0n5m1th/frc-agent-toolkit/actions/workflows/test.yml/badge.svg)](https://github.com/j0n5m1th/frc-agent-toolkit/actions/workflows/test.yml)

> **Early testing version.** Things may break or change. Feedback is very welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).

**Gracious Professionalism.** This project is here to aid your team's engineering, not replace it: you still design, build and decide. Be kind, and share what you learn.

Ask an AI assistant about your robot and get short, correct answers. You can ask about live robot data, match logs, the simulator, your robot code, the Systemcore controller and CTRE devices. It runs as a Claude Code plugin made of small [MCP](https://modelcontextprotocol.io) servers and skills. No robot is needed to start.

<!-- TODO: add a GIF of a real question -->

## Start in three steps

1. **Install the tools.** You need [Claude Code](https://claude.com/claude-code) 2.1.295 or newer (it needs an account; check with `claude --version`) and [uv](https://docs.astral.sh/uv/getting-started/installation/). Windows steps are in [GETTING_STARTED](docs/GETTING_STARTED.md).
2. **Add the plugin.** Open Claude Code (in a terminal, type `claude`). Run these two lines.

   ```text
   /plugin marketplace add j0n5m1th/frc-agent-toolkit
   /plugin install frc-toolkit@frc-toolkit
   ```

3. **Try it with a practice robot.** Open a second terminal window and start the practice robot there. Leave it running. You need `git` installed. Replace `<tag>` with the version on the release page.

   ```powershell
   uvx --python 3.13 --from "git+https://github.com/j0n5m1th/frc-agent-toolkit@<tag>#subdirectory=plugins/frc" frc-mcp demo robot
   ```

   Then, in Claude Code, ask **what topics does the robot have?** Press Ctrl+C in the second terminal when you are done.

Updates do not install by themselves for this marketplace. Run `/plugin update` when you want the newest version.

## What it helps with

The first start can take a minute. All servers start on, and each one costs tokens in every session, so use `/mcp` to switch off what you do not need. The last column is what each situation needs.

| Your situation | What you can ask | Skill | Servers you need |
|---|---|---|---|
| Writing code | Explain our code, build it, review it before competition | `wpilib-project`, `wpilib-build`, `wpilib-deploy` | `vscode` |
| No robot | Does it start in the simulator? What is the value of this topic? Where does Java spend time? | `wpilib-live-check`, `wpilib-profile` | `sim`, `nt4`, `vscode`, `jfr` |
| Robot on the bench | Is the controller healthy? What is on the CAN bus? | `systemcore`, `ctre-diagnose` | `nt4`, `systemcore`, `ctre-diag`, `ctre-tools` |
| After a match | What happened? Why did we brown out? | `wpilib-log-analysis` | `wpilog`, `ctre-tools`, `ctre-owlet` |
| CTRE devices | Which tool versions fit my project? Turn a `.hoot` log or a MIDI file into something usable | `ctre-convert`, `ctre-canivore` | `ctre-tools`, `ctre-owlet`, `ctre-passerine`, `ctre-caniv` |

## Safe by default

Most tools only read. A tool that changes the robot or a device is off until you turn on a switch, and Claude Code asks you first. Building and simulating run your project's own Gradle commands, and a few tools save or edit files without a switch ([list](docs/SETTINGS.md#tools-without-a-switch)). Read [Safety in SETTINGS](docs/SETTINGS.md#safety) before you turn any switch on.

## More

- [Getting started](docs/GETTING_STARTED.md): Windows install, the demo, a real robot, other assistants
- [Settings and safety](docs/SETTINGS.md): every setting in one table
- [FAQ](docs/FAQ.md): common problems
- [Contributing](CONTRIBUTING.md): tests, feedback, how the code is laid out
- [Security](SECURITY.md): how to report a problem privately

## Credits and attribution

- **NetworkTables and WPILib.** The live server talks to robots through [`pyntcore`](https://pypi.org/project/pyntcore/), the Python bindings for [WPILib](https://github.com/wpilibsuite/allwpilib)'s NetworkTables library, and follows the [NetworkTables 4.1 protocol specification](https://github.com/wpilibsuite/allwpilib/blob/main/ntcore/doc/networktables4.adoc). The log server reads the WPILOG files that WPILib's data logger writes. WPILib is BSD 3-Clause. This project is under the [AGPL-3.0](LICENSE).
- **Libraries.** [FastMCP](https://gofastmcp.com) and the [Model Context Protocol](https://modelcontextprotocol.io) SDK, [Pydantic](https://docs.pydantic.dev), [msgpack](https://msgpack.org) and [protobuf](https://protobuf.dev), with licenses listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- **Independent project.** FIRST(R) and FRC(R) are trademarks of FIRST; CTRE, Phoenix, WPILib, Limelight, Claude and other names belong to their owners. This project is not affiliated with or endorsed by any of them.
