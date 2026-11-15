"""Functional tests for the MCP servers: run the installed commands over stdio and use their tools for real.

    uv run --project plugins/frc python functional_tests/cli.py [wpilog] [nt4] [sim] [vscode] [jfr] [systemcore] [ctre-tools] [ctre-owlet] [ctre-passerine] [ctre-caniv] [ctre-diag] [--keep]

With no server named, all run. Exit status is 0 only when every check passed. See README.md.
"""

import argparse
import asyncio
import sys

import ctre_caniv_server
import ctre_diag_server
import ctre_owlet_server
import ctre_passerine_server
import ctre_tools_server
import jfr_server
import nt4_server
import sim_server
import systemcore_server
import vscode_server
import wpilog_server

SERVERS = {"wpilog": wpilog_server, "nt4": nt4_server, "sim": sim_server, "vscode": vscode_server, "jfr": jfr_server,
           "systemcore": systemcore_server,
           "ctre-tools": ctre_tools_server, "ctre-owlet": ctre_owlet_server, "ctre-passerine": ctre_passerine_server, "ctre-caniv": ctre_caniv_server,
           "ctre-diag": ctre_diag_server}


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("servers", nargs="*", choices=list(SERVERS), help="which server(s) to test (default: all)")
    ap.add_argument("--target", choices=["auto", "fake", "real"], default="auto",
                    help="systemcore only: auto = a real Systemcore if one answers (read-only checks), else the fake one; fake; real")
    ap.add_argument("--host", help="systemcore: the real Systemcore's address (default: find it); ctre-diag: run READ-ONLY checks against this real controller instead of the fake server")
    ap.add_argument("--live", action="store_true", help="ctre-tools only: also use CTRE's real website (an index request and a few spaced downloads into a temporary folder)")
    ap.add_argument("--keep", action="store_true", help="keep the generated files in functional_tests/.output/")
    args = ap.parse_args()
    chosen = args.servers or list(SERVERS)
    extra = {"target": args.target, "host": args.host}
    runs = {"systemcore": lambda m: m.run(args.keep, **extra), "ctre-tools": lambda m: m.run(args.keep, live=args.live), "ctre-diag": lambda m: m.run(args.keep, host=args.host)}
    ok = all([await runs.get(name, lambda m: m.run(args.keep))(SERVERS[name]) for name in chosen])
    print("\nALL PASSED" if ok else "\nFAILURES (see FAIL lines above)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
