"""Run the Textual TUI (singleton lock + teardown)."""

from __future__ import annotations

import sys
from pathlib import Path


def run_tui(
    *,
    folder: Path | None = None,
    genomes: list[Path] | None = None,
    window: bool = False,
) -> int:
    """Launch TUI, or spawn a desktop terminal window and return."""
    if window:
        passthrough: list[str] = []
        if folder is not None:
            passthrough += ["--input", str(folder)]
        for g in genomes or []:
            passthrough += ["--genome", str(g)]
        from .window_launch import launch_in_window
        return launch_in_window(passthrough)

    from ..core.single import AlreadyRunning, acquire, db_busy_message, is_db_busy_error
    try:
        acquire()
    except AlreadyRunning as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        from .app import YDnaApp
    except ImportError as exc:
        if (exc.name or "").split(".")[0] in ("textual", "rich"):
            print("Нужна библиотека Textual:  pip install textual", file=sys.stderr)
            return 1
        raise

    try:
        app = YDnaApp(folder, genomes or [])
    except Exception as exc:
        if is_db_busy_error(exc):
            print(db_busy_message(), file=sys.stderr)
            return 1
        raise

    try:
        app.run()
    finally:
        try:
            from ..core import db
            db.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            from ..core.single import release
            release()
        except Exception:  # noqa: BLE001
            pass
    return app.return_code or 0
