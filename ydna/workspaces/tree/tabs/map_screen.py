"""Полноэкранная карта DEFCON поверх дерева (клавиша M)."""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from .data import year_label
from .tabs.worldmap import defcon_map


class _Doc:
    """Минимальная замена Doc: defcon_map нужен только rich.Text в .t"""

    def __init__(self) -> None:
        self.t = Text(no_wrap=True, overflow="crop")


class MapScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close", show=False),
        Binding("m", "close", "Close", show=False),
        Binding("w", "toggle_world", "World / Fit"),
    ]
    DEFAULT_CSS = """
    MapScreen { background: #1E1923; }
    #mapscroll { width: 100%; height: 1fr; background: #1E1923; padding: 0 1;
                 scrollbar-size-vertical: 1; }
    #mapbody { width: auto; background: #1E1923; }
    #maphelp { height: 1; background: #302838; color: #5882C7; padding: 0 1; }
    """

    def __init__(self, node: dict) -> None:
        super().__init__()
        self.node = node
        self.world = False

    def compose(self):
        with VerticalScroll(id="mapscroll"):
            yield Static(id="mapbody")
        yield Static(" W — world / fit data    ↑↓ PgUp PgDn — scroll    Esc / M / Q — close",
                     id="maphelp")

    def on_mount(self) -> None:
        self.draw()

    def on_resize(self, event) -> None:
        self.draw()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_toggle_world(self) -> None:
        self.world = not self.world
        self.draw()

    def draw(self) -> None:
        width, height = self.size
        if width < 30:  # раскладка ещё не посчитана
            return
        # 6 колонок — подписи широты слева; 2 padding; 1 скроллбар; 1 запас
        cols = max(40, min(170, width - 6 - 2 - 1 - 1))
        doc = _Doc()
        defcon_map(doc, self.node.get("migrationmap"), self.node.get("ancient") or [],
                   self.node.get("name", ""), year_label, world=self.world, cols=cols,
                   max_rows=max(16, min(46, height - 10)))
        self.query_one("#mapbody", Static).update(doc.t)