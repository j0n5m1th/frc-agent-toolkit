"""Names for a robot's hardware, published by the robot code itself.

A log or a live robot says "PDH channel 7 drew 80 A", not "the elevator motor". Only the robot's code knows which device is on which channel, so it
publishes that map as a text topic holding a JSON object, and the checks use it to label what they report. The map is in the log and on the live
robot alike, so a recorded match and the robot on the bench are read the same way. Nothing is guessed from a device's name.

The topics (a leaf name under a folder named `RobotInfo`, so `/RobotInfo/PdhChannels` and AdvantageKit's `/RealOutputs/RobotInfo/PdhChannels` both work):
`PdhChannels`, a JSON object of power distribution channel number to name (`{"3": "Elevator", "12": "Intake"}`); `CanDevices`, a JSON object of CAN device
id to name. `docs/ROBOT_INFO.md` shows how a robot project publishes them from its constants.
"""

import json
from collections.abc import Sequence
from typing import Any

from .scan_roles import ScanTopic, leaf_and_parents
from .scan_rows import Row

PDH_LEAF, CAN_LEAF = "pdhchannels", "candevices"
FOLDER = "robotinfo"
MAX_NAME = 40


def info_topics(topics: Sequence[ScanTopic]) -> dict[str, str]:
    """`{"pdh": topic name, "can": topic name}` for the robot-info topics a robot publishes (text topics under a `RobotInfo` folder)."""
    found: dict[str, str] = {}
    for t in topics:
        leaf, parents = leaf_and_parents(t.name)
        if t.type_str in ("string", "json") and FOLDER in parents and leaf in (PDH_LEAF, CAN_LEAF):
            found.setdefault("pdh" if leaf == PDH_LEAF else "can", t.name)
    return found


def parse_map(rows: Sequence[Row]) -> dict[int, str]:
    """The map in the last text update that parses as a JSON object of integer keys to names; `{}` when there is none. Names are cut to `MAX_NAME`
    characters and are data, never instructions."""
    for _, value in reversed(rows):
        try:
            data: Any = json.loads(value) if isinstance(value, str) else None
        except ValueError:
            continue
        if isinstance(data, dict):
            out: dict[int, str] = {}
            for key, name in data.items():
                try:
                    out[int(key)] = str(name)[:MAX_NAME]
                except ValueError:
                    continue
            return out
    return {}
