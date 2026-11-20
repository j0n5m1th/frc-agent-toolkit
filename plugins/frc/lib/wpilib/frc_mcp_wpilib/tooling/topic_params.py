"""The parameter types the topic tools (NT4, wpilog) share; generic server plumbing is `frc_mcp_core.server_support`."""

from typing import Annotated

from pydantic import Field

from ..reduce.models import MAX_ALSO, Condition
from ..reduce.types import Reduce

# Shared parameter types (token-lean: every word is paid in every session). One word means one thing across servers: `topics` (list)
# or `topic` (one), `capture_id` (NT4), `log` (wpilog id), `max_<things>` (result cap), `limit` with `offset` (page). Never a bare `id`.
Fields = Annotated[list[str] | None, Field(description="Struct fields to keep, e.g. ['translation.x'] or ['x']")]
Topics = Annotated[list[str], Field(min_length=1, max_length=50, description="Exact topic names, e.g. ['/DriveState/Pose']")]
Depth = Annotated[int | None, Field(ge=1, le=10, description="Path segments after prefix to show; deeper topics collapse into 'path/': count (1 = children)")]
NameContains = Annotated[str | None, Field(description="Case-insensitive substring; a|b matches either (a name, or a line of text)")]
StructFields = Annotated[bool, Field(description="List each struct/protobuf type's field names once, under struct_fields")]
PageLimit = Annotated[int, Field(ge=1, le=500, description="Max groups + topics per page")]
PageOffset = Annotated[int, Field(ge=0, description="Offset from next_offset")]
ReduceParam = Annotated[Reduce, Field(description="How to reduce each topic")]
Threshold = Annotated[float | None, Field(description="Level for edges (booleans default to 0.5); one topic per call if levels differ")]
MaxSamples = Annotated[int, Field(ge=1, le=500, description="Cap for list results")]
MaxTopics = Annotated[int, Field(ge=1, le=200, description="Cap on topics returned; the rest are counted in omitted_topics")]
# The two row caps every listing tool repeats: a small page of entries, or one section/target's worth of rows.
# Each tool picks its own default (20 for a page, 50 for a section); only the bounds live here.
Limit = Annotated[int, Field(ge=1, le=100, description="Max entries")]
# Windows, conditions and listing filters both servers repeat. `since_ms`/`until_ms` say only "ms": each server's time zero is explained once,
# where it is chosen (wpilog's `align`; NT4's capture start or, with `rel_trigger`, the trigger).
SinceMs = Annotated[float | None, Field(description="Window start (ms)")]
UntilMs = Annotated[float | None, Field(description="Window end (ms)")]
StructField = Annotated[str | None, Field(description="Struct field, e.g. 'vx'")]
Also = Annotated[list[Condition] | None, Field(max_length=MAX_ALSO, description="More conditions that must hold at the same time, e.g. [{topic:'/Robot/Mode',op:'==',value:'auto'}]")]
Prefix = Annotated[str, Field(description="Names starting with this, e.g. '/Tunables/'")]
IncludeHidden = Annotated[bool, Field(description="Show internals (/.schema/, '.'-prefixed); otherwise only counted in hidden_matches")]
# Not shared on purpose: NT4's `prefix` also takes '$' (server meta-topics) and its `type_str` has no examples (wpilog's lists struct: forms);
# `topic`/`op`/`value` differ (NT4 has `exists`); `log` is a bool on nt4_capture_start and a log id on wpilog tools; `topics` means a subset,
# exact names or prefixes depending on the tool. tests/common/test_shared_params.py lists each exception.
MaxRows = Annotated[int, Field(ge=1, le=200, description="Max rows kept per section or target (counts stay the full ones)")]

__all__ = [
    "Also",
    "Depth",
    "Fields",
    "IncludeHidden",
    "Limit",
    "MaxRows",
    "MaxSamples",
    "MaxTopics",
    "NameContains",
    "PageLimit",
    "PageOffset",
    "Prefix",
    "ReduceParam",
    "SinceMs",
    "StructField",
    "StructFields",
    "Threshold",
    "Topics",
    "UntilMs",
]
