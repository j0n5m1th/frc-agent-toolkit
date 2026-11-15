"""Find the installed copy of a CTRE tool that a server should run, or say exactly how to get one."""

import asyncio

from . import catalog
from .errors import CtreError
from .fetch import load_index
from .platform import platform_key
from .store import Installed, scan


def missing_message(tool: str) -> str:
    """Why there is nothing to run for `tool` here, and what to do about it (the one wording every server uses)."""
    platform = platform_key()
    if note := catalog.platform_note(tool, platform):
        return f"{tool} cannot be used on this computer ({platform}). {note}"
    return f"No {tool} is installed. Install it with ctre_install tool={tool} (ctre-tools server, needs CTRE_ALLOW_INSTALL=1)."


def refused_message(tool: str, refused: list[Installed]) -> str:
    """Why copies of `tool` that were found are not run (each one's own reason says what to do)."""
    return f"Found {tool} but it is not verified: " + " ".join(f"{i.version} ({i.source}, {i.path}) {i.problem}" for i in refused[:2])


def newest_installed(tool: str) -> Installed:
    """The newest installed version of `tool`, from any source this package reads. For tools with no compliancy pairing (caniv, passerine)."""
    index = load_index(offline=True).index  # never the network: a server that runs a tool does not also go looking for new ones
    name = index.tool_name(tool)
    found = scan([name], index)[name]
    have = [i for i in found if i.verified]  # a copy that is not verified (changed, or never CTRE's) is never run
    if not have:
        raise CtreError(refused_message(name, found) if found else missing_message(name))
    return have[0]  # newest first


async def newest_installed_async(tool: str) -> Installed:
    return await asyncio.to_thread(newest_installed, tool)
