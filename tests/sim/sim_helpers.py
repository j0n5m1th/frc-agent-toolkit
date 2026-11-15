"""Test helper: a fake WPILib project whose Gradle wrapper is a small Python script."""

import textwrap

from gradle_helpers import write_wrapper

FAKE_GRADLE = textwrap.dedent(
    """
    import sys, time
    args = sys.argv[1:]
    props = {a[2:].split("=")[0]: (a[2:].split("=", 1) + [""])[1] for a in args if a.startswith("-P")}
    scenario = props.get("scenario", "ok")
    print("> Task :" + args[0], flush=True)
    if scenario == "buildfail":
        print("e: Robot.java:12: error: cannot find symbol", flush=True)
        print("FAILURE: Build failed with an exception.", flush=True)
        sys.exit(1)
    print("********** Robot program starting **********", flush=True)
    print("Warning at Joystick.java:1: Joystick on port 0 not available", flush=True)
    print("JAVA_HOME=" + str(__import__("os").environ.get("JAVA_HOME")), flush=True)
    print("DataLog: Logging to 'C:/robot/logs/WPILIB_TBD_ab12.wpilog' (9 GiB free space)", flush=True)
    print("DataLog: Renamed log file from 'WPILIB_TBD_ab12.wpilog' to 'WPILIB_2026.wpilog'", flush=True)
    if scenario == "crash":
        print("Exception in thread main: java.lang.NullPointerException", flush=True)
        print("\\tat first.robot.Robot.<init>(Robot.java:42)", flush=True)
        sys.exit(2)
    if scenario == "spam":
        for i in range(30):
            print(f"Warning: Loop time of 0.005s overrun by {i}.5 ms", flush=True)
    if scenario != "never_ready":
        print("********** Robot program startup complete **********", flush=True)
    print("args=" + " ".join(args), flush=True)
    while True:
        time.sleep(0.2)
    """
)


def make_project(folder, *, text="plugins { id 'edu.wpi.first.GradleRIO' }"):
    """A folder that looks like a robot project; its wrapper runs FAKE_GRADLE with the arguments it is given."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "build.gradle").write_text(text, encoding="utf-8")
    script = folder / "fake_gradle.py"
    script.write_text(FAKE_GRADLE, encoding="utf-8")
    write_wrapper(folder, script)
    return folder
