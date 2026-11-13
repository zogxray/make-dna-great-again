"""ydna cli genome summary — active or --genome file."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import ops
from .jsonio import CliError, emit_err, emit_ok


def add_parser(sub: argparse._SubParsersAction) -> None:
    g = sub.add_parser("genome", help="Genome commands")
    g_sub = g.add_subparsers(dest="genome_command", required=True)
    p = g_sub.add_parser("summary", help="Summary of active or given genome file")
    p.add_argument("--genome", type=Path, default=None, help="Kit path (default: F1 active)")
    p.add_argument(
        "--ingest", action="store_true",
        help="Full ingest if not in DuckDB (default: meta-only when possible)",
    )
    p.set_defaults(_cli_handler=run_summary)

    r = g_sub.add_parser("rs", help="Look up genotypes for rsIDs on the kit")
    r.add_argument("rsids", nargs="+", help="rsIDs (rs123 or 123)")
    r.add_argument("--genome", type=Path, default=None)
    r.set_defaults(_cli_handler=run_rs)


def run_summary(args: argparse.Namespace) -> int:
    try:
        return emit_ok(ops.genome_summary(genome=args.genome, ingest=bool(args.ingest)))
    except CliError as exc:
        return emit_err(exc.code, exc.message, exit_code=exc.exit_code)
    except Exception as exc:  # noqa: BLE001
        return emit_err("internal", str(exc))


def run_rs(args: argparse.Namespace) -> int:
    try:
        return emit_ok(ops.genome_rs(list(args.rsids), genome=args.genome))
    except CliError as exc:
        return emit_err(exc.code, exc.message, exit_code=exc.exit_code)
    except Exception as exc:  # noqa: BLE001
        return emit_err("internal", str(exc))
