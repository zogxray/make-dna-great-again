"""ydna cli gwas — match / search / trait."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import ops
from .jsonio import CliError, emit_err, emit_ok


def add_parser(sub: argparse._SubParsersAction) -> None:
    g = sub.add_parser("gwas", help="GWAS Catalog commands")
    g_sub = g.add_subparsers(dest="gwas_command", required=True)

    p = g_sub.add_parser("match", help="Match genome rsIDs to local GWAS associations")
    p.add_argument("--genome", type=Path, default=None)
    p.add_argument("--gwas-dir", type=Path, default=None)
    p.add_argument("--p-max", type=float, default=5e-8)
    p.add_argument("--no-p-cut", action="store_true")
    p.add_argument("--no-cache", action="store_true")
    p.set_defaults(_cli_handler=run_match)

    s = g_sub.add_parser("search", help="Search matched traits (agent lead-finding)")
    s.add_argument("query", help="Free text: vitamin D, sleep, rsID, gene, …")
    s.add_argument("--genome", type=Path, default=None)
    s.add_argument("--gwas-dir", type=Path, default=None)
    s.add_argument("--p-max", type=float, default=5e-8)
    s.add_argument("--no-p-cut", action="store_true")
    s.add_argument("--no-cache", action="store_true")
    s.add_argument("--limit", type=int, default=40)
    s.set_defaults(_cli_handler=run_search)

    t = g_sub.add_parser("trait", help="Association rows for one trait name")
    t.add_argument("trait", help="Exact DISEASE/TRAIT string (or unique substring)")
    t.add_argument("--genome", type=Path, default=None)
    t.add_argument("--gwas-dir", type=Path, default=None)
    t.add_argument("--p-max", type=float, default=5e-8)
    t.add_argument("--no-p-cut", action="store_true")
    t.add_argument("--no-cache", action="store_true")
    t.add_argument("--limit", type=int, default=80)
    t.set_defaults(_cli_handler=run_trait)


def _pmax(args: argparse.Namespace) -> float | None:
    return None if args.no_p_cut or args.p_max == 0 else float(args.p_max)


def run_match(args: argparse.Namespace) -> int:
    try:
        return emit_ok(ops.gwas_match(
            genome=args.genome,
            gwas_dir=args.gwas_dir,
            p_max=_pmax(args),
            use_cache=not args.no_cache,
        ))
    except CliError as exc:
        return emit_err(exc.code, exc.message, exit_code=exc.exit_code)
    except Exception as exc:  # noqa: BLE001
        return emit_err("internal", str(exc))


def run_search(args: argparse.Namespace) -> int:
    try:
        return emit_ok(ops.gwas_search(
            args.query,
            genome=args.genome,
            gwas_dir=args.gwas_dir,
            p_max=_pmax(args),
            use_cache=not args.no_cache,
            limit=args.limit,
        ))
    except CliError as exc:
        return emit_err(exc.code, exc.message, exit_code=exc.exit_code)
    except Exception as exc:  # noqa: BLE001
        return emit_err("internal", str(exc))


def run_trait(args: argparse.Namespace) -> int:
    try:
        return emit_ok(ops.gwas_trait(
            args.trait,
            genome=args.genome,
            gwas_dir=args.gwas_dir,
            p_max=_pmax(args),
            use_cache=not args.no_cache,
            limit=args.limit,
        ))
    except CliError as exc:
        return emit_err(exc.code, exc.message, exit_code=exc.exit_code)
    except Exception as exc:  # noqa: BLE001
        return emit_err("internal", str(exc))
