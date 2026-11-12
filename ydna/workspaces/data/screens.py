"""Диалог выбора пути к источнику: ввод вручную или выбор в дереве папок."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DirectoryTree, Input, Static

from ..base import ModalDialog


class _Tree(DirectoryTree):
    BINDINGS = [
        Binding("u", "go_up", "Up", show=False),
        Binding("backspace", "go_up", "Up", show=False),
    ]

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [p for p in paths if not p.name.startswith(".")]

    def action_go_up(self) -> None:
        screen = self.screen
        if isinstance(screen, PathScreen):
            screen.go_up()


def existing_dir(p: Path) -> Path:
    p = p if p.is_dir() else p.parent
    while not p.exists() and p != p.parent:
        p = p.parent
    return p if p.exists() else Path.home()


def tree_root(initial: Path) -> Path:
    """Корень дерева = родитель цели, чтобы были видны соседи и можно было подняться выше."""
    p = existing_dir(initial)
    return p.parent if p != p.parent else p


class PathScreen(ModalDialog):
    """kind: 'folder' или 'file'. must_exist=False — для места скачивания (может ещё не существовать)."""

    DEFAULT_CSS = """
    PathScreen { align: center middle; }
    #dialog { width: 100; height: 85%; border: round $accent; padding: 0 1; background: $surface; }
    #path { margin: 1 0 0 0; }
    PathScreen _Tree { height: 1fr; }
    #bottom { height: 3; }
    #hint { width: 1fr; height: 3; content-align: left middle; color: $text-muted; }
    #bottom Button { margin-left: 1; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, initial: Path, kind: str, must_exist: bool) -> None:
        super().__init__()
        self.title_text = title
        self.initial = initial
        self.kind = kind
        self.must_exist = must_exist

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.title_text)
            yield Input(value=str(self.initial), placeholder=f"path to the {self.kind}", id="path")
            yield _Tree(tree_root(self.initial))
            with Horizontal(id="bottom"):
                yield Static("Tree: click fills the field · U / Backspace / Up: parent folder · "
                             "Enter in field: open that folder in the tree · OK: confirm · Esc: cancel",
                             id="hint")
                yield Button("Up", id="up")
                yield Button("OK", id="ok", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#path", Input).focus()

    def _fill(self, p: Path) -> None:
        inp = self.query_one("#path", Input)
        inp.value = str(p)
        inp.cursor_position = len(inp.value)

    def _tree(self) -> _Tree:
        return self.query_one(_Tree)

    def go_up(self) -> None:
        """Поднять корень DirectoryTree на уровень выше (к `/` дальше нельзя)."""
        tree = self._tree()
        cur = Path(tree.path).resolve()
        parent = cur.parent
        if parent == cur:
            self.app.notify("Already at filesystem root", severity="information")
            return
        tree.path = parent
        if self.kind == "folder":
            self._fill(parent)
        tree.focus()

    def _navigate_tree(self, folder: Path) -> None:
        folder = folder.resolve()
        if not folder.is_dir():
            self.app.notify(f"Not a folder: {folder}", severity="warning")
            return
        self._tree().path = folder
        if self.kind == "folder":
            self._fill(folder)

    @on(DirectoryTree.DirectorySelected)
    def _dir(self, event: DirectoryTree.DirectorySelected) -> None:
        if self.kind == "folder":
            self._fill(Path(event.path))

    @on(DirectoryTree.FileSelected)
    def _file(self, event: DirectoryTree.FileSelected) -> None:
        p = Path(event.path)
        self._fill(p if self.kind == "file" else p.parent)

    @on(Input.Submitted, "#path")
    def _submit(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        if not raw:
            return
        p = Path(raw).expanduser()
        # Существующая папка в поле — перейти к ней в дереве (в т.ч. выше текущего корня).
        if p.is_dir():
            self._navigate_tree(p)
            self._tree().focus()
            return
        self._confirm(raw)

    @on(Button.Pressed, "#up")
    def _up_btn(self) -> None:
        self.go_up()

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self._confirm(self.query_one("#path", Input).value)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def _confirm(self, raw: str) -> None:
        raw = raw.strip()
        if not raw:
            return
        p = Path(raw).expanduser().resolve()
        if self.kind == "folder" and p.is_file():
            self.app.notify("This source needs a folder, not a file", severity="warning")
        elif self.kind == "file" and p.is_dir():
            self.app.notify("This source needs a file, not a folder", severity="warning")
        elif self.must_exist and not p.exists():
            self.app.notify(f"Not found: {p}", severity="warning")
        else:
            self.dismiss(p)

    def action_go_up(self) -> None:
        self.go_up()

    def action_cancel(self) -> None:
        self.dismiss(None)


class TokenScreen(ModalDialog):
    """Ввод API-ключа (не путь)."""

    DEFAULT_CSS = """
    TokenScreen { align: center middle; }
    #dialog { width: 72; height: auto; border: round $accent; padding: 1 1 0 1;
              background: $surface; }
    #token { margin: 1 0; }
    #bottom { height: 3; }
    #hint { width: 1fr; height: 3; content-align: left middle; color: $text-muted; }
    #bottom Button { margin-left: 1; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, has_key: bool) -> None:
        super().__init__()
        self.title_text = title
        self.has_key = has_key

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.title_text)
            yield Input(placeholder="AIza…  (stored in DuckDB)", password=True, id="token")
            with Horizontal(id="bottom"):
                yield Static("Ctrl+V pastes the key · Enter or OK · Esc to cancel"
                             + (" · already set, paste a new key to replace" if self.has_key else ""),
                             id="hint")
                yield Button("OK", id="ok", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#token", Input).focus()

    @on(Input.Submitted, "#token")
    def _submit(self, event: Input.Submitted) -> None:
        self._confirm(event.value)

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self._confirm(self.query_one("#token", Input).value)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def _confirm(self, raw: str) -> None:
        raw = raw.strip()
        if not raw:
            self.app.notify("Paste the API key", severity="warning")
            return
        self.dismiss(raw)

    def action_cancel(self) -> None:
        self.dismiss(None)


class GeminiConfigScreen(ModalDialog):
    """F2 · Gemini: AI Studio key + Vertex project + ADC JSON path."""

    DEFAULT_CSS = """
    GeminiConfigScreen { align: center middle; }
    #dialog { width: 86; height: auto; border: round $accent; padding: 1 1 0 1;
              background: $surface; }
    #dialog Input { margin: 0 0 1 0; }
    #lbl-key, #lbl-proj, #lbl-adc { color: $text-muted; margin-top: 0; }
    #bottom { height: 3; }
    #hint { width: 1fr; height: 3; content-align: left middle; color: $text-muted; }
    #bottom Button { margin-left: 1; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, *, has_key: bool, project: str = "", adc: str = "") -> None:
        super().__init__()
        self.has_key = has_key
        self.project = (project or "").strip()
        self.adc = (adc or "").strip()

    def compose(self) -> ComposeResult:
        key_ph = ("•••• leave blank to keep current key" if self.has_key
                  else "AIza…  AI Studio key (DuckDB)")
        with Vertical(id="dialog"):
            yield Static("[b]Google Gemini[/b] · F2 credentials")
            yield Static("AI Studio API key (optional — Studio channel only)", id="lbl-key")
            yield Input(placeholder=key_ph, password=True, id="token")
            yield Static("Vertex AI · Google Cloud project ID", id="lbl-proj")
            yield Input(
                value=self.project,
                placeholder="e.g. project-2257f100-507e-4707-858",
                id="project",
            )
            yield Static(
                "Vertex AI · ADC JSON (gcloud application_default_credentials)",
                id="lbl-adc",
            )
            yield Input(
                value=self.adc,
                placeholder="~/.config/gcloud/application_default_credentials.json",
                id="adc",
            )
            with Horizontal(id="bottom"):
                yield Static(
                    "OK saves · blank key keeps old · ADC is a file path, not an API key · Esc cancel",
                    id="hint",
                )
                yield Button("OK", id="ok", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        focus = "#project" if self.has_key or self.project else "#token"
        self.query_one(focus, Input).focus()

    @on(Input.Submitted, "#token")
    def _submit_token(self) -> None:
        self.query_one("#project", Input).focus()

    @on(Input.Submitted, "#project")
    def _submit_project(self) -> None:
        self.query_one("#adc", Input).focus()

    @on(Input.Submitted, "#adc")
    def _submit_adc(self) -> None:
        self._confirm()

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self._confirm()

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def _confirm(self) -> None:
        key = self.query_one("#token", Input).value.strip()
        project = self.query_one("#project", Input).value.strip()
        adc = self.query_one("#adc", Input).value.strip()
        if not key and not project and not adc and not self.has_key and not self.project and not self.adc:
            self.app.notify("Enter a key, Cloud project, and/or ADC path", severity="warning")
            return
        if adc:
            path = Path(adc).expanduser()
            if not path.is_file():
                self.app.notify(f"ADC file not found: {path}", severity="warning")
                return
        self.dismiss({"key": key or None, "project": project, "adc": adc})

    def action_cancel(self) -> None:
        self.dismiss(None)
