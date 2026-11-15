"""Code shared by the servers that reach the robot controller (`frc_mcp_systemcore`, `frc_mcp_ctre_diag`): put code here when
more than one of them needs it to reach or trust the controller. Today: `hosts.py`, which addresses may be contacted (robot
network only unless the owner allows any) and the opener that never uses a proxy.

Rule: nothing here may import a server or `frc_mcp_ctre`, and it defines no error type: each server passes its own, so its
messages stay its own. (`tests/common/test_layering.py` checks it.)
"""
