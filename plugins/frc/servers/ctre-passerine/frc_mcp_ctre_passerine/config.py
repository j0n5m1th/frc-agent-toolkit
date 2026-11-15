"""Settings of the Passerine server (PASSERINE_*), read at call time; the CTRE_* ones are in `frc_mcp_ctre.config`. The list is in
docs/SETTINGS.md. Nothing outside PASSERINE_DIR is opened or written (`.mid`/`.midi` in, `.chrp` out).
"""

from frc_mcp_core.settings import env_number
from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.folders import Folders

MIDI = Folders("PASSERINE_DIR", (".mid", ".midi"), "MIDI file", "passerine_list", max_mb_env="PASSERINE_MAX_MB", default_max_mb=20)
CHRP_SUFFIX = ".chrp"
CHRP = Folders("PASSERINE_DIR", (CHRP_SUFFIX,), "CHRP file", "passerine_list")


def timeout_s() -> float:
    return env_number("PASSERINE_TIMEOUT_S", 60, 5, 3600, CtreError)
