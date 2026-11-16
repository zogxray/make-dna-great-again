"""F4 PGS screen for textual-wgpu — models · card · overview/compare."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from rich.text import Text

from textual_wgpu.screen import Screen
from textual_wgpu.widgets import (
    Horizontal,
    Input,
    KeyBar,
    ModeBar,
    Option,
    OptionList,
    Panel,
    RichText,
    StatusBar,
    Vertical,
)

from ..core import genome_db, pgs, pgs_db
from ..core.jobs import JOBS
from ..core.store import Store
from ..core.ylink import active_genome, genome_files
from ..workspaces.base import MODE_LABELS, click
from ..workspaces.data.workspace import job_line
from ..workspaces.pgs.view import (
    BROWSE_GATE_LABELS,
    SORTS,
    card,
    compare_view,
    model_prompt,
    overview,
    theme_header,
)
from .app_api import focus_next
from .wgpu_jobs import _text_lines

JOB = "pgs"
TABS = (("results", "YOUR RESULTS"), ("compare", "COMPARE"))
LIST_W = 36
_ACTION_RE = re.compile(r"^screen\.(\w+)\((.*)\)$", re.DOTALL)


class PgsScreen(Screen):
    """Same F4 PGS logic as Textual, hosted on wgpu widgets."""

    DEFAULT_CSS = """
    #main { height: 1fr; }
    #models-box { width: 46; border: round $primary; }
    #card-box { width: 1fr; border: round $primary; }
    #over-box { width: 34%; min-width: 40; border: round $primary; }
    #models-box:focus-within, #card-box:focus-within, #over-box:focus-within { border: round $accent; }
    #models { height: 1fr; border: none; background: transparent; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """

    LIST_NAMES = {
        "curated": "curated",
        "browse": "all models",
        "hold": "hold / triage",
    }

    BINDINGS = [
        ("slash", "focus_search"),
        ("/", "focus_search"),
        ("g", "score_theme"),
        ("a", "score_all"),
        ("r", "rescore"),
        ("c", "cancel"),
        ("v", "toggle_tab"),
        ("l", "toggle_list"),
        ("f", "toggle_gate"),
        ("b", "next_other"),
        ("s", "sort"),
        ("d", "toggle_big"),
        ("x", "score_other"),
        ("enter", "score_current"),
        ("return", "score_current"),
    ]

    def __init__(self, store: Store | None = None) -> None:
        super().__init__()
        self.store = store or Store()
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
        self.catalog_problem = ""
        self.refreshing = False
        self._load_gen = 0
        self.tab = "results"
        self.other: Path | None = None
        self.other_results: dict[str, pgs.Result] = {}
        self.other_stamp: int | None = None
        self.choices: list[Path] = []
        self.sort_mode = "theme"
        self.only_big = False
        self.search_q = ""
        self.browse_gate = "any"
        self._labels = list(MODE_LABELS)
        self._tick_handle = None
        self._models_box: Panel | None = None
        self._over_box: Panel | None = None

    # ── compose ──────────────────────────────────────────────────────
    def compose(self):
        yield ModeBar(
            "pgs",
            self._labels,
            on_select=lambda i: self.app.set_mode(i) if self.app else None,
        )

        list_tabs = RichText(id="list-tabs")
        list_tabs.layout_hint_h = 40
        list_tabs.on_action = self._run_action
        search = Input(
            "",
            id="model-search",
            placeholder="/ trait · PGS id · theme",
            on_change=self._search_changed,
            on_submit=self._search_submit,
        )
        models = OptionList(
            id="models",
            on_highlight=self._on_highlight,
            on_activate=self._on_activate,
            row_lines=4,
        )
        left_inner = Vertical(list_tabs, search, models, id="models-inner")
        self._models_box = Panel(left_inner, title="MODELS", id="models-box")

        card_w = RichText(id="card")
        card_w.on_action = self._run_action
        card_box = Panel(card_w, title="MODEL", id="card-box")

        over_tabs = RichText(id="over-tabs")
        over_tabs.layout_hint_h = 28
        over_tabs.on_action = self._run_action
        over = RichText(id="over")
        over.on_action = self._run_action
        over_inner = Vertical(over_tabs, over, id="over-inner")
        self._over_box = Panel(over_inner, title="OVERVIEW", id="over-box")  # min-width: 40

        yield Horizontal(self._models_box, card_box, self._over_box, id="main")
        yield KeyBar([
            ("Enter", "Score", lambda: self.score_current()),
            ("G", "Theme", lambda: self.score_theme()),
            ("A", "All", lambda: self.score_all()),
            ("R", "Recompute", lambda: self.rescore()),
            ("L", "List", lambda: self.toggle_list()),
            ("F", "Gate", lambda: self.toggle_gate()),
            ("/", "Search", lambda: self.focus_search()),
            ("Esc", "Clear", lambda: self.clear_search()),
            ("C", "Cancel", lambda: self.cancel()),
            ("V", "Compare", lambda: self.toggle_tab()),
            ("B", "Next B", lambda: self.next_other()),
            ("S", "Sort", lambda: self.sort()),
            ("D", "Big Δ", lambda: self.toggle_big()),
            ("X", "Score B", lambda: self.score_other()),
            ("Tab", "Panel", lambda: focus_next(self)),
            ("F1–F10", "Modes", None),
            ("Q", "Quit", lambda: self.app.ask_quit() if self.app else None),
        ])
        yield StatusBar(id="status")

    def on_screen_mount(self) -> None:
        g = self._active()
        if self.app is not None:
            self.app.show_busy(
                "PGS models",
                f"Results for {g.name}" if g else "Loading models…",
            )
        try:
            self.reload()
            ol = self.query_one("#models", OptionList)
            if self.app is not None:
                self.app.set_focus(ol)
            if self.app is not None and hasattr(self.app, "set_interval"):
                self._tick_handle = self.app.set_interval(0.5, self._tick)
        except Exception as exc:  # noqa: BLE001
            self._notify(f"PGS open failed: {exc}")
            if self.app is not None:
                self.app.hide_busy()

    def on_screen_resume(self) -> None:
        try:
            folder = self._path("pgs_models")
        except Exception:  # noqa: BLE001
            folder = self.folder
        if folder != self.folder or self._active() != self.genome:
            self.reload()
        elif self.pick_other():
            self.refresh_all()

    # ── helpers ──────────────────────────────────────────────────────
    def _notify(self, msg: str) -> None:
        if self.app is not None and hasattr(self.app, "notify"):
            self.app.notify(msg)

    def _path(self, key: str) -> Path | None:
        try:
            return self.store.path(key)
        except Exception:  # noqa: BLE001
            return None

    def _active(self) -> Path | None:
        app = self.app
        if app is not None:
            screens = getattr(app, "_screens", {}) or {}
            gws = screens.get("genome")
            active = getattr(gws, "active", None) if gws is not None else None
            if active is not None and Path(active).exists():
                return Path(active)
        try:
            return active_genome()
        except Exception:  # noqa: BLE001
            return None

    def _genome_ws(self):
        if self.app is None:
            return None
        return getattr(self.app, "_screens", {}).get("genome")

    @property
    def menu_models(self) -> list[pgs.Model]:
        ms = self.models_by_menu.get(self.list_tab, [])
        if self.list_tab != "browse":
            return ms
        allowed = pgs_db.BROWSE_GATES.get(self.browse_gate)
        if allowed is None:
            return ms
        return [m for m in ms if (m.status or "") in allowed]

    @property
    def models(self) -> list[pgs.Model]:
        ms = self.menu_models
        q = self.search_q.strip().lower()
        if not q:
            return ms
        return [
            m for m in ms
            if q in m.trait.lower() or q in m.pgs_id.lower()
            or q in (m.efo or "").lower() or q in (m.theme or "").lower()
            or q in (m.status or "").lower()
        ]

    def _menu_title(self, menu_id: str | None = None) -> str:
        mid = menu_id or self.list_tab
        base = self.LIST_NAMES.get(mid) or mid.replace("_", " ")
        if mid == "browse" and self.browse_gate != "any":
            return f"{base} · {self.browse_gate}"
        return base

    def all_models(self) -> list[pgs.Model]:
        out: dict[str, pgs.Model] = {}
        for ms in self.models_by_menu.values():
            for m in ms:
                out.setdefault(m.pgs_id, m)
        return list(out.values())

    @staticmethod
    def _models_for_menu(menu: dict, *, folder_ok: bool) -> list[pgs.Model]:
        kind = menu.get("kind") or ""
        if not folder_ok and kind in ("browse", "hold"):
            return []
        if kind == "browse":
            return pgs_db.load_browse()
        if kind == "hold":
            return pgs_db.load_hold()
        return pgs_db.load_menu_models(menu["id"])

    # ── data load ────────────────────────────────────────────────────
    def reload(self) -> None:
        self._load_gen += 1
        gen = self._load_gen
        folder = self._path("pgs_models")
        try:
            warm = bool(folder and pgs_db.catalog_fresh(folder) and pgs_db.menus_seeded())
        except Exception:  # noqa: BLE001
            warm = False
        if warm:
            self.refreshing = True
            self._set_panel_subs()
            self.update_status()
            try:
                data = self._reload_data(folder, progress=False)
            except Exception as exc:  # noqa: BLE001
                self.refreshing = False
                self.catalog_problem = str(exc)
                self.fill_list()
                self.refresh_all()
                if self.app is not None:
                    self.app.hide_busy()
                return
            self._reload_done(gen, data)
            return
        self.refreshing = True
        self._set_panel_subs()
        self.update_status()
        g = self._active()
        if self.app is not None:
            self.app.show_busy(
                "PGS models",
                f"Results for {g.name}" if g else "Refreshing catalog…",
            )
            self.app.busy_progress("Refreshing models…", frac=0.05)
            self.app.run_worker(lambda: self._reload_work(gen), name="pgs-load", exclusive=True, group="pgs-load")
        else:
            self._reload_work(gen)

    def _reload_data(self, folder: Path | None, *, progress: bool) -> dict:
        catalog_problem = ""
        menus: list = []
        models_by_menu: dict = {}
        app = self.app
        if progress and app is not None:
            app.call_from_thread(app.busy_progress, "Loading models…", frac=0.35)
        if not folder:
            catalog_problem = (
                "PGS catalog is not set.\n\n"
                "In F2 → PGS models, press D to build it "
                "(needs an active genome in F1), or P to an existing catalog folder."
            )
            try:
                pgs_db.seed_menus()
                menus = pgs_db.list_menus()
                for menu in menus:
                    models_by_menu[menu["id"]] = self._models_for_menu(menu, folder_ok=False)
            except Exception as exc:  # noqa: BLE001
                catalog_problem = f"Cannot seed PGS menus:\n{exc}"
        else:
            try:
                if not pgs_db.catalog_fresh(folder):
                    if progress and app is not None:
                        app.call_from_thread(
                            app.busy_progress, "Syncing catalog.tsv…", frac=0.45,
                        )
                    pgs_db.ensure_catalog(folder)
                pgs_db.seed_menus()
                menus = pgs_db.list_menus()
                for menu in menus:
                    models_by_menu[menu["id"]] = self._models_for_menu(menu, folder_ok=True)
            except OSError as exc:
                catalog_problem = f"Cannot read the PGS catalog:\n{exc}"
            except Exception as exc:  # noqa: BLE001
                catalog_problem = f"Cannot read the PGS catalog:\n{exc}"
            else:
                n = sum(len(v) for v in models_by_menu.values())
                if n == 0:
                    catalog_problem = (
                        f"Catalog folder is empty or has no usable models:\n{folder}"
                    )
        genome = self._active()
        if progress and app is not None:
            app.call_from_thread(app.busy_progress, "Loading results…", frac=0.75)
        results, stamp = {}, None
        if genome:
            try:
                stamp = self._stamp(genome)
                results = pgs.load_results(genome)
            except Exception as exc:  # noqa: BLE001
                catalog_problem = catalog_problem or f"Cannot load results:\n{exc}"
        return {
            "folder": folder,
            "catalog_problem": catalog_problem,
            "menus": menus,
            "models_by_menu": models_by_menu,
            "genome": genome,
            "results": results,
            "stamp": stamp,
        }

    def _reload_work(self, gen: int) -> None:
        folder = self._path("pgs_models")
        data = self._reload_data(folder, progress=True)
        app = self.app
        if app is not None:
            app.call_from_thread(self._reload_done, gen, data)
        else:
            self._reload_done(gen, data)

    def _reload_done(self, gen: int, data: dict) -> None:
        if gen != self._load_gen:
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
        if self.app is not None:
            self.app.hide_busy()
        self.fill_list()
        self.refresh_all()

    @staticmethod
    def _stamp(genome: Path) -> int | None:
        try:
            return pgs.results_stamp(genome)
        except Exception:  # noqa: BLE001
            return None

    def load_results(self) -> None:
        self.results, self.stamp = {}, None
        if self.genome:
            self.stamp = self._stamp(self.genome)
            try:
                self.results = pgs.load_results(self.genome)
            except Exception:  # noqa: BLE001
                self.results = {}

    def load_other(self) -> None:
        self.other_results, self.other_stamp = {}, None
        if self.other:
            self.other_stamp = self._stamp(self.other)
            try:
                self.other_results = pgs.load_results(self.other)
            except Exception:  # noqa: BLE001
                self.other_results = {}

    def pick_other(self) -> bool:
        gws = self._genome_ws()
        try:
            files = (
                [p for p in gws.files if p.is_file()]
                if gws is not None and getattr(gws, "files", None)
                else genome_files()
            )
        except Exception:  # noqa: BLE001
            files = []
        try:
            scored = pgs.scored_genomes()
        except Exception:  # noqa: BLE001
            scored = []
        self.choices = [
            p for p in dict.fromkeys(list(files) + list(scored)) if p != self.genome
        ]
        if self.other in self.choices:
            return False
        scored_here = [p for p in self.choices if self._stamp(p) is not None]
        self.other = (scored_here or self.choices or [None])[0]
        self.load_other()
        return True

    # ── list ─────────────────────────────────────────────────────────
    def _group_key(self, m: pgs.Model) -> str:
        if self.list_tab == "hold":
            return m.status or "unknown"
        return m.theme

    def fill_list(self) -> None:
        try:
            ol = self.query_one("#models", OptionList)
        except KeyError:
            return
        keep = self.current.pgs_id if self.current else None
        self.index = {}
        self.by_id = {m.pgs_id: m for m in self.models}
        options: list[Option] = []
        group = None
        for m in self.models:
            g = self._group_key(m)
            if g != group:
                group = g
                options.append(
                    Option(
                        g,
                        id=f"grp:{g}",
                        disabled=True,
                        lines=_text_lines(self._header(g)),
                    ),
                )
            self.index[m.pgs_id] = len(options)
            options.append(
                Option(
                    m.pgs_id,
                    id=m.pgs_id,
                    lines=_text_lines(model_prompt(m, self.results.get(m.pgs_id), width=LIST_W)),
                ),
            )
        ol.set_options(options, keep_id=keep if keep in self.index else None)
        if self.models and keep not in self.index:
            first = self.models[0].pgs_id
            for i, o in enumerate(ol.options):
                if o.id == first:
                    ol.highlighted = i
                    break
        self._set_panel_subs()
        self._list_tabs()

    def _list_tabs(self) -> None:
        t = Text()
        for menu in self.menus:
            key, label = menu["id"], menu["title"]
            sty = "bold reverse #729fcf" if key == self.list_tab else "#AEA5B8"
            t.append(f" {label} ", style=click(f"screen.set_list_tab({key!r})", sty))
        t.append(" L", style="#8A8294")
        if self.list_tab == "browse":
            t.append("\n")
            for gate, lab in BROWSE_GATE_LABELS:
                sty = "bold reverse #729fcf" if gate == self.browse_gate else "#8A8294"
                t.append(f" {lab} ", style=click(f"screen.set_browse_gate({gate!r})", sty))
            t.append(" F", style="#71697A")
        try:
            tabs = self.query_one("#list-tabs", RichText)
            tabs.on_action = self._run_action
            tabs.update(t)
        except KeyError:
            pass

    def _header(self, group: str) -> Text:
        ms = [m for m in self.models if self._group_key(m) == group]
        done = sum(1 for m in ms if (r := self.results.get(m.pgs_id)) and not r.error)
        return theme_header(group, len(ms), done)

    def refresh_prompts(self) -> None:
        try:
            ol = self.query_one("#models", OptionList)
        except KeyError:
            return
        for m in self.models:
            idx = self.index.get(m.pgs_id)
            if idx is None or idx >= len(ol.options):
                continue
            ol.options[idx].lines = _text_lines(
                model_prompt(m, self.results.get(m.pgs_id), width=LIST_W),
            )
            # header above
            if idx > 0 and ol.options[idx - 1].disabled:
                g = self._group_key(m)
                ol.options[idx - 1].lines = _text_lines(self._header(g))
        if self.app is not None:
            self.app.request_draw()

    @property
    def current(self) -> pgs.Model | None:
        try:
            ol = self.query_one("#models", OptionList)
        except KeyError:
            return None
        if not self.models:
            return None
        opt = ol.highlighted_option
        if opt is None or not opt.id or opt.disabled:
            return None
        return self.by_id.get(opt.id)

    def _on_highlight(self, opt: Option | None) -> None:
        if opt is None or opt.disabled:
            return
        self.refresh_card()
        if self.app is not None:
            self.app.request_draw()

    def _on_activate(self, opt: Option | None) -> None:
        """OptionList click/Enter → score (Textual OptionSelected)."""
        if opt is None or opt.disabled:
            return
        self.score_current()

    def _run_action(self, action: str) -> None:
        """Dispatch Rich ``ydna_click`` actions (``screen.select('PGS…')``, …)."""
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

    def select(self, pgs_id: str) -> None:
        """Jump to a model from overview/compare clicks."""
        if pgs_id not in self.index:
            return
        try:
            ol = self.query_one("#models", OptionList)
        except KeyError:
            return
        ol.highlighted = self.index[pgs_id]
        if self.app is not None:
            self.app.set_focus(ol)
        self.refresh_card()

    # ── paint ────────────────────────────────────────────────────────
    def _panel_label(self) -> str:
        try:
            info = pgs.panel_info()
        except Exception:  # noqa: BLE001
            info = None
        return (
            f"{info['variants']:,} SNPs"
            if info
            else "not built (first run builds it, a few minutes)"
        )

    def _results_label(self) -> str:
        if self.refreshing:
            return "Refreshing models…"
        if self.genome:
            return f"Results for {self.genome.name}"
        return "no genome (F1)"

    def _set_panel_subs(self) -> None:
        which = self._menu_title()
        shown, total = len(self.models), len(self.menu_models)
        count = (
            f"{shown}/{total}"
            if self.search_q.strip() and shown != total
            else str(shown)
        )
        if self._models_box is not None:
            if self.refreshing:
                self._models_box.subtitle = "Refreshing models…"
            elif self.genome:
                self._models_box.subtitle = f"{count} {which} · {self.genome.name}"
            else:
                self._models_box.subtitle = f"{count} {which} · no genome"
        if self._over_box is not None:
            self._over_box.subtitle = self._results_label()

    def refresh_all(self) -> None:
        self._list_tabs()
        self.refresh_card()
        self.refresh_over()
        self.update_status()
        if self.app is not None:
            self.app.request_draw()

    def refresh_over(self) -> None:
        t = Text()
        for key, label in TABS:
            sty = "bold reverse #729fcf" if key == self.tab else "#AEA5B8"
            t.append(f" {label} ", style=click(f"screen.set_over_tab({key!r})", sty))
            t.append(" ")
        t.append("V", style="#8A8294")
        try:
            tabs = self.query_one("#over-tabs", RichText)
            tabs.on_action = self._run_action
            tabs.update(t)
            over = self.query_one("#over", RichText)
            over.on_action = self._run_action
        except KeyError:
            return
        name = self.genome.name if self.genome else None
        if self.refreshing:
            over.update(Text("Refreshing models…", style="#B04A4A"))
        elif self.catalog_problem:
            over.update(Text(self.catalog_problem, style="#B04A4A"))
        elif not self.genome:
            over.update(Text(
                "No active genome.\n\n"
                "Open a genotype file in F1. The PGS catalog is separate — set it in F2 if needed.",
                style="#AEA5B8",
            ))
        elif self.tab == "results":
            over.update(overview(self.models, self.results, name, self._panel_label()))
        else:
            stats = pgs.pair_up(self.models, self.results, self.other_results)
            width = 48
            over.update(compare_view(
                self.models, self.results, self.other_results, name,
                self.other.name if self.other else None, len(self.choices),
                self.sort_mode, self.only_big, stats, pgs.profile_r(stats[0]), width,
            ))
        self._set_panel_subs()

    def refresh_card(self) -> None:
        m = self.current
        if m:
            other = (
                (self.other.name, self.other_results.get(m.pgs_id))
                if self.tab == "compare" and self.other
                else None
            )
            body = card(
                m,
                self.results.get(m.pgs_id),
                self.genome.name if self.genome else None,
                other,
            )
        elif self.refreshing:
            body = Text("Refreshing models…", style="#B04A4A")
        elif self.catalog_problem:
            body = Text(self.catalog_problem, style="#B04A4A")
        elif not self.genome:
            body = Text(
                "No active genome (F1).\nCatalog setup is in F2 → PGS models.",
                style="#AEA5B8",
            )
        elif not self.models:
            body = Text("No models in this list.", style="#AEA5B8")
        else:
            body = Text("Select a model.", style="#AEA5B8")
        try:
            card_w = self.query_one("#card", RichText)
            card_w.on_action = self._run_action
            card_w.update(body)
        except KeyError:
            pass

    def update_status(self) -> None:
        try:
            bar = self.query_one("#status", StatusBar)
        except KeyError:
            return
        if self.refreshing:
            bar.update("Refreshing models…")
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
            ok_b = sum(
                1 for m in active
                if (r := self.other_results.get(m.pgs_id)) and not r.error
            )
            line += f"    B: {self.other.name} ({ok_b})"
        if job and job.running:
            line += f"    ⚙ {job.stage} · {job_line(job)}"
        bar.update(line)

    def _tick(self) -> None:
        if self.app is None or getattr(self.app, "mode_key", None) != "pgs":
            return
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
            self._notify(f"{job.title}: {job.message}")
            self.refresh_all()

    # ── search ───────────────────────────────────────────────────────
    def _search_changed(self, value: str) -> None:
        self.search_q = value
        self.fill_list()
        self._set_panel_subs()
        self.refresh_card()
        self.refresh_over()
        if self.app is not None:
            self.app.request_draw()

    def _search_submit(self) -> None:
        try:
            ol = self.query_one("#models", OptionList)
        except KeyError:
            return
        if self.app is not None:
            self.app.set_focus(ol)

    def focus_search(self) -> None:
        try:
            inp = self.query_one("#model-search", Input)
        except KeyError:
            return
        inp.focus()
        inp.action_select_all()

    def clear_search(self) -> bool:
        """Esc handler: clear search filter. Returns True if something was cleared."""
        try:
            inp = self.query_one("#model-search", Input)
        except KeyError:
            return False
        focused = self.app is not None and self.app._focus is inp
        if not focused and not self.search_q:
            return False
        # Avoid double-fill from value setter → on_change; set quietly then refresh once.
        if inp.value:
            inp.load_text("")
        self.search_q = ""
        self.fill_list()
        self.refresh_all()
        if self.app is not None:
            self.app.set_focus(self.query_one("#models", OptionList))
        return True

    # ── actions ──────────────────────────────────────────────────────
    def _start(self, models: list[pgs.Model], label: str, genome: Path | None = None) -> None:
        if self.catalog_problem:
            self._notify(self.catalog_problem.split("\n")[0])
            return
        gp = genome or self.genome
        if not gp:
            self._notify("No active genome — open a genotype file in F1 first")
            return
        ref = self._path("1000g")
        if not ref or not (ref / "all_hg38.pgen").exists():
            self._notify("1000 Genomes is not ready: set or download it in F2")
            return
        if (job := JOBS.get(JOB)) and job.running:
            self._notify("PGS scoring is already running (C cancels it)")
            return
        if not models:
            self._notify("Nothing to score")
            return
        store, everything = self.store, self.all_models()
        gws = self._genome_ws()
        loaded = None
        if gws is not None and getattr(gws, "genome", None) is not None:
            try:
                if genome_db.same_path(gws.genome, gp):
                    loaded = gws.genome
            except Exception:  # noqa: BLE001
                loaded = None
        JOBS.start(
            JOB,
            f"PGS · {gp.name} · {label}",
            lambda j: pgs.run_models(store, gp, models, everything, j, loaded),
        )
        self.update_status()

    def score_current(self) -> None:
        m = self.current
        if not m:
            return
        r = self.results.get(m.pgs_id)
        if r and not r.error:
            self._notify("Already scored; R recomputes it")
            return
        self._start([m], m.pgs_id)

    def rescore(self) -> None:
        if m := self.current:
            self._start([m], m.pgs_id)

    def score_theme(self) -> None:
        m = self.current
        if not m:
            return
        todo = [
            x for x in self.models
            if x.theme == m.theme
            and (x.pgs_id not in self.results or self.results[x.pgs_id].error)
        ]
        self._start(todo, pgs.THEME_LABEL.get(m.theme, m.theme))

    def score_all(self) -> None:
        todo = [
            x for x in self.models
            if x.pgs_id not in self.results or self.results[x.pgs_id].error
        ]
        self._start(todo, f"{len(todo)} models")

    def cancel(self) -> None:
        job = JOBS.get(JOB)
        if job and job.running:
            job.cancel()
            self._notify("Cancelling PGS scoring (finished models are kept)")
        else:
            self._notify("Nothing is running")

    def set_list_tab(self, key: str) -> None:
        """Click / L cycle — Textual ``action_list_tab``."""
        ids = {m["id"] for m in self.menus}
        if key not in ids or key == self.list_tab:
            return
        self.list_tab = key
        self.fill_list()
        self.refresh_all()
        if self.app is not None:
            self.app.set_focus(self.query_one("#models", OptionList))

    def set_browse_gate(self, key: str) -> None:
        if key not in pgs_db.BROWSE_GATES or key == self.browse_gate:
            return
        self.browse_gate = key
        if self.list_tab != "browse":
            self.list_tab = "browse"
        self.fill_list()
        self.refresh_all()
        if self.app is not None:
            self.app.set_focus(self.query_one("#models", OptionList))

    def toggle_gate(self) -> None:
        keys = [g for g, _ in BROWSE_GATE_LABELS]
        if self.list_tab != "browse":
            self.set_list_tab("browse")
            return
        i = keys.index(self.browse_gate) if self.browse_gate in keys else -1
        self.set_browse_gate(keys[(i + 1) % len(keys)])

    def toggle_list(self) -> None:
        if len(self.menus) < 2:
            return
        ids = [m["id"] for m in self.menus]
        i = ids.index(self.list_tab) if self.list_tab in ids else -1
        self.set_list_tab(ids[(i + 1) % len(ids)])

    def set_over_tab(self, key: str) -> None:
        """Click results/compare chips."""
        if key != self.tab:
            self.tab = key
            self.refresh_all()

    def toggle_tab(self) -> None:
        self.set_over_tab("compare" if self.tab == "results" else "results")

    def _compare(self) -> bool:
        if self.tab == "compare":
            return True
        self.set_over_tab("compare")
        return False

    def next_other(self) -> None:
        if not self._compare() or len(self.choices) < 2:
            return
        i = self.choices.index(self.other) if self.other in self.choices else -1
        self.other = self.choices[(i + 1) % len(self.choices)]
        self.load_other()
        self.refresh_all()

    def sort(self, key: str | None = None) -> None:
        if key is None and not self._compare():
            return
        keys = list(SORTS)
        self.sort_mode = key or keys[(keys.index(self.sort_mode) + 1) % len(keys)]
        self.refresh_over()
        if self.app is not None:
            self.app.request_draw()

    def toggle_big(self) -> None:
        if self._compare():
            self.only_big = not self.only_big
            self.refresh_over()
            if self.app is not None:
                self.app.request_draw()

    def score_other(self) -> None:
        if not self._compare():
            return
        if not self.other:
            self._notify("No genome B: add another genotype file in F1")
            return
        todo = [
            m for m in self.models
            if (r := self.results.get(m.pgs_id)) and not r.error
            and m.pgs_id not in self.other_results
        ]
        self._start(todo, f"B · {self.other.name} · {len(todo)} models", self.other)
