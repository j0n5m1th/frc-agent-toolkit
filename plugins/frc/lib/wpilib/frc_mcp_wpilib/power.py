"""Power facts shared by the scan and the power check: the brownout setting.

A Systemcore browns out below a voltage that is configurable (6 V nominal; the vendor specification also lists 6.3 V with a 7.5 V restoration voltage,
both configurable, and says brownout protection is off in some alpha images). So the number is read from the robot when it publishes it, and only
otherwise assumed, and a reply says which.
"""

from collections.abc import Sequence

from .scan_roles import ScanTopic, by_updates, leaf_and_parents
from .scan_rows import Row, finite_numbers

BROWNOUT_DEFAULT_V = 6.0  # the Systemcore's nominal setting (vendor specification, not seen on a robot here)
BROWNOUT_LEAVES = ("brownoutvoltage",)
DEFAULT_BASIS = "default: the Systemcore's nominal setting, which is configurable"


def threshold_topic(visible: Sequence[ScanTopic]) -> ScanTopic | None:
    """The topic that holds the robot's brownout setting: a numeric leaf named `BrownoutVoltage`."""
    found = [t for t in visible if t.is_numeric and leaf_and_parents(t.name)[0] in BROWNOUT_LEAVES]
    return by_updates(found)[0] if found else None


def brownout_threshold(topic: ScanTopic | None, rows: Sequence[Row], given: float | None = None) -> tuple[float, str]:
    """`(volts, basis)`: the value the caller gave, else the robot's own setting (its last finite reading), else the Systemcore's nominal one."""
    if given is not None:
        return given, "given"
    if topic is not None and (values := finite_numbers(rows)):
        return float(values[-1][1]), f"logged ({topic.name})"
    return BROWNOUT_DEFAULT_V, DEFAULT_BASIS
