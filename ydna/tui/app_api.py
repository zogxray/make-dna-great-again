"""ModeApp contract helpers for ``--ui wgpu`` screens.

Modal APIs (``ask_form``, ``ask_prompt``, ``ask_open_file``, ``ask_confirm``,
``show_overlay``, ``show_doc``, ``ask_pick``, ``ask_text``) are required on the
host app — not optional ``hasattr`` probes that hide a missing ``screen.app``.
"""

from __future__ import annotations

from typing import Any


def require_app(screen: Any, *, what: str) -> bool:
    """True if ``screen.app`` is set. Otherwise notify once and return False."""
    app = getattr(screen, "app", None)
    if app is not None:
        return True
    notify = getattr(screen, "_notify", None)
    if callable(notify):
        notify(f"No app · {what}")
    return False


def focus_next(screen: Any) -> None:
    """Cycle focus via ModeApp — required API, not optional ``hasattr``."""
    if not require_app(screen, what="focus next"):
        return
    screen.app.focus_next()
