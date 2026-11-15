"""Environment-driven settings of the CANivore server (read at call time). The CTRE_* settings (tools folder, index) are in `frc_mcp_ctre.config`.

CANIV_FIRMWARE_DIR=/fw;/more  folders `.crf` firmware files may be read from (the files directly in them). Optional: the default is CTRE Tuner X's app-data folder, where it saves the
                              firmware it has downloaded (seen on a real Windows machine). Nothing outside these folders is ever read.
CANIV_ALLOW_CONFIG=1          renaming a CANivore and resetting it are off until this is set.
CANIV_ALLOW_FLASH=1           flashing firmware onto a CANivore is off until this is set.
CANIV_TIMEOUT_S=30            one caniv run is stopped after this many seconds (a firmware flash has its own, CANIV_FLASH_TIMEOUT_S=300).
CANIV_TOOLSET=full|core       core hides caniv_configure and caniv_flash: fewer schema tokens in every session.

Reading (list, info, firmware files) and blinking the LED need no switch. Not available on macOS: CTRE publishes no macOS caniv.
"""

from frc_mcp_core.settings import Capability, env_number
from frc_mcp_core.toolset import Toolset
from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.folders import Folders
from frc_mcp_ctre.store import tunerx_dirs

FIRMWARE = Folders("CANIV_FIRMWARE_DIR", (".crf",), "firmware file", "caniv_firmware", default_roots=tunerx_dirs, default_label="CTRE Tuner X's saved files", recursive=False)
ALLOW_CONFIG = Capability("CANIV_ALLOW_CONFIG", "Renaming or resetting a CANivore", CtreError, why="it changes the device, and a reset drops it off the CAN bus for a moment")
ALLOW_FLASH = Capability("CANIV_ALLOW_FLASH", "Flashing CANivore firmware", CtreError, why="it replaces the firmware on the device")


def timeout_s() -> float:
    return env_number("CANIV_TIMEOUT_S", 30, 5, 600, CtreError)


def flash_timeout_s() -> float:
    return env_number("CANIV_FLASH_TIMEOUT_S", 300, 30, 3600, CtreError)


TOOLSET = Toolset("CANIV_TOOLSET", CtreError)  # read it with TOOLSET.current(); `create_server` makes the server's apply_toolset from it
