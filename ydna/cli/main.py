"""CLI router. Subcommands land here; keep Textual out."""

from __future__ import annotations

import argparse
import sys

from . import (
    cmd_aadr, cmd_genome, cmd_gwas, cmd_investigate, cmd_kg, cmd_pgs,
    cmd_profile, cmd_status, cmd_story, cmd_y,
)
from .jsonio import EXIT_USAGE, emit_err


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ydna cli",
        description="Headless Make DNA Great Again (JSON stdout). No TUI.",
    )
    sub = p.add_subparsers(dest="command")
    sub.add_parser("help", help="Show this help")
    cmd_status.add_parser(sub)
    cmd_genome.add_parser(sub)
    cmd_gwas.add_parser(sub)
    cmd_pgs.add_parser(sub)
    cmd_profile.add_parser(sub)
    cmd_investigate.add_parser(sub)
    cmd_story.add_parser(sub)
    cmd_y.add_parser(sub)
    cmd_aadr.add_parser(sub)
    cmd_kg.add_parser(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv or argv[0] in ("-h", "--help", "help"):
        parser.print_help()
        return 0
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else EXIT_USAGE
        return code or 0

    handler = getattr(args, "_cli_handler", None)
    if handler is None:
        if not getattr(args, "command", None) or args.command == "help":
            parser.print_help()
            return 0
        return emit_err("usage", f"unknown command {args.command!r}", exit_code=EXIT_USAGE)
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
