"""CLI entry point: `python -m pipeline run --site <site_id>` (Phase 1 DoD, `CLAUDE.md` §9)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from pipeline.core.exceptions import PipelineError
from pipeline.orchestrator.run import run_site


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments, run the requested command, and return a process exit code."""
    parser = argparse.ArgumentParser(prog="python -m pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run one site's scraper end to end.")
    run_parser.add_argument("--site", required=True, help="Site id, as registered in sites.yaml.")

    args = parser.parse_args(argv)

    try:
        result = asyncio.run(run_site(args.site))
    except PipelineError as exc:
        print(f"run failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"run {result.run_id} ({result.site_id}): {result.record_count} records, "
        f"{result.quarantined_count} quarantined, gate={result.gate_status}, "
        f"fetches: {result.http_only_fetch_count} http-only / "
        f"{result.escalated_fetch_count} escalated to browser"
    )
    return 0 if result.gate_status != "fail" else 1


if __name__ == "__main__":
    sys.exit(main())
