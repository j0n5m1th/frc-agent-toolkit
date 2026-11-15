"""frc_mcp_core.network: the robot-network address rule, and that the Systemcore address check (which may not import it) says the same."""

import pytest

from frc_mcp_core.network import is_robot_address
from frc_mcp_systemcore_common.hosts import _private

ADDRESSES = [
    ("127.0.0.1", True), ("10.99.99.2", True), ("172.30.0.1", True), ("192.168.1.5", True), ("169.254.7.7", True),
    ("::1", True), ("fe80::1", True), ("fe80::1%usb0", True), ("fd12:3456::1", True), ("fc00::1", True),
    ("8.8.8.8", False), ("100.64.0.1", False), ("198.18.0.1", False), ("198.19.255.255", False), ("192.0.0.8", False),
    ("192.0.2.1", False), ("198.51.100.1", False), ("203.0.113.1", False),
    ("2002:808:808::1", False), ("2001:db8::1", False), ("2001:0:4136:e378:8000:63bf:3fff:fdd2", False),
    ("2606:4700::1111", False), ("::ffff:8.8.8.8", False), ("ff02::1", False),
]


@pytest.mark.parametrize(("address", "expected"), ADDRESSES)
def test_which_addresses_are_robot_network_ones(address, expected):
    assert is_robot_address(address) is expected
    assert _private(address) is expected  # the Systemcore copy of the rule agrees


def test_a_text_that_is_not_an_address_raises():
    with pytest.raises(ValueError):
        is_robot_address("roborio.local")
