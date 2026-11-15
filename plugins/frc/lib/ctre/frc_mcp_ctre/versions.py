"""Order CTRE version strings: `1.0.1.1`, `26.3.0`, `26.70.0-alpha-2`."""

import re
from dataclasses import dataclass

from .errors import CtreError

_VERSION = re.compile(r"^(\d+(?:\.\d+){1,3})(?:-([0-9A-Za-z.\-]+))?$")


@dataclass(frozen=True)
class Version:
    text: str
    numbers: tuple[int, ...]
    pre: str  # "" for a release, else what follows the first "-" (e.g. "alpha-2")

    @property
    def prerelease(self) -> bool:
        return bool(self.pre)

    @property
    def season(self) -> int | None:
        """The FRC season a `YY.x.y` version belongs to (26.1.3 -> 2026); None for old schemes such as 1.0.1.1."""
        return 2000 + self.numbers[0] if 20 <= self.numbers[0] <= 99 else None

    def sort_key(self) -> tuple[tuple[int, ...], int, tuple[tuple[int, str], ...]]:
        """Numbers first; a release sorts after its own pre-releases; pre-release parts compare as numbers when they are numbers."""
        parts = tuple((0, f"{int(p):09d}") if p.isdigit() else (1, p) for p in re.split(r"[.\-]", self.pre) if p)
        return (self.numbers + (0,) * (4 - len(self.numbers)), 0 if self.pre else 1, parts)

    def __lt__(self, other: "Version") -> bool:
        return self.sort_key() < other.sort_key()


def parse_version(text: str) -> Version:
    match = _VERSION.match(text.strip())
    if not match:
        raise CtreError(f"{text!r} is not a CTRE version (examples: 26.3.0, 26.70.0-alpha-2).")
    return Version(text.strip(), tuple(int(n) for n in match.group(1).split(".")), match.group(2) or "")


def try_parse_version(text: str) -> Version | None:
    try:
        return parse_version(text)
    except CtreError:
        return None
