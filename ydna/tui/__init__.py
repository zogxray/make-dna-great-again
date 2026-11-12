"""Textual TUI layer — app shell and desktop window launch.

Must not be imported by ``ydna.core`` or ``ydna.cli``.
"""

from __future__ import annotations

from .app import YDnaApp

__all__ = ["YDnaApp"]
