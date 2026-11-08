"""Режим PGS: модели из catalog.tsv, счёт для активного генома F1 и сравнение с 1000 Genomes."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ...core import genome_db, pgs, pgs_db
from ...core.jobs import JOBS
from ...core.store import Store
from ...core.ylink import active_genome, genome_files
from ..base import KeyBar, ModeBar, Workspace, alt_keys, busy_progress, click, hide_busy, show_busy
from ..data.workspace import job_line
from .view import SORTS, card, compare_view, model_prompt, overview, theme_header

JOB = "pgs"
TABS = (("results", "YOUR RESULTS"), ("compare", "COMPARE"))


class PgsWorkspace(Workspace):
    mode_key = "pgs"
    DEFAULT_CSS = """
    #main { height: 1fr; }
    #models-box { width: 46; border: round $primary; }
    #card-box { width: 1fr; border: round $primary; }
    #over-box { width: 34%; min-width: 40; border: round $primary; }
    #models-box:focus-within, #card-box:focus-within, #over-box:focus-within { border: round $accent; }
    #models { height: 1fr; border: none; background: transparent; }
    #list-tabs, #over-tabs { height: 1; padding: 0 1; }
    #card-scroll, #over-scroll { height: 1fr; scrollbar-size-vertical: 1; }
    #card, #over { padding: 0 1; }
    #help { height: 1; padding: 0 1; color: $text-muted; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """
    BINDINGS = [
        Binding(alt_keys("g"), "score_theme", "Score theme"),
        Binding(alt_keys("a"), "score_all", "Score all"),
        Binding(alt_keys("r"), "rescore", "Recompute"),
        Binding(alt_keys("c"), "cancel", "Cancel"),
        Binding(alt_keys("v"), "toggle_tab", "Compare"),
        Binding(alt_keys("l"), "toggle_list", "Next list"),
        Binding(alt_keys("b"), "next_other", "Next B", show=False),
        Binding(alt_keys("s"), "sort", "Sort", show=False),
        Binding(alt_keys("d"), "toggle_big", "Only big Δ", show=False),
        Binding(alt_keys("x"), "score_other", "Score for B", show=False),
    ]

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.folder: Path | None = None
        self.menus: list[dict] = []
        self.models_by_menu: dict[str, list[pgs.Model]] = {}
        self.list_tab = "curated"
        self.by_id: dict[str, pgs.Model] = {}
        self.index: dict[str, int] = {}
        self.genome: Path | None = None
        self.results: dict[str, pgs.Result] = {}
        self.stamp: int | None = None
        self.seen_job: int | None = id(JOBS.get(JOB)) if JOBS.get(JOB) else None
        self.catalog_problem = ""   # каталог не настроен / не читается (не путать с «нет генома»)
        self.refreshing = False
        self._load_gen = 0
        self.tab = "results"
        self.other: Path | None = None
        self.other_results: dict[str, pgs.Result] = {}
        self.other_stamp: int | None = None
        self.choices: list[Path] = []
        self.sort_mode = "theme"
        self.only_big = False

    @property
    def models(self) -> list[pgs.Model]:
        return self.models_by_menu.get(self.list_tab, [])

    def _menu_title(self, menu_id: str | None = None) -> str:
        mid = menu_id or self.list_tab
        for m in self.menus:
            if m["id"] == mid:
                return m["title"]
        return mid.upper() if mid else ""

    def all_models(self) -> list[pgs.Model]:
        """Объединение всех вкладок для панели SNPs (без дублей ID)."""
        out: dict[str, pgs.Model] = {}
        for ms in self.models_by_menu.values():
            for m in ms:
                out.setdefault(m.pgs_id, m)
        return list(out.values())

    # — компоновка —
    def compose(self) -> ComposeResult:
        yield ModeBar(self.mode_key)
        with Horizontal(id="main"):
            with Vertical(id="models-box"):
                yield Static(id="list-tabs")
                yield OptionList(id="models")
            with Vertical(id="card-box"):
                with VerticalScroll(id="card-scroll"):
                    yield Static(id="card")
            with Vertical(id="over-box"):
                yield Static(id="over-tabs")
                with VerticalScroll(id="over-scroll"):
                    yield Static(id="over")
        yield KeyBar([("Enter", "Score", "screen.score_current"), ("G", "Score theme", "screen.score_theme"),
                      ("A", "Score all", "screen.score_all"), ("R", "Recompute", "screen.rescore"),
                      ("L", "List", "screen.toggle_list"),
                      ("C", "Cancel", "screen.cancel"), ("V", "Compare", "screen.toggle_tab"),
                      ("Tab", "Panel", "app.focus_next"),
                      ("F1–F7", "Modes", None), ("Q", "Quit", "app.quit")], id="help")
        yield Static(id="status")

    def on_mount(self) -> None:
        self.query_one("#models-box").border_title = "MODELS"
        self.query_one("#card-box").border_title = "MODEL"
        self.query_one("#over-box").border_title = "OVERVIEW"
        self.reload()
        self.query_one("#models", OptionList).focus()
        self.set_interval(0.5, self._tick)

    def on_screen_resume(self) -> None:
        if self.store.path("pgs_models") != self.folder or self._active() != self.genome:
            self.reload()
        elif self.pick_other():
            self.refresh_all()

    def on_resize(self) -> None:
        self.refresh_over()

    # — данные —
    def _active(self) -> Path | None:
        ws = getattr(self.app, "genome_ws", None)
        if ws and ws.active and ws.active.exists():
            return ws.active
        return active_genome()

    def _results_label(self) -> str:
        if self.refreshing:
            return "Refreshing models…"
        if self.genome:
            return f"Results for {self.genome.name}"
        return "no genome (F1)"

    def _set_panel_subs(self) -> None:
        self.query_one("#over-box").border_subtitle = self._results_label()
        which = self._menu_title().lower()
        if self.refreshing:
            self.query_one("#models-box").border_subtitle = "Refreshing models…"
        elif self.genome:
            self.query_one("#models-box").border_subtitle = f"{len(self.models)} {which} · {self.genome.name}"
        else:
            self.query_one("#models-box").border_subtitle = f"{len(self.models)} {which} · no genome"

    def reload(self) -> None:
        """Каталог + results в фоне; прогресс — общий BusyModal."""
        self._load_gen = getattr(self, "_load_gen", 0) + 1
        gen = self._load_gen
        self.refreshing = True
        self._set_panel_subs()
        self.update_status()
        g = self._active()
        show_busy(self.app, "PGS models",
                  f"Results for {g.name}" if g else "Refreshing catalog…")
        busy_progress(self.app, "Refreshing models…", frac=0.05)
        self.run_worker(lambda: self._reload_work(gen), thread=True,
                        exclusive=True, group="pgs-load")

    def _reload_work(self, gen: int) -> None:
        folder = self.store.path("pgs_models")
        catalog_problem = ""
        menus: list = []
        models_by_menu: dict = {}
        self.app.call_from_thread(busy_progress, self.app, "Loading models…", frac=0.35)
        if not folder:
            catalog_problem = (
                "PGS catalog is not set.\n\n"
                "In F2 → PGS models, point to the build_catalog.py work folder "
                "(needs catalog/catalog.tsv).")
            pgs_db.seed_menus()
            menus = pgs_db.list_menus()
            for menu in menus:
                if menu["kind"] == "catalog":
                    models_by_menu[menu["id"]] = []
                else:
                    models_by_menu[menu["id"]] = pgs_db.load_menu_models(menu["id"])
        else:
            try:
                # TSV→DB только если каталог пуст / устарел; иначе SELECT из DuckDB.
                if not pgs_db.catalog_fresh(folder):
                    self.app.call_from_thread(
                        busy_progress, self.app, "Syncing catalog.tsv…", frac=0.45)
                    pgs_db.ensure_catalog(folder)
                menus = pgs_db.list_menus()
                for menu in menus:
                    if menu["kind"] == "catalog":
                        models_by_menu[menu["id"]] = pgs_db.load_catalog_ok()
                    else:
                        models_by_menu[menu["id"]] = pgs_db.load_menu_models(menu["id"])
            except OSError as exc:
                catalog_problem = f"Cannot read the PGS catalog:\n{exc}"
            else:
                n = sum(len(v) for v in models_by_menu.values())
                if n == 0:
                    catalog_problem = (
                        f"Catalog folder is empty or has no usable models:\n{folder}")
        genome = self._active()
        self.app.call_from_thread(busy_progress, self.app, "Loading results…", frac=0.75)
        results, stamp = {}, None
        if genome:
            stamp = self._stamp(genome)
            results = pgs.load_results(genome)
        self.app.call_from_thread(self._reload_done, gen, {
            "folder": folder, "catalog_problem": catalog_problem,
            "menus": menus, "models_by_menu": models_by_menu,
            "genome": genome, "results": results, "stamp": stamp,
        })

    def _reload_done(self, gen: int, data: dict) -> None:
        if gen != getattr(self, "_load_gen", 0):
            return
        self.folder = data["folder"]
        self.catalog_problem = data["catalog_problem"]
        self.menus = data["menus"]
        self.models_by_menu = data["models_by_menu"]
        if self.list_tab not in self.models_by_menu and self.menus:
            self.list_tab = self.menus[0]["id"]
        self.genome = data["genome"]
        self.results = data["results"]
        self.stamp = data["stamp"]
        self.pick_other()
        self.refreshing = False
        hide_busy(self.app)
        self.fill_list()
        self.refresh_all()

    @staticmethod
    def _stamp(genome: Path) -> int | None:
        return pgs.results_stamp(genome)

    def load_results(self) -> None:
        self.results, self.stamp = {}, None
        if self.genome:
            self.stamp = self._stamp(self.genome)
            self.results = pgs.load_results(self.genome)

    def load_other(self) -> None:
        self.other_results, self.other_stamp = {}, None
        if self.other:
            self.other_stamp = self._stamp(self.other)
            self.other_results = pgs.load_results(self.other)

    def pick_other(self) -> bool:
        """Обновляет список кандидатов в B (файлы F1 и всё, что уже считалось); True, если B сменился."""
        ws = getattr(self.app, "genome_ws", None)
        files = [p for p in ws.files if p.is_file()] if ws else genome_files()
        self.choices = [p for p in dict.fromkeys(files + pgs.scored_genomes()) if p != self.genome]
        if self.other in self.choices:
            return False
        scored = [p for p in self.choices if self._stamp(p) is not None]
        self.other = (scored or self.choices or [None])[0]
        self.load_other()
        return True

    def fill_list(self) -> None:
        ol = self.query_one("#models", OptionList)
        keep = self.current.pgs_id if self.current else None
        ol.clear_options()
        self.index = {}
        self.by_id = {m.pgs_id: m for m in self.models}
        options: list[Option] = []
        theme = None
        for m in self.models:
            if m.theme != theme:
                theme = m.theme
                options.append(Option(self._header(theme), disabled=True))
            self.index[m.pgs_id] = len(options)
            options.append(Option(model_prompt(m, self.results.get(m.pgs_id)), id=m.pgs_id))
        ol.add_options(options)
        if self.models:
            ol.highlighted = self.index.get(keep or "", self.index[self.models[0].pgs_id])
        self._set_panel_subs()
        self._list_tabs()

    def _list_tabs(self) -> None:
        t = Text(no_wrap=True)
        for menu in self.menus:
            key, label = menu["id"], menu["title"]
            n = len(self.models_by_menu.get(key, []))
            sty = "bold reverse #D4AD72" if key == self.list_tab else "#AEA5B8"
            t.append(f" {label} ", style=click(f"screen.list_tab({key!r})", sty))
            t.append(f"{n} ", style="#8A8294")
        t.append("L", style="#8A8294")
        self.query_one("#list-tabs", Static).update(t)

    def _header(self, theme: str) -> Text:
        ms = [m for m in self.models if m.theme == theme]
        done = sum(1 for m in ms if (r := self.results.get(m.pgs_id)) and not r.error)
        return theme_header(theme, len(ms), done)

    def refresh_prompts(self) -> None:
        ol = self.query_one("#models", OptionList)
        theme = None
        for m in self.models:
            if m.theme != theme:
                theme = m.theme
                ol.replace_option_prompt_at_index(self.index[m.pgs_id] - 1, self._header(theme))
            ol.replace_option_prompt_at_index(self.index[m.pgs_id],
                                             model_prompt(m, self.results.get(m.pgs_id)))

    @property
    def current(self) -> pgs.Model | None:
        ol = self.query_one("#models", OptionList)
        if ol.highlighted is None or not self.models:
            return None
        opt = ol.get_option_at_index(ol.highlighted)
        return self.by_id.get(opt.id) if opt.id else None

    # — отрисовка —
    def _panel_label(self) -> str:
        info = pgs.panel_info()
        return f"{info['variants']:,} SNPs" if info else "not built (first run builds it, a few minutes)"

    def refresh_all(self) -> None:
        self._list_tabs()
        self.refresh_card()
        self.refresh_over()
        self.update_status()

    def refresh_over(self) -> None:
        t = Text()
        for key, label in TABS:
            t.append(f" {label} ", style=click(f"screen.tab({key!r})",
                                               "bold reverse #D4AD72" if key == self.tab else "#AEA5B8"))
            t.append(" ")
        t.append("V", style="#8A8294")
        self.query_one("#over-tabs", Static).update(t)
        over = self.query_one("#over", Static)
        name = self.genome.name if self.genome else None
        if self.refreshing:
            over.update(Text("Refreshing models…", style="#B04A4A"))
        elif self.catalog_problem:
            over.update(Text(self.catalog_problem, style="#B04A4A"))
        elif not self.genome:
            over.update(Text(
                "No active genome.\n\n"
                "Open a genotype file in F1. The PGS catalog is separate — set it in F2 if needed.",
                style="#AEA5B8"))
        elif self.tab == "results":
            over.update(overview(self.models, self.results, name, self._panel_label()))
        else:
            stats = pgs.pair_up(self.models, self.results, self.other_results)
            width = max(36, over.content_size.width or 50)
            over.update(compare_view(self.models, self.results, self.other_results, name,
                                     self.other.name if self.other else None, len(self.choices),
                                     self.sort_mode, self.only_big, stats, pgs.profile_r(stats[0]), width))
        self._set_panel_subs()

    def refresh_card(self) -> None:
        m = self.current
        if m:
            other = (self.other.name, self.other_results.get(m.pgs_id)) if self.tab == "compare" and self.other \
                else None
            t = card(m, self.results.get(m.pgs_id), self.genome.name if self.genome else None, other)
        elif self.refreshing:
            t = Text("Refreshing models…", style="#B04A4A")
        elif self.catalog_problem:
            t = Text(self.catalog_problem, style="#B04A4A")
        elif not self.genome:
            t = Text("No active genome (F1).\nCatalog setup is in F2 → PGS models.", style="#AEA5B8")
        elif not self.models:
            t = Text("No models in this list.", style="#AEA5B8")
        else:
            t = Text("Select a model.", style="#AEA5B8")
        self.query_one("#card", Static).update(t)

    def update_status(self) -> None:
        if self.refreshing:
            self.query_one("#status", Static).update("Refreshing models…")
            return
        job = JOBS.get(JOB)
        active = self.models
        ok = sum(1 for m in active if (r := self.results.get(m.pgs_id)) and not r.error)
        which = self._menu_title().lower()
        if self.genome:
            line = f"Results for {self.genome.name}    SCORED: {ok}/{len(active)} ({which})"
        else:
            line = f"GENOME: — (F1)    SCORED: {ok}/{len(active)} ({which})"
        if self.catalog_problem:
            line += "    catalog: not ready (F2)"
        if self.tab == "compare" and self.other:
            ok_b = sum(1 for m in active if (r := self.other_results.get(m.pgs_id)) and not r.error)
            line += f"    B: {self.other.name} ({ok_b})"
        if job and job.running:
            line += f"    ⚙ {job.stage} · {job_line(job)}"
        self.query_one("#status", Static).update(line)

    def _tick(self) -> None:
        job = JOBS.get(JOB)
        if job and job.running:
            self.update_status()
        if self.genome and self._stamp(self.genome) != self.stamp:
            self.load_results()
            self.refresh_prompts()
            self.refresh_all()
        if self.other and self._stamp(self.other) != self.other_stamp:
            self.load_other()
            self.refresh_all()
        if job and not job.running and id(job) != self.seen_job:
            self.seen_job = id(job)
            sev = {"done": "information", "failed": "error"}.get(job.status, "warning")
            self.app.notify(f"{job.title}: {job.message}", severity=sev, timeout=8)
            self.refresh_all()

    # — события —
    @on(OptionList.OptionHighlighted, "#models")
    def _highlighted(self) -> None:
        self.refresh_card()
        self.query_one("#card-scroll").scroll_home(animate=False)

    @on(OptionList.OptionSelected, "#models")
    def _selected(self) -> None:
        self.action_score_current()

    # — действия —
    def _start(self, models: list[pgs.Model], label: str, genome: Path | None = None) -> None:
        if self.catalog_problem:
            self.app.notify(self.catalog_problem.split("\n")[0], severity="warning")
            return
        gp = genome or self.genome
        if not gp:
            self.app.notify("No active genome — open a genotype file in F1 first", severity="warning")
            return
        ref = self.store.path("1000g")
        if not ref or not (ref / "all_hg38.pgen").exists():
            self.app.notify("1000 Genomes is not ready: set or download it in F2", severity="warning")
            return
        if (job := JOBS.get(JOB)) and job.running:
            self.app.notify("PGS scoring is already running (C cancels it)", severity="warning")
            return
        if not models:
            self.app.notify("Nothing to score")
            return
        store, everything = self.store, self.all_models()
        ws = getattr(self.app, "genome_ws", None)
        loaded = ws.genome if ws and genome_db.same_path(ws.genome, gp) else None
        JOBS.start(JOB, f"PGS · {gp.name} · {label}",
                   lambda j: pgs.run_models(store, gp, models, everything, j, loaded))
        self.update_status()

    def action_score_current(self) -> None:
        m = self.current
        if not m:
            return
        r = self.results.get(m.pgs_id)
        if r and not r.error:
            self.app.notify("Already scored; R recomputes it", timeout=3)
            return
        self._start([m], m.pgs_id)

    def action_rescore(self) -> None:
        if m := self.current:
            self._start([m], m.pgs_id)

    def action_score_theme(self) -> None:
        m = self.current
        if not m:
            return
        todo = [x for x in self.models if x.theme == m.theme and (x.pgs_id not in self.results
                                                                 or self.results[x.pgs_id].error)]
        self._start(todo, pgs.THEME_LABEL.get(m.theme, m.theme))

    def action_score_all(self) -> None:
        todo = [x for x in self.models if x.pgs_id not in self.results or self.results[x.pgs_id].error]
        self._start(todo, f"{len(todo)} models")

    def action_cancel(self) -> None:
        job = JOBS.get(JOB)
        if job and job.running:
            job.cancel()
            self.app.notify("Cancelling PGS scoring (finished models are kept)")
        else:
            self.app.notify("Nothing is running")

    def action_select(self, pgs_id: str) -> None:
        if pgs_id in self.index:
            ol = self.query_one("#models", OptionList)
            ol.highlighted = self.index[pgs_id]
            ol.focus()

    def action_list_tab(self, key: str) -> None:
        ids = {m["id"] for m in self.menus}
        if key not in ids or key == self.list_tab:
            return
        self.list_tab = key
        self.fill_list()
        self.refresh_all()
        self.query_one("#models", OptionList).focus()

    def action_toggle_list(self) -> None:
        if len(self.menus) < 2:
            return
        ids = [m["id"] for m in self.menus]
        i = ids.index(self.list_tab) if self.list_tab in ids else -1
        self.action_list_tab(ids[(i + 1) % len(ids)])

    # — сравнение —
    def action_tab(self, key: str) -> None:
        if key != self.tab:
            self.tab = key
            self.refresh_all()
            self.query_one("#over-scroll").scroll_home(animate=False)

    def action_toggle_tab(self) -> None:
        self.action_tab("compare" if self.tab == "results" else "results")

    def _compare(self) -> bool:
        """Клавиши сравнения сначала открывают вкладку COMPARE; True, если она уже была открыта."""
        if self.tab == "compare":
            return True
        self.action_tab("compare")
        return False

    def action_next_other(self) -> None:
        if not self._compare() or len(self.choices) < 2:
            return
        i = self.choices.index(self.other) if self.other in self.choices else -1
        self.other = self.choices[(i + 1) % len(self.choices)]
        self.load_other()
        self.refresh_all()

    def action_sort(self, key: str | None = None) -> None:
        if key is None and not self._compare():
            return
        keys = list(SORTS)
        self.sort_mode = key or keys[(keys.index(self.sort_mode) + 1) % len(keys)]
        self.refresh_over()

    def action_toggle_big(self) -> None:
        if self._compare():
            self.only_big = not self.only_big
            self.refresh_over()

    def action_score_other(self) -> None:
        if not self._compare():
            return
        if not self.other:
            self.app.notify("No genome B: add another genotype file in F1", severity="warning")
            return
        todo = [m for m in self.models if (r := self.results.get(m.pgs_id)) and not r.error
                and m.pgs_id not in self.other_results]
        self._start(todo, f"B · {self.other.name} · {len(todo)} models", self.other)
