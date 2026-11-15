"""The Systemcore server's FastMCP instance (made from `SPEC`) and annotations."""

from frc_mcp_core.server_support import FULL_ONLY_TAG, tool_errors

from .spec import SPEC

# Hints are sent only where they differ from the protocol defaults (not read-only, destructive, not idempotent, open world): every
# tool's schema is paid for in every session. Unlisted hints take the default.
READ_ONLY = {"readOnlyHint": True}
# Saves a file on this machine (a bundle or a log copy) and, for a bundle, starts a job on the controller; deletes nothing.
FETCH = {"destructiveHint": False}
# Removes files on the controller; refused unless SYSTEMCORE_ALLOW_PRUNE=1.
PRUNE = {"destructiveHint": True}
# Changes only this server's own state folder (pinned host keys, remembered devices).
STATE = {"destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
# Reads the public GitHub release list and saves it in the state folder; changes nothing else.
LOOKUP = {"destructiveHint": False, "idempotentHint": True}
# Installs this server's public key on the controller (its authorized_keys); the password comes from the server's settings.
PROVISION = {"destructiveHint": False, "idempotentHint": True}
# Stops the robot program (or reboots the controller) on the Systemcore; refused unless SYSTEMCORE_ALLOW_RESTART=1.
RESTART = {"destructiveHint": True}
# Changes this computer's Wi-Fi connection (drops the network it is on); refused unless SYSTEMCORE_ALLOW_WIFI_JOIN=1.
CONNECT = {"destructiveHint": True, "idempotentHint": True}

# The parameter every tool shares; the instructions (in `spec.py`) say what it means once, so its schema carries no description.
Host = str | None

mcp, apply_toolset = SPEC.create()  # the tool modules in `SPEC.tool_modules` register on this instance


__all__ = ["CONNECT", "FETCH", "FULL_ONLY_TAG", "Host", "LOOKUP", "RESTART", "PROVISION", "PRUNE", "READ_ONLY", "STATE", "apply_toolset", "mcp", "tool_errors"]
