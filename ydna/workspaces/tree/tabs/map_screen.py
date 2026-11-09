"""Полноэкранная карта DEFCON (клавиша M): карта слева, Contact log + Migration справа."""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ..data import year_label
from . import Doc
from .node import _WIDTH, render_migration
from .worldmap import contact_log_lines, defcon_map


class _MapDoc:
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
    #maprow { height: 1fr; }
    #mapscroll { width: 1fr; height: 1fr; background: #1E1923; padding: 0 1;
                 scrollbar-size-vertical: 1; }
    #mapbody { width: auto; background: #1E1923; }
    #sidescroll { width: 42; min-width: 34; max-width: 52; height: 1fr;
                  background: #241E2A; border-left: tall #483A59; padding: 0 1;
                  scrollbar-size-vertical: 1; }
    #sidebody { width: 100%; background: #241E2A; }
    #maphelp { height: 1; background: #302838; color: #5882C7; padding: 0 1; }
    """

    def __init__(self, node: dict, *, world: bool = False) -> None:
        super().__init__()
        self.node = node
        self.world = bool(world)

    def compose(self):
        with Horizontal(id="maprow"):
            with VerticalScroll(id="mapscroll"):
                yield Static(id="mapbody")
            with VerticalScroll(id="sidescroll"):
                yield Static(id="sidebody")
        yield Static(" W — world / fit    ↑↓ scroll    Esc / M / Q — close",
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
        if width < 40:  # раскладка ещё не посчитана
            return
        side_w = 42 if width >= 100 else max(30, width // 3)
        try:
            self.query_one("#sidescroll").styles.width = side_w
        except Exception:
            pass
        # карта: минус правая панель, подписи широты, padding, скролл
        cols = max(36, min(140, width - side_w - 6 - 2 - 1 - 1))
        doc = _MapDoc()
        centers = defcon_map(
            doc, self.node.get("migrationmap"), self.node.get("ancient") or [],
            self.node.get("name", ""), year_label, world=self.world, cols=cols,
            max_rows=max(16, min(46, height - 4)), with_log=False,
        ) or []
        self.query_one("#mapbody", Static).update(doc.t)

        side = Doc()
        _WIDTH[0] = max(28, side_w - 4)
        for text, style in contact_log_lines(centers, year_label, limit=60):
            if style.startswith("bold"):
                side.title(text)
            else:
                side.line(text, style, indent=0)
        render_migration(side, self.node, compact=True)
        self.query_one("#sidebody", Static).update(side.t)
