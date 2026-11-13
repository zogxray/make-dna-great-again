"""ydna cli status — version, paths, lock, sources, active genome."""

from __future__ import annotations

import argparse

from . import ops
from .jsonio import CliError, emit_err, emit_ok


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("status", help="App status: version, lock, sources, active genome")
    p.set_defaults(_cli_handler=run)


def run(args: argparse.Namespace) -> int:
    try:
        return emit_ok(ops.status())
    except CliError as exc:
        return emit_err(exc.code, exc.message, exit_code=exc.exit_code)
    except Exception as exc:  # noqa: BLE001
        return emit_err("internal", str(exc))
