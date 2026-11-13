"""ydna cli y — paternal path / lore atoms."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import ops
from .jsonio import CliError, emit_err, emit_ok


def add_parser(sub: argparse._SubParsersAction) -> None:
    g = sub.add_parser("y", help="Y-DNA path / lore (headless F3 atoms)")
    g_sub = g.add_subparsers(dest="y_command", required=True)

    p = g_sub.add_parser("path", help="Our Y tip→root path")
    p.add_argument("--genome", type=Path, default=None)
    p.set_defaults(_cli_handler=run_path)

    l = g_sub.add_parser("lore", help="LORE brief for tip or clade")
    l.add_argument("clade", nargs="?", default=None)
    l.add_argument("--genome", type=Path, default=None)
    l.set_defaults(_cli_handler=run_lore)

    m = g_sub.add_parser("milestones", help="Subsampled path + optional lore")
    m.add_argument("--genome", type=Path, default=None)
    m.add_argument("--limit", type=int, default=12)
    m.add_argument("--no-lore", action="store_true")
    m.set_defaults(_cli_handler=run_milestones)


def run_path(args: argparse.Namespace) -> int:
    try:
        return emit_ok(ops.y_path(genome=args.genome))
    except CliError as exc:
        return emit_err(exc.code, exc.message, exit_code=exc.exit_code)
    except Exception as exc:  # noqa: BLE001
        return emit_err("internal", str(exc))


def run_lore(args: argparse.Namespace) -> int:
    try:
        return emit_ok(ops.y_lore(args.clade, genome=args.genome))
    except CliError as exc:
        return emit_err(exc.code, exc.message, exit_code=exc.exit_code)
    except Exception as exc:  # noqa: BLE001
        return emit_err("internal", str(exc))


def run_milestones(args: argparse.Namespace) -> int:
    try:
        return emit_ok(ops.y_milestones(
            genome=args.genome, limit=args.limit, with_lore=not args.no_lore,
        ))
    except CliError as exc:
        return emit_err(exc.code, exc.message, exit_code=exc.exit_code)
    except Exception as exc:  # noqa: BLE001
        return emit_err("internal", str(exc))
