"""`frc_mcp_core.settings`: one model for reading settings (the environment, a blank value counts as not set), the optional `.env` layer,
the size reader and check, and the two records the NT4 and log servers extend."""

import os

import pytest

from frc_mcp_core.errors import FrcMcpError
from frc_mcp_core.folders import check_size
from frc_mcp_core.settings import (
    Capability,
    EnvFile,
    env_flag,
    env_int,
    env_megabytes,
    env_str,
    nonblank,
)
from frc_mcp_nt4.access.topic_listing import TopicFilter as LiveFilter
from frc_mcp_nt4.capture.query import QuerySpec as LiveSpec
from frc_mcp_wpilib.tooling.query_bases import ReduceKnobs, TopicMatch
from frc_mcp_wpilog.logfile import TopicFilter as LogFilter
from frc_mcp_wpilog.queries import QuerySpec as LogSpec


class Problem(FrcMcpError):
    pass


def test_a_blank_flag_is_a_mistake_unless_read_through_nonblank_and_a_bad_one_names_what_it_expects(monkeypatch):
    for blank in ("", "   "):
        monkeypatch.setenv("T_FLAG", blank)
        with pytest.raises(Problem, match="T_FLAG must be a boolean"):  # safety switches fail closed
            env_flag("T_FLAG", False, Problem)
        assert env_flag("T_FLAG", False, Problem, getenv=nonblank) is False and env_flag("T_FLAG", True, Problem, getenv=nonblank) is True
    for word, expected in (("YES", True), (" off ", False), ("1", True), ("false", False)):
        monkeypatch.setenv("T_FLAG", word)
        assert env_flag("T_FLAG", not expected, Problem) is expected
    monkeypatch.setenv("T_FLAG", "Maybe")
    with pytest.raises(Problem, match=r"T_FLAG must be a boolean \(1/0, true/false, yes/no, or on/off\), not 'maybe'"):
        env_flag("T_FLAG", False, Problem)
    with pytest.raises(Problem, match="T_FLAG must be 1 or 0, not 'maybe'"):
        env_flag("T_FLAG", False, Problem, expects="1 or 0")


def test_a_capability_can_say_what_a_bad_value_should_be():
    values = {"T_ALLOW": "perhaps"}
    gate = Capability("T_ALLOW", "Doing it", Problem, why="it matters", getenv=values.get, expects="1 or 0")
    with pytest.raises(Problem, match="T_ALLOW must be 1 or 0"):
        gate.enabled()
    del values["T_ALLOW"]
    assert gate.refusal() == "Doing it is off. Set T_ALLOW=1 on the MCP server to allow it (it matters)."


def test_megabytes_become_bytes_with_the_same_range_check(monkeypatch):
    monkeypatch.delenv("T_MB", raising=False)
    assert env_megabytes("T_MB", 2, 1, 10, Problem) == 2 * 2**20
    monkeypatch.setenv("T_MB", "1.5")
    assert env_megabytes("T_MB", 2, 1, 10, Problem) == int(1.5 * 2**20)
    monkeypatch.setenv("T_MB", "11")
    with pytest.raises(Problem, match="T_MB must be a number from 1 to 10, not '11'"):
        env_megabytes("T_MB", 2, 1, 10, Problem)


def test_check_size_names_the_setting_that_raises_the_limit(tmp_path):
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (3 * 2**20))
    check_size(big, 3 * 2**20, "T_MAX_MB", Problem, "read this file")  # exactly at the limit is fine
    with pytest.raises(Problem, match=r"big\.bin is 3 MB, over the T_MAX_MB limit; raise it to read this file\."):
        check_size(big, 2**20, "T_MAX_MB", Problem, "read this file")


def test_the_env_file_layers_behind_the_real_environment_and_never_enters_it(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("T_A=from file\nexport T_B = 'p#ss word'\nT_C=5 # note\nOTHER=x\nT_REAL_ONLY=file\n# T_D=commented\n", encoding="utf-8")
    monkeypatch.delenv("T_A", raising=False)
    monkeypatch.setenv("T_B", "from env")
    monkeypatch.setenv("T_C", "  ")  # blank: the file's value is used
    monkeypatch.delenv("T_REAL_ONLY", raising=False)
    file = EnvFile("T_", lambda: path, real_env_only=("T_REAL_ONLY",))
    assert file.get("T_A") is None  # not read yet
    assert sorted(file.load()) == ["T_A", "T_C"]  # T_B comes from the real environment
    assert file.get("T_A") == "from file" and file.get("T_B") == "from env" and file.get("T_C") == "5"
    assert file.get("OTHER") is None and file.get("T_REAL_ONLY") is None and file.get("T_D") is None
    assert "T_A" not in os.environ  # nothing from the file is put into the environment
    assert env_str("T_A", getenv=file.get) == "from file"
    path.unlink()
    assert file.load() == [] and file.get("T_A") is None  # a missing file is fine, and the old values are forgotten


# -- the shared bases of the NT4 and log servers' records -----------------------------------------------------------------


def test_both_topic_filters_share_the_matching_rules():
    assert issubclass(LiveFilter, TopicMatch) and issubclass(LogFilter, TopicMatch)
    for make in (LiveFilter, LogFilter):
        f = make(prefix="/a", name_contains="ARM", type_str="double")
        assert f.name_matches("/a/Arm/x") and not f.name_matches("/a/leg") and f.type_matches("double") and not f.type_matches("int")
        assert make().name_matches("anything") and make().type_matches("anything") and not make().include_hidden
    assert LiveFilter("/", flag="cached").flag == "cached" and LogFilter("/", counts=True).counts is True


def test_both_query_specs_share_the_reduce_knobs_and_keep_their_defaults():
    assert issubclass(LiveSpec, ReduceKnobs) and issubclass(LogSpec, ReduceKnobs)
    log = LogSpec()
    assert (log.reduce, log.fields, log.threshold, log.max_samples, log.max_topics) == ("summary", None, None, 20, 40)
    live = LiveSpec(topics=None, since_ms=None, until_ms=None, reduce="edges", max_samples=5)
    assert (live.reduce, live.max_samples, live.max_topics, live.precision) == ("edges", 5, 40, log.precision)
    with pytest.raises(TypeError):
        LiveSpec(None, "summary")  # type: ignore[misc]  # the live record is built by keyword


def test_an_env_file_in_utf16_or_utf8_with_a_bom_is_read_and_unreadable_bytes_get_a_clean_error(tmp_path):
    path = tmp_path / ".env"
    file = EnvFile("T_", lambda: path)
    path.write_bytes("T_A=one\r\nT_B=two\r\n".encode("utf-16"))  # what PowerShell 5.1 `>` writes: BOM first
    file.load()
    assert file.values == {"T_A": "one", "T_B": "two"}
    path.write_bytes(b"\xef\xbb\xbfT_A=bom\n")
    file.load()
    assert file.values == {"T_A": "bom"}
    path.write_bytes(b"T_A=pw\xff\xfe\x80\n")
    with pytest.raises(FrcMcpError, match="not UTF-8") as bad:
        file.load()
    assert "pw" not in str(bad.value)  # never any of the file's text


def test_a_quoted_env_value_loses_its_quotes_and_a_trailing_comment(tmp_path):
    file = EnvFile("T_", lambda: tmp_path / ".env")
    parsed = file.parse('T_A="pw # not a comment" # note\nT_B=\'x y\'   # note\nT_C="plain"\nT_D=bare # note\n')
    assert parsed == {"T_A": "pw # not a comment", "T_B": "x y", "T_C": "plain", "T_D": "bare"}


@pytest.mark.parametrize("text", ['T_A="abc', "T_A='abc", 'T_A="', "T_A=\"abc'", 'T_A="abc" tail', "export T_A='abc # note"])
def test_an_env_value_with_an_unclosed_quote_is_an_error_naming_the_line_never_the_value(tmp_path, text):
    file = EnvFile("T_", lambda: tmp_path / ".env")
    with pytest.raises(FrcMcpError, match=r"\.env, line 3: .*quote") as bad:
        file.parse(f"# note\nT_B=fine\n{text}\nT_C=after\n")
    assert "abc" not in str(bad.value) and "tail" not in str(bad.value)
    (tmp_path / ".env").write_text(f"T_B=1\n\n{text}\n", encoding="utf-8")
    with pytest.raises(FrcMcpError, match="line 3"):
        file.load()


def test_quotes_inside_or_after_a_value_are_not_unclosed_quotes(tmp_path):
    file = EnvFile("T_", lambda: tmp_path / ".env")
    text = "T_A=ab\"cd\nT_B=it's\nT_C=\"\"\nT_D=''\nT_E=\nOTHER=\"open\n"  # OTHER has no T_ prefix: not ours, not judged
    assert file.parse(text) == {"T_A": 'ab"cd', "T_B": "it's", "T_C": "", "T_D": "", "T_E": ""}


@pytest.mark.parametrize(("text", "value"), [
    (r'T_A="a\"b"', 'a"b'),  # an escaped quote
    (r'T_A="a\\b"', "a\\b"),  # an escaped backslash
    (r'T_A="line1\nline2"', "line1\nline2"),
    (r'T_A="pa\ss"', "pa\\ss"),  # any other backslash stays
    (r"T_A='a\nb'", r"a\nb"),  # single quotes are literal
    (r"T_A='trailing\'", "trailing\\"),
    ('T_A="x"#c', "x"),  # a comment right after the closing quote
    ("T_A='x'  # c", "x"),
    ('T_A="x"   ', "x"),
])
def test_escapes_in_double_quotes_and_what_may_follow_a_closing_quote(tmp_path, text, value):
    assert EnvFile("T_", lambda: tmp_path / ".env").parse(text + "\n") == {"T_A": value}


@pytest.mark.parametrize("text", ['T_A="abc"def', "T_A='abc'def # c", 'T_A="abc" "def"'])
def test_text_after_a_closing_quote_is_an_error_that_names_the_line_not_the_value(tmp_path, text):
    with pytest.raises(FrcMcpError, match=r"\.env, line 2: text follows the closing quote") as bad:
        EnvFile("T_", lambda: tmp_path / ".env").parse(f"T_B=1\n{text}\n")
    assert "abc" not in str(bad.value) and "def" not in str(bad.value)


@pytest.mark.parametrize("encoding", ["utf-32", "utf-16-le", "utf-16-be", "utf-32-le"])
def test_a_utf32_or_bom_less_wide_file_is_a_clean_error_not_empty_settings(tmp_path, encoding):
    file = EnvFile("T_", lambda: tmp_path / ".env")
    (tmp_path / ".env").write_bytes("T_A=hunter2\n".encode(encoding))
    with pytest.raises(FrcMcpError, match="not UTF-8") as bad:
        file.load()
    assert "hunter2" not in str(bad.value) and file.values == {}


def test_a_utf16_file_with_a_bom_is_still_read(tmp_path):
    file = EnvFile("T_", lambda: tmp_path / ".env")
    (tmp_path / ".env").write_bytes("T_A=ok\r\n".encode("utf-16"))
    file.load()
    assert file.values == {"T_A": "ok"}


def test_a_whole_number_setting_rejects_decimals_and_exponents(monkeypatch):
    for bad in ("600.9", "1e3", "x"):
        monkeypatch.setenv("T_INT", bad)
        with pytest.raises(Problem, match="T_INT must be a number from 1 to 1000"):
            env_int("T_INT", 5, 1, 1000, Problem)
    monkeypatch.setenv("T_INT", " 600 ")
    assert env_int("T_INT", 5, 1, 1000, Problem) == 600
    monkeypatch.setenv("T_INT", "1001")
    with pytest.raises(Problem):
        env_int("T_INT", 5, 1, 1000, Problem)
