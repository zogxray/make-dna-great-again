"""Основа режимов: Workspace, ModeBar, KeyBar, ModalDialog, BusyModal (единый лоадер)."""

from __future__ import annotations

import time
from pathlib import Path

from rich.console import Console
from rich.style import Style
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalGroup
from textual.content import Content
from textual.screen import ModalScreen, Screen
from textual.selection import Selection
from textual.widgets import Button, ProgressBar, Static, TextArea

from ..theme.styles import HI, HI_REV, MUTED

# Порядок = клавиши F1, F2, ...
MODE_LABELS = [("genome", "Genome"), ("data", "Data"), ("tree", "Tree"),
               ("pgs", "PGS"), ("gwas", "GWAS"), ("reference", "Reference"),
               ("chat", "Chat"), ("jobs", "Jobs"), ("samples", "Samples"),
               ("help", "Help")]


RU_LAYOUT = dict(zip("qwertyuiopasdfghjklzxcvbnm", "йцукенгшщзфывапролдячсмить"))


def alt_keys(key: str) -> str:
    """Клавиша-буква в Binding: срабатывает и с Shift/Caps Lock, и в русской раскладке."""
    ru = RU_LAYOUT[key]
    return ",".join((key, key.upper(), ru, ru.upper()))


def fit(s: str, width: int) -> str:
    """Сокращает строку многоточием в середине, чтобы были видны начало и конец."""
    if len(s) <= width:
        return s
    head = (width - 1) // 2
    return s[:head] + "…" + s[len(s) - (width - 1 - head):]


def short_path(p: Path) -> str:
    home, s = str(Path.home()), str(p)
    return "~" + s[len(home):] if s.startswith(home) else s


CLICK_META = "ydna_click"


def click(action: str, style: str = "") -> Style:
    """Стиль Rich, по клику на текст с которым YDnaApp.on_click выполняет action ('screen.x(1)', 'app.y').

    Не "@click": такой текст Textual считает ссылкой и перекрашивает/подчёркивает по теме."""
    meta = Style.from_meta({CLICK_META: action})
    return Style.parse(style) + meta if style else meta


class ModeBar(Static):
    DEFAULT_CSS = "ModeBar { height: 1; padding: 0 1; background: $boost; }"

    def __init__(self, active: str) -> None:
        t = Text(no_wrap=True)
        for i, (key, title) in enumerate(MODE_LABELS):
            style = HI_REV if key == active else MUTED
            t.append(f" F{i + 1} {title} ", style=click(f"app.mode('{key}')", style))
            t.append(" ")
        super().__init__(t)


class KeyBar(Static):
    """Строка подсказок. items: (клавиша, подпись, action или None). Пункты с action кликабельны."""
    DEFAULT_CSS = "KeyBar { height: 1; padding: 0 1; color: $text-muted; }"

    def __init__(self, items: list[tuple[str, str, str | None]], **kw) -> None:
        super().__init__(self._render_items(items), **kw)

    @staticmethod
    def _render_items(items: list[tuple[str, str, str | None]]) -> Text:
        t = Text(no_wrap=True, overflow="ellipsis")
        for i, (key, title, action) in enumerate(items):
            if i:
                t.append("  ")
            if action:
                t.append(key, style=click(action, f"bold {HI}"))
                t.append(f" {title}", style=click(action))
            else:
                t.append(f"{key} {title}", style=MUTED)
        return t

    def set_items(self, items: list[tuple[str, str, str | None]]) -> None:
        self.update(self._render_items(items))


class SelectableRichStatic(Static):
    """Static с screen-selection для любого Rich renderable (Markdown, Group, …).

    У обычного Static get_selection() умеет только Text/Content — Markdown даёт None,
    поэтому drag+Ctrl+C в чате ничего не копировал.
    """

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        visual = self._render()
        if isinstance(visual, Text):
            text = visual.plain
        elif isinstance(visual, Content):
            text = str(visual)
        else:
            width = max(1, self.content_size.width)
            console = Console(
                width=width,
                force_terminal=False,
                color_system=None,
                highlight=False,
            )
            with console.capture() as cap:
                console.print(visual, end="")
            text = "\n".join(line.rstrip() for line in cap.get().splitlines())
        if not text:
            return None
        return selection.extract(text), "\n"


class ClipTextArea(TextArea):
    """TextArea: выделяешь кусок (мышь / Shift+стрелки), Ctrl+C копирует его в ОС.

    ALLOW_SELECT=False — иначе Screen перехватывает drag и гасит selection TextArea
    (остаётся «пустое» выделение → Ctrl+C не копирует фрагмент).
    Ctrl+A = select all (у Textual по умолчанию это начало строки).
    """

    ALLOW_SELECT = False  # своё выделение TextArea, не screen-level select
    BINDINGS = [
        Binding("ctrl+a", "select_all", "Select all", show=False, priority=True),
        Binding("ctrl+c", "copy_clip", "Copy", show=False, priority=True),
        Binding("ctrl+insert", "copy_clip", "Copy", show=False, priority=True),
    ]

    def action_copy_clip(self) -> None:
        text = self.selected_text
        if not text:
            self.app.notify(
                "Select a fragment first · drag or Shift+arrows · Ctrl+A = all",
                severity="warning",
            )
            return
        self.app.copy_to_clipboard(text)
        self.app.notify(f"Copied selection · {len(text):,} chars")


class ReadOnlyClipTextArea(ClipTextArea):
    """Тот же select/copy, но без правки (история чата)."""

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("read_only", True)
        kwargs.setdefault("show_line_numbers", False)
        super().__init__(*args, **kwargs)


class ModalDialog(ModalScreen):
    """Модальное окно, которое закрывается (с результатом None) кликом по затемнённому фону."""

    def on_click(self, event: events.Click) -> None:
        if event.widget is self:
            self.dismiss(None)


class ConfirmScreen(ModalDialog):
    """Да/нет. ←/→/Tab — кнопки, Enter — выбранная, Y — OK, Esc/N — отмена."""

    BINDINGS = [
        Binding("escape", "dismiss(False)", "Cancel"),
        Binding("enter", "activate", "Confirm", show=False),
        Binding("y", "yes", "Confirm", show=False),
        Binding("n", "dismiss(False)", "Cancel", show=False),
        Binding("left", "focus_btn(-1)", "Prev button", show=False),
        Binding("right", "focus_btn(1)", "Next button", show=False),
        Binding("up", "focus_btn(-1)", "Prev button", show=False),
        Binding("down", "focus_btn(1)", "Next button", show=False),
    ]
    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    #dialog { width: 56; height: auto; border: round $accent; padding: 1 2;
              background: $surface; }
    #msg { margin: 1 0; height: auto; color: $text-muted; }
    #bottom { height: 3; align: right middle; }
    #bottom Button { margin-left: 1; }
    """

    def __init__(self, title: str, message: str = "", *, ok: str = "OK",
                 danger: bool = False) -> None:
        super().__init__()
        self._title = title
        self._message = message
        self._ok = ok
        self._danger = danger

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self._title)
            if self._message:
                yield Static(self._message, id="msg")
            with Horizontal(id="bottom"):
                yield Button(self._ok, id="ok",
                             variant="error" if self._danger else "primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def _buttons(self) -> list[Button]:
        return list(self.query("#bottom Button").results(Button))

    def action_focus_btn(self, step: int) -> None:
        buttons = self._buttons()
        if not buttons:
            return
        focused = self.app.focused
        try:
            idx = next(i for i, b in enumerate(buttons) if b is focused)
        except StopIteration:
            idx = 0 if step > 0 else len(buttons) - 1
            buttons[idx].focus()
            return
        buttons[(idx + step) % len(buttons)].focus()

    def action_activate(self) -> None:
        """Enter активирует кнопку в фокусе (не всегда OK)."""
        focused = self.app.focused
        if isinstance(focused, Button) and focused.id == "ok":
            self.dismiss(True)
        elif isinstance(focused, Button) and focused.id == "cancel":
            self.dismiss(False)
        else:
            # Фокус не на кнопке — безопасный дефолт как раньше: подтвердить.
            self.dismiss(True)

    @on(Button.Pressed, "#ok")
    def _ok_btn(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def _cancel_btn(self) -> None:
        self.dismiss(False)

    def action_yes(self) -> None:
        self.dismiss(True)


class BusyModal(ModalScreen[None]):
    """Стандартный модальный лоадер: карточка по центру, фон затемнён."""

    DEFAULT_CSS = """
    BusyModal {
        align: center middle;
        background: $background 55%;
    }
    #busy-box {
        width: 64;
        height: auto !important;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #busy-title { text-style: bold; }
    #busy-sub { color: $text-muted; margin-bottom: 1; height: auto; }
    #busy-stage { margin-top: 1; height: auto; }
    #busy-bar { width: 1fr; }
    #busy-detail { color: $text-muted; height: auto; }
    """

    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        self._title = title
        self._subtitle = subtitle
        self.t0 = time.monotonic()

    def compose(self) -> ComposeResult:
        with VerticalGroup(id="busy-box"):
            yield Static(self._title, id="busy-title")
            yield Static(self._subtitle or " ", id="busy-sub")
            yield Static("Starting…", id="busy-stage")
            yield ProgressBar(total=100, show_eta=False, id="busy-bar")
            yield Static("", id="busy-detail")

    def on_mount(self) -> None:
        self.t0 = time.monotonic()

    def set_header(self, title: str, subtitle: str | None = None) -> None:
        self._title = title
        try:
            self.query_one("#busy-title", Static).update(title)
            if subtitle is not None:
                self._subtitle = subtitle
                self.query_one("#busy-sub", Static).update(subtitle or " ")
        except Exception:
            pass

    def update_progress(self, stage: str, *, frac: float | None = None,
                        done: int | None = None, total: int | None = None) -> None:
        """frac — 0..1; либо done/total. Без обоих — только текст этапа (бар не двигаем)."""
        label = stage
        overall: float | None = None
        counts = ""
        if frac is not None:
            overall = max(0.0, min(1.0, frac)) * 100
        elif done is not None and total and total > 0:
            overall = done / total * 100
            counts = f"{done:,} / {total:,}   ·   "
            label = f"{stage}  ({done:,}/{total:,})"
        elif done is not None and total == 0:
            overall = 100.0
        try:
            self.query_one("#busy-stage", Static).update(label)
            if overall is not None:
                self.query_one("#busy-bar", ProgressBar).update(progress=overall)
            self.query_one("#busy-detail", Static).update(
                f"{counts}{time.monotonic() - self.t0:.1f} s")
        except Exception:
            pass


def show_busy(app: App, title: str, subtitle: str = "") -> BusyModal:
    """Показать единственный BusyModal (если уже открыт — обновить заголовок)."""
    top = app.screen
    if isinstance(top, BusyModal):
        top.set_header(title, subtitle)
        top.t0 = time.monotonic()
        return top
    modal = BusyModal(title, subtitle)
    app.push_screen(modal)
    return modal


def busy_progress(app: App, stage: str, *, frac: float | None = None,
                  done: int | None = None, total: int | None = None,
                  title: str | None = None, subtitle: str | None = None) -> None:
    top = app.screen
    if not isinstance(top, BusyModal):
        return
    if title is not None or subtitle is not None:
        top.set_header(title or top._title, subtitle if subtitle is not None else top._subtitle)
    top.update_progress(stage, frac=frac, done=done, total=total)


def hide_busy(app: App) -> None:
    if isinstance(app.screen, BusyModal):
        app.pop_screen()


class Workspace(Screen):
    """Режим приложения. Наследник задаёт mode_key из MODE_LABELS и в compose() первым отдаёт ModeBar."""
    mode_key: str = ""


class StubWorkspace(Workspace):
    """Заглушка: показывает название режима и список запланированного. Заменить на настоящий compose()."""
    heading: str = ""
    planned: tuple[str, ...] = ()

    def compose(self) -> ComposeResult:
        yield ModeBar(self.mode_key)
        t = Text()
        t.append(f"\n  {self.heading}\n", style="bold #FBF6F0")
        t.append("  Not implemented yet. Planned:\n", style="#AEA5B8")
        for item in self.planned:
            t.append(f"   • {item}\n")
        yield Static(t)
