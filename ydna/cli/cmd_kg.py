"""ydna cli kg — modern affinity from cached f3."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import ops
from .jsonio import CliError, emit_err, emit_ok


def add_parser(sub: argparse._SubParsersAction) -> None:
    g = sub.add_parser("kg", help="1000G / modern affinity (cached f3)")
    g_sub = g.add_subparsers(dest="kg_command", required=True)
    a = g_sub.add_parser("affinity", help="Top cached AADR+1000G f3 ranks")
    a.add_argument("--genome", type=Path, default=None)
    a.add_argument("--limit", type=int, default=15)
    a.set_defaults(_cli_handler=run_affinity)


def run_affinity(args: argparse.Namespace) -> int:
    try:
        return emit_ok(ops.modern_affinity(genome=args.genome, limit=args.limit))
    except CliError as exc:
        return emit_err(exc.code, exc.message, exit_code=exc.exit_code)
    except Exception as exc:  # noqa: BLE001
        return emit_err("internal", str(exc))
