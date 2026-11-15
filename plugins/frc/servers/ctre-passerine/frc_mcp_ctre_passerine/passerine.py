"""Running passerine: MIDI in, CHRP out.
Command line (seen from `passerine --help` on 1.0.0): `passerine --input-midi <file> --output-chrp <file>`. Seen on real runs: it
silently overwrites an existing output, does not create missing folders, accepts any output name, prints `Output has been written
to <file>` on success, and on failure prints `Error: ...` and exits 1 (`Error: invalid midi: not a midi file`). So this module
refuses to overwrite, checks the file really is MIDI first, and checks that the output exists afterwards.
"""

from pathlib import Path

from frc_mcp_ctre.errors import CtreError
from frc_mcp_ctre.resolve import newest_installed_async
from frc_mcp_ctre.runner import run_tool

from . import config

MIDI_MAGIC = b"MThd"  # every standard MIDI file starts with this header chunk


def check_midi(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            head = handle.read(len(MIDI_MAGIC))
    except OSError as e:
        raise CtreError(f"{path.name} could not be read: {e}") from e
    if head != MIDI_MAGIC:
        raise CtreError(f"{path.name} is not a MIDI file (a MIDI file starts with 'MThd'). Check it was exported as .mid, not renamed from another format.")


def _reason(output: str) -> str:
    text = output.strip().removeprefix("Error:").strip() or "no message"
    return "the file is not valid MIDI" if "invalid midi" in text.lower() else text[:300]


async def convert(midi: Path, target: Path) -> dict[str, object]:
    """Convert `midi` to `target` (a new file) with the newest installed passerine, and describe the result."""
    check_midi(midi)
    exe = await newest_installed_async("passerine")
    try:
        result = await run_tool(exe.path, ["--input-midi", str(midi), "--output-chrp", str(target)], config.timeout_s())
    except CtreError:  # timed out or could not start: nothing half-written stays
        target.unlink(missing_ok=True)
        raise
    if result.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise CtreError(f"passerine {exe.version} failed (exit {result.returncode}): {_reason(result.output)}.")
    return {"file": target.name, "kb": round(target.stat().st_size / 1024, 1), "passerine": exe.version}
