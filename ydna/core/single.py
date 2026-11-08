"""Один процесс приложения: flock + PID до открытия DuckDB.

Пользователь не видит lock/DuckDB — только «already running» или старт.
После kill -9 ОС отпускает flock сама; stale PID в файле не блокирует.
"""

from __future__ import annotations

import atexit
import fcntl
import os
from pathlib import Path

_lock_fh = None  # держим открытым, пока жив процесс


class AlreadyRunning(Exception):
    """Другой инстанс держит singleton."""

    def __init__(self, pid: int | None) -> None:
        from .store import APP_TITLE

        self.pid = pid
        if pid:
            msg = (
                f"{APP_TITLE} is already running (PID {pid}).\n"
                f"Close that window, or:  kill {pid}"
            )
        else:
            msg = (
                f"{APP_TITLE} is already running.\n"
                "Close the other window, or kill the leftover process."
            )
        super().__init__(msg)


def lock_path() -> Path:
    from .store import config_dir
    return config_dir() / "ydna.lock"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_pid(fh) -> int | None:
    try:
        fh.seek(0)
        raw = (fh.read() or "").strip().split()
        return int(raw[0]) if raw else None
    except (ValueError, OSError):
        return None


def acquire() -> None:
    """Взять exclusive flock. При занятости — AlreadyRunning (не трогаем DuckDB)."""
    global _lock_fh
    if _lock_fh is not None:
        return
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+", encoding="utf-8")  # noqa: SIM115 — держим до exit
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        other = _read_pid(fh)
        try:
            fh.close()
        except OSError:
            pass
        if other is not None and not _alive(other):
            # Файл устарел, но flock чужой — всё равно кто-то живой без нашего PID.
            other = None
        raise AlreadyRunning(other) from None
    fh.seek(0)
    fh.truncate()
    fh.write(f"{os.getpid()} ydna\n")
    fh.flush()
    try:
        os.fsync(fh.fileno())
    except OSError:
        pass
    _lock_fh = fh
    atexit.register(release)


def release() -> None:
    global _lock_fh
    fh = _lock_fh
    _lock_fh = None
    if fh is None:
        return
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        fh.close()
    except OSError:
        pass


def is_db_busy_error(exc: BaseException) -> bool:
    """DuckDB single-writer / lock — на случай обхода singleton."""
    text = str(exc).lower()
    return "conflicting lock" in text or "could not set lock" in text


def db_busy_message() -> str:
    from .store import APP_TITLE

    return (
        f"{APP_TITLE} cannot open its data store — another copy may still be running.\n"
        "Close the other window, or:  pkill -f ydna_tui ; pkill -f 'python -m ydna'"
    )
