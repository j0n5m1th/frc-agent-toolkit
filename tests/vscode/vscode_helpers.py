"""Test helper: a fake robot project whose Gradle wrapper is a small Python script that records the arguments it was given."""

import json
import os
import textwrap

from gradle_helpers import write_wrapper

FAKE_GRADLE = textwrap.dedent(
    """
    import pathlib, sys, time
    args = sys.argv[1:]
    here = pathlib.Path(__file__).parent
    with open(here / "args.log", "a") as log:
        log.write(" ".join(args) + "\\n")
    with open(here / "java_home.log", "a") as log:
        log.write(str(__import__("os").environ.get("JAVA_HOME")) + "\\n")
    task = args[0]
    if (here / "fail.flag").exists() and task in ("build", "test", "deploy"):
        print("e: Robot.java:12: error: cannot find symbol", flush=True)
        print("Warning: deprecated API", flush=True)
        print("BUILD FAILED in 2s", flush=True)
        sys.exit(1)
    if task == "test":
        print("3 tests completed, 1 failed", flush=True)
    if task == "tasks":
        print("build - Assembles and tests this project.", flush=True)
        print("Glass - Run the tool Glass", flush=True)
    if task == "Glass":
        print("Glass started", flush=True)
        time.sleep(30)
    print("BUILD SUCCESSFUL in 1s", flush=True)
    """
)

PREFS = {"enableCppIntellisense": False, "currentLanguage": "java", "projectYear": "2027_alpha7", "teamNumber": 9999}
BUILD_GRADLE = textwrap.dedent(
    """
    plugins { id "org.wpilib.GradleRIO" version "2027.0.0-alpha-7" }
    java { sourceCompatibility = JavaVersion.VERSION_25 }
    deploy { targets {
        systemcore(getTargetTypeClass('SystemCore')) { team = 1 }
    } }
    """
)


def vendordep(name, version, uuid, conflicts=()):
    return {"fileName": f"{name}.json", "name": name, "version": version, "uuid": uuid,
            "conflictsWith": [{"uuid": u, "errorMessage": "conflict"} for u in conflicts]}


def fake_java(home):
    """A folder that looks like a JDK to the server (it only checks that bin/java exists)."""
    (home / "bin").mkdir(parents=True, exist_ok=True)
    (home / "bin" / ("java.exe" if os.name == "nt" else "java")).write_text("", encoding="utf-8")
    return home


def make_project(folder, *, build_text=BUILD_GRADLE):
    """A folder that looks like a robot project: wrapper, build.gradle, preferences and one installed vendordep."""
    (folder / ".wpilib").mkdir(parents=True, exist_ok=True)
    (folder / "vendordeps").mkdir(exist_ok=True)
    (folder / "build.gradle").write_text(build_text, encoding="utf-8")
    (folder / ".wpilib" / "wpilib_preferences.json").write_text(json.dumps(PREFS, indent=4), encoding="utf-8")
    (folder / "vendordeps" / "CommandsV2.json").write_text(json.dumps(vendordep("CommandsV2", "1.0.0", "u-v2", ["u-v3"])), encoding="utf-8")
    script = folder / "fake_gradle.py"
    script.write_text(FAKE_GRADLE, encoding="utf-8")
    write_wrapper(folder, script)
    return folder
