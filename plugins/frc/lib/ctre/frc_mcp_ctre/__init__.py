"""Code shared by the CTRE servers (`frc_mcp_ctre_tools`, `_owlet`, `_passerine`, `_caniv`). It works without a robot, a device
or a network: it reads CTRE's tool index, knows the installed tool versions and the project's vendordeps, and picks the
tool version that fits. Rule: nothing here may import a server; it may import `frc_mcp_wpilib` and nothing else here.
"""
