"""Полноэкранная карта DEFCON (клавиша M): карта слева, Contact log + Migration справа."""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ..data import year_label
from . import Doc
from .node import _WIDTH, render_migration
from .worldmap import contact_log_lines, defcon_map

# Строки хрома вокруг braille-холста (title, ═, axis, ═, symbols, LEGEND row).
_MAP_CHROME = 6


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
    #mappane { width: 1fr; height: 1fr; background: #1E1923; padding: 0 1;
               overflow-x: auto; overflow-y: hidden; }
    #mapbody { width: auto; height: auto; background: #1E1923; }
    #sidescroll { width: 52; min-width: 44; max-width: 64; height: 1fr;
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
            with Vertical(id="mappane"):
                yield Static(id="mapbody")
            with VerticalScroll(id="sidescroll"):
                yield Static(id="sidebody")
        yield Static(" W — world/fit · ◆/labels = path colors · LEGEND under map · Esc/M/Q",
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
        # Чуть шире боковая панель; на узких терминалах — доля ширины.
        if width >= 120:
            side_w = 56
        elif width >= 100:
            side_w = 52
        else:
            side_w = max(40, min(52, width // 3 + 4))
        try:
            self.query_one("#sidescroll").styles.width = side_w
        except Exception:
            pass

        # Высота под карту: без вертикального скролла (+1 строка LEGEND под картой).
        map_h = max(16, height - 1)  # минус #maphelp
        try:
            pane_h = self.query_one("#mappane").size.height
            if pane_h >= 12:
                map_h = pane_h
        except Exception:
            pass
        max_rows = max(12, map_h - _MAP_CHROME)

        cols = max(36, min(140, width - side_w - 6 - 2 - 1 - 1))
        doc = _MapDoc()
        centers = defcon_map(
            doc, self.node.get("migrationmap"), self.node.get("ancient") or [],
            self.node.get("name", ""), year_label, world=self.world, cols=cols,
            max_rows=max_rows, with_log=False, show_path_legend=True,
        ) or []
        self.query_one("#mapbody", Static).update(doc.t)

        side = Doc()
        side_inner = max(32, side_w - 4)
        _WIDTH[0] = side_inner
        for text, style in contact_log_lines(centers, year_label, limit=60, width=side_inner):
            if style.startswith("bold"):
                side.title(text)
            else:
                side.line(text, style, indent=0)
        render_migration(side, self.node, compact=True)
        self.query_one("#sidebody", Static).update(side.t)
