"""ModeApp contract helpers for ``--ui wgpu`` screens.

Modal APIs (``ask_form``, ``ask_prompt``, ``ask_open_file``, ``ask_confirm``,
``show_overlay``, ``show_doc``, ``ask_pick``, ``ask_text``) are required on the
host app — not optional ``hasattr`` probes that hide a missing ``screen.app``.
"""

from __future__ import annotations

from typing import Any


def _hex_rgba(hex_color: str) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 6:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
    return (184, 77, 86, 255)


def active_jobs_count() -> int:
    from ..core.jobs import JOBS

    return sum(1 for j in JOBS.items.values() if j.active or j.status == "queued")


def mode_bar_item(
    index: int,
    key: str,
    title: str,
) -> tuple[str, tuple[int, int, int, int] | None, tuple[int, int, int, int] | None]:
    """Highlight ``F8 Jobs(X)`` in harmony crimson when jobs are active/queued."""
    if key == "jobs":
        n = active_jobs_count()
        if n > 0:
            from ..theme.styles import RED

            return f" F{index + 1} {title}({n}) ", _hex_rgba(RED), None
    return f" F{index + 1} {title} ", None, None


def register_click_meta() -> None:
    """Register MDGA's ``ydna_click`` Rich meta key on ``textual_wgpu.widgets.RichText``."""
    from textual_wgpu.widgets import RichText

    from ..workspaces.base import CLICK_META

    if CLICK_META not in RichText.CLICK_META_KEYS:
        RichText.CLICK_META_KEYS = (CLICK_META, *RichText.CLICK_META_KEYS)


def make_mode_bar(screen: Any, active: str, labels: Any):
    """Build a ``textual_wgpu.widgets.ModeBar`` wired with live F8 Jobs(X) badge."""
    from textual_wgpu.widgets import ModeBar

    register_click_meta()
    return ModeBar(
        active,
        labels,
        on_select=lambda i: screen.app.set_mode(i) if screen.app else None,
        item_formatter=mode_bar_item,
    )


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
