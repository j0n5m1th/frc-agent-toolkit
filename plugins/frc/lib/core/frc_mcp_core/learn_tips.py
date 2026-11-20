"""The tips learning mode adds to tool replies (see `learn.py`): tool name -> one small piece of context that makes a student curious.

Rules for a tip: one or two short sentences, plain words, true without a robot in front of you, no numbers a team's setup could change. A tip explains
why something works or shares something interesting; it is a statement, never a question or a quiz. Only read-only tools have tips: a tool that changes
something is not a place to teach. `tests/common/test_learn.py` checks that every name here is a real tool and that no tip is long or asks a question.
"""

TIPS: dict[str, str] = {
    # Logs
    "wpilog_open": "A .wpilog holds only what the robot code chose to log, so a missing value usually means nobody logged it, not that it did not happen.",
    "wpilog_topics": "Every logged value is a topic: a name, a type and a list of timestamped samples. The names your code picks decide how easy a log is to read later.",
    "wpilog_scan": "A scan ranks leads. A flag says something looks odd, not why, and the real cause often sits in a different signal at the same moment.",
    "wpilog_when": "How long a condition lasted often matters more than whether it happened: a brief blip is usually noise, a long stretch is usually a cause.",
    "wpilog_query": "Peaks show the worst moment and averages hide it, which is why a robot can fail on a spike that the average never shows.",
    "wpilog_get_values": "One sample is one moment, and one moment can mislead. Patterns show up in a window of samples, not in a single point.",
    "wpilog_diff": "Comparing a good run with a bad run is one of the fastest ways to find what changed, because it points at the difference instead of the whole log.",
    "wpilog_sync": "Two logs have two clocks that start at different moments. Lining them up on a shared event is what lets you compare the same instant.",
    "wpilog_text": "Console text in a log is what the code said at the time, so its timing can line up with an odd value and hint at the cause.",
    # Live NetworkTables
    "nt4_connection": "NetworkTables is client and server: the robot hosts the table and tools join it. A tool that shows nothing may be missing the connection or the data, and those are different problems.",
    "nt4_list_topics": "NetworkTables is a shared table of named values that robot code publishes. Some names come from WPILib and some from your own code.",
    "nt4_inspect": "A topic has a type and properties as well as a value. The type tells you how to read the number, for example whether it is one value or a list.",
    "nt4_get_values": "A value read now is only the latest one, and the robot may already have changed it. Trends need a series, not a snapshot.",
    "nt4_watch": "Watching shows how a value changes, not just what it is. Smooth, noisy and stuck each point at a different kind of cause.",
    "nt4_wait_for": "Waiting for a value to cross a line turns a hunch into a test. The strongest tests are the ones that could show the hunch is wrong.",
    "nt4_capture_query": "A capture records values as they happen, so a moment that is already gone can still be studied.",
    "nt4_health": "When battery voltage falls too low, the controller browns out and cuts motor output to protect itself. A dip usually follows something that drew a lot of current just before.",
    # Simulation
    "sim_start": "Simulation runs your real robot code on this computer with pretend hardware. It is great for logic mistakes, but it cannot reproduce real friction, battery sag or wiring.",
    "sim_output": "The console is your robot code's own diary. The first error is usually the cause and later ones are often fallout.",
    # Profiling
    "jfr_summary": "A recording is samples of what the program was doing. Samples show where time goes, which is a good start but not an explanation by itself.",
    "jfr_hotspots": "A hotspot is where a program spends the most time. Fast code that runs constantly can cost more than slow code that runs once.",
    "jfr_memory": "Java cleans up unused memory in short pauses. Making many new objects every loop can cause pauses that feel like lag.",
    "jfr_around": "Zooming in on one slow moment shows what ran then, which is often different from what is slow on average.",
    "jfr_compare": "A guess about speed is only a guess until you measure. Comparing before and after is how teams find out whether a change helped.",
    # Controller and devices
    "systemcore_info": "The Systemcore is the robot's computer, and it runs your code. Many odd robot behaviors start as something simple on this computer.",
    "systemcore_logs": "The controller's logs list what the system and your code did, in order, so the first thing that went wrong is often the best place to look.",
    "diag_bus": "CAN is one shared wire that many devices talk on. When it gets busy or noisy, every device on it can suffer, not only the one with the fault.",
    "diag_device": "Each CAN device has an ID, and two devices of the same kind cannot share one. Duplicate IDs are a classic reason a mechanism acts oddly.",
    "caniv_list": "A CANivore adds its own CAN bus over USB, which gives a team a second bus so busy devices do not crowd each other.",
    "owlet_signals": "Signals in a CTRE log can be recorded faster than your code's main loop, so a hoot log can show detail a slower log would miss.",
    # Project
    "wpilib_info": "A WPILib project is a Gradle project, and its build files choose the library versions it uses. Mismatched versions are a common cause of builds that suddenly break.",
    "ctre_versions": "Phoenix has a version for each season, and parts that disagree on version can act strangely.",
}
