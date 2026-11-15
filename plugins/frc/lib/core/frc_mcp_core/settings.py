"""Settings: how every server reads what its owner configured.
A setting is an environment variable. Text and number settings treat a blank one as not set; a flag treats a blank one as a mistake
(safety switches fail closed) unless its server reads it through `nonblank`. A server with secrets may layer a plain `.env` file
behind the environment (`EnvFile`): the real environment wins, and nothing from the file goes into `os.environ`, so programs the
server starts never inherit it. Every reader takes an optional `getenv`, so one server's layering reaches all its settings.
"""

import codecs
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .errors import FrcMcpError

Getenv = Callable[[str], str | None]  # where a setting comes from: the environment by default, or a server's own layering of it

TRUE_WORDS = ("1", "true", "yes", "on")
FALSE_WORDS = ("0", "false", "no", "off")
BOOLEAN_WORDS = "a boolean (1/0, true/false, yes/no, or on/off)"


def environ(name: str) -> str | None:
    """The raw environment variable `name` (None when unset): the default source of every reader here."""
    return os.environ.get(name)


def nonblank(name: str) -> str | None:
    """The environment variable `name`, with a blank one counted as not set: a `getenv` for a flag whose blank value should mean its default."""
    return value if (value := os.environ.get(name)) is not None and value.strip() else None


def env_str(name: str, default: str | None = None, *, getenv: Getenv | None = None) -> str | None:
    """Setting `name`, stripped; `default` when it is unset or blank."""
    return ((getenv or environ)(name) or "").strip() or default


def env_flag(name: str, default: bool, error: type[Exception], *, getenv: Getenv | None = None, expects: str = BOOLEAN_WORDS) -> bool:
    """Setting `name` as a boolean (1/0, true/false, yes/no, on/off); `default` when unset, `error` (saying it must be `expects`) for anything else, a blank value included."""
    value = (getenv or environ)(name)
    if value is None:
        return default
    value = value.strip().lower()
    if value in TRUE_WORDS:
        return True
    if value in FALSE_WORDS:
        return False
    raise error(f"{name} must be {expects}, not {value!r}.")


def env_number(name: str, default: float, low: float, high: float, error: type[Exception], *, getenv: Getenv | None = None) -> float:
    """Setting `name` as a number in [low, high]; `default` when unset, `error` for anything else."""
    raw = env_str(name, getenv=getenv)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = float("nan")
    if not low <= value <= high:
        raise error(f"{name} must be a number from {low:g} to {high:g}, not {raw!r}.")
    return value


def env_int(name: str, default: int, low: int, high: int, error: type[Exception], *, getenv: Getenv | None = None) -> int:
    """Setting `name` as a whole number in [low, high]; `default` when unset, `error` for anything else (`600.9` and `1e3` included)."""
    raw = env_str(name, getenv=getenv)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = low - 1
    if not low <= value <= high:
        raise error(f"{name} must be a number from {low:g} to {high:g} with no decimals, not {raw!r}.")
    return value


def env_megabytes(name: str, default_mb: float, low: float, high: float, error: type[Exception], *, getenv: Getenv | None = None) -> int:
    """Setting `name` (a size in megabytes, default `default_mb`, allowed `low` to `high`) as a number of bytes."""
    return int(env_number(name, default_mb, low, high, error, getenv=getenv) * 2**20)


@dataclass(frozen=True)
class Capability:
    """Something a server does only when its owner turns it on (an environment flag): one rule for the tool that refuses and the tool that reports."""

    env_name: str
    what: str
    error: type[Exception]
    why: str = ""
    getenv: Getenv | None = None  # a server that layers a settings file behind the environment passes its own reader
    expects: str = BOOLEAN_WORDS  # what a bad value is told to be

    def enabled(self) -> bool:
        return env_flag(self.env_name, False, self.error, getenv=self.getenv, expects=self.expects)

    def refusal(self) -> str | None:
        """The sentence naming the setting to change, or None when it is on: the one wording the refusing tool and the status report share."""
        if self.enabled():
            return None
        reason = f" ({self.why})" if self.why else ""
        return f"{self.what} is off. Set {self.env_name}=1 on the MCP server to allow it{reason}."

    def require(self) -> None:
        """Raise the server's error, naming the setting to change, unless the capability is on."""
        if (sentence := self.refusal()) is not None:
            raise self.error(sentence)


@dataclass
class EnvFile:
    """A plain `NAME=value` settings file layered behind the environment, for a server that keeps secrets there.

    Only names starting with `prefix` are read, `real_env_only` names never from the file (where the file is cannot be set by the file), and a real,
    non-blank environment variable wins. The file is read by `load()` (once, when the server starts) into `values` and never into `os.environ`.
    """

    prefix: str
    path: Callable[[], Path]
    real_env_only: tuple[str, ...] = ()
    values: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._line = re.compile(rf"^(?:export\s+)?({re.escape(self.prefix)}[A-Z0-9_]+)\s*=\s*(.*)$")

    def get(self, name: str) -> str | None:
        """A setting: the real environment first, then the file. A blank value counts as not set. (A `Getenv`.)"""
        value = os.environ.get(name)
        return value if value is not None and value.strip() else self.values.get(name)

    def parse(self, text: str) -> dict[str, str]:
        """`NAME=value` lines (names with the prefix only). Quote a value to keep a `#` or spaces; unquoted values lose a trailing ` # comment`.

        In double quotes a backslash then a quote, a backslash or n stands for that quote, one backslash or a new line (any other backslash stays); single quotes are literal.
        Only whitespace or a `# comment` may follow the closing quote. Raises FrcMcpError (naming the line number, never the value) for a value
        whose quote is never closed or is followed by other text.
        """
        found: dict[str, str] = {}
        for number, line in enumerate(text.splitlines(), start=1):
            match = self._line.match(line.strip())
            if not match or match[1] in self.real_env_only:
                continue
            value = match[2].strip()
            if value[:1] in ("'", '"'):
                found[match[1]] = self._quoted(value, number)
            else:
                found[match[1]] = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        return found

    def _quoted(self, value: str, number: int) -> str:
        """The text inside the quotes of `value` (it starts with the quote); the line `number` is for the error message."""
        quote, out, i = value[0], [], 1
        while i < len(value):
            char = value[i]
            if quote == '"' and char == "\\" and value[i + 1:i + 2] in ('"', "\\", "n"):
                out.append("\n" if value[i + 1] == "n" else value[i + 1])
                i += 2
                continue
            if char == quote:
                rest = value[i + 1:]
                if rest.strip() and not rest.strip().startswith("#"):
                    raise FrcMcpError(f"{self.path().name}, line {number}: text follows the closing quote of a value. "
                                      "Only a space and a # comment may follow it. Put the whole value in quotes (in double quotes, write a quote inside it as backslash and quote).")
                return "".join(out)
            out.append(char)
            i += 1
        raise FrcMcpError(f"{self.path().name}, line {number}: a value starts with a quote that is never closed. "
                          "Close the quote, or remove it if the value does not need one.")

    def load(self) -> list[str]:
        """Read the file (a missing one is fine); returns the names whose value now comes from it, not from the environment."""
        self.values.clear()
        try:
            raw = self.path().read_bytes()
        except OSError:
            return []
        try:
            # PowerShell 5.1 `>` writes UTF-16 with a BOM; everything else is UTF-8, with or without a BOM. UTF-32 (its BOM starts like UTF-16's)
            # and text with NUL bytes (UTF-16 or UTF-32 without a BOM) would decode into nothing useful, so they are refused.
            utf16 = raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) and not raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE))
            if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)) or (not utf16 and b"\x00" in raw):
                raise UnicodeDecodeError("utf-8", b"", 0, 1, "not UTF-8 or UTF-16 text")
            text = raw.decode("utf-16") if utf16 else raw.decode("utf-8-sig")
        except UnicodeDecodeError as e:  # the message never holds the file's text (it may hold a password)
            raise FrcMcpError(f"{self.path().name} is not UTF-8 text (or UTF-16 with a byte-order mark; UTF-32 is not read). Save it as UTF-8 and try again.") from e
        self.values.update(self.parse(text))
        return [name for name in self.values if not (os.environ.get(name) or "").strip()]
