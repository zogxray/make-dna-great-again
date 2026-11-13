"""ydna cli investigate — Flash plan → local facts → Argon Oracle."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import ops
from .jsonio import CliError, emit_err, emit_ok


def add_parser(sub: argparse._SubParsersAction) -> None:
    g = sub.add_parser(
        "investigate",
        help="Dual-tier brief: fast plan → GWAS/PGS facts → reasoning Oracle",
    )
    g.add_argument("question", help="Natural-language investigation question")
    g.add_argument("--genome", type=Path, default=None)
    g.add_argument("--gwas-dir", type=Path, default=None)
    g.add_argument("--p-max", type=float, default=5e-8)
    g.add_argument("--no-p-cut", action="store_true")
    g.add_argument("--no-cache", action="store_true")
    g.add_argument(
        "--facts-only", action="store_true",
        help="Skip LLMs: use heuristic plan + local gather only",
    )
    g.add_argument(
        "--no-story", action="store_true",
        help="Skip human Story pass after Oracle",
    )
    g.add_argument(
        "--lang", default=None,
        help="Story language hint (e.g. Russian, English)",
    )
    g.set_defaults(_cli_handler=run)


def run(args: argparse.Namespace) -> int:
    try:
        p_max = None if args.no_p_cut or args.p_max == 0 else float(args.p_max)
        return emit_ok(ops.investigate(
            args.question,
            genome=args.genome,
            gwas_dir=args.gwas_dir,
            p_max=p_max,
            use_cache=not args.no_cache,
            facts_only=bool(args.facts_only),
            with_story=not bool(args.no_story),
            story_language=args.lang,
        ))
    except CliError as exc:
        return emit_err(exc.code, exc.message, exit_code=exc.exit_code)
    except Exception as exc:  # noqa: BLE001
        return emit_err("internal", str(exc))
