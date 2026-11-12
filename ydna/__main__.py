"""Entry dispatcher: TUI by default; ``cli`` subcommand → headless.

Textual is imported only on the TUI path.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# First-token commands routed away from the TUI.
_CLI_TOKENS = frozenset({"cli"})


def _load_dotenv() -> None:
    try:
        from .core.llm import load_dotenv
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass


def _tui_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ydna",
        description="Make DNA Great Again: local TUI for your genotype.",
    )
    parser.add_argument(
        "--window", action="store_true",
        help="Open in a separate terminal window (desktop), then exit this process",
    )
    parser.add_argument(
        "--input", type=Path, default=None,
        help="Папка с JSON-файлами Discover (по умолчанию — путь из режима Data, "
             "иначе data/ftdna/discover)",
    )
    parser.add_argument(
        "--genome", action="append", default=[], type=Path, metavar="FILE",
        help="Файл генотипирования для режима Genome (можно несколько раз)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] in _CLI_TOKENS:
        from .cli.main import main as cli_main
        # ``ydna cli …`` → pass the rest; bare ``ydna cli`` → help inside cli.
        return cli_main(argv[1:])

    # ``python -m ydna.cli`` is separate; here only the ``ydna cli`` spelling.

    args = _tui_parser().parse_args(argv)
    from .tui.run import run_tui
    return run_tui(folder=args.input, genomes=args.genome, window=args.window)


if __name__ == "__main__":
    raise SystemExit(main())
