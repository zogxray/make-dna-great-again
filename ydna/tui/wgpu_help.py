"""F10 Help screen for textual-wgpu — MarkdownViewer (TOC + Markdown) + lib widgets."""

from __future__ import annotations

from textual_wgpu.screen import Screen
from textual_wgpu.widgets import (
    KeyBar,
    Markdown,
    MarkdownViewer,
    ModeBar,
    Panel,
    StatusBar,
    VerticalScroll,
)

from ..workspaces.base import MODE_LABELS
from ..workspaces.help.view import HELP_MD


class HelpScreen(Screen):
    """Same Markdown guide as Textual F10, hosted on ``MarkdownViewer`` (TOC + body).

    Drag to select · Ctrl+C to copy · T toggles Table of Contents.
    """

    DEFAULT_CSS = """
    #help-box { height: 1fr; border: round $primary; }
    #help-box:focus-within { border: round $accent; }
    #help-viewer { height: 1fr; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """

    BINDINGS = [
        ("up", "scroll_up"),
        ("down", "scroll_down"),
        ("pageup", "scroll_page_up"),
        ("pagedown", "scroll_page_down"),
        ("home", "scroll_home"),
        ("end", "scroll_end"),
        ("t", "toggle_toc"),
        ("ctrl+a", "select_all"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._labels = list(MODE_LABELS)
        self._first_paint_done = False
        self._help_w = 0

    def compose(self):
        yield ModeBar(
            "help",
            self._labels,
            on_select=lambda i: self.app.set_mode(i) if self.app else None,
        )
        viewer = MarkdownViewer(HELP_MD, show_table_of_contents=True, id="help-viewer")
        yield Panel(viewer, title="HELP · flow & screens", id="help-box")
        yield KeyBar([
            ("↑↓", "Scroll", None),
            ("PgUp/Dn", "Page", None),
            ("Home/End", "Top/Bottom", None),
            ("T", "TOC", lambda: self.toggle_toc()),
            ("drag", "Select", None),
            ("Ctrl+C", "Copy", None),
            ("Ctrl+A", "All", lambda: self.select_all()),
            ("F1–F10", "Modes", None),
            ("Q", "Quit", lambda: self.app.ask_quit() if self.app else None),
        ])
        yield StatusBar(id="status")

    def on_screen_mount(self) -> None:
        if self.app is not None:
            self.app.show_busy("Help", "Loading guide…")
        try:
            self._paint_help()
            self._update_status()
            sc = self._scroll()
            if self.app is not None and sc is not None:
                self.app.set_focus(sc)
            self._first_paint_done = True
        finally:
            if self.app is not None:
                self.app.hide_busy()

    def on_screen_resume(self) -> None:
        sc = self._scroll()
        if self.app is not None and sc is not None:
            self.app.set_focus(sc)

    def on_resize(self) -> None:
        if not self._first_paint_done:
            return
        body = self._body()
        if body is None:
            return
        w = body.rect.w or 0
        if w and abs(w - (self._help_w or 0)) >= 8:
            self._paint_help()

    def _viewer(self) -> MarkdownViewer | None:
        try:
            return self.query_one("#help-viewer", MarkdownViewer)
        except KeyError:
            return None

    def _body(self) -> Markdown | None:
        v = self._viewer()
        return v.document if v is not None else None

    def _scroll(self) -> VerticalScroll | None:
        v = self._viewer()
        return v._scroll if v is not None else None

    def _update_status(self) -> None:
        v = self._viewer()
        toc_state = "on" if (v is not None and v.show_table_of_contents) else "off"
        line = (
            f"Advanced-amateur guide · TOC={toc_state} (T) · "
            "drag+Ctrl+C copy · F1–F10 switch modes"
        )
        try:
            self.query_one("#status", StatusBar).update(line)
        except KeyError:
            pass

    def _paint_help(self) -> None:
        body = self._body()
        scroll = self._scroll()
        if body is None or scroll is None:
            return
        w = max(40, (body.rect.w or scroll.rect.w or 720) - 8)
        self._help_w = w
        body.invalidate()
        body.ensure_baked(w)
        if self.app is not None:
            self.app.request_draw()

    def toggle_toc(self) -> None:
        v = self._viewer()
        if v is None:
            return
        v.show_table_of_contents = not v.show_table_of_contents
        self._update_status()
        if self.app is not None:
            self.app.request_draw()

    def action_toggle_toc(self) -> None:
        self.toggle_toc()

    def select_all(self) -> None:
        body = self._body()
        if body is None:
            return
        body.select_all()
        self._notify("Selected all · Ctrl+C to copy")

    def _notify(self, msg: str) -> None:
        if self.app is not None and hasattr(self.app, "notify"):
            self.app.notify(msg)

    def scroll_up(self) -> None:
        sc = self._scroll()
        if sc is not None:
            sc.scroll(-3)

    def scroll_down(self) -> None:
        sc = self._scroll()
        if sc is not None:
            sc.scroll(3)

    def scroll_page_up(self) -> None:
        sc = self._scroll()
        if sc is not None:
            sc.scroll(-sc._visible_lines())

    def scroll_page_down(self) -> None:
        sc = self._scroll()
        if sc is not None:
            sc.scroll(sc._visible_lines())

    def scroll_home(self) -> None:
        sc = self._scroll()
        if sc is not None:
            sc.scroll_home()

    def scroll_end(self) -> None:
        sc = self._scroll()
        if sc is not None:
            sc.scroll_end()
