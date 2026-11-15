"""Settings of the Owlet server (OWLET_*), read at call time; the CTRE_* ones are in `frc_mcp_ctre.config`. The list is in
docs/SETTINGS.md. Nothing outside OWLET_DIR is opened or written. Use the same folder as WPILOG_DIR so a converted file opens
right away in the log server.
"""

from frc_mcp_core.settings import env_number
from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.folders import Folders

SUFFIX = ".hoot"
HOOTS = Folders("OWLET_DIR", (SUFFIX,), "hoot file", "owlet_list", max_mb_env="OWLET_MAX_MB", default_max_mb=4096)


def timeout_s() -> float:
    return env_number("OWLET_TIMEOUT_S", 600, 10, 86400, CtreError)
