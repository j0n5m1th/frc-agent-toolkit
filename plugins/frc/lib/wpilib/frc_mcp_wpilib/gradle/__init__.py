"""Running a robot project's Gradle wrapper as a child process, for the servers that launch one (simulation, VS Code commands).
`project` (which folder may run, `gradlew`, property checks), `run` (one child process: console reader, time limit, killing the
process tree), `console` (the bounded console buffer). Each server decides which tasks it allows and passes plain arguments.
"""
