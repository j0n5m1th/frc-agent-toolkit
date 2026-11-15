"""WPILib VS Code commands server: let an AI assistant do what the "WPILib: ..." command palette entries do, without VS Code.
Those are a thin layer over `./gradlew` tasks and a few file edits (Build, Test, Clean, Deploy, Install Tools, Start Tool, Set Team
Number, Manage Vendor Libraries). This server runs the same tasks and edits, with results cut down to what a model needs.
Running the simulator is `frc_mcp_sim`.
"""
