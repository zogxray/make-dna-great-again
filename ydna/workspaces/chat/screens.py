"""Диалоги выбора LLM-канала и модели."""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from ...core import llm
from ...core.chat import ChatModel, short_model
from ...theme.styles import DIM, MUTED, OK_BOLD, WARN
from ..base import ClipTextArea, ConfirmScreen, ModalDialog  # noqa: F401  ConfirmScreen re-export


class ProviderScreen(ModalDialog):
    """Explicit pick: AI Studio ↔ Vertex (no silent cycle)."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]
    DEFAULT_CSS = """
    ProviderScreen { align: center middle; }
    #res-box { width: 72; height: auto; max-height: 70%; border: round $accent;
               background: $surface; padding: 1 2; }
    #res-box OptionList { height: auto; max-height: 16; }
    #hint { color: $text-muted; margin-top: 1; }
    """

    def __init__(self, current: llm.Provider, rows: list[tuple[llm.Provider, bool, str]]) -> None:
        super().__init__()
        self.current = current
        self.rows = rows

    def compose(self) -> ComposeResult:
        opts = []
        for mode, ok, detail in self.rows:
            on = mode == self.current
            t = Text(no_wrap=True, overflow="ellipsis")
            t.append("● " if on else "  ", style=OK_BOLD if on else DIM)
            t.append(f"{llm.provider_label(mode):<12}", style="bold")
            if ok:
                t.append(detail, style=MUTED)
            else:
                t.append(detail, style=WARN)
            opts.append(Option(t, id=mode))
        with Vertical(id="res-box"):
            yield Label("LLM channel · Enter: use · Esc: cancel")
            yield OptionList(*opts)
            yield Static(
                "Choice only — no failover. Credentials: F2 · Gemini · P.",
                id="hint",
            )

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        idx = next((i for i, (m, _, __) in enumerate(self.rows) if m == self.current), 0)
        ol.highlighted = idx
        ol.focus()

    @on(OptionList.OptionSelected)
    def _pick(self, event: OptionList.OptionSelected) -> None:
        mode = str(event.option.id) if event.option and event.option.id else ""
        if mode in ("vertex", "aistudio"):
            self.dismiss(mode)
        else:
            self.dismiss(None)


class ModelScreen(ModalDialog):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]
    DEFAULT_CSS = """
    ModelScreen { align: center middle; }
    #res-box { width: 78; height: 70%; border: round $accent; background: $surface; padding: 1 2; }
    #res-box OptionList { height: 1fr; }
    #custom { margin-top: 1; }
    #hint { color: $text-muted; }
    """

    def __init__(self, models: list[ChatModel], current: str | None,
                 mode: llm.Provider = "aistudio") -> None:
        super().__init__()
        self.models = models
        self.current = current
        self.mode = mode

    def compose(self) -> ComposeResult:
        channel = llm.provider_label(self.mode)
        opts = []
        for m in self.models:
            on = m.id == self.current or short_model(m.id) == short_model(self.current or "")
            t = Text(no_wrap=True, overflow="ellipsis")
            t.append("● " if on else "  ", style=OK_BOLD if on else DIM)
            t.append(f"{m.label:<28}", style="bold")
            t.append(m.name, style=MUTED)
            low = m.name.lower()
            if "argon" in low or "carbon" in low:
                t.append("  top", style=OK_BOLD)
            elif "pro" in low.split("-"):
                t.append("  pro", style=DIM)
            if "lite" in low:
                t.append("  lite", style=DIM)
            opts.append(Option(t, id=m.name))
        with Vertical(id="res-box"):
            yield Label(
                f"{channel} models (tops first) · Enter: use · type id · Esc: cancel"
            )
            yield OptionList(*opts)
            yield Input(
                placeholder="custom model id (e.g. gemini-2.5-pro)",
                id="custom",
            )
            yield Static("List is for this channel only — AI Studio ≠ Vertex.", id="hint")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        if not self.models:
            self.query_one("#custom", Input).focus()
            return
        idx = next(
            (i for i, m in enumerate(self.models)
             if m.id == self.current or short_model(m.id) == short_model(self.current or "")),
            0,
        )
        ol.highlighted = idx
        ol.focus()

    @on(OptionList.OptionSelected)
    def _pick(self, event: OptionList.OptionSelected) -> None:
        name = str(event.option.id) if event.option and event.option.id else ""
        mid = next((m.id for m in self.models if m.name == name), None)
        self.dismiss(mid)

    @on(Input.Submitted, "#custom")
    def _custom(self, event: Input.Submitted) -> None:
        raw = (event.value or "").strip()
        if not raw:
            self.app.notify("Type a model id", severity="warning")
            return
        self.dismiss(raw)


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

