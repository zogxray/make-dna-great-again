"""Open Humans helpers shared with F9 Samples (query dialog)."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, Static

from ..base import ModalDialog


class QueryScreen(ModalDialog):
    """Substring filter for the OpenHumans catalog."""

    DEFAULT_CSS = """
    QueryScreen { align: center middle; }
    #dialog { width: 64; height: auto; border: round $accent; padding: 1 1 0 1;
              background: $surface; }
    #q { margin: 1 0; }
    #hint { color: $text-muted; height: 2; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, initial: str = "") -> None:
        super().__init__()
        self.initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("[b]Filter query[/b] · basename / tags / user")
            yield Input(value=self.initial, placeholder="23andMe · Ancestry · username…", id="q")
            yield Static("Enter apply · Esc clear&close if empty else cancel", id="hint")

    def on_mount(self) -> None:
        self.query_one("#q", Input).focus()

    @on(Input.Submitted, "#q")
    def _submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)
