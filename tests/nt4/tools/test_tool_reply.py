"""The one reply entry every NT4 read tool uses for a read result."""

from frc_mcp_nt4.access.reading import Observed, ReadMiss
from frc_mcp_nt4.tools.reply import NO_OPTIONS, ReplySpec, reply_entry


class Value:
    """The three ntcore.Value methods `Observed` reads: value, time and server_time."""

    def __init__(self, value, t=100, server=200):
        self._v, self._t, self._s = value, t, server

    def value(self):
        return self._v

    def time(self):
        return self._t

    def server_time(self):
        return self._s


def observed(type_string, value, t=100, server=200):
    return Observed(type_string, Value(value, t, server))


def test_a_miss_is_the_shared_err_shape():
    assert reply_entry(ReadMiss("not announced")) == {"err": "not announced"}


def test_a_scalar_gets_only_its_shaped_value():
    # double/string are self-describing, so no `type` key is wasted on them.
    assert reply_entry(observed("double", 1.23456)) == {"v": 1.2346}
    assert reply_entry(observed("string", "hi")) == {"v": "hi"}
    assert reply_entry(observed("double", 1.0), NO_OPTIONS) == {"v": 1.0}


def test_a_type_the_json_cannot_show_is_stated_once():
    assert reply_entry(observed("double[]", [1.0, 2.0])) == {"type": "double[]", "v": [1.0, 2.0]}


def test_the_optional_knobs_only_add_what_they_ask_for():
    entry = reply_entry(observed("double", 1.0), ReplySpec(unit="V", timestamps=True))
    assert entry == {"v": 1.0, "unit": "V", "ts_us": 200}


def test_the_server_timestamp_is_dropped_when_ntcore_has_no_real_one():
    # server_time 1 means "published by this client", which is not a server time worth reporting.
    entry = reply_entry(observed("double", 1.0, server=1), ReplySpec(timestamps=True))
    assert entry == {"v": 1.0, "ts_us": None}


def test_age_is_the_last_update_reply_of_the_shared_clock():
    entry = reply_entry(observed("double", 1.0, t=100), ReplySpec(age=(1_000_100, True)))
    assert entry == {"v": 1.0, "last_update": {"age_ms": 1_000.0}}


def test_an_undatable_update_says_why():
    entry = reply_entry(observed("double", 1.0, t=0), ReplySpec(age=(1_000_100, True)))
    assert entry["last_update"] == {"age_ms": None, "reason": "update timestamp unavailable"}
