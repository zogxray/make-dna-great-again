"""Shared clipboard path for ``--ui wgpu`` screens → ``app.copy_to_clipboard``.

Always prefer the ModeApp/App contract (in-app buffer + best-effort OS write).
Do not call ``ydna.core.clip`` from screens; that bypasses the app buffer.
"""

from __future__ import annotations

from typing import Any


def copy_text(app: Any, text: str) -> bool:
    """Copy ``text`` via ``app.copy_to_clipboard``.

    Returns True if an OS clipboard tool accepted the write.
    False if there is no app API, or only the in-app buffer was updated.
    """
    if not text:
        return False
    if app is None or not hasattr(app, "copy_to_clipboard"):
        return False
    try:
        return bool(app.copy_to_clipboard(text))
    except Exception:  # noqa: BLE001
        return False


def copy_status(ok: bool, *, success: str, label: str | None = None) -> str:
    """Honest notify line — no false OSC 52 claims (wgpu has none)."""
    if ok:
        return success
    if label:
        return f"{label} · in-app only (no wl-copy/xclip/pbcopy)"
    return "In-app copy only (no wl-copy/xclip/pbcopy)"
