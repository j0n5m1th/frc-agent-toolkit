"""Which of CTRE's download keys this computer needs (`windowsx86-64`, `linuxx86-64`, `linuxarm64`, `linuxarm32`, `macosuniversal`)."""

import platform as _platform
import sys

from frc_mcp_core.settings import env_str

from .errors import CtreError

KNOWN = ("windowsx86-64", "linuxx86-64", "linuxarm64", "linuxarm32", "macosuniversal", "linuxsystemcore", "linuxathena")


def platform_key() -> str:
    """CTRE_PLATFORM if set (for a test or a download for another computer), else the key of this computer."""
    if forced := env_str("CTRE_PLATFORM"):
        if forced not in KNOWN:
            raise CtreError(f"CTRE_PLATFORM must be one of {', '.join(KNOWN)}, not {forced!r}.")
        return forced
    machine = _platform.machine().lower()
    if sys.platform == "win32" and machine in ("amd64", "x86_64"):
        return "windowsx86-64"
    if sys.platform == "darwin":
        return "macosuniversal"
    if sys.platform.startswith("linux"):
        if machine in ("x86_64", "amd64"):
            return "linuxx86-64"
        if machine in ("aarch64", "arm64"):
            return "linuxarm64"
        if machine.startswith("armv7"):
            return "linuxarm32"
    raise CtreError(f"CTRE publishes no tools for {sys.platform}/{machine}. Set CTRE_PLATFORM to a key from the index to override.")
