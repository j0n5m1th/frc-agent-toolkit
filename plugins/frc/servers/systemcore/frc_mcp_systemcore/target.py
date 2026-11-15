"""Which controller a tool talks to."""

from . import config, net, state
from .errors import SystemcoreError


async def resolve(host: str | None) -> str:
    """The checked host: the tool's argument, else SYSTEMCORE_HOST, else the last controller found."""
    chosen = host or config.host() or state.last_host()
    if not chosen:
        raise SystemcoreError("No Systemcore is known yet. Run systemcore_discover, or pass host (for example 172.30.0.1 on its Wi-Fi).")
    return await net.check_host(chosen)
