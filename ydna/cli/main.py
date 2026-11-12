"""CLI router. Subcommands land here; keep Textual out."""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ydna cli",
        description="Headless Make DNA Great Again (JSON/text). No TUI.",
    )
    sub = p.add_subparsers(dest="command")
    sub.add_parser("help", help="Show this help (default)")
    # Future: match-gwas, genome, ask — each in its own module under ydna.cli.
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv or argv[0] in ("-h", "--help", "help"):
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    if not args.command or args.command == "help":
        parser.print_help()
        return 0
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
