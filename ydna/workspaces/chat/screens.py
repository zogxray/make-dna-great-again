"""Диалог выбора Gemini Flash из живого списка API."""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label, OptionList, Static
from textual.widgets.option_list import Option

from ...core.chat import ChatModel
from ...theme.styles import DIM, MUTED, OK_BOLD
from ..base import ClipTextArea, ConfirmScreen, ModalDialog  # noqa: F401  ConfirmScreen re-export


class ModelScreen(ModalDialog):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]
    DEFAULT_CSS = """
    ModelScreen { align: center middle; }
    #res-box { width: 72; height: 70%; border: round $accent; background: $surface; padding: 1 2; }
    #res-box OptionList { height: 1fr; }
    """

    def __init__(self, models: list[ChatModel], current: str | None) -> None:
        super().__init__()
        self.models = models
        self.current = current

    def compose(self) -> ComposeResult:
        opts = []
        for m in self.models:
            on = m.id == self.current
            t = Text(no_wrap=True, overflow="ellipsis")
            t.append("● " if on else "  ", style=OK_BOLD if on else DIM)
            t.append(f"{m.label:<28}", style="bold")
            t.append(m.name, style=MUTED)
            if "lite" in m.name.lower():
                t.append("  lite", style=DIM)
            opts.append(Option(t, id=m.name))
        with Vertical(id="res-box"):
            yield Label("Free Flash models (live list)   Enter: use · Esc: cancel")
            yield OptionList(*opts)

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        idx = next((i for i, m in enumerate(self.models) if m.id == self.current), 0)
        ol.highlighted = idx
        ol.focus()

    @on(OptionList.OptionSelected)
    def _pick(self, event: OptionList.OptionSelected) -> None:
        name = str(event.option.id) if event.option and event.option.id else ""
        mid = next((m.id for m in self.models if m.name == name), None)
        self.dismiss(mid)


class EditScreen(ModalDialog):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    EditScreen { align: center middle; }
    #dialog { width: 84; height: 28; border: round $accent; padding: 1 1 0 1;
              background: $surface; }
    #body { height: 1fr; margin: 1 0; }
    #bottom { height: 3; }
    #hint { width: 1fr; height: 3; content-align: left middle; color: $text-muted; }
    #bottom Button { margin-left: 1; }
    """

    def __init__(self, text: str, resend: bool) -> None:
        super().__init__()
        self.start = text
        self.resend = resend

    def compose(self) -> ComposeResult:
        hint = ("Resend in this chat · drops replies after this message"
                if self.resend else
                "Saves a new chat from this point · original is kept")
        with Vertical(id="dialog"):
            yield Static("Edit · resend" if self.resend else "Edit · new branch")
            yield ClipTextArea(self.start, id="body", show_line_numbers=False)
            with Horizontal(id="bottom"):
                yield Static(hint + " · Esc cancel", id="hint")
                yield Button("OK", id="ok", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#body", ClipTextArea).focus()

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self._confirm()

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def _confirm(self) -> None:
        text = self.query_one("#body", ClipTextArea).text.strip()
        if not text:
            self.app.notify("Message is empty", severity="warning")
            return
        self.dismiss(text)

    def action_cancel(self) -> None:
        self.dismiss(None)

