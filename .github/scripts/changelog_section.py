"""Print the CHANGELOG.md section for one version (used by the release workflow)."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def changelog_section(text: str, version: str) -> str:
    """Return the body under `## [version]`, up to the next `## [` heading. Raises ValueError if missing."""
    lines = text.splitlines()
    heading = re.compile(r"^## \[" + re.escape(version) + r"\](\s|$)")
    start = next((i for i, line in enumerate(lines) if heading.match(line)), None)
    if start is None:
        raise ValueError(f"no section for version {version}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## [")), len(lines))
    body = "\n".join(lines[start + 1 : end]).strip()
    if not body:
        raise ValueError(f"section for version {version} is empty")
    return body + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: changelog_section.py CHANGELOG.md VERSION", file=sys.stderr)
        return 2
    try:
        text = Path(argv[1]).read_text(encoding="utf-8")
        sys.stdout.write(changelog_section(text, argv[2]))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
