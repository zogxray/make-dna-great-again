"""Запуск:  python -m ydna [--input папка_с_json]   или   ydna [--input ...]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Make DNA Great Again: local TUI for your genotype.",
    )
    parser.add_argument("--input", type=Path, default=None,
                        help="Папка с JSON-файлами Discover (по умолчанию — путь из режима Data, "
                             "иначе data/ftdna/discover)")
    parser.add_argument("--genome", action="append", default=[], type=Path, metavar="FILE",
                        help="Файл генотипирования для режима Genome (можно несколько раз)")
    args = parser.parse_args(argv)

    from .core.single import AlreadyRunning, acquire, db_busy_message, is_db_busy_error
    try:
        acquire()
    except AlreadyRunning as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        from .app import YDnaApp
    except ImportError as exc:
        if (exc.name or "").split(".")[0] in ("textual", "rich"):
            sys.exit("Нужна библиотека Textual:  pip install textual")
        raise
    try:
        app = YDnaApp(args.input, args.genome)
    except Exception as exc:
        if is_db_busy_error(exc):
            print(db_busy_message(), file=sys.stderr)
            return 1
        raise
    app.run()
    return app.return_code or 0


if __name__ == "__main__":
    raise SystemExit(main())
