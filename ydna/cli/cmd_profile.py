"""ydna cli profile micronutrients — GWAS∩pack (+ PGS when present)."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import ops
from .jsonio import CliError, emit_err, emit_ok


def add_parser(sub: argparse._SubParsersAction) -> None:
    g = sub.add_parser("profile", help="Composite profiles for agents")
    g_sub = g.add_subparsers(dest="profile_command", required=True)
    p = g_sub.add_parser(
        "micronutrients",
        help="Vitamins/minerals: GWAS hits ∩ curated pack (+ PGS if usable)",
    )
    p.add_argument("--genome", type=Path, default=None)
    p.add_argument("--gwas-dir", type=Path, default=None)
    p.add_argument("--p-max", type=float, default=5e-8)
    p.add_argument("--no-p-cut", action="store_true")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--age", type=int, default=None, help="Optional age (context only)")
    p.add_argument(
        "--lab", action="append", default=[], metavar="NAME=VALUE",
        help="Optional lab, e.g. --lab vitamin_d=28 (repeatable)",
    )
    p.set_defaults(_cli_handler=run_micronutrients)


def _parse_labs(items: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for raw in items or []:
        if "=" not in raw:
            raise CliError("usage", f"bad --lab {raw!r}, expected NAME=VALUE", exit_code=2)
        k, v = raw.split("=", 1)
        out[k.strip()] = float(v.strip())
    return out


def run_micronutrients(args: argparse.Namespace) -> int:
    try:
        p_max = None if args.no_p_cut or args.p_max == 0 else float(args.p_max)
        labs = _parse_labs(args.lab)
        return emit_ok(ops.profile_micronutrients(
            genome=args.genome,
            gwas_dir=args.gwas_dir,
            p_max=p_max,
            use_cache=not args.no_cache,
            age=args.age,
            labs=labs or None,
        ))
    except CliError as exc:
        return emit_err(exc.code, exc.message, exit_code=exc.exit_code)
    except Exception as exc:  # noqa: BLE001
        return emit_err("internal", str(exc))
