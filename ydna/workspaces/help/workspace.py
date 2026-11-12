"""Режим Help (F9): флоу и UX по экранам."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from ..base import (KeyBar, ModeBar, SelectableRichStatic, Workspace, hide_busy,
                    show_busy)
from .view import help_view


class HelpWorkspace(Workspace):
    mode_key = "help"
    DEFAULT_CSS = """
    #help-box { height: 1fr; border: round $primary; }
    #help-box:focus-within { border: round $accent; }
    #help-scroll { height: 1fr; scrollbar-size-vertical: 1; }
    #help-body { padding: 0 1; }
    #help { height: 1; padding: 0 1; color: $text-muted; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """
    BINDINGS = [
        Binding("up", "scroll(-3)", "Up", show=False),
        Binding("down", "scroll(3)", "Down", show=False),
        Binding("pageup", "scroll(-20)", "PgUp", show=False),
        Binding("pagedown", "scroll(20)", "PgDn", show=False),
        Binding("home", "scroll_home", "Home", show=False),
        Binding("end", "scroll_end", "End", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._first_paint_done = False
        self._help_w = 0

    def compose(self) -> ComposeResult:
        yield ModeBar(self.mode_key)
        with Vertical(id="help-box"):
            with VerticalScroll(id="help-scroll"):
                yield SelectableRichStatic(id="help-body")
        yield KeyBar([("↑↓", "Scroll", None), ("PgUp/Dn", "Page", None),
                      ("F1–F9", "Modes", None), ("Q", "Quit", "app.quit")], id="help")
        yield Static(id="status")

    def on_mount(self) -> None:
        self.query_one("#help-box").border_title = "HELP · flow & screens"
        # Spinner first frame; Markdown bake after paint (~100 ms).
        show_busy(self.app, "Help", "Loading guide…")
        self.call_after_refresh(self._first_paint)

    def _first_paint(self) -> None:
        if self._first_paint_done or not self.is_mounted:
            hide_busy(self.app)
            return
        self._first_paint_done = True
        try:
            self._paint_help()
            self.query_one("#status", Static).update(
                "Advanced-amateur guide · not an R/admixture2 manual · F1–F9 switch modes")
            self.query_one("#help-scroll").focus()
        finally:
            hide_busy(self.app)

    def on_resize(self) -> None:
        if not self._first_paint_done:
            return
        # Re-bake only when width jumps a lot (wrap changes); cheap Text otherwise.
        body = self.query_one("#help-body", SelectableRichStatic)
        w = body.content_size.width or body.size.width
        if w and abs(w - (getattr(self, "_help_w", 0) or 0)) >= 8:
            self._paint_help()

    def _paint_help(self) -> None:
        body = self.query_one("#help-body", SelectableRichStatic)
        w = body.content_size.width or body.size.width or 100
        self._help_w = w
        body.update(help_view(width=max(40, w - 2)))

    def action_scroll(self, delta: int) -> None:
        self.query_one("#help-scroll").scroll_relative(y=delta, animate=False)

    def action_scroll_home(self) -> None:
        self.query_one("#help-scroll").scroll_home(animate=False)

    def action_scroll_end(self) -> None:
        self.query_one("#help-scroll").scroll_end(animate=False)
