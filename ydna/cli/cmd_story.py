"""ydna cli story — human Markdown Story from the latest Oracle in a chat."""

from __future__ import annotations

import argparse

from . import ops
from .jsonio import CliError, emit_err, emit_ok


def add_parser(sub: argparse._SubParsersAction) -> None:
    g = sub.add_parser(
        "story",
        help="Human Story Markdown from the latest Oracle in a chat thread",
    )
    g.add_argument("--thread", default=None, help="Chat thread id (default: newest)")
    g.add_argument("--lang", default=None, help="Language hint (e.g. Russian)")
    g.set_defaults(_cli_handler=run)


def run(args: argparse.Namespace) -> int:
    try:
        return emit_ok(ops.story(thread_id=args.thread, language=args.lang))
    except CliError as exc:
        return emit_err(exc.code, exc.message, exit_code=exc.exit_code)
    except Exception as exc:  # noqa: BLE001
        return emit_err("internal", str(exc))
