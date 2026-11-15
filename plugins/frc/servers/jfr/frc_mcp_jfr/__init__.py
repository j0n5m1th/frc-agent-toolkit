"""JFR profiling server: let an AI assistant record and read Java Flight Recorder data from any JVM.
Standalone: it needs only a JDK (`jfr` and `jcmd`), knows nothing about robots or WPILib, and imports only `frc_mcp_core`.
A project is referenced only by where its recordings live (JFR_DIR) and a text filter on the JVM's command line.
"""
