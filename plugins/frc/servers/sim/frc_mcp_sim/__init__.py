"""Simulation server: let an AI assistant run the robot project in WPILib desktop simulation and read its console.
It launches the project's own Gradle simulate task as a child process, watches the console, and says whether the program really
started (a clean compile is not a clean run). Reading the robot's data while it simulates is the NT4 server's job.
"""
