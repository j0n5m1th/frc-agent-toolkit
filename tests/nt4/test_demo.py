"""`frc-mcp demo robot` (`frc_mcp_nt4.demo`): the practice robot starts on a free port, says what to type, and serves a readable topic."""

import socket
import subprocess
import sys
import threading
import time

import ntcore
import pytest

from frc_mcp_nt4 import config, demo

START = "from frc_mcp_core.command import main; raise SystemExit(main(['demo', 'robot', '--port', sys.argv[1]]))"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_the_default_port_is_the_nt4_servers_own_default_and_port_can_be_changed():
    assert demo.DEFAULT_PORT == config.DEFAULT_PORT == 5810
    assert demo.parse_args([]).port == 5810
    assert demo.parse_args(["--port", "5999"]).port == 5999
    assert demo.parse_args([], default_port=5820).port == 5820  # what the evaluations pass


@pytest.mark.parametrize("value", ["0", "70000", "-1"])
def test_a_port_outside_the_valid_range_is_refused(value, capsys):
    with pytest.raises(SystemExit) as stop:
        demo.parse_args(["--port", value])
    assert stop.value.code == 2 and "--port" in capsys.readouterr().err


def test_the_robot_starts_through_the_command_prints_its_settings_and_serves_a_topic():
    port = free_port()
    robot = subprocess.Popen([sys.executable, "-c", "import sys\n" + START, str(port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    watchdog = threading.Timer(60, robot.kill)  # a robot that never starts must not hang the suite
    watchdog.start()
    client = ntcore.NetworkTableInstance.create()
    try:
        assert robot.stdout is not None
        lines = [robot.stdout.readline() for _ in range(5)]
        text = "".join(lines)
        assert f"fake robot listening on 127.0.0.1:{port}" in lines[0]
        assert "NT4_HOST = localhost" in text and f"NT4_PORT = {port}" in text and "Ctrl+C" in text
        topic = client.getDoubleTopic("/Fake/Elevator/Current").subscribe(-1.0)
        client.startClient4("demo-test")
        client.setServer("127.0.0.1", port)
        deadline = time.monotonic() + 15
        while topic.get(-1.0) == -1.0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert topic.get(-1.0) in (demo.BASE_A, 42.5)
    finally:
        watchdog.cancel()
        ntcore.NetworkTableInstance.destroy(client)
        robot.kill()
        robot.wait(timeout=10)
        assert robot.stdout is not None
        robot.stdout.close()
