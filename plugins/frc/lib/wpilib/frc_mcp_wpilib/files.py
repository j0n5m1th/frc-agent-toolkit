"""Small file helpers every server shares."""

import os
from pathlib import Path


def write_atomic(path: Path, data: str | bytes) -> None:
    """Write `data` through a temporary sibling and replace `path` with it, so a reader never sees a half-written file.

    Text is written as UTF-8. Raises OSError (the caller decides how to word it); the temporary file is removed on failure.
    """
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        if isinstance(data, str):
            temporary.write_text(data, encoding="utf-8")
        else:
            temporary.write_bytes(data)
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
