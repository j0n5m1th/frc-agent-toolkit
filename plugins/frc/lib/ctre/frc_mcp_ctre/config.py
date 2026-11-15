"""Settings of the CTRE servers (CTRE_*), read at call time. The list is in docs/SETTINGS.md.

Only https://redist.ctr-electronics.com is fetched. Requests are never repeated: no retries, a saved index is reused for
CTRE_INDEX_MAX_AGE_H, and a refresh asks "has it changed?" first (ETag). CTRE_REQUEST_DELAY_S (0 to 60) is the least gap
between two requests. No sign was found that CTRE Tuner X uses CTRE_TOOLS_DIR (it keeps private copies in its own app data).
"""

import os
import sys
from pathlib import Path

from frc_mcp_core.settings import Capability, env_megabytes, env_number, env_str

from .errors import CtreError

INDEX_URL = "https://redist.ctr-electronics.com/index.json"
ALLOWED_HOSTS = frozenset({"redist.ctr-electronics.com"})  # the index and every download must come from here

INSTALL = Capability("CTRE_ALLOW_INSTALL", "Installing CTRE tools", CtreError, why="it downloads programs into the tools folder")


def tools_dir() -> Path:
    if configured := env_str("CTRE_TOOLS_DIR"):
        return Path(configured).expanduser().resolve()
    home = Path.home()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "ctre"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "ctre"
    return home / ".local" / "share" / "ctre"


def project_dir() -> Path | None:
    configured = env_str("CTRE_PROJECT_DIR")
    return Path(configured).expanduser().resolve() if configured else None


def index_file() -> Path | None:
    configured = env_str("CTRE_INDEX_FILE")
    return Path(configured).expanduser().resolve() if configured else None


def index_url() -> str:
    return env_str("CTRE_INDEX_URL", INDEX_URL) or INDEX_URL


def index_max_age_s() -> float:
    return env_number("CTRE_INDEX_MAX_AGE_H", 24, 0, 8760, CtreError) * 3600


def timeout_s() -> float:
    return env_number("CTRE_TIMEOUT_S", 60, 2, 3600, CtreError)


def request_delay_s() -> float:
    return env_number("CTRE_REQUEST_DELAY_S", 1, 0, 60, CtreError)


def max_download_bytes() -> int:
    return env_megabytes("CTRE_MAX_DOWNLOAD_MB", 500, 1, 8192, CtreError)
