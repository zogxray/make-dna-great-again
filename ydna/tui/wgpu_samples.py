"""F9 Samples screen for textual-wgpu — Open Humans catalog (app logic, lib widgets)."""

from __future__ import annotations

import ast
import re
from dataclasses import replace
from pathlib import Path

from textual_wgpu.screen import Screen
from textual_wgpu.widgets import (
    Horizontal,
    KeyBar,
    ModeBar,
    Option,
    OptionList,
    Panel,
    RichText,
    Static,
    StatusBar,
    Vertical,
)

from ..core import openhumans as oh
from ..core import openhumans_db as ohdb
from ..core.jobs import JOBS
from ..core.sources import BY_KEY
from ..core.store import Store
from ..workspaces.base import MODE_LABELS, fit
from ..workspaces.samples.view import file_detail_text, filter_bar_text

_ACTION_RE = re.compile(r"^screen\.(\w+)(?:\((.*)\))?$", re.DOTALL)

_DB_GLYPH = {
    ohdb.ST_INGESTED: "D",
    ohdb.ST_DOWNLOADED: "d",
    ohdb.ST_ERROR: "!",
    ohdb.ST_CATALOG: " ",
}

_COMPAT_SHORT = {
    oh.COMPAT_READY: "READY",
    oh.COMPAT_NEED: "NEED ",
    oh.COMPAT_PEEK: "PEEK ",
    oh.COMPAT_SKIP: "SKIP ",
}


def _hex_rgba(hex_color: str) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 6:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
    return (174, 165, 184, 255)


class SamplesScreen(Screen):
    """Same Open Humans flow as Textual F9, hosted on wgpu widgets."""

    DEFAULT_CSS = """
    #oh-main { height: 1fr; }
    #oh-list-box { width: 1fr; border: round $primary; }
    #oh-file-box { width: 42; min-width: 36; border: round $primary; }
    #oh-list-box:focus-within, #oh-file-box:focus-within { border: round $accent; }
    #oh-files { height: 1fr; border: none; background: transparent; }
    #oh-status { height: 1; padding: 0 1; background: $boost; }
    """

    BINDINGS = [
        ("space", "toggle_mark"),
        ("d", "download"),
        ("a", "mark_ready"),
        ("c", "clear_marks"),
        ("p", "preset"),
        ("x", "toggle_noise"),
        ("f", "cycle_kind"),
        ("w", "cycle_vendor"),
        ("h", "cycle_have"),
        ("k", "cycle_compat"),
        ("y", "cycle_y"),
        ("/", "query"),
        ("slash", "query"),
        ("r", "reload"),
        ("v", "sync_db"),
        ("enter", "open_local"),
        ("return", "open_local"),
    ]

    def __init__(self, store: Store | None = None) -> None:
        super().__init__()
        self.store = store or Store()
        self.cat = oh.OhCatalog()
        self.flt = oh.OhFilters()
        self.shown: list[oh.OhFile] = []
        self.marked: set[int] = set()
        self._building = False
        self._labels = list(MODE_LABELS)

    def compose(self):
        yield ModeBar(
            "samples",
            self._labels,
            on_select=lambda i: self.app.set_mode(i) if self.app else None,
        )
        title = Static(
            "Open Humans · D downloads this row · ● READY / ○ NEED labeled, both OK",
            id="oh-title",
        )
        title.layout_hint_h = 24
        yield title

        filters = RichText(id="oh-filters")
        filters.layout_hint_h = 40
        ol = OptionList(id="oh-files", on_highlight=self._on_highlight)
        list_body = Vertical(filters, ol, id="oh-list-inner")
        list_panel = Panel(list_body, title="CATALOG", id="oh-list-box")

        detail = RichText(id="oh-file")
        detail.on_action = self._run_action
        file_panel = Panel(detail, title="FILE", id="oh-file-box")

        yield Horizontal(list_panel, file_panel, id="oh-main")

        yield KeyBar([
            ("↑↓", "Row", None),
            ("Enter", "Open F1", lambda: self.open_local()),
            ("D", "Download", lambda: self.download()),
            ("Space", "Mark", lambda: self.toggle_mark()),
            ("V", "Update", lambda: self.sync_db()),
            ("R", "Reload", lambda: self.reload()),
            ("A", "Mark READY", lambda: self.mark_ready()),
            ("P", "Preset", lambda: self.preset()),
            ("C", "Clear", lambda: self.clear_marks()),
            ("F/W/H/K/Y", "Filters", None),
            ("X", "Noise", lambda: self.toggle_noise()),
            ("/", "Query", lambda: self.query()),
            ("Q", "Quit", lambda: self.app.ask_quit() if self.app else None),
        ])
        yield StatusBar(id="oh-status")

    def on_screen_mount(self) -> None:
        folder = self._folder()
        if self.store.path("openhumans") is None:
            self.store.set_path("openhumans", folder)
            folder.mkdir(parents=True, exist_ok=True)
        if self.app is not None:
            self.app.show_busy("Open Humans", "Loading catalog…")
        try:
            self.reload_catalog()
        finally:
            if self.app is not None:
                self.app.hide_busy()
        try:
            ol = self.query_one("#oh-files", OptionList)
            if self.app is not None:
                self.app.set_focus(ol)
        except KeyError:
            pass

    def on_screen_resume(self) -> None:
        self.refresh_status()
        if self.app is not None:
            self.app.request_draw()

    def _run_action(self, action: str) -> None:
        """Dispatch Rich ``ydna_click`` (``screen.download``, ``screen.open_local``)."""
        m = _ACTION_RE.match((action or "").strip())
        if not m:
            return
        name, args_src = m.group(1), (m.group(2) or "").strip()
        fn = getattr(self, name, None) or getattr(self, f"action_{name}", None)
        if not callable(fn):
            return
        try:
            args = ast.literal_eval(f"({args_src},)") if args_src else ()
        except Exception:  # noqa: BLE001
            return
        if not isinstance(args, tuple):
            args = (args,)
        fn(*args)

    def _folder(self) -> Path:
        src = BY_KEY.get("openhumans")
        if src:
            return self.store.path(src.key) or src.suggest(self.store)
        return oh.default_samples_dir()

    def _on_highlight(self, _opt: Option | None) -> None:
        if not self._building:
            self.refresh_file()

    def reload_catalog(self, *, force: bool = False) -> None:
        self.cat = oh.load_catalog(force=force)
        try:
            ohdb.scan_folder(self._folder(), self.cat)
        except Exception:  # noqa: BLE001
            pass
        self.rebuild_list()

    def rebuild_list(self, *, keep_id: int | None = None) -> None:
        self._building = True
        folder = self._folder()
        self.shown = oh.apply_filters(self.cat.files, self.flt, folder)
        ol = self.query_one("#oh-files", OptionList)
        options: list[Option] = []
        have = oh.local_ids(folder)
        try:
            db_st = ohdb.status_map([f.id for f in self.shown])
        except Exception:  # noqa: BLE001
            db_st = {}
        for f in self.shown:
            mark = "▸" if f.id in self.marked else " "
            local = "L" if f.id in have else " "
            db = _DB_GLYPH.get(db_st.get(f.id, ohdb.ST_CATALOG), " ")
            glyph = oh.COMPAT_GLYPH.get(f.compat, "?")
            style = _hex_rgba(oh.COMPAT_STYLE.get(f.compat, "#AEA5B8"))
            vend = oh.vendor_short(f.vendor).ljust(3)
            comp = _COMPAT_SHORT.get(f.compat, "???? ")
            who = (f.user_name or f.username or "—").strip() or "—"
            who = fit(who, 14).ljust(14)
            fname = fit(f.basename or str(f.id), 32)
            prefix = f"{mark}{local}{db} "
            pref_fill = _hex_rgba("#729fcf" if f.id in self.marked else "#71697A")
            runs = [
                (prefix, pref_fill),
                (f"{glyph}{comp} ", style),
                (f"{vend} ", _hex_rgba("#8A8294")),
                (f"{who} ", _hex_rgba("#C3BCCC")),
                (fname, _hex_rgba("#FBF6F0") if f.id in self.marked else _hex_rgba("#C3BCCC")),
            ]
            label = prefix + f"{glyph}{comp} {vend} {who} {fname}"
            options.append(Option(label, id=f"oh:{f.id}", runs=runs, data=f))
        keep = f"oh:{keep_id}" if keep_id is not None else None
        ol.set_options(options, keep_id=keep)
        people = len({f.user_id for f in self.shown if f.user_id})
        box = self.query_one("#oh-list-box", Panel)
        box.subtitle = f"{len(self.shown):,} shown · {people:,} people · marked {len(self.marked)}"
        self._building = False
        self.refresh_filters()
        self.refresh_file()
        self.refresh_status()
        if self.app is not None:
            self.app.request_draw()

    def refresh_filters(self) -> None:
        self.query_one("#oh-filters", RichText).update(filter_bar_text(self.flt))

    def _current(self) -> oh.OhFile | None:
        opt = self.query_one("#oh-files", OptionList).highlighted_option
        if opt is None:
            return None
        if isinstance(opt.data, oh.OhFile):
            return opt.data
        raw = str(opt.id or "")
        if raw.startswith("oh:"):
            raw = raw[3:]
        try:
            fid = int(raw)
        except ValueError:
            return None
        for f in self.shown:
            if f.id == fid:
                return f
        return None

    def refresh_file(self) -> None:
        f = self._current()
        detail = self.query_one("#oh-file", RichText)
        detail.on_action = self._run_action
        detail.update(file_detail_text(
            f,
            folder=self._folder(),
            marked=bool(f and f.id in self.marked),
        ))

    def refresh_status(self) -> None:
        folder = self._folder()
        n_local = len(oh.local_ids(folder))
        job = JOBS.get("openhumans")
        bits = [
            f"catalog {self.cat.n:,}",
            f"local {n_local:,}",
            f"marked {len(self.marked)}",
            f"→ {folder}",
        ]
        try:
            st = ohdb.stats()
            bits.insert(1, f"DB {st['ingested']:,} ingested/{st['local']:,} local")
        except Exception:  # noqa: BLE001
            pass
        if job and job.active:
            bits.append(f"⇣ {job.stage}")
        self.query_one("#oh-status", StatusBar).update(" · ".join(bits))

    def _notify(self, msg: str) -> None:
        if self.app is not None and hasattr(self.app, "notify"):
            self.app.notify(msg)

    def toggle_mark(self) -> None:
        f = self._current()
        if not f:
            return
        if f.id in self.marked:
            self.marked.discard(f.id)
        else:
            self.marked.add(f.id)
        self.rebuild_list(keep_id=f.id)

    def clear_marks(self) -> None:
        self.marked.clear()
        cur = self._current()
        self.rebuild_list(keep_id=cur.id if cur else None)
        self._notify("Marks cleared")

    def mark_ready(self) -> None:
        n = 0
        for f in self.shown:
            if f.compat == oh.COMPAT_READY:
                self.marked.add(f.id)
                n += 1
        cur = self._current()
        self.rebuild_list(keep_id=cur.id if cur else None)
        self._notify(f"Marked {n} READY in view")

    def preset(self) -> None:
        picks = oh.preset_starter(self.cat.files, self._folder())
        for f in picks:
            self.marked.add(f.id)
        cur = self._current()
        self.rebuild_list(keep_id=cur.id if cur else None)
        self._notify(f"Preset marked {len(picks)} files")

    def toggle_noise(self) -> None:
        self.flt = replace(self.flt, hide_noise=not self.flt.hide_noise)
        self.rebuild_list()

    def cycle_kind(self) -> None:
        self.flt = oh.cycle_kind(self.flt)
        self.rebuild_list()

    def cycle_vendor(self) -> None:
        self.flt = oh.cycle_vendor(self.flt)
        self.rebuild_list()

    def cycle_have(self) -> None:
        self.flt = oh.cycle_have(self.flt)
        self.rebuild_list()

    def cycle_compat(self) -> None:
        self.flt = oh.cycle_compat(self.flt)
        self.rebuild_list()

    def cycle_y(self) -> None:
        self.flt = oh.cycle_y(self.flt)
        self.rebuild_list()
        self._notify(f"y={self.flt.y} · kind={self.flt.kind}")

    def query(self) -> None:
        """``/`` — QueryScreen analogue (placeholder · Esc clears if empty)."""
        from .app_api import require_app

        if not require_app(self, what="samples filter"):
            return

        def done(value: str | None) -> None:
            if value is None:
                return  # cancel (Esc with non-empty keeps current filter)
            self.flt = replace(self.flt, query=value.strip())
            self.rebuild_list()

        self.app.ask_prompt(
            "Filter query · basename / tags / user",
            self.flt.query,
            done,
            placeholder="23andMe · Ancestry · username…",
            hint="Enter apply · Esc clear&close if empty else cancel",
            esc_clears_if_empty=True,
        )

    def reload(self) -> None:
        if self.app is not None:
            self.app.show_busy("Open Humans", "Reloading catalog…")
        try:
            self.reload_catalog(force=True)
        finally:
            if self.app is not None:
                self.app.hide_busy()
        self._notify("Catalog reloaded from disk")

    def sync_db(self) -> None:
        job = JOBS.get("openhumans")
        if job and job.active:
            self._notify("Open Humans task already running (see F8)")
            return
        folder = self._folder()
        folder.mkdir(parents=True, exist_ok=True)
        self.store.set_path("openhumans", folder)
        store = self.store
        flt = self.flt
        JOBS.start(
            "openhumans", "OH update new",
            lambda j: ohdb.update_new_items(folder, j, store=store, flt=flt),
        )
        self._notify("Update: API new → download missing → DuckDB")
        self.refresh_status()

    def _enqueue_download(self, files: list[oh.OhFile], *, note: str) -> None:
        job = JOBS.get("openhumans")
        if job and job.active:
            self._notify("Open Humans download already running (see F8)")
            return
        if not files:
            self._notify("Nothing to download")
            return
        folder = self._folder()
        folder.mkdir(parents=True, exist_ok=True)
        self.store.set_path("openhumans", folder)
        store = self.store
        need_n = sum(1 for f in files if f.compat != oh.COMPAT_READY)
        JOBS.start(
            "openhumans", f"OH get · {len(files)}",
            lambda j: oh.download_files(folder, files, j, store=store),
        )
        extra = f" · {need_n} not F1-ready" if need_n else ""
        self._notify(f"{note}{extra} → DuckDB")
        self.refresh_status()

    def download(self) -> None:
        by_id = {f.id: f for f in self.cat.files}
        if self.marked:
            files = [by_id[i] for i in sorted(self.marked) if i in by_id]
            self._enqueue_download(files, note=f"Marked {len(files)}")
            return
        cur = self._current()
        if not cur:
            self._notify("Select a catalog row (or Space-mark several)")
            return
        label = oh.COMPAT_LABEL.get(cur.compat, cur.compat)
        self._enqueue_download([cur], note=f"This file · {label}")

    def open_local(self) -> None:
        f = self._current()
        if not f:
            return
        path = f.local_path(self._folder())
        if not path.is_file():
            self._notify("Not on disk — press D to download this row")
            return
        if f.compat != oh.COMPAT_READY:
            self._notify(
                f"{oh.COMPAT_LABEL.get(f.compat)} — F1 may not parse this; opening anyway",
            )
        if self.app is None:
            return
        # Switch to F1 Genome and activate the local file.
        genome_i = next(
            (i for i, (k, _) in enumerate(self._labels) if k == "genome"),
            0,
        )
        self.app.set_mode(genome_i)
        gws = getattr(self.app, "_screens", {}).get("genome")
        if gws is None:
            gws = getattr(self.app, "genome_ws", None)
        if gws is None:
            self._notify("Genome workspace not ready")
            return
        files = getattr(gws, "files", None)
        if isinstance(files, list) and path not in files:
            files.append(path)
        activate = getattr(gws, "activate", None)
        if callable(activate):
            activate(path)
        else:
            self._notify(f"Opened path · {path.name}")
