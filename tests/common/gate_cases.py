"""What each gated server hides: shared by the gate tests."""

from frc_mcp_ctre_caniv.spec import SPEC as CANIV
from frc_mcp_ctre_tools.spec import SPEC as CTRE_TOOLS
from frc_mcp_jfr.spec import SPEC as JFR
from frc_mcp_nt4.spec import SPEC as NT4
from frc_mcp_systemcore.spec import SPEC as SYSTEMCORE
from frc_mcp_vscode.spec import SPEC as VSCODE

# (spec, the environment that turns every gate on, the tools that gate hides, one gate's env name)
CASES = {
    "nt4": (NT4, {"NT4_ALLOW_WRITES": "1", "NT4_WRITE_PREFIXES": "/Test", "NT4_READ_ONLY": "0"}, {"nt4_set_value", "nt4_release_publisher"}, "NT4_ALLOW_WRITES"),
    "vscode": (VSCODE, {"VSCODE_ALLOW_DEPLOY": "1"}, {"wpilib_deploy"}, "VSCODE_ALLOW_DEPLOY"),
    "jfr": (JFR, {"JFR_ALLOW_RECORD": "1"}, {"jfr_record"}, "JFR_ALLOW_RECORD"),
    "ctre-tools": (CTRE_TOOLS, {"CTRE_ALLOW_INSTALL": "1"}, {"ctre_install"}, "CTRE_ALLOW_INSTALL"),
    "ctre-caniv": (CANIV, {"CANIV_ALLOW_CONFIG": "1", "CANIV_ALLOW_FLASH": "1"}, {"caniv_configure", "caniv_flash"}, "CANIV_ALLOW_CONFIG"),
    "systemcore": (SYSTEMCORE, {"SYSTEMCORE_ALLOW_RESTART": "1", "SYSTEMCORE_ALLOW_PROVISION": "1"},
                   {"systemcore_restart_robot_code", "systemcore_reboot", "systemcore_provision", "systemcore_ssh_forget"}, "SYSTEMCORE_ALLOW_PROVISION"),
}
