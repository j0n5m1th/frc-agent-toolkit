"""The one unwrap of a tool reply, shared by the pytest helpers and the functional-test harness.

Both entry points assert on the same envelope and raise the same errors from it, so a reply that is
malformed fails identically whichever runner checked it.
"""

import json
from typing import Any

from mcp.types import CallToolResult, TextContent


def j(result: CallToolResult) -> Any:
    """Tools return one compact-JSON text block (no structuredContent duplicate); unwrap that block."""
    assert len(result.content) == 1 and result.structured_content is None
    block = result.content[0]
    assert isinstance(block, TextContent), f"unexpected reply block {type(block).__name__}"
    return json.loads(block.text)
