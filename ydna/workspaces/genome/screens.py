"""Диалог добавления файла генотипирования: путь вручную или выбор в дереве папок."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DirectoryTree, Input, Static

from ..base import ModalDialog

GENOME_SUFFIXES = {".txt", ".csv", ".tsv", ".zip", ".gz"}


class GenomeFileTree(DirectoryTree):
    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [p for p in paths if not p.name.startswith(".")
                and (p.is_dir() or p.suffix.lower() in GENOME_SUFFIXES)]


class OpenFileScreen(ModalDialog):
    DEFAULT_CSS = """
    OpenFileScreen { align: center middle; }
    #dialog { width: 96; height: 85%; border: round $accent; padding: 0 1; background: $surface; }
    #path { margin: 1 0 0 0; }
    GenomeFileTree { height: 1fr; }
    #bottom { height: 3; }
    #hint { width: 1fr; height: 3; content-align: left middle; color: $text-muted; }
    #bottom Button { margin-left: 1; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, start: Path) -> None:
        super().__init__()
        self.start = start

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("[b]Add genotype file[/b]  (23andMe, Argenética, MyHeritage, FTDNA, AncestryDNA; "
                         ".txt .csv .tsv .zip .gz)")
            yield Input(value=f"{self.start}/", placeholder="path to a file or folder", id="path")
            yield GenomeFileTree(self.start)
            with Horizontal(id="bottom"):
                yield Static("Click or Enter on a file: add it · Enter in the field: open the file or "
                             "go to the folder · Esc or click outside: cancel", id="hint")
                yield Button("Add", id="ok", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one(GenomeFileTree).focus()

    def _open(self, raw: str) -> None:
        p = Path(raw.strip()).expanduser()
        if p.is_file():
            self.dismiss(p.resolve())
        elif p.is_dir():
            tree = self.query_one(GenomeFileTree)
            tree.path = p.resolve()
            tree.focus()
        else:
            self.app.notify(f"Not found: {p}", severity="warning")

    @on(Input.Submitted, "#path")
    def _typed(self, event: Input.Submitted) -> None:
        self._open(event.value)

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self._open(self.query_one("#path", Input).value)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(DirectoryTree.FileSelected)
    def _picked(self, event: DirectoryTree.FileSelected) -> None:
        self.dismiss(Path(event.path).resolve())

    @on(DirectoryTree.DirectorySelected)
    def _dir(self, event: DirectoryTree.DirectorySelected) -> None:
        self.query_one("#path", Input).value = f"{event.path}/"

    def action_cancel(self) -> None:
        self.dismiss(None)
