"""Режим Samples (F8): каталог Open Humans — сразу, без промежуточного экрана."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ...core import openhumans as oh
from ...core import openhumans_db as ohdb
from ...core.jobs import JOBS
from ...core.sources import BY_KEY, human_size
from ...core.store import Store
from ..base import (KeyBar, ModeBar, Workspace, alt_keys, click, fit, hide_busy,
                    show_busy)
from ..data.openhumans_screen import QueryScreen

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


class SamplesWorkspace(Workspace):
    """F8 · Open Humans catalog (filter, per-row D, DuckDB)."""

    mode_key = "samples"
    DEFAULT_CSS = """
    #oh-title { height: 1; padding: 0 1; }
    #oh-main { height: 1fr; }
    #oh-list-box { width: 1fr; border: round $primary; }
    #oh-file-box { width: 42; min-width: 36; border: round $primary; }
    #oh-list-box:focus-within, #oh-file-box:focus-within { border: round $accent; }
    #oh-filters { height: 3; padding: 0 1; color: $text-muted; }
    #oh-files { height: 1fr; border: none; background: transparent; }
    #oh-file-scroll { height: 1fr; scrollbar-size-vertical: 1; }
    #oh-file { padding: 0 1; }
    #oh-help { height: 1; padding: 0 1; color: $text-muted; }
    #oh-status { height: 1; padding: 0 1; background: $boost; }
    """

    BINDINGS = [
        Binding("space", "toggle_mark", "Mark", show=False),
        Binding(alt_keys("d"), "download", "Download this / marked"),
        Binding(alt_keys("a"), "mark_ready", "Mark READY"),
        Binding(alt_keys("c"), "clear_marks", "Clear marks"),
        Binding(alt_keys("p"), "preset", "Preset starter"),
        Binding(alt_keys("x"), "toggle_noise", "Noise"),
        Binding(alt_keys("f"), "cycle_kind", "Kind"),
        Binding(alt_keys("w"), "cycle_vendor", "Vendor"),
        Binding(alt_keys("h"), "cycle_have", "Have"),
        Binding(alt_keys("k"), "cycle_compat", "Compat"),
        Binding(alt_keys("y"), "cycle_y", "Y / BigY"),
        Binding("slash", "query", "Query"),
        Binding(alt_keys("r"), "reload", "Reload"),
        Binding(alt_keys("v"), "sync_db", "Update new"),
        Binding("enter", "open_local", "Open in F1", show=False),
    ]

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.cat = oh.OhCatalog()
        self.flt = oh.OhFilters()
        self.shown: list[oh.OhFile] = []
        self.marked: set[int] = set()
        self._building = False
        self._first_paint_done = False

    def _key_items(self) -> list[tuple[str, str, str | None]]:
        return [
            ("↑↓", "Row", None),
            ("D", "Download this", "screen.download"),
            ("Space", "Mark", "screen.toggle_mark"),
            ("V", "Update new", "screen.sync_db"),
            ("A", "Mark READY", "screen.mark_ready"),
            ("P", "Preset", "screen.preset"),
            ("C", "Clear", "screen.clear_marks"),
            ("F/W/H/K/Y", "Filters", None),
            ("X", "Noise", "screen.toggle_noise"),
            ("/", "Query", "screen.query"),
            ("Enter", "F1 if local", "screen.open_local"),
            ("F1–F9", "Modes", None),
            ("Q", "Quit", "app.quit"),
        ]

    def compose(self) -> ComposeResult:
        yield ModeBar(self.mode_key)
        yield Static(
            "[b]Open Humans[/b] · D downloads this row · ● READY / ○ NEED labeled, both OK",
            id="oh-title",
        )
        with Horizontal(id="oh-main"):
            with Vertical(id="oh-list-box"):
                yield Static(id="oh-filters")
                yield OptionList(id="oh-files")
            with Vertical(id="oh-file-box"):
                with VerticalScroll(id="oh-file-scroll"):
                    yield Static(id="oh-file")
        yield KeyBar(self._key_items(), id="oh-help")
        yield Static(id="oh-status")

    def on_mount(self) -> None:
        self.query_one("#oh-list-box").border_title = "CATALOG"
        self.query_one("#oh-file-box").border_title = "FILE"
        # Spinner first frame, then catalog + OptionList (can be large).
        show_busy(self.app, "Samples", "Loading Open Humans…")
        self.call_after_refresh(self._first_paint)

    def _first_paint(self) -> None:
        if self._first_paint_done or not self.is_mounted:
            hide_busy(self.app)
            return
        self._first_paint_done = True
        try:
            folder = self._folder()
            if self.store.path("openhumans") is None:
                self.store.set_path("openhumans", folder)
                folder.mkdir(parents=True, exist_ok=True)
            self.reload_catalog()
            self.query_one("#oh-files", OptionList).focus()
        finally:
            hide_busy(self.app)

    def on_screen_resume(self) -> None:
        if self.is_mounted and self._first_paint_done:
            self.refresh_status()

    def _folder(self) -> Path:
        src = BY_KEY.get("openhumans")
        if src:
            return self.store.path(src.key) or src.suggest(self.store)
        return oh.default_samples_dir()

    def reload_catalog(self, *, force: bool = False) -> None:
        """Load catalog (DuckDB when fresh; jsonl only if stale/forced)."""
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
        ol.clear_options()
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
            style = oh.COMPAT_STYLE.get(f.compat, "#AEA5B8")
            vend = oh.vendor_short(f.vendor).ljust(3)
            comp = _COMPAT_SHORT.get(f.compat, "???? ")
            who = (f.user_name or f.username or "—").strip() or "—"
            who = fit(who, 14).ljust(14)
            fname = fit(f.basename or str(f.id), 32)
            t = Text()
            t.append(f"{mark}{local}{db} ", style="#D4AD72" if f.id in self.marked else "#71697A")
            t.append(f"{glyph}{comp} ", style=style)
            t.append(f"{vend} ", style="#8A8294")
            t.append(f"{who} ", style="#C3BCCC")
            t.append(fname, style="bold #FBF6F0" if f.id in self.marked else "")
            options.append(Option(t, id=f"oh:{f.id}"))
        if options:
            ol.add_options(options)
            idx = 0
            if keep_id is not None:
                for i, f in enumerate(self.shown):
                    if f.id == keep_id:
                        idx = i
                        break
            ol.highlighted = idx
        self._building = False
        people = len({f.user_id for f in self.shown if f.user_id})
        self.query_one("#oh-list-box").border_subtitle = (
            f"{len(self.shown):,} shown · {people:,} people · marked {len(self.marked)}"
        )
        self.refresh_filters()
        self.refresh_file()
        self.refresh_status()

    def refresh_filters(self) -> None:
        t = Text()
        t.append("FILTER  ", style="bold #D4AD72")
        t.append(self.flt.label() + "\n", style="#C3BCCC")
        t.append("  F kind · W vendor · H have · K compat · Y y/bigy · X noise · / query",
                 style="#71697A")
        self.query_one("#oh-filters", Static).update(t)

    def _current(self) -> oh.OhFile | None:
        ol = self.query_one("#oh-files", OptionList)
        opt = ol.highlighted_option
        if not opt or not opt.id:
            return None
        raw = str(opt.id)
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
        t = Text()
        if f is None:
            t.append("No rows match the filter.\n", style="#8A8294")
            t.append("Try Y (y/bigy), F kind=all_geno, or /bigy.", style="#71697A")
            self.query_one("#oh-file", Static).update(t)
            return
        folder = self._folder()
        local = f.is_local(folder)
        glyph = oh.COMPAT_GLYPH.get(f.compat, "?")
        label = oh.COMPAT_LABEL.get(f.compat, f.compat)
        style = oh.COMPAT_STYLE.get(f.compat, "#AEA5B8")
        t.append(f"{f.basename}\n", style="bold #FBF6F0")
        t.append(f"{glyph} {label}", style=f"bold {style}")
        t.append(f"  ·  {f.vendor}  ·  {f.kind}\n", style="#C3BCCC")

        t.append("\nDOWNLOAD\n", style="bold #D4AD72")
        t.append("  [D] ", style=click("screen.download", "bold #D4AD72"))
        if local:
            t.append("re-check / DuckDB this file\n", style=click("screen.download"))
        else:
            t.append("download this genome\n", style=click("screen.download"))
        if f.compat != oh.COMPAT_READY:
            t.append("  (not F1-ready — still allowed)\n", style="#8A8294")

        t.append("\nMETA\n", style="bold #D4AD72")
        for k, v in (
            ("id", str(f.id)),
            ("user", f"{f.username or '—'} · {f.user_name or ''}".strip(" ·")),
            ("source", f.source or "—"),
            ("ext", f.ext or "—"),
            ("tags", f.tags_join or "—"),
        ):
            t.append(f"  {k}: ", style="#AEA5B8")
            t.append(f"{v}\n", style="#C3BCCC")
        if f.description:
            t.append("  desc: ", style="#AEA5B8")
            t.append(f"{f.description}\n", style="#8A8294")

        t.append("\nCOMPAT\n", style="bold #D4AD72")
        if f.compat == oh.COMPAT_READY:
            t.append("  ● SUPPORTED — F1 parses 23andMe / AncestryDNA chip .txt.\n",
                     style="#53A94B")
        elif f.compat == oh.COMPAT_NEED:
            t.append("  ○ NOT YET — FTDNA / Big Y / VCF / zip need more code.\n",
                     style="#D4AD72")
            t.append("  Download is allowed; ingest to genome.* may fail.\n", style="#8A8294")
        elif f.compat == oh.COMPAT_PEEK:
            t.append("  △ UNKNOWN — download and inspect before coding.\n",
                     style="#8BAB4A")
        else:
            t.append("  ✖ SKIP noise — wearable/survey; still downloadable.\n",
                     style="#71697A")

        t.append("\nLOCAL\n", style="bold #D4AD72")
        dest = f.local_path(folder)
        if local:
            size = dest.stat().st_size
            t.append("  ● on disk  ", style="#53A94B")
            t.append(f"{human_size(size)}\n", style="#C3BCCC")
            t.append(f"  {dest}\n", style="#8A8294")
            t.append("\n  Enter → open in F1\n", style=click("screen.open_local", "#D4AD72"))
        else:
            t.append("  ○ not on disk\n", style="#8A8294")
            t.append(f"  will save as {dest.name}\n", style="#71697A")

        t.append("\nDUCKDB\n", style="bold #D4AD72")
        try:
            row = ohdb.get_row(f.id)
        except Exception:  # noqa: BLE001
            row = None
        if row:
            st = str(row.get("db_status") or ohdb.ST_CATALOG)
            t.append(f"  status: {st}", style="#C3BCCC")
            if row.get("snps"):
                t.append(f" · {row['snps']:,} SNPs", style="#53A94B")
            t.append("\n")
            if row.get("genome_file_id") is not None:
                t.append(f"  genome.files id: {row['genome_file_id']}\n", style="#8A8294")
            if row.get("error"):
                t.append(f"  error: {row['error']}\n", style="#B04A4A")
            if st == ohdb.ST_INGESTED:
                t.append("  already in DuckDB\n", style="#53A94B")
        else:
            t.append("  not in DuckDB yet\n", style="#8A8294")

        marked = f.id in self.marked
        t.append("\nMARK\n", style="bold #D4AD72")
        t.append(("  ▸ in multi-download set (Space)\n" if marked
                  else "  · Space adds to multi-download set\n"),
                 style="#D4AD72" if marked else "#8A8294")
        self.query_one("#oh-file", Static).update(t)

    def refresh_status(self) -> None:
        folder = self._folder()
        n_local = len(oh.local_ids(folder))
        job = JOBS.get("openhumans")
        bits = [f"catalog {self.cat.n:,}", f"local {n_local:,}",
                f"marked {len(self.marked)}", f"→ {folder}"]
        try:
            st = ohdb.stats()
            bits.insert(1, f"DB {st['ingested']:,} ingested/{st['local']:,} local")
        except Exception:  # noqa: BLE001
            pass
        if job and job.active:
            bits.append(f"⇣ {job.stage}")
        self.query_one("#oh-status", Static).update(" · ".join(bits))

    @on(OptionList.OptionHighlighted, "#oh-files")
    def _hi(self) -> None:
        if not self._building:
            self.refresh_file()

    def action_toggle_mark(self) -> None:
        f = self._current()
        if not f:
            return
        if f.id in self.marked:
            self.marked.discard(f.id)
        else:
            self.marked.add(f.id)
        self.rebuild_list(keep_id=f.id)

    def action_clear_marks(self) -> None:
        self.marked.clear()
        cur = self._current()
        self.rebuild_list(keep_id=cur.id if cur else None)
        self.app.notify("Marks cleared")

    def action_mark_ready(self) -> None:
        n = 0
        for f in self.shown:
            if f.compat == oh.COMPAT_READY:
                self.marked.add(f.id)
                n += 1
        cur = self._current()
        self.rebuild_list(keep_id=cur.id if cur else None)
        self.app.notify(f"Marked {n} READY in view (NEED still downloadable with D)")

    def action_preset(self) -> None:
        picks = oh.preset_starter(self.cat.files, self._folder())
        for f in picks:
            self.marked.add(f.id)
        cur = self._current()
        self.rebuild_list(keep_id=cur.id if cur else None)
        self.app.notify(f"Preset marked {len(picks)} files (READY + a few NEED)")

    def action_toggle_noise(self) -> None:
        from dataclasses import replace
        self.flt = replace(self.flt, hide_noise=not self.flt.hide_noise)
        self.rebuild_list()

    def action_cycle_kind(self) -> None:
        self.flt = oh.cycle_kind(self.flt)
        self.rebuild_list()

    def action_cycle_vendor(self) -> None:
        self.flt = oh.cycle_vendor(self.flt)
        self.rebuild_list()

    def action_cycle_have(self) -> None:
        self.flt = oh.cycle_have(self.flt)
        self.rebuild_list()

    def action_cycle_compat(self) -> None:
        self.flt = oh.cycle_compat(self.flt)
        self.rebuild_list()

    def action_cycle_y(self) -> None:
        self.flt = oh.cycle_y(self.flt)
        self.rebuild_list()
        self.app.notify(f"y={self.flt.y} · kind={self.flt.kind}", timeout=3)

    def action_query(self) -> None:
        def done(value: str | None) -> None:
            if value is None:
                return
            from dataclasses import replace
            self.flt = replace(self.flt, query=value.strip())
            self.rebuild_list()
        self.app.push_screen(QueryScreen(self.flt.query), done)

    def action_reload(self) -> None:
        self.reload_catalog(force=True)
        self.app.notify("Catalog reloaded from disk")

    def action_sync_db(self) -> None:
        job = JOBS.get("openhumans")
        if job and job.active:
            self.app.notify("Open Humans task already running (see F7)", severity="warning")
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
        self.app.notify(
            "Update: API new items → download only missing (keep existing) → DuckDB",
            timeout=7,
        )
        self.refresh_status()

    def _enqueue_download(self, files: list[oh.OhFile], *, note: str) -> None:
        job = JOBS.get("openhumans")
        if job and job.active:
            self.app.notify("Open Humans download already running (see F7)", severity="warning")
            return
        if not files:
            self.app.notify("Nothing to download", severity="warning")
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
        self.app.notify(f"{note}{extra} → DuckDB", timeout=7)
        self.refresh_status()

    def action_download(self) -> None:
        by_id = {f.id: f for f in self.cat.files}
        if self.marked:
            files = [by_id[i] for i in sorted(self.marked) if i in by_id]
            self._enqueue_download(files, note=f"Marked {len(files)}")
            return
        cur = self._current()
        if not cur:
            self.app.notify("Select a catalog row (or Space-mark several)", severity="warning")
            return
        label = oh.COMPAT_LABEL.get(cur.compat, cur.compat)
        self._enqueue_download([cur], note=f"This file · {label}")

    def action_open_local(self) -> None:
        f = self._current()
        if not f:
            return
        path = f.local_path(self._folder())
        if not path.is_file():
            self.app.notify("Not on disk — press D to download this row", severity="warning")
            return
        if f.compat != oh.COMPAT_READY:
            self.app.notify(
                f"{oh.COMPAT_LABEL.get(f.compat)} — F1 may not parse this; opening anyway",
                severity="warning", timeout=5,
            )
        self.app.action_mode("genome")
        gws = getattr(self.app, "genome_ws", None)
        if gws is None:
            self.app.notify("Genome workspace not ready", severity="error")
            return
        if path not in getattr(gws, "files", []):
            gws.files.append(path)
        gws.activate(path)
