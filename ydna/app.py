"""Backward-compatible import path for ``YDnaApp``.

Prefer ``from ydna.tui import YDnaApp``.
"""

from __future__ import annotations

from ydna.tui.app import YDnaApp

__all__ = ["YDnaApp"]
