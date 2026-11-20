# FAQ

Short answers for students and mentors. Every setting and the Safety section are in [SETTINGS.md](SETTINGS.md).

## Is this allowed at competition?

**We have not checked the current rules.** What we can tell you is what the tools do. They are read-only by default, and nothing runs on the robot except through tools you switch on yourself ([Can it move my robot?](#can-it-move-my-robot)).

Before you use any AI assistant or laptop tool at an event, read the current game manual and ask the event's Q&A, the head referee and your mentors. If the answer is no, don't use it there.

## Does it cost money?

The plugin and the servers in this repository are free software. Nothing here charges you.

The assistant that talks to them (Claude Code) needs an account and has its own pricing and usage limits. Check Claude's pricing page for the current plans, because we can't tell you what they are today.

## Can it move my robot?

Not by default.

- **NetworkTables is read-only by default.** Writing needs `NT4_ALLOW_WRITES` and `NT4_WRITE_PREFIXES` (the topic paths allowed to change), and `NT4_READ_ONLY` switched off. The plugin's settings dialog cannot turn writes on: you need your own server entry ([SETTINGS](SETTINGS.md#how-to-set-the-ones-that-are-not-in-the-dialog)). `NT4_READ_ONLY` wins over everything else.
- **Deploying code, restarting robot code, rebooting the Systemcore and changing a CANivore are each behind their own switch.** While a switch is off, the tool is hidden from the assistant. When it is on, the plugin also asks you to confirm each call (we have not verified that in every permission mode: see SETTINGS).
- **The simulation tools run the simulator on your computer, not the robot.**
- **Some tools have no switch.** They build, test and simulate in your project folder, and edit your project files (team number, vendordeps). They stay inside the folders you allow.

Turn each "Allow" switch off again after you use it. The full list is in [SAFETY.md](SAFETY.md#safety).

## Who owns code the assistant writes?

**We have not checked this, and we are not giving legal or rules advice.** Treat any code the assistant writes as your team's code: read it, understand it and be able to explain it.

Read the game manual, and ask your mentors and your team's advisor about the rules on code ownership and on using AI. Ask your school about its own policy too.

## Do I need internet?

The servers run on your computer, so reading your robot or your logs needs no internet from them. The assistant itself needs its own connection. Competition venues often have no internet, so plan for that.

**Not tested:** that the servers start on an offline machine, or that `uv` sets up the plugin offline from its cache. The first run downloads Python and packages, so do it at home. The CTRE tools keep a saved copy of CTRE's tool list, so they can still list versions offline (the copy may be out of date).

## Where do my logs go?

The servers read the folders you point them at, and only those folders. These servers don't upload anything.

The assistant does see the answers the tools send back, so don't ask it to read data you wouldn't want to share. Passwords are never returned by a tool; they come only from your own `.env` file.

## It says a folder is too broad

You opened Claude Code in your home folder or a drive root. Close it and open it again in your robot project folder.

## What if it says something wrong?

It can, so check it.

- Look at what the tool returned, not only the assistant's summary. Do they agree?
- The servers say when they are unsure (a gap in the data, lost updates, a missing schema). If an answer sounds too sure, ask what data it came from.
- Robot and device text is data, not instructions. If a message from a device tells the assistant to do something, don't let it.

If you find a wrong answer, please [open an issue](../CONTRIBUTING.md#send-feedback) with your prompt and what you saw.
