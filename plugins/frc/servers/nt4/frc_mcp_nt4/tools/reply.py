"""The NT4 server's reply entry for one read result; the size-bounded reply machinery is shared (`frc_mcp_core.reply`).

Reply keys are short because they repeat for every topic: `v` value, `t_ms` time in ms, `ts_us` the server's timestamp in
microseconds, `n` samples in a reduced section, `err` the in-band error text.
"""

from dataclasses import dataclass
from typing import Any

from frc_mcp_core.reply import topic_err
from frc_mcp_wpilib.values import type_field

from ..access.reading import Observed, ReadMiss


@dataclass(frozen=True)
class ReplySpec:
    """The optional knobs `reply_entry` applies to a value entry: `fields` (struct fields to decode), `age` (the
    `(now_us, synchronized)` pair behind `last_update`), `timestamps` (emit `ts_us`), `unit` (the topic's unit label)."""

    fields: list[str] | None = None
    age: tuple[int | None, bool] | None = None
    timestamps: bool = False
    unit: str | None = None


NO_OPTIONS = ReplySpec()

CAPTURE_BULK_KEYS = ("data", "constants", "evidence")  # capture-result sections kept topic by topic when a reply is trimmed (both capture tool modules read them from here)


def reply_entry(res: Observed | ReadMiss, spec: ReplySpec = NO_OPTIONS) -> dict[str, Any]:
    """The one reply entry for a read result. A miss is the shared `err` shape; a value is its `type` and shaped `v`, plus
    optional `unit`, `ts_us` (when `spec.timestamps`) and `last_update` (from `spec.age`, the `(now_us, synchronized)` pair).
    """
    if isinstance(res, ReadMiss):
        return topic_err(res.hint)
    out: dict[str, Any] = {**type_field(res.type_string), "v": res.shaped(spec.fields)}
    if spec.unit:
        out["unit"] = spec.unit
    if spec.timestamps:
        out["ts_us"] = res.reported_server_time_us
    if spec.age is not None:
        out["last_update"] = res.age(*spec.age)
    return out
