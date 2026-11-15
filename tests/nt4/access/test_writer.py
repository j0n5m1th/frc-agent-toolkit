"""Direct tests of the write path in `access/writer.py`, against a real local ntcore instance (no server, no session).

The tool-layer tests in test_server_writes.py cover the same paths end to end; these pin the function contracts.
"""

import ntcore
import pytest

from frc_mcp_nt4.access import writer
from frc_mcp_nt4.access.publisher_registry import PublisherRegistry
from frc_mcp_nt4.access.publishing import PublishOptions
from frc_mcp_nt4.errors import NT4Error

pytestmark = pytest.mark.usefixtures("isolate_capture_logs", "allow_test_writes")


@pytest.fixture
def inst():
    """A standalone local instance: nothing connects, so every publish stays in this process."""
    i = ntcore.NetworkTableInstance.create()
    yield i
    ntcore.NetworkTableInstance.destroy(i)


@pytest.fixture
def publishers():
    r = PublisherRegistry()
    yield r
    r.close()  # cancels any TTL timer a test left armed


def test_refused_when_writes_are_disabled_or_outside_prefixes(inst, publishers, monkeypatch):
    monkeypatch.setenv("NT4_READ_ONLY", "1")
    with pytest.raises(NT4Error, match="Writes are disabled"):
        writer.set_value(inst, publishers, "/t/x", 1, "int", PublishOptions())
    monkeypatch.delenv("NT4_READ_ONLY")
    with pytest.raises(NT4Error, match="outside the writable prefixes"):
        writer.set_value(inst, publishers, "/Drive/x", 1, "int", PublishOptions())
    with pytest.raises(NT4Error, match="outside the writable prefixes"):
        writer.unpublish(inst, publishers, "/Drive/x")
    assert not inst.getTopic("/Drive/x").exists() and publishers.names == []


def test_new_topic_needs_a_type(inst, publishers):
    with pytest.raises(NT4Error, match="Pass `type`"):
        writer.set_value(inst, publishers, "/t/new", 1, None, PublishOptions())
    assert not inst.getTopic("/t/new").exists()


def test_publish_new_topic_returns_written_value_and_type(inst, publishers):
    result = writer.set_value(inst, publishers, "/t/x", 2, "int", PublishOptions())
    assert result.topic == "/t/x" and result.type_str == "int" and result.written == 2
    assert result.default is False and result.publisher_expires_in_s is None
    assert "/t/x" in publishers and publishers.get("/t/x")[0] == "int"
    assert inst.getTopic("/t/x").getTypeString() == "int"


def test_properties_only_change_on_existing_topic(inst, publishers):
    writer.set_value(inst, publishers, "/t/p", 1.5, "double", PublishOptions())
    result = writer.set_value(inst, publishers, "/t/p", None, None, PublishOptions(properties={"unit": "m"}))
    assert result.type_str is None and result.written is None and result.properties == ("unit",)
    assert inst.getTopic("/t/p").getProperties().get("unit") == "m"


def test_properties_only_change_on_missing_topic_is_an_error(inst, publishers):
    with pytest.raises(NT4Error, match="does not exist"):
        writer.set_value(inst, publishers, "/t/missing", None, None, PublishOptions(properties={"unit": "m"}))
    with pytest.raises(NT4Error, match="Nothing to do"):
        writer.set_value(inst, publishers, "/t/p", None, None, PublishOptions())


def test_default_option_marks_the_result_as_weak(inst, publishers):
    result = writer.set_value(inst, publishers, "/t/d", 3, "int", PublishOptions(default=True))
    assert result.default is True and result.written == 3


async def test_publisher_ttl_is_reflected_in_expires_in(inst, publishers):
    result = writer.set_value(inst, publishers, "/t/lease", 4, "int", PublishOptions(publisher_ttl_s=0.6))
    assert result.publisher_expires_in_s is not None and 0 < result.publisher_expires_in_s <= 0.6
    assert publishers.has_timer("/t/lease")


def test_unpublish_reports_held_once(inst, publishers):
    writer.set_value(inst, publishers, "/t/u", 1, "int", PublishOptions())
    assert writer.unpublish(inst, publishers, "/t/u") is True
    assert writer.unpublish(inst, publishers, "/t/u") is False
    assert "/t/u" not in publishers


def test_retained_topic_leaves_a_ghost_after_release(inst, publishers):
    writer.set_value(inst, publishers, "/gh/keep", 5, "int", PublishOptions(properties={"retained": True}))
    assert writer.unpublish(inst, publishers, "/gh/keep") is True
    ghost = publishers.ghost("/gh/keep")
    assert ghost is not None and ghost[0] == "int" and ghost[1].get("retained") is True
    # publishing again brings the topic back to life and clears the ghost
    writer.set_value(inst, publishers, "/gh/keep", 6, "int", PublishOptions())
    assert publishers.ghost("/gh/keep") is None
