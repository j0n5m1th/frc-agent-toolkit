"""What a project's `build.gradle` says, read as text: is it a WPILib project, which GradleRIO, Java and deploy targets.
A few regular expressions in one place, so project detection and `wpilib_info` cannot disagree. A version kept in
`gradle.properties` or a catalog is simply absent from the result.
"""

import re
from typing import Any

_GRADLERIO_RE = re.compile(r"""GradleRIO["']?\)?\s+version\s+["']([^"']+)["']""")  # Groovy `id "...GradleRIO" version "x"` and Kotlin `id("...GradleRIO") version "x"`
_JAVA_RE = re.compile(r"JavaVersion\.VERSION_(\d+)")
_TARGET_RE = re.compile(r"^\s+(\w+)\(getTargetTypeClass\(", re.M)


def is_wpilib(text: str) -> bool:
    """True if the build file mentions GradleRIO or wpilib."""
    lowered = text.lower()
    return "gradlerio" in lowered or "wpilib" in lowered


def facts(text: str) -> dict[str, Any]:
    """GradleRIO version, Java target and deploy targets, each only when the build file has it."""
    found: dict[str, Any] = {}
    if match := _GRADLERIO_RE.search(text):
        found["gradlerio"] = match.group(1)
    if match := _JAVA_RE.search(text):
        found["java"] = int(match.group(1))
    if targets := _TARGET_RE.findall(text):
        found["deploy_targets"] = targets
    return found
