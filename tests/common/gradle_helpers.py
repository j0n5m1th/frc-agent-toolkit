"""Shared test helpers for the Gradle layer (`frc_mcp_wpilib.gradle`), importable by plain name from any test."""

import os
import stat
import sys
from pathlib import Path


def write_wrapper(folder: Path, script: Path) -> None:
    """Write the `gradlew` wrapper of a fake robot project: it runs the Python script `script` with the arguments it is given.

    Windows gets `gradlew.bat` (with `%*`), elsewhere `gradlew` is an `sh` script that `exec`s the interpreter and is made executable."""
    if os.name == "nt":
        (folder / "gradlew.bat").write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        return
    wrapper = folder / "gradlew"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
