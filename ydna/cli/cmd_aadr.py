"""ydna cli aadr — ancient hits / modern affinity atoms."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import ops
from .jsonio import CliError, emit_err, emit_ok


def add_parser(sub: argparse._SubParsersAction) -> None:
    g = sub.add_parser("aadr", help="AADR Y-hits by epoch (headless F6 atoms)")
    g_sub = g.add_subparsers(dest="aadr_command", required=True)

    h = g_sub.add_parser("hits", help="Our-branch Y hits grouped by epoch")
    h.add_argument("--genome", type=Path, default=None)
    h.add_argument("--epoch", action="append", default=[], help="Filter epoch key (repeatable)")
    h.add_argument("--limit", type=int, default=8)
    h.set_defaults(_cli_handler=run_hits)


def run_hits(args: argparse.Namespace) -> int:
    try:
        epochs = list(args.epoch) or None
        return emit_ok(ops.aadr_y_hits(
            genome=args.genome, epochs=epochs, limit_per_epoch=args.limit,
        ))
    except CliError as exc:
        return emit_err(exc.code, exc.message, exit_code=exc.exit_code)
    except Exception as exc:  # noqa: BLE001
        return emit_err("internal", str(exc))
