"""Режим Help (F7): флоу и UX по экранам."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from ..base import KeyBar, ModeBar, SelectableRichStatic, Workspace
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

    def compose(self) -> ComposeResult:
        yield ModeBar(self.mode_key)
        with Vertical(id="help-box"):
            with VerticalScroll(id="help-scroll"):
                yield SelectableRichStatic(id="help-body")
        yield KeyBar([("↑↓", "Scroll", None), ("PgUp/Dn", "Page", None),
                      ("F1–F7", "Modes", None), ("Q", "Quit", "app.quit")], id="help")
        yield Static(id="status")

    def on_mount(self) -> None:
        self.query_one("#help-box").border_title = "HELP · flow & screens"
        self.query_one("#help-body", SelectableRichStatic).update(help_view())
        self.query_one("#status", Static).update(
            "Advanced-amateur guide · not an R/admixture2 manual · F1–F7 switch modes")
        self.query_one("#help-scroll").focus()

    def action_scroll(self, delta: int) -> None:
        self.query_one("#help-scroll").scroll_relative(y=delta, animate=False)

    def action_scroll_home(self) -> None:
        self.query_one("#help-scroll").scroll_home(animate=False)

    def action_scroll_end(self) -> None:
        self.query_one("#help-scroll").scroll_end(animate=False)
