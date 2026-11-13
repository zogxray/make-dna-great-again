"""ydna cli pgs search."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import ops
from .jsonio import CliError, emit_err, emit_ok


def add_parser(sub: argparse._SubParsersAction) -> None:
    g = sub.add_parser("pgs", help="PGS catalog commands")
    g_sub = g.add_subparsers(dest="pgs_command", required=True)
    s = g_sub.add_parser("search", help="Search local PGS models by trait/EFO/id")
    s.add_argument("query")
    s.add_argument("--genome", type=Path, default=None, help="Attach scored? flags for this kit")
    s.add_argument("--limit", type=int, default=40)
    s.set_defaults(_cli_handler=run_search)


def run_search(args: argparse.Namespace) -> int:
    try:
        return emit_ok(ops.pgs_search(
            args.query, genome=args.genome, limit=args.limit,
        ))
    except CliError as exc:
        return emit_err(exc.code, exc.message, exit_code=exc.exit_code)
    except Exception as exc:  # noqa: BLE001
        return emit_err("internal", str(exc))
