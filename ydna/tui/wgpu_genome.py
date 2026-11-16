"""F1 Genome screen for textual-wgpu — files · chroms · summary."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from rich.text import Text

from textual_wgpu.on import on
from textual_wgpu.screen import Screen
from textual_wgpu.widgets import (
    Horizontal,
    KeyBar,
    ModeBar,
    Option,
    OptionList,
    Panel,
    RichText,
    StatusBar,
    TextArea,
    Vertical,
    VerticalScroll,
)

from ..core import genome_db
from ..core.genome import Genome
from ..core.store import Store, data_dir
from ..core.ylink import YLink, link_genome, load_cached_ylink
from ..workspaces.base import MODE_LABELS, fit, short_path
from ..workspaces.genome.screens import GENOME_SUFFIXES
from ..workspaces.genome.summary import (
    STATE_WORD,
    human_size,
    linking_summary,
    onboarding_summary,
    summary_text,
)
from ..workspaces.genome.view import HEAD, INFO
from .wgpu_chrom import WgpuChromView
from .wgpu_jobs import _text_lines

CACHE_SIZE = 3
PROMPT_W = 27
_ACTION_RE = re.compile(r"^screen\.(\w+)\((.*)\)$", re.DOTALL)


class GenomeScreen(Screen):
    """F1 Genome on wgpu — same store/DuckDB/Y-link path as Textual."""

    DEFAULT_CSS = """
    #main { height: 1fr; }
    #files-box { width: 34; border: round $primary; }
    #content-box { width: 1fr; border: round $primary; }
    #sum-box { width: 38%; min-width: 40; border: round $primary; }
    #files-box:focus-within, #content-box:focus-within, #sum-box:focus-within { border: round $accent; }
    #files { height: 1fr; border: none; background: transparent; }
    #chrom-filter { height: 3; margin: 0 1; border: tall $primary; }
    #chrom-filter:focus { border: tall $accent; }
    #colhead { height: 1; padding: 0 1; }
    #chroms { height: 1fr; }
    #summary-scroll { height: 1fr; }
    #summary { padding: 0 1; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """

    BINDINGS = [
        ("o", "open"),
        ("insert", "open"),
        ("delete", "remove"),
        ("x", "remove"),
        ("n", "next_derived"),
        ("/", "focus_filter"),
        ("slash", "focus_filter"),
        ("f", "focus_filter"),
        ("enter", "load_highlighted"),
        ("return", "load_highlighted"),
        ("z", "collapse_all"),
        # Textual Binding("escape", "clear_chrom_filter") — False if empty → Esc falls through.
        ("escape", "clear_chrom_filter"),
    ]

    def __init__(self, store: Store | None = None, extra: list[Path] | None = None) -> None:
        super().__init__()
        self.store = store or Store()
        self.link: YLink | str | None = None
        self.files: list[Path] = []
        self.info: dict[str, dict] = {}
        self.active: Path | None = None
        self.load_path: Path | None = None
        self.phase: str | None = None
        self.genome: Genome | None = None
        self.cache: dict[str, tuple[int, Genome]] = {}
        self._extra = [Path(p).expanduser().resolve() for p in (extra or [])]
        self._labels = list(MODE_LABELS)

    def compose(self):
        from .app_api import make_mode_bar

        yield make_mode_bar(self, "genome", self._labels)
        files = OptionList(
            id="files",
            row_lines=4,
        )
        files_box = Panel(files, title="FILES", id="files-box")

        filt = TextArea(
            "",
            id="chrom-filter",
            placeholder="filter · rsID / position / chrom  (/ or F)",
            single_line=True,
            on_change=self._filter_typed,
            on_submit=self._filter_submit,
        )
        filt.layout_hint_rows = 3
        head = RichText(WgpuChromView.header(), id="colhead", can_focus=False)
        head.layout_hint_rows = 1
        chroms = WgpuChromView(id="chroms")
        chroms.on_moved = self._chrom_moved
        chroms.on_filter_changed = self._filter_changed
        content_inner = Vertical(filt, head, chroms, id="content-inner")
        content_box = Panel(content_inner, title="CHROMOSOMES", id="content-box")

        summary = RichText(id="summary", can_focus=False)
        summary.on_action = self._run_action
        sum_box = Panel(
            VerticalScroll(summary, id="summary-scroll"),
            title="SUMMARY",
            id="sum-box",
        )

        yield Horizontal(files_box, content_box, sum_box, id="main")
        from .app_api import focus_next

        yield KeyBar([
            ("Enter", "Load", lambda: self.load_highlighted()),
            ("O/Ins", "Add", lambda: self.open()),
            ("Del", "Remove", lambda: self.remove()),
            ("/|F", "Filter", lambda: self.focus_filter()),
            ("↑↓", "Nav", None),
            ("←→", "Fold", None),
            ("Space", "Toggle", None),
            ("Z", "Collapse", lambda: self.collapse_all()),
            ("N", "Next Y", lambda: self.next_derived()),
            ("Esc", "Clear filter", lambda: self.clear_chrom_filter()),
            ("Tab", "Panel", lambda: focus_next(self)),
            ("Q", "Quit", lambda: self.app.ask_quit() if self.app else None),
        ])
        yield StatusBar(id="status")

    def on_screen_mount(self) -> None:
        if self.app is not None:
            self.app.show_busy("Genome", self._extra[-1].name if self._extra else "Opening…")
        try:
            self._read_config()
            self._apply_extras()
            self.refresh_files()
            if self.app is not None:
                self.app.set_focus(self.query_one("#files", OptionList))
            if self.active and self.active.exists():
                self.activate(self.active)
            else:
                self.active = None
                try:
                    genome_db.set_active(None)
                except Exception:  # noqa: BLE001
                    pass
                self._show_onboarding()
                self.update_status()
                if self.app is not None:
                    self.app.hide_busy()
        except Exception as exc:  # noqa: BLE001
            self._notify(f"Genome open failed: {exc}")
            if self.app is not None:
                self.app.hide_busy()

    def _notify(self, msg: str) -> None:
        if self.app is not None and hasattr(self.app, "notify"):
            self.app.notify(msg)

    def _read_config(self) -> None:
        try:
            rows = genome_db.list_files()
        except Exception:  # noqa: BLE001
            return
        self.files = [r["path"] for r in rows]
        self.active = next((r["path"] for r in rows if r["active"]), None)
        self.info = {}
        self._hydrate_info_from_db(rows)

    def _apply_extras(self) -> None:
        for p in self._extra:
            if p not in self.files:
                self.files.append(p)
            self.active = p

    def _hydrate_info_from_db(self, rows: list[dict] | None = None) -> None:
        if rows is None:
            try:
                rows = genome_db.list_files()
            except Exception:  # noqa: BLE001
                return
        for row in rows:
            key = str(row["path"])
            label = None
            maj = row.get("build_majority")
            if maj:
                try:
                    builds = genome_db.chrom_builds(row["path"])
                except Exception:  # noqa: BLE001
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
        try:
            for p in self.files:
                genome_db.ensure_listed(p)
            genome_db.set_active(self.active)
        except Exception as exc:  # noqa: BLE001
            self._notify(f"Could not save the file list: {exc}")

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
            line = f"{inf['snps']:,} SNPs · {size}" if inf and inf.get("snps") else size
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
                bit = f"{inf.get('format') or ''}" + (f" · {bl}" if bl else "")
                mixed = "mixed" in (bl or "")
                t.append("  " + fit(bit, PROMPT_W) + "\n",
                         style="#729fcf" if mixed else "#AEA5B8")
        t.append("  " + fit(short_path(p.parent), PROMPT_W), style="#71697A")
        return t

    def refresh_files(self) -> None:
        ol = self.query_one("#files", OptionList)
        keep_id = str(self.active) if self.active else None
        options = [
            Option(p.name, id=str(p), lines=_text_lines(self._prompt(p)), data=p)
            for p in self.files
        ]
        ol.set_options(options, keep_id=keep_id)
        box = self.query_one("#files-box", Panel)
        box.subtitle = f"{len(self.files)} file(s) · O add · Del remove"
        if self.app is not None:
            self.app.request_draw()

    def _show_onboarding(self) -> None:
        self.query_one("#content-box", Panel).subtitle = "O add a genotype file"
        self.query_one("#sum-box", Panel).subtitle = "onboarding"
        self.show_summary(onboarding_summary())

    def _set_panel_titles(self, path: Path | None = None) -> None:
        content = self.query_one("#content-box", Panel)
        summary = self.query_one("#sum-box", Panel)
        if self.phase == "reading" and path:
            content.subtitle = f"parsing {path.name}"
            summary.subtitle = "1/2 parsing RAW → DuckDB"
        elif self.phase == "loading" and path:
            content.subtitle = f"loading {path.name}"
            summary.subtitle = "1/2 DuckDB"
        elif self.phase == "linking" and path:
            content.subtitle = path.name
            summary.subtitle = "2/2 linking Y"
        else:
            content.subtitle = path.name if path else ""
            summary.subtitle = ""

    def activate(self, path: Path) -> None:
        if not path.exists():
            self._notify(f"File not found: {path}")
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
        if self.app is not None:
            self.app.show_busy("Reading file", path.name)
            self.app.run_worker(lambda: self._load(path, mtime), name="genome-load", exclusive=True, group="genome")

    def _load(self, path: Path, mtime: int) -> None:
        def prog(frac: float | None, lines: int) -> None:
            if self.app is not None:
                self.app.call_from_thread(self._progress, path, frac, lines)

        try:
            g = genome_db.ingest(path, prog, self.store, set_active_flag=True)
        except Exception as exc:  # noqa: BLE001
            if self.app is not None:
                self.app.call_from_thread(self._failed, path, exc)
            return
        if self.app is not None:
            self.app.call_from_thread(self._loaded, path, mtime, g)

    def _progress(self, path: Path, frac: float | None, lines: int) -> None:
        if path != self.load_path or self.phase != "reading":
            return
        detail = f"{frac:.0%}" if frac is not None else f"{lines:,} lines"
        self.update_status(detail)
        if self.app is not None:
            self.app.busy_progress(f"Parsing RAW · {detail}", frac=frac)

    def _failed(self, path: Path, exc: Exception) -> None:
        if path != self.load_path and self.phase not in (None, "reading", "loading"):
            pass
        self.load_path = None
        self.phase = None
        if self.app is not None:
            self.app.hide_busy()
        self._set_panel_titles(self.active)
        self.refresh_files()
        self.show_summary(Text(f"Could not read {path.name}:\n{exc}", style="#8BAB4A"))
        self.update_status()
        self._notify(f"{path.name}: {exc}")

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
        self._show(g)

    def _show(self, g: Genome) -> None:
        self.genome = g
        self.link = None
        self._sync_filter_input("")
        self.query_one("#chroms", WgpuChromView).set_genome(g)
        self._set_panel_titles(g.path)
        self.refresh_files()
        try:
            cached = load_cached_ylink(g.path, self.store)
        except Exception:  # noqa: BLE001
            cached = None
        if cached is not None:
            if "Y" in g.chroms and not g.chroms["Y"].calls_loaded:
                try:
                    genome_db.ensure_chrom(g, "Y")
                except Exception:  # noqa: BLE001
                    pass
            self.phase = None
            if self.app is not None:
                self.app.hide_busy()
            self.update_status()
            self._linked(g, cached)
            return
        self.phase = "linking"
        self.show_summary(linking_summary(g))
        self.update_status()
        self.relink()

    def relink(self) -> None:
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
        if self.app is not None:
            self.app.show_busy("Linking Y", g.path.name)
            self.app.busy_progress("Matching named Y markers…", frac=0.05)

        def work() -> None:
            try:
                if "Y" in g.chroms and not g.chroms["Y"].calls_loaded:
                    genome_db.ensure_chrom(g, "Y")
                res: YLink | str = link_genome(g, self.store)
            except Exception as exc:  # noqa: BLE001
                res = f"could not link with the haplotree: {exc}"
            if self.app is not None:
                self.app.call_from_thread(self._linked, g, res)

        if self.app is not None:
            self.app.run_worker(work, name="ylink", exclusive=True, group="ylink")

    def _linked(self, g: Genome, res: YLink | str) -> None:
        if g is not self.genome:
            return
        self.link = res
        self.phase = None
        if self.app is not None:
            self.app.hide_busy()
        self.query_one("#chroms", WgpuChromView).set_link(
            res if isinstance(res, YLink) else None,
        )
        self.show_summary(summary_text(g, res))
        self._set_panel_titles(g.path)
        self.refresh_files()
        self.update_status()

    def show_summary(self, t: Text, *, home: bool = True) -> None:
        self.query_one("#summary", RichText).update(t, home=home)
        if self.app is not None:
            self.app.request_draw()

    def _run_action(self, action: str) -> None:
        """Dispatch Rich ``ydna_click`` actions (e.g. ``screen.goto_snp('Y', 12)``)."""
        m = _ACTION_RE.match((action or "").strip())
        if not m:
            return
        name, args_src = m.group(1), m.group(2).strip()
        fn = getattr(self, name, None)
        if not callable(fn):
            return
        try:
            args = ast.literal_eval(f"({args_src},)") if args_src else ()
        except Exception:  # noqa: BLE001
            return
        if not isinstance(args, tuple):
            args = (args,)
        fn(*args)

    def goto_snp(self, chrom: str, i: int) -> None:
        view = self.query_one("#chroms", WgpuChromView)
        view.goto_snp(chrom, i)
        view.focus()

    def update_status(self, reading_detail: str | None = None) -> None:
        view = self.query_one("#chroms", WgpuChromView)
        g = self.genome
        name = (g.path.name if g else None) or (self.active.name if self.active else "—")
        if self.phase == "reading":
            bit = reading_detail or "…"
            msg = f"ACTIVE: {name}    PARSING RAW {bit}    next: linking Y"
        elif self.phase == "loading":
            msg = f"ACTIVE: {name}    LOADING DUCKDB    next: linking Y"
        elif self.phase == "linking" and g:
            msg = f"ACTIVE: {name}    {g.total:,} SNPs    LINKING Y…"
        elif self.phase == "removing":
            bit = reading_detail or "…"
            msg = f"ACTIVE: {name}    REMOVING INDEX {bit}"
        elif not g:
            msg = f"ACTIVE: {name}    O add a genotype file" if name != "—" else (
                "ACTIVE: —    O add a genotype file"
            )
        else:
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
                    where = (
                        f"    CHR: {cname}    SNP: {c.ids[i]} @ {c.pos[i]:,} = "
                        f"{c.gts[i] or '∅'}    {i + 1:,}/{c.n:,}"
                    )
                    m = view.mark(cname, i)
                    if m:
                        where += (
                            f"    {m.node}: {m.anc}→{m.der} {STATE_WORD[m.state]}"
                            + ("" if m.downloaded else " · not in Discover")
                        )
            filt = (
                f"    filter {view.filter!r} · {view.filter_matches:,}"
                if view.filter else ""
            )
            msg = (
                f"ACTIVE: {g.path.name}    {g.total:,} SNPs{where}{filt}"
                f"    ROWS: {len(view.rows):,}"
            )
        self.query_one("#status", StatusBar).update(msg)
        if self.app is not None:
            self.app.request_draw()

    def _sync_filter_input(self, value: str) -> None:
        inp = self.query_one("#chrom-filter", TextArea)
        if inp.text != value:
            inp.load_text(value)

    def _filter_typed(self, value: str) -> None:
        self.query_one("#chroms", WgpuChromView).set_filter(value)

    def _filter_submit(self) -> None:
        self.query_one("#chroms", WgpuChromView).focus()

    def _filter_changed(self, query: str, matches: int) -> None:
        box = self.query_one("#content-box", Panel)
        if query:
            box.subtitle = f"filter {query!r} · {matches:,} SNP(s)"
        elif self.genome:
            box.subtitle = self.genome.path.name
        if not query:
            self._sync_filter_input("")
        self.update_status()

    def _chrom_moved(self) -> None:
        self.update_status()

    def focus_filter(self) -> None:
        if not self.genome:
            return
        inp = self.query_one("#chrom-filter", TextArea)
        inp.focus()
        inp.select_all()

    def clear_chrom_filter(self) -> bool:
        """Return True if a filter was cleared (consume Esc)."""
        view = self.query_one("#chroms", WgpuChromView)
        inp = self.query_one("#chrom-filter", TextArea)
        if not (view.filter or inp.text):
            return False
        self._sync_filter_input("")
        view.set_filter("")
        view.focus()
        return True

    def _add_path(self, path: Path) -> None:
        path = path.resolve()
        if path not in self.files:
            self.files.append(path)
        self._write_config()
        self.refresh_files()
        ol = self.query_one("#files", OptionList)
        try:
            ol.highlighted = self.files.index(path)
        except ValueError:
            pass
        self.activate(path)

    def open(self) -> None:
        from .app_api import require_app

        if not require_app(self, what="add genotype"):
            return
        genomes = data_dir() / "genomes"
        if self.files and self.files[-1].parent.is_dir():
            start = self.files[-1].parent
        elif genomes.is_dir():
            start = genomes
        else:
            start = Path.cwd()

        def done(path: Path | None) -> None:
            if path is not None:
                self._add_path(path)

        self.app.ask_open_file(
            start,
            done,
            title="Add genotype file",
            subtitle=(
                "23andMe, Argenética, MyHeritage, FTDNA, Big Y CSV, AncestryDNA, VCF; "
                ".txt .csv .tsv .vcf .zip .gz .bz2"
            ),
            suffixes=GENOME_SUFFIXES,
        )

    @on(OptionList.OptionSelected, "#files")
    def _on_file_selected(self, event: OptionList.OptionSelected) -> None:
        opt = event.option
        if opt is None or not isinstance(opt.data, Path):
            return
        path = opt.data
        if path != self.active or (self.genome is None and self.load_path != path):
            self.activate(path)
        else:
            self.query_one("#chroms", WgpuChromView).focus()

    def load_highlighted(self) -> None:
        ol = self.query_one("#files", OptionList)
        if self.files and 0 <= ol.highlighted < len(self.files):
            self.activate(self.files[ol.highlighted])

    def collapse_all(self) -> None:
        self.query_one("#chroms", WgpuChromView).collapse_all()

    def next_derived(self) -> None:
        view = self.query_one("#chroms", WgpuChromView)
        view.next_derived()
        view.focus()

    def remove(self) -> None:
        """Del — only when FILES list is focused (Textual action_remove)."""
        from .app_api import require_app

        if not require_app(self, what="remove genotype"):
            return
        ol = self.query_one("#files", OptionList)
        focused = self.app._focus is ol
        if not focused or not self.files or not (0 <= ol.highlighted < len(self.files)):
            return
        path = self.files[ol.highlighted]
        info = self.info.get(str(path)) or {}
        snps = info.get("snps")
        detail = f"{snps:,} SNPs in the index · " if isinstance(snps, int) else ""
        detail += "RAW file on disk is not deleted"

        def done(ok: bool) -> None:
            if ok:
                self._remove_confirmed(path)

        self.app.ask_confirm(
            f"Remove · {path.name}",
            detail,
            done,
            ok="Remove",
            danger=True,
        )

    def _remove_confirmed(self, path: Path) -> None:
        if path not in self.files:
            return
        if self.load_path == path:
            self.load_path = None
            self.phase = None
        if self.app is not None:
            self.app.hide_busy()
            self.app.show_busy("Removing from index", path.name)
            self.app.busy_progress("Starting…", frac=0.0)
        self.phase = "removing"
        self.load_path = path
        self.update_status()

        def work() -> None:
            def prog(frac: float | None, _lines: int) -> None:
                if self.app is not None:
                    self.app.call_from_thread(self._remove_progress, path, frac)

            try:
                genome_db.remove_file(path, progress=prog)
            except Exception as exc:  # noqa: BLE001
                if self.app is not None:
                    self.app.call_from_thread(self._remove_failed, path, exc)
                return
            if self.app is not None:
                self.app.call_from_thread(self._remove_done, path)

        if self.app is not None:
            self.app.run_worker(work, name="genome-remove", exclusive=True, group="genome")

    def _remove_progress(self, path: Path, frac: float | None) -> None:
        if path != self.load_path or self.phase != "removing":
            return
        detail = f"{frac:.0%}" if frac is not None else "…"
        if self.app is not None:
            self.app.busy_progress(f"Deleting DuckDB rows · {detail}", frac=frac)
        self.update_status(detail)

    def _remove_failed(self, path: Path, exc: Exception) -> None:
        if path != self.load_path or self.phase != "removing":
            return
        self.load_path = None
        self.phase = None
        if self.app is not None:
            self.app.hide_busy()
        self.update_status()
        self._notify(f"Remove failed: {path.name}: {exc}")

    def _remove_done(self, path: Path) -> None:
        if path != self.load_path or self.phase != "removing":
            return
        self.load_path = None
        self.phase = None
        if self.app is not None:
            self.app.hide_busy()
        if path in self.files:
            self.files.remove(path)
        self.cache.pop(str(path), None)
        self.info.pop(str(path), None)
        if path == self.active:
            self.active, self.genome = None, None
            self.link = None
            self.query_one("#chroms", WgpuChromView).set_genome(None)
            self._set_panel_titles(None)
            self._show_onboarding()
        self._write_config()
        self.refresh_files()
        self.update_status()
        self._notify(f"Removed from list: {path.name} (file untouched)")
