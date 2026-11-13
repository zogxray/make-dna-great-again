"""Open Store/DuckDB for CLI with the same singleton flock as the TUI."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from ..core.single import AlreadyRunning, acquire, lock_path, release
from ..core.store import Store
from .jsonio import EXIT_LOCKED, CliError


def lock_snapshot() -> dict:
    """Non-destructive peek: try brief exclusive lock."""
    path = lock_path()
    info = {"path": str(path), "held": False, "pid": None}
    try:
        acquire()
    except AlreadyRunning as exc:
        info["held"] = True
        info["pid"] = exc.pid
        return info
    release()
    return info


@contextmanager
def cli_session(*, take_lock: bool = True) -> Iterator[Store]:
    """Ensure DuckDB; hold app flock for the command unless take_lock=False."""
    if take_lock:
        try:
            acquire()
        except AlreadyRunning as exc:
            pid = f" (PID {exc.pid})" if exc.pid else ""
            raise CliError(
                "locked",
                f"App lock held{pid}. Close the TUI or wait.",
                exit_code=EXIT_LOCKED,
            ) from exc
    store = Store()
    try:
        store.ensure()
        yield store
    finally:
        try:
            from ..core import db
            db.close()
        except Exception:  # noqa: BLE001
            pass
        if take_lock:
            release()
