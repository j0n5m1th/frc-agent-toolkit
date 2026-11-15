"""Code every MCP server in this repository shares: the plumbing that sets a server up (`spec.py`: one `ServerSpec` per
server; `command.py`: the `frc-mcp` command; `server.py`, `toolset.py`, `tools.py`, `schema_trim.py`, `server_support.py`),
size-bounded replies (`reply.py`), settings (`settings.py`), allowed folders (`folders.py`) and the base error `FrcMcpError`.

Rule: nothing here may import another package of this repository, and the only error type it defines is the base
`FrcMcpError` (each server passes its own subclass, so its messages stay its own). `tests/common/test_layering.py` checks it.
"""
