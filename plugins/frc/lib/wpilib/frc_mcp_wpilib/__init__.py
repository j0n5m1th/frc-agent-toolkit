"""Code shared by the WPILib servers (`frc_mcp_nt4`, `frc_mcp_wpilog`, `frc_mcp_sim`, `frc_mcp_vscode`). The profiling
server `frc_mcp_jfr` is standalone. What every server shares is `frc_mcp_core`.

Nothing here cares where the data comes from (live NetworkTables, a WPILOG file, or the project's Gradle build). Subpackages:
`reduce/` (the reducers), `tooling/` (topic pages and parameter types), `gradle/` (running a project's Gradle wrapper),
`schema/` (struct and protobuf registries). Rule: nothing here may import a server package.
"""
