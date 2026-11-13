"""Stable JSON envelope + exit codes for the headless CLI."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


# 0 ok · 1 error · 2 usage · 3 locked/busy · 4 missing inputs/data
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_LOCKED = 3
EXIT_MISSING = 4


class CliError(Exception):
    def __init__(self, code: str, message: str, *, exit_code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


def _default(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(f"not JSON-serializable: {type(obj)!r}")


def emit_ok(data: Any, *, file=None) -> int:
    payload = {"ok": True, "data": data}
    print(json.dumps(payload, ensure_ascii=False, default=_default), file=file or sys.stdout)
    return EXIT_OK


def emit_err(code: str, message: str, *, exit_code: int = EXIT_ERROR, file=None) -> int:
    payload = {"ok": False, "error": {"code": code, "message": message}}
    print(json.dumps(payload, ensure_ascii=False), file=file or sys.stdout)
    return exit_code
