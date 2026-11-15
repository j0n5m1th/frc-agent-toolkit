"""A deterministic practice log for the evaluations and tests: a made-up 30 second match recorded to a `.wpilog` file.

The code lives in the log server package (`frc_mcp_wpilog.demo`, also `frc-mcp demo log`); this script is a thin wrapper that keeps the
plain-script imports (`import fake_log`) working. `frc_mcp_wpilog/demo.py` documents the match and every signal.

    uv run --project plugins/frc python evaluations/fake_log.py [--out practice.wpilog] [--seconds 30]
"""

from frc_mcp_wpilog.demo import DEFAULT_OUT, DEFAULT_SECONDS, SCHEMAS, build, main

if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DEFAULT_OUT", "DEFAULT_SECONDS", "SCHEMAS", "build", "main"]
