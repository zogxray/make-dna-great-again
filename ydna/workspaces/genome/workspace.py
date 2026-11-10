"""Режим Genome: список файлов генотипирования, хромосомы активного файла и общая сводка."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ...core.genome import Genome
from ...core import genome_db
from ...core.store import Store, data_dir
from ...core.ylink import YLink, link_genome, load_cached_ylink
from ..base import (ConfirmScreen, KeyBar, ModeBar, Workspace, alt_keys,
                    busy_progress, fit, hide_busy, short_path, show_busy)
from .screens import OpenFileScreen
from .summary import (STATE_WORD, human_size, linking_summary, onboarding_summary,
                      summary_text)
from .view import HEAD, INFO, ChromView

CACHE_SIZE = 3
PROMPT_W = 27                # ширина строки в списке файлов: #files-box 34 минус рамка, отступы, маркер


class GenomeWorkspace(Workspace):
    mode_key = "genome"
    DEFAULT_CSS = """
    #main { height: 1fr; }
    #files-box { width: 34; border: round $primary; }
    #content-box { width: 1fr; border: round $primary; }
    #sum-box { width: 38%; min-width: 40; border: round $primary; }
    #files-box:focus-within, #content-box:focus-within, #sum-box:focus-within { border: round $accent; }
    #files { height: 1fr; border: none; background: transparent; }
    #chrom-filter {
        height: 3; margin: 0 1; background: transparent;
        border: tall $primary-darken-2;
    }
    #chrom-filter:focus { border: tall $accent; }
    #colhead { height: 1; padding: 0 1; }
    ChromView { height: 1fr; background: transparent; scrollbar-size-vertical: 1; }
    #summary-scroll { height: 1fr; scrollbar-size-vertical: 1; }
    #summary { padding: 0 1; }
    #help { height: 1; padding: 0 1; color: $text-muted; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """
    BINDINGS = [
        Binding(alt_keys("o"), "open", "Open file"),
        Binding("insert", "open", "Open file", show=False),
        Binding("delete", "remove", "Remove from list"),
        Binding(alt_keys("x"), "remove", "Remove from list", show=False),
        Binding(alt_keys("n"), "next_derived", "Next derived Y marker", show=False),
        Binding("slash", "focus_filter", "Filter chromosomes", show=False),
        Binding(alt_keys("f"), "focus_filter", "Filter chromosomes", show=False),
        Binding("escape", "clear_chrom_filter", "Clear filter", show=False),
    ]

    def __init__(self, store: Store, extra: list[Path] | None = None) -> None:
        super().__init__()
        self.store = store
        self.link: YLink | str | None = None
        self.files: list[Path] = []
        self.info: dict[str, dict] = {}
        self.active: Path | None = None
        self.load_path: Path | None = None
        self.phase: str | None = None          # "reading" | "linking" | None
        self.genome: Genome | None = None
        self.cache: dict[str, tuple[int, Genome]] = {}
        self._read_config()
        for p in extra or []:
            p = Path(p).expanduser().resolve()
            if p not in self.files:
                self.files.append(p)
            self.active = p

    # — список файлов (genome.files) —
    def _read_config(self) -> None:
        try:
            rows = genome_db.list_files()
        except Exception:
            return
        self.files = [r["path"] for r in rows]
        self.active = next((r["path"] for r in rows if r["active"]), None)
        self.info = {}
        self._hydrate_info_from_db()

    def _hydrate_info_from_db(self) -> None:
        """Подтянуть snps/format/build_label из DuckDB."""
        try:
            rows = genome_db.list_files()
        except Exception:
            return
        for row in rows:
            key = str(row["path"])
            label = None
            maj = row.get("build_majority")
            if maj:
                try:
                    builds = genome_db.chrom_builds(row["path"])
                except Exception:
                    builds = {}
                mixed = sorted(
                    f"{c}:{b}" for c, b in builds.items()
                    if b in ("37", "38") and b != maj
                )
                label = f"GRCh{maj}" + (f" mixed ({', '.join(mixed)})" if mixed else "")
            prev = self.info.get(key, {})
            self.info[key] = {
                **prev,
                "format": row.get("fmt") or prev.get("format"),
                "snps": row.get("snps") or prev.get("snps"),
                "build": maj or prev.get("build"),
                "build_label": label or prev.get("build_label"),
            }

    def _write_config(self) -> None:
        """Список/active → genome.files."""
        try:
            for p in self.files:
                genome_db.ensure_listed(p)
            genome_db.set_active(self.active)
        except Exception as exc:  # noqa: BLE001
            self.app.notify(f"Could not save the file list: {exc}", severity="warning")

    def _phase_for(self, p: Path) -> str | None:
        if p == self.load_path and self.phase in ("reading", "loading"):
            return self.phase
        if p == self.active and self.phase == "linking" and self.genome and self.genome.path == p:
            return "linking"
        return None

    def _prompt(self, p: Path) -> Text:
        active = p == self.active
        phase = self._phase_for(p)
        t = Text()
        t.append("● " if active else "  ", style="#53A94B")
        t.append(fit(p.name, PROMPT_W) + "\n", style="bold #FBF6F0" if active else "")
        if not p.exists():
            t.append("  file not found\n", style="#8BAB4A")
        else:
            inf = self.info.get(str(p))
            size = human_size(p.stat().st_size)
            line = f"{inf['snps']:,} SNPs · {size}" if inf else size
            if phase == "reading":
                line += " · parsing…"
            elif phase == "loading":
                line += " · DuckDB…"
            elif phase == "linking":
                line += " · linking Y…"
            busy = phase is not None
            t.append("  " + fit(line, PROMPT_W) + "\n", style="#B04A4A" if busy else "#AEA5B8")
            if inf:
                bl = inf.get("build_label") or (
                    f"GRCh{inf['build']}" if inf.get("build") else "")
                bit = f"{inf['format']}" + (f" · {bl}" if bl else "")
                mixed = "mixed" in (bl or "")
                t.append("  " + fit(bit, PROMPT_W) + "\n",
                         style="#D4AD72" if mixed else "#AEA5B8")
        t.append("  " + fit(short_path(p.parent), PROMPT_W), style="#71697A")
        return t

    def refresh_files(self) -> None:
        ol = self.query_one("#files", OptionList)
        keep = ol.highlighted
        ol.clear_options()
        ol.add_options([Option(self._prompt(p), id=str(p)) for p in self.files])
        if self.files:
            if keep is None and self.active in self.files:
                keep = self.files.index(self.active)
            ol.highlighted = min(keep or 0, len(self.files) - 1)
        self.query_one("#files-box").border_subtitle = f"{len(self.files)} file(s) · O add · Del remove"

    # — компоновка —
    def compose(self) -> ComposeResult:
        yield ModeBar(self.mode_key)
        with Horizontal(id="main"):
            with Vertical(id="files-box"):
                yield OptionList(id="files")
            with Vertical(id="content-box"):
                yield Input(placeholder="filter · rsID / position / chrom  (/ or F)",
                            id="chrom-filter")
                yield Static(ChromView.header(), id="colhead")
                yield ChromView(id="chroms")
            with Vertical(id="sum-box"):
                with VerticalScroll(id="summary-scroll"):
                    yield Static(id="summary")
        yield KeyBar([("Enter", "Load file", "screen.load_highlighted"), ("O/Ins", "Add file", "screen.open"),
                      ("Del", "Remove", "screen.remove(True)"), ("/|F", "Filter", "screen.focus_filter"),
                      ("↑↓", "Navigate", None), ("←→", "Fold", None), ("Space", "Toggle", None),
                      ("Z", "Collapse all", "screen.collapse_all"),
                      ("N", "Next derived Y", "screen.next_derived"), ("Esc", "Clear filter", None),
                      ("Tab", "Panel", "app.focus_next"),
                      ("F1–F9", "Modes", None), ("Q", "Quit", "app.quit")], id="help")
        yield Static(id="status")

    def on_mount(self) -> None:
        self.query_one("#files-box").border_title = "FILES"
        self.query_one("#content-box").border_title = "CHROMOSOMES"
        self.query_one("#sum-box").border_title = "SUMMARY"
        self.refresh_files()
        self.query_one("#files", OptionList).focus()
        if self.active and self.active.exists():
            self.activate(self.active)
        else:
            self.active = None
            try:
                genome_db.set_active(None)
            except Exception:
                pass
            self._show_onboarding()
        self.update_status()

    def _show_onboarding(self) -> None:
        self.query_one("#content-box").border_subtitle = "O add a genotype file"
        self.query_one("#sum-box").border_subtitle = "onboarding"
        self.show_summary(onboarding_summary())

    # — загрузка —
    def _set_panel_titles(self, path: Path | None = None) -> None:
        content = self.query_one("#content-box")
        summary = self.query_one("#sum-box")
        if self.phase == "reading" and path:
            content.border_subtitle = f"parsing {path.name}"
            summary.border_subtitle = "1/2 parsing RAW → DuckDB"
        elif self.phase == "loading" and path:
            content.border_subtitle = f"loading {path.name}"
            summary.border_subtitle = "1/2 DuckDB"
        elif self.phase == "linking" and path:
            content.border_subtitle = path.name
            summary.border_subtitle = "2/2 linking Y"
        else:
            content.border_subtitle = path.name if path else ""
            summary.border_subtitle = ""

    def activate(self, path: Path) -> None:
        if not path.exists():
            self.app.notify(f"File not found: {path}", severity="error")
            return
        self.active = path
        self._write_config()
        key, mtime = str(path), path.stat().st_mtime_ns
        hit = self.cache.get(key)
        if hit and hit[0] == mtime:
            self.load_path = None
            self.phase = None
            genome_db.set_active(path)
            g = hit[1]
            self.info[str(path)] = {
                "format": g.fmt, "snps": g.total,
                "build": g.build_detected or g.build,
                "build_label": g.build_label(),
            }
            self._show(g)
            return
        # Fresh in DuckDB: meta shells (+ process cache) — SUMMARY/HEAD without
        # dumping all ~800k calls. ChromView loads a chrom when expanded.
        if genome_db.is_fresh(path):
            try:
                g = genome_db.ensure_meta(path, set_active_flag=True)
            except Exception as exc:  # noqa: BLE001
                self._failed(path, exc)
                return
            self.cache[str(path)] = (mtime, g)
            while len(self.cache) > CACHE_SIZE:
                self.cache.pop(next(iter(self.cache)))
            self.info[str(path)] = {
                "format": g.fmt, "snps": g.total,
                "build": g.build_detected or g.build,
                "build_label": g.build_label(),
            }
            self.load_path = None
            self.phase = None
            self._write_config()
            self._show(g)
            return
        self.load_path = path
        self.phase = "reading"
        self._set_panel_titles(path)
        self.refresh_files()
        self.update_status()
        show_busy(self.app, "Reading file", path.name)
        self.run_worker(lambda: self._load(path, mtime), thread=True, exclusive=True, group="genome")

    def _load(self, path: Path, mtime: int) -> None:
        def prog(frac: float | None, lines: int) -> None:
            self.app.call_from_thread(self._progress, path, frac, lines)
        try:
            g = genome_db.ingest(path, prog, self.store, set_active_flag=True)
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(self._failed, path, exc)
            return
        self.app.call_from_thread(self._loaded, path, mtime, g)

    def _progress(self, path: Path, frac: float | None, lines: int) -> None:
        if path != self.load_path or self.phase != "reading":
            return
        detail = f"{frac:.0%}" if frac is not None else f"{lines:,} lines"
        self.update_status(detail)
        if frac is not None:
            busy_progress(self.app, f"Parsing RAW · {detail}", frac=frac,
                          title="Reading file", subtitle=path.name)
        else:
            busy_progress(self.app, f"Parsing RAW · {detail}",
                          title="Reading file", subtitle=path.name)

    def _failed(self, path: Path, exc: Exception) -> None:
        if path != self.load_path:
            return
        self.load_path = None
        self.phase = None
        hide_busy(self.app)
        self._set_panel_titles(self.active)
        self.refresh_files()
        self.show_summary(Text(f"Could not read {path.name}:\n{exc}", style="#8BAB4A"))
        self.update_status()
        self.app.notify(f"{path.name}: {exc}", severity="error")

    def _loaded(self, path: Path, mtime: int, g: Genome) -> None:
        self.cache[str(path)] = (mtime, g)
        while len(self.cache) > CACHE_SIZE:
            self.cache.pop(next(iter(self.cache)))
        self.info[str(path)] = {
            "format": g.fmt, "snps": g.total,
            "build": g.build_detected or g.build,
            "build_label": g.build_label(),
        }
        if path != self.load_path:
            return
        self.load_path = None
        self._write_config()
        # Модал не гасим: relink() сменит заголовок на Linking Y.
        self._show(g)

    def _show(self, g: Genome) -> None:
        self.genome = g
        self.link = None
        self._sync_filter_input("")
        self.query_one(ChromView).set_genome(g)
        self._set_panel_titles(g.path)
        self.refresh_files()
        # DuckDB hit → skip Linking Y modal entirely.
        try:
            cached = load_cached_ylink(g.path, self.store)
        except Exception:  # noqa: BLE001
            cached = None
        if cached is not None:
            # Derived Y clicks need Y call arrays; load only that chrom.
            if "Y" in g.chroms and not g.chroms["Y"].calls_loaded:
                try:
                    genome_db.ensure_chrom(g, "Y")
                except Exception:  # noqa: BLE001
                    pass
            self.phase = None
            self.update_status()
            self._linked(g, cached)
            return
        self.phase = "linking"
        self.show_summary(linking_summary(g))
        self.update_status()
        self.relink()

    def relink(self) -> None:
        """Сопоставляет именованные Y-маркеры с Haplotree и Discover.

        Fresh genome.y_links → instant. Miss → BusyModal + background link + cache write.
        """
        g = self.genome
        if not g:
            return
        try:
            cached = load_cached_ylink(g.path, self.store)
        except Exception:  # noqa: BLE001
            cached = None
        if cached is not None:
            self.phase = None
            self._linked(g, cached)
            return
        self.phase = "linking"
        self._set_panel_titles(g.path)
        self.refresh_files()
        self.update_status()
        show_busy(self.app, "Linking Y", g.path.name)
        busy_progress(self.app, "Matching named Y markers to the haplotree…", frac=0.05)

        def work() -> None:
            try:
                if "Y" in g.chroms and not g.chroms["Y"].calls_loaded:
                    genome_db.ensure_chrom(g, "Y")
                res: YLink | str = link_genome(g, self.store)
            except Exception as exc:  # noqa: BLE001
                res = f"could not link with the haplotree: {exc}"
            self.app.call_from_thread(self._linked, g, res)
        self.run_worker(work, thread=True, exclusive=True, group="ylink")

    def _linked(self, g: Genome, res: YLink | str) -> None:
        if g is not self.genome:
            return
        self.link = res
        self.phase = None
        hide_busy(self.app)
        self.query_one(ChromView).set_link(res if isinstance(res, YLink) else None)
        self.show_summary(summary_text(g, res), home=False)
        self._set_panel_titles(g.path)
        self.refresh_files()
        self.update_status()

    def show_summary(self, t: Text, home: bool = True) -> None:
        self.query_one("#summary", Static).update(t)
        if home:
            self.query_one("#summary-scroll").scroll_home(animate=False)

    def update_status(self, reading_detail: str | None = None) -> None:
        view = self.query_one(ChromView)
        g = self.genome
        name = (g.path.name if g else None) or (self.active.name if self.active else "—")
        if self.phase == "reading":
            bit = reading_detail or "…"
            self.query_one("#status", Static).update(
                f"ACTIVE: {name}    PARSING RAW {bit}    next: linking Y")
            return
        if self.phase == "loading":
            self.query_one("#status", Static).update(
                f"ACTIVE: {name}    LOADING DUCKDB    next: linking Y")
            return
        if self.phase == "linking" and g:
            self.query_one("#status", Static).update(
                f"ACTIVE: {name}    {g.total:,} SNPs    LINKING Y…")
            return
        if not g:
            self.query_one("#status", Static).update(f"ACTIVE: {name}")
            return
        cur = view.current
        where = ""
        if cur:
            kind, cname, i = cur
            c = g.chroms[cname]
            if kind == HEAD:
                where = f"    CHR: {cname} ({c.n:,} SNPs)"
            elif kind == INFO:
                where = f"    CHR: {cname} · summary"
            else:
                where = (f"    CHR: {cname}    SNP: {c.ids[i]} @ {c.pos[i]:,} = "
                         f"{c.gts[i] or '∅'}    {i + 1:,}/{c.n:,}")
                m = view.mark(cname, i)
                if m:
                    where += f"    {m.node}: {m.anc}→{m.der} {STATE_WORD[m.state]}" + \
                             ("" if m.downloaded else " · not in Discover")
        self.query_one("#status", Static).update(
            f"ACTIVE: {g.path.name}    {g.total:,} SNPs{where}    ROWS: {len(view.rows):,}")

    # — события —
    @on(OptionList.OptionSelected, "#files")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        path = Path(event.option.id)
        if path != self.active or (self.genome is None and self.load_path != path):
            self.activate(path)
        else:
            self.query_one(ChromView).focus()

    @on(ChromView.Moved)
    def _moved(self) -> None:
        self.update_status()

    def _sync_filter_input(self, value: str = "") -> None:
        inp = self.query_one("#chrom-filter", Input)
        if inp.value != value:
            with inp.prevent(Input.Changed):
                inp.value = value

    @on(ChromView.FilterChanged)
    def _filter_changed(self, event: ChromView.FilterChanged) -> None:
        box = self.query_one("#content-box")
        if event.query:
            box.border_subtitle = f"filter {event.query!r} · {event.matches:,} SNP(s)"
        elif self.genome:
            box.border_subtitle = self.genome.path.name
        if not event.query:
            self._sync_filter_input("")
        self.update_status()

    @on(Input.Changed, "#chrom-filter")
    def _filter_typed(self, event: Input.Changed) -> None:
        self.query_one(ChromView).set_filter(event.value)

    @on(Input.Submitted, "#chrom-filter")
    def _filter_submit(self) -> None:
        self.query_one(ChromView).focus()

    def action_focus_filter(self) -> None:
        if not self.genome:
            return
        inp = self.query_one("#chrom-filter", Input)
        inp.focus()
        inp.action_select_all()

    def action_clear_chrom_filter(self) -> None:
        view = self.query_one(ChromView)
        inp = self.query_one("#chrom-filter", Input)
        if not (view.filter or inp.value):
            return
        self._sync_filter_input("")
        view.set_filter("")
        view.focus()

    def action_open(self) -> None:
        genomes = data_dir() / "genomes"
        if self.files and self.files[-1].parent.is_dir():
            start = self.files[-1].parent
        elif genomes.is_dir():
            start = genomes
        else:
            start = Path.cwd()

        def done(path: Path | None) -> None:
            if not path:
                return
            if path not in self.files:
                self.files.append(path)
            self._write_config()
            self.refresh_files()
            self.query_one("#files", OptionList).highlighted = self.files.index(path)
            self.activate(path)
        self.app.push_screen(OpenFileScreen(start), done)

    def action_load_highlighted(self) -> None:
        ol = self.query_one("#files", OptionList)
        if ol.highlighted is not None and self.files:
            self.activate(self.files[ol.highlighted])

    def action_collapse_all(self) -> None:
        self.query_one(ChromView).action_collapse_all()

    def action_goto_snp(self, chrom: str, i: int) -> None:
        view = self.query_one(ChromView)
        view.goto_snp(chrom, i)
        view.focus()

    def action_next_derived(self) -> None:
        view = self.query_one(ChromView)
        view.next_derived()
        view.focus()

    def action_remove(self, anywhere: bool = False) -> None:
        """Del — ConfirmScreen, затем BusyModal + транзакционное удаление индекса."""
        ol = self.query_one("#files", OptionList)
        if not (anywhere or ol.has_focus) or ol.highlighted is None or not self.files:
            return
        path = self.files[ol.highlighted]
        info = self.info.get(str(path)) or {}
        snps = info.get("snps")
        detail = f"{snps:,} SNPs in the index · " if isinstance(snps, int) else ""
        detail += "RAW file on disk is not deleted"

        def done(ok: bool | None) -> None:
            if not ok:
                return
            self._remove_confirmed(path)

        self.app.push_screen(
            ConfirmScreen(
                f"Remove · {path.name}",
                f"{detail}",
                ok="Remove",
                danger=True,
            ),
            done,
        )

    def _remove_confirmed(self, path: Path) -> None:
        if path not in self.files:
            return
        # Прервать чужой лоадер по этому файлу, затем свой Remove-прогресс.
        if self.load_path == path:
            self.load_path = None
            self.phase = None
        hide_busy(self.app)
        self.phase = "removing"
        self.load_path = path
        show_busy(self.app, "Removing from index", path.name)
        busy_progress(self.app, "Starting…", frac=0.0)
        self.run_worker(
            lambda: self._remove_worker(path),
            thread=True,
            exclusive=True,
            group="genome",
        )

    def _remove_worker(self, path: Path) -> None:
        def prog(frac: float | None, _lines: int) -> None:
            self.app.call_from_thread(self._remove_progress, path, frac)

        try:
            genome_db.remove_file(path, progress=prog)
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(self._remove_failed, path, exc)
            return
        self.app.call_from_thread(self._remove_done, path)

    def _remove_progress(self, path: Path, frac: float | None) -> None:
        if path != self.load_path or self.phase != "removing":
            return
        detail = f"{frac:.0%}" if frac is not None else "…"
        busy_progress(
            self.app,
            f"Deleting DuckDB rows · {detail}",
            frac=frac,
            title="Removing from index",
            subtitle=path.name,
        )
        self.update_status(detail)

    def _remove_failed(self, path: Path, exc: Exception) -> None:
        if path != self.load_path or self.phase != "removing":
            return
        self.load_path = None
        self.phase = None
        hide_busy(self.app)
        self.update_status()
        self.app.notify(f"Remove failed: {path.name}: {exc}", severity="error")

    def _remove_done(self, path: Path) -> None:
        if path != self.load_path or self.phase != "removing":
            return
        self.load_path = None
        self.phase = None
        hide_busy(self.app)
        if path in self.files:
            self.files.remove(path)
        self.cache.pop(str(path), None)
        self.info.pop(str(path), None)
        if path == self.active:
            self.active, self.genome = None, None
            self.link = None
            self.query_one(ChromView).set_genome(None)
            self._set_panel_titles(None)
            self._show_onboarding()
        self._write_config()
        self.refresh_files()
        self.update_status()
        self.app.notify(f"Removed from list: {path.name} (the file itself is untouched)")
