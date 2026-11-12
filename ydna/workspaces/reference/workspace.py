"""Режим Reference (F5): AADR по эпохам — список, карта, карточка группы; f3 в фоне."""

from __future__ import annotations

import time
import webbrowser
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

# kg / admix / fstats pull numpy — import lazily on 1000G / Layers / score paths.
from ...core import aadr, chat, genome_db, ref_db
from ...core.jobs import JOBS
from ...core.pgs import SUPERPOPS
from ...core.store import Store
from ...core.ylink import YBranch, YLink, active_genome, link_genome, load_haplotree, our_branch
from ..base import KeyBar, ModeBar, Workspace, alt_keys, busy_progress, fit, hide_busy, show_busy
from ..data.workspace import job_line
from .view import (CARD_TABS, FILTER_TABS, REGIONS, card, card_tab_bar, decide_key, draw_map, draw_y_map,
                   epoch_bar, group_prompt, layers_view, ranks_of, region_bar, region_of,
                   score_filter_panel, seshat_layer_for_groups, sort_tuple, source_bar,
                   summary_prompt, tab_bar, tree_view)


def _kg():
    from ...core import kg
    return kg


def _admix():
    from ...core import admix
    return admix


def _fstats():
    from ...core import fstats
    return fstats

JOB = "aadr-f3"
JOB_KG = "kg-f3"
JOB_PCA = "aadr-pca"
JOB_QPADM = "aadr-qpadm"
JOB_IMPUTE = "aadr-impute"
JOB_PAINT = "kg-paint"
JOB_SUMMARY = "ref-summary"
JOBS_ALL = (JOB, JOB_KG, JOB_PCA, JOB_QPADM, JOB_IMPUTE, JOB_PAINT, JOB_SUMMARY)
TABS = ("map", "y", "tree", "layers")


class ReferenceWorkspace(Workspace):
    mode_key = "reference"
    DEFAULT_CSS = """
    #main { height: 1fr; }
    #left-col { width: 58; height: 1fr; }
    #groups-box { height: 1fr; border: round $primary; }
    #card-box { height: 1fr; border: round $primary; }
    #map-box { width: 1fr; border: round $primary; }
    #groups-box:focus-within, #map-box:focus-within, #card-box:focus-within { border: round $accent; }
    #groups { height: 1fr; border: none; background: transparent; }
    #source, #epochs, #regions, #view-tabs, #card-tabs { height: 1; padding: 0 1; }
    #map-scroll { height: 1fr; overflow-x: hidden; scrollbar-size-vertical: 1; background: #1E1923; }
    #score-filter { height: 6; padding: 0 1; background: #1E1923; }
    #card-scroll { height: 1fr; scrollbar-size-vertical: 1; }
    #map { padding: 0; background: #1E1923; }
    #card { padding: 0 1; }
    #help { height: 1; padding: 0 1; color: $text-muted; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """
    BINDINGS = [
        Binding("left", "epoch_prev", "Epoch −"),
        Binding("right", "epoch_next", "Epoch +"),
        Binding("space", "toggle_play", "Play epochs", show=False),
        Binding(alt_keys("f"), "score", "Scan f3"),
        Binding(alt_keys("y"), "toggle_y", "Y line"),
        Binding(alt_keys("p"), "toggle_people", "People"),
        Binding(alt_keys("u"), "toggle_quest", "Questionable"),
        Binding(alt_keys("w"), "toggle_world", "World map"),
        Binding(alt_keys("r"), "region_next", "Region"),
        Binding(alt_keys("g"), "toggle_source", "AADR/1000G"),
        Binding(alt_keys("v"), "toggle_tab", "Map/Tree/Layers"),
        Binding(alt_keys("c"), "cancel", "Cancel"),
        Binding(alt_keys("x"), "score_clear", "Clear score filter"),
    ]

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.cat: aadr.Catalog | None = None
        self.problem = ""
        self.genome: Path | None = None
        self.results: dict[str, aadr.F3] = {}
        self.people: dict[str, aadr.F3] = {}
        self.ranks: dict[str, float] = {}
        self.people_ranks: dict[str, float] = {}
        self.hits: dict[str, aadr.Hit] = {}
        self.hits_people: dict[str, aadr.Hit] = {}
        self.y_branch: YBranch | None = None
        self.y_ours_other: set[str] = set()
        self.check: dict | None = None
        self.epoch = "all"
        self.y_only = False
        self.people_mode = False
        self.questionable = False
        self.world = True
        self.playing = False
        self.source = "aadr"
        self.area = "all"
        self.tab = "map"
        self.paint: dict | None = None
        self.phase: dict | None = None
        self.kg_people_groups: list[aadr.Group] = []
        self.pca: dict | None = None
        self.qpadm: list | None = None
        self.imputed: dict[str, aadr.F3] = {}
        self.kg_groups: list[aadr.Group] = []
        self.kg_problem = ""
        self.stamp: tuple | None = None
        self.layer_stamp: tuple | None = None
        self.layer_aadr: aadr.Bundle | None = None
        self.layer_aadr_imp: aadr.Bundle | None = None
        self.layer_kg: Any = None
        self.layer_kg_imp: Any = None
        self.seen_job: set[int] = {id(j) for k in JOBS_ALL if (j := JOBS.get(k))}
        self._play = None
        self._meta_by_name: dict[str, dict] = {}
        self.loading_msg: str | None = None
        # False while worker OR post-loader GROUPS/MAP/GROUP paint runs.
        self.ui_ready: bool = True
        self.last_load_s: float = 0.0
        self.last_apply_s: float = 0.0
        self.last_paint_s: float = 0.0
        self.last_fill_list_s: float = 0.0
        self.last_refresh_map_s: float = 0.0
        self.last_refresh_card_s: float = 0.0
        # Score floors + context under the map (None / defaults = off).
        self.score_lo_min: float | None = None   # cut far (low lo)
        self.score_z_min: float | None = None    # cut imprecise Z
        self.score_snp_min: int | None = None    # cut thin coverage
        self.score_n_min: int | None = None      # cut small groups
        self.score_se_max: float | None = None   # cut noisy SE
        self.score_y: str = "all"                # all | line | same
        self.score_coords: bool = False          # only groups with lat/lon
        self.score_bp_max: float | None = None   # cut older than BP
        self.filter_tab: str = "floors"          # floors | tips
        # GROUP card tabs: our | catalog | summary
        self.card_tab: str = "our"
        # name → {text, model, ts}; same lifetime as lore_cache (session)
        self.summary_cache: dict[str, dict] = {}
        self.summary_job_key: str | None = None  # subject currently generating
        self._sel_blink: bool = True  # ◆ pulse on selected map point
        # Ocean-legend focus: 'c:Culture' / 's:Seshat' → DEFCON rings on map.
        self.map_focus: str | None = None
        # Seshat anchors: recompute only when epoch / visible set changes.
        self._seshat_cache_key: tuple | None = None
        self._seshat_cache: list[dict] = []
        # Last GROUPS rows — blink / highlight redraw without re-running visible().
        self._last_vis: list[aadr.Group] | None = None
        # LAYERS panel: skip tick repaint when stamps + job stages unchanged.
        self._layers_shown_sig: tuple | None = None
        # name → kept sample count (invalidated with catalog / Q).
        self._gcount_cache: dict[str, int] = {}
        self._gcount_q: bool | None = None
        # gid → Group for people mode (built once per catalog; Q only filters).
        self._people_by_gid: dict[str, aadr.Group] = {}
        # Blink phases for map/Y: swap Text instead of redrawing scatter.
        self._map_blink_key: tuple | None = None
        self._map_blink_frames: dict[bool, Text] = {}
        # Idle stamp poll: every N ticks (~2s); every tick while jobs active.
        self._stamp_tick: int = 0

        self._load_gen = 0

    def compose(self) -> ComposeResult:
        yield ModeBar(self.mode_key)
        yield Static(id="source")
        yield Static(id="epochs")
        yield Static(id="regions")
        with Horizontal(id="main"):
            with Vertical(id="left-col"):
                with Vertical(id="groups-box"):
                    yield OptionList(id="groups")
                with Vertical(id="card-box"):
                    yield Static(id="card-tabs")
                    with VerticalScroll(id="card-scroll"):
                        yield Static(id="card")
            with Vertical(id="map-box"):
                yield Static(id="view-tabs")
                with VerticalScroll(id="map-scroll"):
                    yield Static(id="map")
                yield Static(id="score-filter")
        yield KeyBar([("←→", "Epoch", "screen.epoch_step(1)"), ("Space", "Play", "screen.toggle_play"),
                      ("F", "Scan f3", "screen.score"), ("G", "AADR/1000G", "screen.toggle_source"),
                      ("V", "View", "screen.toggle_tab"),
                      ("Y", "Y line", "screen.toggle_y"),
                      ("P", "People", "screen.toggle_people"),
                      ("U", "Questionable", "screen.toggle_quest"),
                      ("W", "World/fit", "screen.toggle_world"),
                      ("R", "Region", "screen.region_next"),
                      ("X", "Clear filter", "screen.score_clear()"),
                      ("C", "Cancel", "screen.cancel"),
                      ("Tab", "Panel", "app.focus_next"),
                      ("F1–F9", "Modes", None), ("Q", "Quit", "app.quit")], id="help")
        yield Static(id="status")

    def on_mount(self) -> None:
        self.query_one("#groups-box").border_title = "GROUPS"
        self.query_one("#map-box").border_title = "MAP"
        self.query_one("#card-box").border_title = "GROUP"
        self.query_one("#view-tabs", Static).update(tab_bar(self.tab))
        self.query_one("#card-tabs", Static).update(card_tab_bar(self.card_tab))
        # Shared F6 thread + persisted briefs (ref.summaries ↔ chat.ref-f5).
        try:
            chat.ensure_reference_thread()
            self.summary_cache = ref_db.load_summaries()
        except Exception:
            self.summary_cache = {}
        self.reload()
        self.query_one("#groups", OptionList).focus()
        self.set_interval(0.5, self._tick)
        self.set_interval(0.4, self._sel_blink_tick)
        self._play = self.set_interval(2.4, self._play_tick, pause=True)

    def on_screen_resume(self) -> None:
        folder = self.store.path("aadr")
        folder_changed = folder != (self.cat.folder if self.cat else None)
        genome = self._active()
        if folder_changed or self.cat is None:
            self.reload()
        elif genome != self.genome:
            self.refresh_genome()

    def on_resize(self) -> None:
        self.refresh_map()

    def _active(self) -> Path | None:
        ws = getattr(self.app, "genome_ws", None)
        if ws and ws.active and ws.active.exists():
            return ws.active
        return active_genome()

    def _set_groups_title(self) -> None:
        box = self.query_one("#groups-box")
        if self.loading_msg:
            box.border_title = f"GROUPS · {self.loading_msg}"
        elif self.genome:
            box.border_title = f"GROUPS · for {self.genome.name}"
        else:
            box.border_title = "GROUPS · no genome (F1)"

    def _begin_load(self, msg: str, *, title: str) -> int:
        self._load_gen += 1
        self.ui_ready = False
        self.last_load_s = 0.0
        self.last_apply_s = 0.0
        self.last_paint_s = 0.0
        self.last_fill_list_s = 0.0
        self.last_refresh_map_s = 0.0
        self.last_refresh_card_s = 0.0
        self._last_vis = None
        self._gcount_cache = {}
        self._gcount_q = None
        self._people_by_gid = {}
        self._map_blink_key = None
        self._map_blink_frames = {}
        self.loading_msg = msg
        self.genome = self._active()
        self._set_groups_title()
        self.update_status()
        show_busy(self.app, title, msg)
        busy_progress(self.app, msg, frac=0.02)
        return self._load_gen

    def _finish_paint(self) -> None:
        """GROUPS + MAP + GROUP card — still under BusyModal so bench/user see real cost."""
        busy_progress(self.app, "GROUPS · MAP…", frac=0.92, title="Reference",
                      subtitle="GROUPS · MAP…")
        # Clear loading_msg so refresh_map/card draw for real (BusyModal still up).
        self.loading_msg = None
        self._set_groups_title()
        t0 = time.perf_counter()
        t = time.perf_counter()
        self.fill_list()
        self.last_fill_list_s = time.perf_counter() - t
        # refresh_all minus map/card so we can split timings
        src = source_bar(self.source)
        self.query_one("#source", Static).update(src)
        self.query_one("#epochs", Static).update(
            epoch_bar(self.epoch, self.present_epochs(), self.playing, self._epoch_labels()))
        self.query_one("#regions", Static).update(
            region_bar(self.area, self.present_regions()))
        self.query_one("#view-tabs", Static).update(tab_bar(self.tab))
        title = {"map": "MAP", "y": "Y", "tree": "TREE", "layers": "LAYERS"}.get(self.tab, "MAP")
        self.query_one("#map-box").border_title = title
        t = time.perf_counter()
        self.refresh_map()
        self.last_refresh_map_s = time.perf_counter() - t
        t = time.perf_counter()
        self.refresh_card()
        self.last_refresh_card_s = time.perf_counter() - t
        self.refresh_score_filter()
        self.update_status()
        self.last_paint_s = time.perf_counter() - t0
        hide_busy(self.app)
        self.ui_ready = True

    def reload(self) -> None:
        """Полная перезагрузка каталога + результатов + Y — в фоне, модальный BusyModal."""
        gen = self._begin_load("Loading AADR…", title="Reference")
        self.run_worker(lambda: self._reload_work(gen), thread=True, exclusive=True, group="ref-load")

    def refresh_genome(self) -> None:
        """Смена генома без перечитывания AADR-каталога."""
        g = self._active()
        msg = f"Updating Y for {g.name}…" if g else "Updating…"
        gen = self._begin_load(msg, title="Reference")
        self.run_worker(lambda: self._genome_work(gen), thread=True, exclusive=True, group="ref-load")

    def _reload_work(self, gen: int) -> None:
        t0 = time.perf_counter()
        store = self.store
        source = self.source
        questionable = self.questionable
        genome = self._active()
        folder = store.path("aadr")
        cat = None
        aadr_problem = ""
        if not folder:
            aadr_problem = "Set the AADR folder in F2."
        else:
            try:
                cat = aadr.load_catalog(folder)
            except (OSError, RuntimeError, ValueError) as exc:
                aadr_problem = str(exc)
        # Skip 1000G/numpy until user is on kg (G) — AADR map does not need it.
        kg_groups, kg_people, kg_problem = [], [], ""
        if source == "kg":
            kg = _kg()
            try:
                kg_groups = kg.groups(store)
                kg_people = kg.person_groups(store)
            except (OSError, RuntimeError, ValueError) as exc:
                kg_problem = str(exc)
            if not kg_groups and not kg_problem:
                try:
                    kg.ref_path(store)
                except RuntimeError as exc:
                    kg_problem = str(exc)
        if genome:
            self.app.call_from_thread(self._load_progress, gen,
                                      f"Updating Y for {genome.name}…", 0.45)
        results = self._read_results(genome, source, questionable)
        if genome:
            self.app.call_from_thread(self._load_progress, gen,
                                      f"Updating Y for {genome.name}…", 0.7)
        y = self._compute_y(cat, genome, store)
        problem = aadr_problem if source == "aadr" else (
            kg_problem or ("No 1000 Genomes pops." if not kg_groups else ""))
        load_s = time.perf_counter() - t0
        self.app.call_from_thread(self._apply_reload, gen, {
            "genome": genome, "cat": cat, "problem": problem,
            "kg_groups": kg_groups, "kg_people_groups": kg_people, "kg_problem": kg_problem,
            "load_s": load_s,
            **results, **y,
        })

    def _genome_work(self, gen: int) -> None:
        t0 = time.perf_counter()
        genome = self._active()
        source = self.source
        questionable = self.questionable
        cat = self.cat
        self.app.call_from_thread(self._load_progress, gen,
                                  f"Updating Y for {genome.name}…" if genome else "Updating…", 0.3)
        results = self._read_results(genome, source, questionable)
        self.app.call_from_thread(self._load_progress, gen,
                                  f"Updating Y for {genome.name}…" if genome else "Updating…", 0.65)
        y = self._compute_y(cat, genome, self.store)
        load_s = time.perf_counter() - t0
        self.app.call_from_thread(self._apply_genome, gen, {
            "genome": genome, "load_s": load_s, **results, **y,
        })

    def _load_progress(self, gen: int, msg: str, frac: float = 0.1) -> None:
        if gen != self._load_gen:
            return
        self.loading_msg = msg
        self._set_groups_title()
        self.update_status()
        busy_progress(self.app, msg, frac=frac, title="Reference", subtitle=msg)

    def _apply_reload(self, gen: int, data: dict) -> None:
        if gen != self._load_gen:
            return
        self.last_load_s = float(data.get("load_s") or 0.0)
        try:
            self.genome = data["genome"]
            self.cat = data["cat"]
            self.problem = data["problem"]
            self.kg_groups = data["kg_groups"]
            self.kg_people_groups = data["kg_people_groups"]
            self.kg_problem = data["kg_problem"]
            self._rebuild_gcount_cache()
            self._rebuild_people_cache()
            busy_progress(self.app, "apply · layers…", frac=0.88, title="Reference",
                          subtitle="apply · layers…")
            t = time.perf_counter()
            self._apply_results_y(data)
            self.last_apply_s = time.perf_counter() - t
            self._finish_paint()
        except Exception:
            self.loading_msg = None
            hide_busy(self.app)
            self.ui_ready = True
            raise

    def _apply_genome(self, gen: int, data: dict) -> None:
        if gen != self._load_gen:
            return
        self.last_load_s = float(data.get("load_s") or 0.0)
        try:
            self.genome = data["genome"]
            if self.source == "aadr":
                self.problem = "" if self.cat else "Set the AADR folder in F2."
            else:
                self.problem = self.kg_problem or (
                    "No 1000 Genomes pops." if not self.kg_groups else "")
            busy_progress(self.app, "apply · layers…", frac=0.88, title="Reference",
                          subtitle="apply · layers…")
            t = time.perf_counter()
            self._apply_results_y(data)
            self.last_apply_s = time.perf_counter() - t
            self._finish_paint()
        except Exception:
            self.loading_msg = None
            hide_busy(self.app)
            self.ui_ready = True
            raise

    def _apply_results_y(self, data: dict) -> None:
        self.results = data["results"]
        self.people = data["people"]
        self.ranks = data["ranks"]
        self.people_ranks = data["people_ranks"]
        self.check = data["check"]
        self.pca = data["pca"]
        self.qpadm = data["qpadm"]
        self.imputed = data["imputed"]
        self.paint = data["paint"]
        self.phase = data["phase"]
        self.stamp = data["stamp"]
        self.hits = data["hits"]
        self.hits_people = data["hits_people"]
        self.y_branch = data["y_branch"]
        self.y_ours_other = data["y_ours_other"]
        # LAYERS: не читать aadr/kg бандлы второй раз, если _read_results уже их дал
        self.layer_stamp = None
        self._load_layer_bundles(self.genome, self.questionable,
                                 reuse=data.get("reuse_layers"))

    def _panel_stamp(self, genome: Path | None, source: str, questionable: bool) -> tuple[int, int]:
        if not genome:
            return (0, 0)
        q = questionable if source == "aadr" else False
        aadr_p, kg_p, _ = ref_db.genome_run_stamps(genome, q)
        return aadr_p if source == "aadr" else kg_p

    def _poll_db_stamps(self) -> None:
        """Compare panel + layer stamps in one DuckDB round-trip; reload if changed."""
        genome = self.genome
        if not genome:
            return
        q = self.questionable if self.source == "aadr" else False
        aadr_p, kg_p, layer_stamp = ref_db.genome_run_stamps(genome, q)
        stamp = aadr_p if self.source == "aadr" else kg_p
        if stamp != self.stamp or layer_stamp != self.layer_stamp:
            self.load_results()
            self._refresh_list_views(bars=False)

    def _load_layer_bundles(self, genome: Path | None, questionable: bool,
                            reuse: dict | None = None) -> None:
        """Бандлы для LAYERS — AADR всегда; 1000G/numpy только на Layers или source=kg."""
        if not genome:
            self.layer_aadr = self.layer_aadr_imp = None
            self.layer_kg = self.layer_kg_imp = None
            self.layer_stamp = (0, 0, 0, 0)
            return
        stamp = ref_db.layer_stamps(genome, questionable)
        need_kg = self.tab == "layers" or self.source == "kg"
        if (stamp == self.layer_stamp and self.layer_aadr is not None
                and (not need_kg or self.layer_kg is not None)):
            return
        reuse = reuse or {}
        if self.layer_aadr is None or stamp != self.layer_stamp:
            self.layer_aadr = reuse.get("aadr") or aadr.load_bundle(genome, questionable)
            self.layer_aadr_imp = (reuse.get("aadr_imp")
                                   or aadr.load_bundle(genome, questionable, kind="imp"))
        if need_kg:
            kg = _kg()
            if self.layer_kg is None or stamp != self.layer_stamp:
                self.layer_kg = reuse.get("kg") or kg.load_bundle(genome)
                self.layer_kg_imp = reuse.get("kg_imp") or kg.load_bundle(genome, kind="imp")
        elif stamp != self.layer_stamp:
            self.layer_kg = self.layer_kg_imp = None
        self.layer_stamp = stamp

    def _read_results(self, genome: Path | None, source: str, questionable: bool) -> dict:
        out = {
            "results": {}, "people": {}, "ranks": {}, "people_ranks": {},
            "check": None, "pca": None, "qpadm": None, "imputed": {},
            "paint": None, "phase": None,
            "stamp": self._panel_stamp(genome, source, questionable),
            "reuse_layers": {},
        }
        if not genome:
            return out
        if source == "aadr":
            bundle = aadr.load_bundle(genome, questionable)
            out["results"], out["people"] = bundle.groups, bundle.people
            out["check"], out["pca"], out["qpadm"] = bundle.check, bundle.pca, bundle.qpadm
            imp = aadr.load_bundle(genome, questionable, kind="imp")
            out["imputed"] = imp.groups
            out["reuse_layers"] = {"aadr": bundle, "aadr_imp": imp}
        else:
            kg = _kg()
            bundle = kg.load_bundle(genome)
            out["results"], out["pca"] = bundle.groups, bundle.pca
            out["people"] = bundle.people
            out["paint"] = bundle.paint
            out["phase"] = bundle.phase
            imp = kg.load_bundle(genome, kind="imp")
            out["imputed"] = imp.groups
            if imp.paint:
                out["paint"] = imp.paint
            if imp.phase:
                out["phase"] = imp.phase
            out["reuse_layers"] = {"kg": bundle, "kg_imp": imp}
        out["ranks"] = ranks_of(out["results"])
        out["people_ranks"] = ranks_of(out["people"])
        return out

    def load_results(self) -> None:
        """Синхронно (переключатель Q / тик по stamp) — каталог уже в памяти."""
        data = self._read_results(self.genome, self.source, self.questionable)
        self.results = data["results"]
        self.people = data["people"]
        self.ranks = data["ranks"]
        self.people_ranks = data["people_ranks"]
        self.check = data["check"]
        self.pca = data["pca"]
        self.qpadm = data["qpadm"]
        self.imputed = data["imputed"]
        self.paint = data["paint"]
        self.phase = data["phase"]
        self.stamp = data["stamp"]
        self._load_layer_bundles(self.genome, self.questionable,
                                 reuse=data.get("reuse_layers"))

    def _compute_y(self, cat: aadr.Catalog | None, genome: Path | None, store: Store) -> dict:
        hits: dict[str, aadr.Hit] = {}
        hits_people: dict[str, aadr.Hit] = {}
        y_branch: YBranch | None = None
        y_ours_other: set[str] = set()
        empty = {"hits": hits, "hits_people": hits_people, "y_branch": y_branch,
                 "y_ours_other": y_ours_other}
        if not cat:
            return empty
        tree_path = store.path("ftdna_haplotree")
        disc = store.path("ftdna_discover")
        link = None
        g = None
        ws = getattr(self.app, "genome_ws", None)
        if ws and ws.genome and (not genome or genome_db.same_path(ws.genome, genome)):
            g = ws.genome
            if isinstance(getattr(ws, "link", None), YLink):
                link = ws.link
        # Ветка без haplotree: F3 workspace или tree.branches.
        tws = getattr(self.app, "tree_ws", None)
        if genome and tws and tws.branch and tws.branch_genome == genome:
            y_branch = tws.branch
        elif genome and tree_path:
            try:
                from ...core import tree_db
                y_branch = tree_db.load_branch(genome, tree_path, disc)
            except Exception:
                y_branch = None
        # При cache hit ветки — бейджи из ref.y_badges, без load_haplotree (~2s).
        if genome and y_branch and tree_path:
            try:
                from ...core import tree_db
                cat_key = aadr.catalog_cache_key(cat.folder)
                if cat_key is not None:
                    _gmt, tfp, dfp = tree_db.branch_fps(genome, tree_path, disc)
                    cached = ref_db.load_y_badges(
                        genome, ref_db.catalog_fingerprint(cat_key), tfp, dfp,
                        y_branch.tip, y_branch.path, link,
                    )
                    if cached is not None:
                        return {"hits": cached["hits"], "hits_people": cached["hits_people"],
                                "y_branch": y_branch, "y_ours_other": cached["y_ours_other"]}
            except Exception:
                pass
        tree = load_haplotree(tree_path) if tree_path and Path(tree_path).is_file() else None
        if genome and tree and link is None:
            try:
                g = genome_db.get_y(genome, g, store=store)
                got = link_genome(g, store)
                link = got if isinstance(got, YLink) else None
            except (OSError, ValueError, RuntimeError):
                link = None
        if isinstance(link, YLink) and tree and y_branch is None:
            try:
                from ...core import tree_db
                y_branch = our_branch(link, tree, g, disc)
                if y_branch and tree_path:
                    try:
                        tree_db.save_branch(genome, y_branch, tree_path, disc)
                    except Exception:
                        pass
            except (OSError, ValueError, RuntimeError):
                y_branch = None
        if tree:
            hits, hits_people = aadr.y_hits(cat, tree, link, y_branch)
            extra: set[int] = set()
            if isinstance(link, YLink) and link.derived:
                on_path = set(y_branch.path) if y_branch else set()
                extra = {tree.index[link.marks[i].node]
                         for i in link.derived
                         if link.marks[i].node in tree.index
                         and link.marks[i].node not in on_path}
            if extra:
                for name, h in (*hits.items(), *hits_people.items()):
                    if h.relation != "other":
                        continue
                    n = tree.index.get(h.node)
                    if n is not None and extra & tree.lineage({n}):
                        y_ours_other.add(name)
            if genome and y_branch and tree_path:
                try:
                    from ...core import tree_db
                    cat_key = aadr.catalog_cache_key(cat.folder)
                    if cat_key is not None:
                        _gmt, tfp, dfp = tree_db.branch_fps(genome, tree_path, disc)
                        ref_db.save_y_badges(
                            genome, ref_db.catalog_fingerprint(cat_key), tfp, dfp,
                            y_branch.tip, y_branch.path, link,
                            hits, hits_people, y_ours_other,
                        )
                except Exception:
                    pass
        return {"hits": hits, "hits_people": hits_people, "y_branch": y_branch,
                "y_ours_other": y_ours_other}

    def _on_line(self, name: str) -> bool:
        return name in (self.hits_people if self.people_mode else self.hits)

    def _y_rank(self, name: str) -> int:
        h = self._hit_of(name)
        if not h:
            return 4
        return {"same": 0, "ancestor": 1, "downstream": 2, "other": 3}.get(h.relation, 4)

    def _kept(self, s: aadr.Sample) -> bool:
        return s.keep or (self.questionable and s.questionable)

    def _rebuild_gcount_cache(self) -> None:
        """One pass over the catalog: kept samples per group for current Q flag."""
        self._gcount_cache = {}
        self._gcount_q = self.questionable
        if not self.cat:
            return
        samples = self.cat.samples
        q = self.questionable
        for name, g in self.cat.groups.items():
            if not g.samples:
                self._gcount_cache[name] = g.n
                continue
            n = 0
            for i in g.samples:
                s = samples[i]
                if s.keep or (q and s.questionable):
                    n += 1
            self._gcount_cache[name] = n

    def _rebuild_people_cache(self) -> None:
        """gid → as_group(sample); once per catalog (Q only filters later)."""
        self._people_by_gid = {}
        if not self.cat:
            return
        self._people_by_gid = {
            s.gid: aadr.as_group(s) for s in self.cat.samples
        }

    def _person_group(self, s: aadr.Sample) -> aadr.Group:
        g = self._people_by_gid.get(s.gid)
        if g is not None:
            return g
        g = aadr.as_group(s)
        self._people_by_gid[s.gid] = g
        return g

    def _gcount(self, g: aadr.Group) -> int:
        if not self.cat or not g.samples:
            return g.n
        if self._gcount_q != self.questionable or not self._gcount_cache:
            self._rebuild_gcount_cache()
        return self._gcount_cache.get(g.name, g.n)

    def _scores(self) -> dict[str, aadr.F3]:
        return self.people if self.people_mode else self.results

    def _ranks(self) -> dict[str, float]:
        cached = self.people_ranks if self.people_mode else self.ranks
        if cached:
            return cached
        return ranks_of(self._scores())

    def _hit_of(self, name: str) -> aadr.Hit | None:
        return (self.hits_people if self.people_mode else self.hits).get(name)

    def _in_region(self, g: aadr.Group) -> bool:
        if self.area == "all":
            return True
        return region_of(g.lat, g.lon) == self.area

    def _filter_group(self, name: str) -> aadr.Group | None:
        if self.source == "kg":
            pool = self.kg_people_groups if self.people_mode else self.kg_groups
            return next((g for g in pool if g.name == name), None)
        if not self.cat:
            return None
        if self.people_mode:
            g = self._people_by_gid.get(name)
            if g is not None:
                return g
            i = self.cat.by_gid.get(name)
            if i is None:
                return None
            return self._person_group(self.cat.samples[i])
        return self.cat.groups.get(name)

    def _passes_score_filter(self, name: str, scores: dict[str, aadr.F3]) -> bool:
        """Floors + context under the map."""
        need_score = (self.score_lo_min is not None or self.score_z_min is not None
                      or self.score_snp_min is not None or self.score_n_min is not None
                      or self.score_se_max is not None)
        need_ctx = (self.score_y != "all" or self.score_coords
                    or self.score_bp_max is not None)
        if not need_score and not need_ctx:
            return True
        r = scores.get(name)
        if need_score:
            if not r or not r.ok:
                return False
            lo = r.est - 2 * r.se
            if self.score_lo_min is not None and lo < self.score_lo_min:
                return False
            if self.score_z_min is not None and r.z < self.score_z_min:
                return False
            if self.score_snp_min is not None and r.snps < self.score_snp_min:
                return False
            if self.score_n_min is not None and r.n < self.score_n_min:
                return False
            if self.score_se_max is not None and r.se > self.score_se_max:
                return False
        if self.score_y != "all":
            hit = self._hit_of(name)
            if self.score_y == "same":
                if not hit or hit.relation != "same":
                    return False
            elif self.score_y == "line":
                if not hit or hit.relation not in aadr.ON_BRANCH:
                    return False
        if self.score_coords or self.score_bp_max is not None:
            g = self._filter_group(name)
            if self.score_coords:
                if not g or g.lat is None or g.lon is None or (g.lat == 0 and g.lon == 0):
                    return False
            if self.score_bp_max is not None:
                if not g or g.bp is None or g.bp > self.score_bp_max:
                    return False
        return True

    def _row_key(self, g: aadr.Group, scores: dict[str, aadr.F3]) -> tuple:
        """Один ряд: lo = est − 2·se, потом Z, SNP, Y."""
        r = scores.get(g.name)
        yr = self._y_rank(g.name)
        n = self._gcount(g)
        if r and r.ok:
            return (0, -(r.est - 2 * r.se), -r.z, -r.snps, yr, g.name)
        if r:
            return (1, -r.snps, yr, g.name)
        return (2, 0 if self._on_line(g.name) else 1, yr, -n, g.name)

    def visible(self) -> list[aadr.Group]:
        if self.source == "kg":
            scores = self._scores()
            pool = self.kg_people_groups if self.people_mode else self.kg_groups
            rows = [g for g in pool
                    if (self.epoch == "all" or g.epoch == self.epoch)
                    and self._in_region(g)
                    and self._passes_score_filter(g.name, scores)]
            rows.sort(key=lambda g: self._row_key(g, scores))
            if (self.epoch == "all" and self.area == "all" and self.people_mode
                    and len(rows) > 250):
                rows = self._cap_rows(rows, scores)
            return rows
        if not self.cat:
            return []
        scores = self._scores()
        rows: list[aadr.Group] = []
        if self.people_mode:
            if not self._people_by_gid:
                self._rebuild_people_cache()
            for s in self.cat.samples:
                if not self._kept(s) or s.group == self.cat.outgroup:
                    continue
                g = self._person_group(s)
                if self.epoch != "all" and g.epoch != self.epoch:
                    continue
                if not self._in_region(g):
                    continue
                if self.y_only and not self._on_line(s.gid):
                    continue
                if self.y_only or s.gid in self.hits_people or s.lat is not None or s.gid in self.people:
                    if self._passes_score_filter(s.gid, scores):
                        rows.append(g)
        else:
            for g in self.cat.groups.values():
                if self._gcount(g) <= 0:
                    continue
                if self.epoch != "all" and g.epoch != self.epoch:
                    continue
                if not self._in_region(g):
                    continue
                if self.y_only and not self._on_line(g.name):
                    continue
                if self.y_only or g.name in self.hits or g.lat is not None or g.name in self.results:
                    if self._passes_score_filter(g.name, scores):
                        rows.append(g)

        rows.sort(key=lambda g: self._row_key(g, scores))
        if (self.epoch == "all" and self.area == "all" and not self.y_only
                and len(rows) > 250):
            rows = self._cap_rows(rows, scores)
        return rows

    def _cap_rows(self, rows: list[aadr.Group], scores: dict[str, aadr.F3]) -> list[aadr.Group]:
        """Первые 200 по lo, плюс Y-линия и хвост unscored — не больше 250."""
        # Compare by name — `g in list` uses Group dataclass __eq__ (~n² field scans).
        ok = [g for g in rows if (r := scores.get(g.name)) and r.ok]
        ok_names = {g.name for g in ok}
        other = [g for g in rows if g.name not in ok_names]
        out: list[aadr.Group] = []
        seen: set[str] = set()
        for g in ok[:200]:
            out.append(g)
            seen.add(g.name)
        extra_y = [g for g in rows if self._on_line(g.name) and g.name not in seen]
        extra_y.sort(key=lambda g: self._y_rank(g.name))
        for g in extra_y:
            out.append(g)
            seen.add(g.name)
        for g in other:
            if g.name not in seen:
                out.append(g)
                seen.add(g.name)
            if len(out) >= 250:
                break
        return out[:250]

    def _list_meta(self, rows: list[aadr.Group]) -> list[dict]:
        scores = self._scores()
        meta = []
        prev_ok: tuple | None = None
        place = 0
        ok_total = sum(1 for g in rows if (r := scores.get(g.name)) and r.ok)
        seen_out = False
        for g in rows:
            r = scores.get(g.name)
            hit = self._hit_of(g.name)
            cur = sort_tuple(g, r, hit)
            decide = decide_key(prev_ok if (r and r.ok) else None, cur, r)
            outside = not (r and r.ok)
            head = outside and not seen_out
            if outside:
                seen_out = True
            if r and r.ok:
                place += 1
                prev_ok = cur
                pl = place
            else:
                pl = None
            meta.append({"g": g, "r": r, "hit": hit, "decide": decide,
                         "place": pl, "ok_total": ok_total, "outside_head": head})
        return meta

    def _sel_blink_tick(self) -> None:
        if self.loading_msg or self.tab not in ("map", "y"):
            return
        if not self.current:
            return
        self._sel_blink = not self._sel_blink
        cached = self._map_blink_frames.get(self._sel_blink)
        key = self._map_blink_key
        if cached is not None and key is not None:
            # key: (tab, source, epoch, world, people, focus, sel, cols, rows, …)
            box = self.query_one("#map-box")
            inner = self.query_one("#map-scroll")
            w = inner.size.width or box.size.width
            h = inner.size.height or box.size.height
            cols = max(40, (w or 80) - 6)
            rows_n = max(12, (h or 24) - 4)
            sel = self.current.name if self.current else None
            if (key[0] == self.tab and key[7] == cols and key[8] == rows_n
                    and key[6] == sel):
                self.query_one("#map", Static).update(cached)
                return
        # First off-phase (or size/selection change): draw once and keep the frame.
        self.refresh_map(vis=self._last_vis)

    def _map_blink_content_key(
        self, *, cols: int, rows_n: int, title: str, sel: str | None,
        vis: list[aadr.Group],
    ) -> tuple:
        return (
            self.tab, self.source, self.epoch, self.world, self.people_mode,
            self.map_focus, sel, cols, rows_n, title,
            tuple(g.name for g in vis),
            id(self._scores()), id(self._ranks()),
        )

    def _store_map_blink_frame(self, key: tuple, text: Text) -> None:
        if key != self._map_blink_key:
            self._map_blink_key = key
            self._map_blink_frames = {}
        self._map_blink_frames[self._sel_blink] = text

    def fill_list(self, rows: list[aadr.Group] | None = None,
                  *, update_filter: bool = True) -> list[aadr.Group]:
        ol = self.query_one("#groups", OptionList)
        keep = self.current.name if self.current else None
        if rows is None:
            rows = self.visible()
        self._last_vis = rows
        ranks = self._ranks()
        meta = self._list_meta(rows)
        self._meta_by_name = {m["g"].name: m for m in meta}
        opts = []
        for m in meta:
            g = m["g"]
            opts.append(Option(group_prompt(g, m["r"], m["hit"], ranks.get(g.name),
                                            place=m["place"], decide=m["decide"],
                                            outside_head=m["outside_head"]),
                               id=g.name))
        ol.clear_options()
        ol.add_options(opts)
        if rows:
            ids = [g.name for g in rows]
            ol.highlighted = ids.index(keep) if keep in ids else 0
        bits = [f"{len(rows)} {'people' if self.people_mode else 'groups'}",
                "lo › Z › SNP › Y"]
        if self.y_only:
            bits.append("Y-only")
        if self.questionable:
            bits.append("Q")
        self.query_one("#groups-box").border_subtitle = " · ".join(bits)
        self._set_groups_title()
        if update_filter:
            self.refresh_score_filter(shown=len(rows))
        return rows

    def _refresh_bars(self) -> None:
        """Source / epoch / region / tab chrome (no list or map)."""
        self.query_one("#source", Static).update(source_bar(self.source))
        self.query_one("#epochs", Static).update(
            epoch_bar(self.epoch, self.present_epochs(), self.playing, self._epoch_labels()))
        self.query_one("#regions", Static).update(
            region_bar(self.area, self.present_regions()))
        self.query_one("#view-tabs", Static).update(tab_bar(self.tab))
        title = {"map": "MAP", "y": "Y", "tree": "TREE", "layers": "LAYERS"}.get(self.tab, "MAP")
        self.query_one("#map-box").border_title = title

    def _refresh_list_views(self, *, bars: bool = False) -> None:
        """One visible() → list + map + card + filter. Optional chrome bars."""
        rows = self.visible()
        self.fill_list(rows, update_filter=False)
        if bars:
            self._refresh_bars()
        self.refresh_map(vis=rows)
        self.refresh_card()
        self.refresh_score_filter(shown=len(rows))
        self.update_status()

    def _refresh_score_views(self) -> None:
        """Score-filter axes changed — same as list refresh, chrome untouched."""
        self._refresh_list_views(bars=False)

    def refresh_score_filter(self, shown: int | None = None) -> None:
        scores = self._scores()
        if shown is None:
            shown = len(self.visible())
        total = sum(1 for r in scores.values() if r.ok)
        box = self.query_one("#score-filter", Static)
        # Content width inside padding: 0 1 → two cells of pad.
        width = max(52, (box.size.width or 96) - 2)
        # Two columns + "│ " gutter — must fit without wrap (wrap ate SE chrome).
        col_w = max(24, (width - 2) // 2)
        bar_w = max(6, col_w - 20)
        bps: list[float] = []
        for name, r in scores.items():
            if not r.ok:
                continue
            g = self._filter_group(name)
            if g and g.bp is not None and g.bp > 0:
                bps.append(float(g.bp))
        box.update(score_filter_panel(
            scores,
            lo_min=self.score_lo_min,
            z_min=self.score_z_min,
            snp_min=self.score_snp_min,
            n_min=self.score_n_min,
            se_max=self.score_se_max,
            y_mode=self.score_y,
            coords_only=self.score_coords,
            bp_max=self.score_bp_max,
            bp_values=bps,
            shown=shown,
            total=total,
            bar_width=bar_w,
            col_width=col_w,
            tab=self.filter_tab,
        ))

    @property
    def current(self) -> aadr.Group | None:
        ol = self.query_one("#groups", OptionList)
        if ol.highlighted is None:
            return None
        opt = ol.get_option_at_index(ol.highlighted)
        if not opt.id:
            return None
        # Prefer the row object already built for the list (no as_group / scan).
        meta = self._meta_by_name.get(opt.id)
        if meta is not None:
            return meta["g"]
        if self.source == "kg":
            pool = self.kg_people_groups if self.people_mode else self.kg_groups
            return next((g for g in pool if g.name == opt.id), None)
        if not self.cat:
            return None
        if self.people_mode:
            g = self._people_by_gid.get(opt.id)
            if g is not None:
                return g
            i = self.cat.by_gid.get(opt.id)
            return self._person_group(self.cat.samples[i]) if i is not None else None
        return self.cat.groups.get(opt.id)

    def present_epochs(self) -> list[str]:
        if self.source == "kg":
            have = {g.epoch for g in self.kg_groups if g.epoch}
            return [k for k in ("EUR", "EAS", "SAS", "AFR", "AMR") if k in have]
        if not self.cat:
            return []
        have: set[str] = set()
        for g in self.cat.groups.values():
            if self._gcount(g) > 0:
                have.add(g.epoch)
        return [k for k, *_ in reversed(aadr.EPOCHS) if k in have]

    def present_regions(self) -> list[str]:
        have: set[str] = set()
        pool = (self.kg_people_groups if self.source == "kg" and self.people_mode
                else self.kg_groups if self.source == "kg"
                else (list(self.cat.groups.values()) if self.cat else []))
        for g in pool:
            r = region_of(g.lat, g.lon)
            if r:
                have.add(r)
        return [r for r in REGIONS if r in have]

    def _epoch_labels(self) -> dict[str, str]:
        return dict(SUPERPOPS) if self.source == "kg" else aadr.EPOCH_LABEL

    def refresh_all(self) -> None:
        """Full chrome + map/card/filter. Prefer _refresh_list_views when the list changes."""
        self._refresh_bars()
        self.refresh_map(vis=self._last_vis)
        self.refresh_card()
        shown = len(self._last_vis) if self._last_vis is not None else None
        self.refresh_score_filter(shown=shown)
        self.update_status()

    def refresh_map(self, vis: list[aadr.Group] | None = None) -> None:
        body = self.query_one("#map", Static)
        if self.loading_msg and self.tab != "layers":
            body.update(Text(self.loading_msg, style="#B04A4A"))
            return
        if self.problem and self.tab != "layers":
            body.update(Text(self.problem, style="#B04A4A"))
            return
        if vis is None:
            vis = self._last_vis if self._last_vis is not None else self.visible()
        self._last_vis = vis
        labels = self._epoch_labels()
        if self.tab == "y":
            self.query_one("#map-scroll").styles.overflow_y = "hidden"
            if self.source != "aadr":
                self._map_blink_key = None
                self._map_blink_frames = {}
                body.update(Text("Y map is AADR-only. Switch to AADR with G.", style="#AEA5B8"))
                return
            box = self.query_one("#map-box")
            inner = self.query_one("#map-scroll")
            w = inner.size.width or box.size.width
            h = inner.size.height or box.size.height
            cols = max(40, (w or 80) - 6)
            rows_n = max(12, (h or 24) - 4)
            if self.epoch == "all":
                title = "Y"
            else:
                title = f"Y · {labels.get(self.epoch, self.epoch)}"
            if self.people_mode:
                title += " · people"
            if self.y_branch:
                title += f" · {self.y_branch.tip}"
            sel = self.current.name if self.current else None
            key = self._map_blink_content_key(
                cols=cols, rows_n=rows_n, title=title, sel=sel, vis=vis)
            hits = self.hits_people if self.people_mode else self.hits
            # Same GROUPS rows as the list — draw_y_map drops non-hits / no coords.
            text = draw_y_map(
                vis, self._scores(), self._ranks(), hits,
                self.y_ours_other, sel,
                title, self.world, cols, rows_n,
                selected_blink=self._sel_blink)
            self._store_map_blink_frame(key, text)
            body.update(text)
            return
        if self.tab == "tree":
            self._map_blink_key = None
            self._map_blink_frames = {}
            self.query_one("#map-scroll").styles.overflow_y = "auto"
            body.update(tree_view(
                vis, self._scores(), self._ranks(),
                self.hits_people if self.people_mode else self.hits,
                self.qpadm, self.present_epochs() if self.epoch == "all" else [self.epoch],
                labels, self.current.name if self.current else None,
                paint=self.paint))
            return
        if self.tab == "layers":
            self._map_blink_key = None
            self._map_blink_frames = {}
            self.query_one("#map-scroll").styles.overflow_y = "auto"
            self._load_layer_bundles(self.genome, self.questionable)
            aadr_b, aadr_imp = self.layer_aadr, self.layer_aadr_imp
            kg_b, kg_imp = self.layer_kg, self.layer_kg_imp
            rows = _admix().layer_state(self.store, self.genome, self.source,
                                        aadr_b, kg_b, aadr_imp, kg_imp)
            if self.source == "aadr":
                chip = aadr_b.groups if aadr_b else {}
                imp = aadr_imp.groups if aadr_imp else {}
            else:
                chip = kg_b.groups if kg_b else {}
                imp = kg_imp.groups if kg_imp else {}
            body.update(layers_view(rows, chip, imp))
            self._layers_shown_sig = self._layers_paint_sig()
            return
        box = self.query_one("#map-box")
        inner = self.query_one("#map-scroll")
        inner.styles.overflow_y = "hidden"
        w = inner.size.width or box.size.width
        h = inner.size.height or box.size.height
        # scatter_map: 6-char lat gutter + cols; 4 chrome rows (title, 2 rules, lon axis)
        cols = max(40, (w or 80) - 6)
        rows_n = max(12, (h or 24) - 4)
        if self.epoch == "all":
            title = "ALL EPOCHS" if self.source == "aadr" else "1000 GENOMES"
        else:
            title = labels.get(self.epoch, self.epoch)
        if self.people_mode:
            title += " · people"
        seshat: list[dict] = []
        if self.epoch != "all" and self.source == "aadr":
            skey = (self.epoch, tuple(g.name for g in vis))
            if skey != self._seshat_cache_key:
                self._seshat_cache_key = skey
                self._seshat_cache = seshat_layer_for_groups(
                    vis, epoch=self.epoch, store=self.store)
            seshat = self._seshat_cache
            if seshat:
                title += f" · §{len(seshat)}"
        sel = self.current.name if self.current else None
        key = self._map_blink_content_key(
            cols=cols, rows_n=rows_n, title=title, sel=sel, vis=vis)
        text = draw_map(vis, self._scores(), self._ranks(),
                        sel, title,
                        self.world, cols, rows_n,
                        culture_labels=(self.epoch != "all"),
                        seshat_anchors=seshat or None,
                        selected_blink=self._sel_blink,
                        map_focus=self.map_focus)
        self._store_map_blink_frame(key, text)
        body.update(text)

    def _card_subject(self) -> tuple[aadr.Group | None, aadr.Sample | None, str | None]:
        """Current GROUP/PERSON selection + cache key for SUMMARY."""
        g = self.current
        sample = None
        if (self.source == "aadr" and self.people_mode and self.cat and g
                and g.name in self.cat.by_gid):
            sample = self.cat.samples[self.cat.by_gid[g.name]]
        key = sample.gid if sample is not None else (g.name if g else None)
        return g, sample, key

    def refresh_card(self) -> None:
        g, sample, key = self._card_subject()
        if g is None:
            title = "GROUP"
        elif sample is not None:
            title = f"PERSON · {sample.gid}"
        else:
            title = f"GROUP · {g.name}"
        self.query_one("#card-box").border_title = fit(title, 40)
        self.query_one("#card-tabs", Static).update(card_tab_bar(self.card_tab))
        info = self._meta_by_name.get(g.name) if g else None
        ranks = self._ranks()
        busy = bool(key and self.summary_job_key == key
                    and (job := JOBS.get(JOB_SUMMARY)) and job.active)
        draft = ""
        model_hint = ""
        if busy and (job := JOBS.get(JOB_SUMMARY)):
            draft = job.draft or ""
            model_hint = chat.wait_line(job)
        card_box = self.query_one("#card")
        md_w = max(36, (card_box.size.width or 52) - 2)
        t = card(g, sample=sample,
                 cat=self.cat if self.source == "aadr" else None,
                 hit=self._hit_of(g.name) if g else None,
                 branch=self.y_branch.tip if self.y_branch else None,
                 questionable=self.questionable,
                 qpadm=self.qpadm if self.source == "aadr" else None,
                 paint=self.paint,
                 result=self._scores().get(g.name) if g else None,
                 place=info["place"] if info else None,
                 ok_total=info["ok_total"] if info else 0,
                 decide=info["decide"] if info else "lo",
                 rank=ranks.get(g.name) if g else None,
                 tab=self.card_tab,
                 summary=self.summary_cache.get(key) if key else None,
                 summary_busy=busy,
                 summary_draft=draft,
                 summary_model=model_hint,
                 summary_width=md_w,
                 y_hits=self.hits_people)
        card_box.update(t)

    def update_status(self) -> None:
        if self.loading_msg:
            g = f"    GENOME: {self.genome.name}" if self.genome else "    GENOME: —"
            self.query_one("#status", Static).update(f"{self.loading_msg}{g}")
            return
        scored = sum(1 for r in self.results.values() if r.ok)
        if self.source == "aadr":
            n = self.cat.n_pass if self.cat else 0
            g = len(self.cat.groups) if self.cat else 0
            og = self.cat.outgroup if self.cat else "—"
            people = sum(1 for r in self.people.values() if r.ok)
            line = f"AADR: {n:,} samples · {g:,} groups · outgroup {og}    f3: {scored}g/{people}p"
            if self.y_branch:
                line += f"    Y {self.y_branch.tip}"
            if self.questionable:
                line += "    Q"
            if self.check:
                mark = "ok" if self.check.get("ok") else f"#{self.check.get('rank')}"
                line += f"    LOO {self.check.get('gid')}: {mark}"
        else:
            people_n = sum(1 for r in self.people.values() if r.ok)
            line = f"1000G: {len(self.kg_groups)} pops · outgroup YRI    f3: {scored}g/{people_n}p"
            if self.imputed:
                line += f"    imputed {sum(1 for r in self.imputed.values() if r.ok)}"
            if self.paint and self.paint.get("pops"):
                line += "    painted"
            if self.phase:
                line += "    phased" if self.phase.get("phased") else "    unphased"
        if self.genome:
            line += f"    GENOME: {self.genome.name}"
        else:
            line += "    GENOME: — (F1)"
        active = [j for k in JOBS_ALL if (j := JOBS.get(k)) and j.active]
        if active:
            job = active[0]
            if job.key == JOB_SUMMARY or (job.slot or "") == JOB_SUMMARY:
                detail = chat.wait_line(job)
            else:
                detail = f"{job.stage} · {job_line(job)}" if job.stage else job_line(job)
            line += f"    ⚙ {job.title}: {detail}"
        self.query_one("#status", Static).update(line)

    def _layers_paint_sig(self) -> tuple:
        """LAYERS content identity — DB stamps + live job stages (not wall-clock)."""
        job_bits: list[tuple] = []
        for k in (JOB, JOB_KG, JOB_PCA, JOB_QPADM, JOB_IMPUTE, JOB_PAINT):
            j = JOBS.get(k)
            if j and j.active:
                job_bits.append((
                    k, "active", j.stage or "",
                    int(round((j.fraction or 0.0) * 100)),
                ))
            elif j:
                job_bits.append((k, j.status, j.finished))
        return (
            self.source,
            str(self.genome) if self.genome else None,
            self.questionable,
            self.layer_stamp,
            tuple(job_bits),
        )

    def _tick(self) -> None:
        active = [j for k in JOBS_ALL if (j := JOBS.get(k)) and j.active]
        summary = JOBS.get(JOB_SUMMARY)
        if active or self.loading_msg:
            self.update_status()
        # LAYERS: repaint only when stamps / job stages change (not every 0.5s).
        if self.tab == "layers" and not self.loading_msg:
            sig = self._layers_paint_sig()
            if sig != self._layers_shown_sig:
                self.refresh_map()
        # Stream SUMMARY draft + live wait_line into the card while Gemini answers.
        if self.card_tab == "summary" and summary and summary.active:
            self.refresh_card()
        if self.genome and not self.loading_msg:
            self._stamp_tick += 1
            # ~2s idle; every 0.5s while a ref job is running (results land soon).
            if active or self._stamp_tick % 4 == 0:
                self._poll_db_stamps()
        for k in JOBS_ALL:
            job = JOBS.get(k)
            if job and not job.active and id(job) not in self.seen_job:
                self.seen_job.add(id(job))
                if k == JOB_SUMMARY:
                    self._finish_summary_job(job)
                    continue
                sev = {"done": "information", "failed": "error"}.get(job.status, "warning")
                self.app.notify(f"{job.title}: {job.message}", severity=sev, timeout=8)
                # Job wrote DuckDB — pull stamps/results now, don't wait for idle poll.
                self.load_results()
                self._refresh_list_views(bars=True)

    def _play_tick(self) -> None:
        if self.playing:
            self.action_epoch_step(1)

    @on(OptionList.OptionHighlighted, "#groups")
    def _highlighted(self) -> None:
        self._sel_blink = True
        self.refresh_card()
        # Same visible set — only selection glyph / card change.
        self.refresh_map(vis=self._last_vis)

    def action_score_set(self, axis: str, value: float) -> None:
        """Click a band cell: floor (≥) or ceiling (≤) depending on axis."""
        if axis == "lo":
            self.score_lo_min = float(value)
        elif axis == "z":
            self.score_z_min = float(value)
        elif axis == "snp":
            self.score_snp_min = int(round(float(value)))
        elif axis == "n":
            self.score_n_min = max(1, int(round(float(value))))
        elif axis == "se":
            self.score_se_max = float(value)
        elif axis == "age":
            self.score_bp_max = float(value)
        else:
            return
        self._refresh_score_views()

    def _score_axis_values(self, axis: str) -> list[float]:
        """Sorted unique observed values for one filter axis (for edge/step)."""
        scores = self._scores()
        ok = [r for r in scores.values() if r.ok]
        if not ok:
            return []
        raw: list[float] = []
        if axis == "lo":
            raw = [r.est - 2 * r.se for r in ok]
        elif axis == "z":
            raw = [r.z for r in ok]
        elif axis == "snp":
            raw = [float(r.snps) for r in ok]
        elif axis == "n":
            raw = [float(max(1, r.n)) for r in ok]
        elif axis == "se":
            raw = [r.se for r in ok if r.se > 0]
        elif axis == "age":
            for r in ok:
                g = self._filter_group(r.name)
                if g and g.bp is not None and g.bp > 0:
                    raw.append(float(g.bp))
        else:
            return []
        # Unique sorted — step lands on real observed values (critical for clustered lo).
        return sorted({float(v) for v in raw})

    @staticmethod
    def _log_step_size(i: int, n: int, *, dense_at_high: bool) -> int:
        """Index step: finer near the dense end (lo packs at the high end).

        Same spirit as ranks_of log bend — top of the lo scale needs 1-rank nudges.
        """
        if n <= 2:
            return 1
        # 0 at dense end → 1; 1 at sparse end → up to ~n/20
        if dense_at_high:
            dist = (n - 1 - i) / (n - 1)
        else:
            dist = i / (n - 1)
        return max(1, int(round(1 + dist * max(1.0, n / 20.0))))

    def action_score_step(self, axis: str, end: str) -> None:
        """‹ / › — one log-aware step along observed values.

        Floors: ‹ weaker (lower threshold), › stricter (higher).
        Ceilings: ‹ stricter (lower), › looser (higher).
        """
        vals = self._score_axis_values(axis)
        if not vals:
            return
        end = "hi" if end in ("hi", "right", "strong") else "lo"
        floor = axis in ("lo", "z", "snp", "n")
        # Dense packing: lo/Z/SNP/n near high; SE near low; age more even → treat as high-sparse.
        dense_at_high = axis in ("lo", "z", "snp", "n")
        cur = {
            "lo": self.score_lo_min,
            "z": self.score_z_min,
            "snp": float(self.score_snp_min) if self.score_snp_min is not None else None,
            "n": float(self.score_n_min) if self.score_n_min is not None else None,
            "se": self.score_se_max,
            "age": self.score_bp_max,
        }.get(axis)
        n = len(vals)
        if cur is None:
            # First step engages the filter from the inclusive end.
            i = 0 if (floor and end == "lo") or (not floor and end == "hi") else n - 1
            if floor and end == "lo":
                # ‹ from off → lightest floor (min)
                i = 0
            elif floor and end == "hi":
                i = max(0, n - 1 - self._log_step_size(n - 1, n, dense_at_high=dense_at_high))
            elif not floor and end == "lo":
                i = min(n - 1, self._log_step_size(0, n, dense_at_high=dense_at_high))
            else:
                i = n - 1
        else:
            # Nearest index in sorted unique values.
            i = min(range(n), key=lambda k: (abs(vals[k] - float(cur)), k))
            direction = -1 if end == "lo" else 1
            step = self._log_step_size(i, n, dense_at_high=dense_at_high)
            i = max(0, min(n - 1, i + direction * step))
        self.action_score_set(axis, vals[i])

    def action_score_edge(self, axis: str, end: str) -> None:
        """« / » — jump to bar left (lo) or right (hi) extreme.

        Floors (lo/Z/SNP/n): « clears (off), » sets max (strictest floor).
        Ceilings (SE/age): « sets min (strictest), » clears (off).
        """
        vals = self._score_axis_values(axis)
        if not vals:
            return
        end = "hi" if end in ("hi", "right", "strong") else "lo"
        floor = axis in ("lo", "z", "snp", "n")

        def clear() -> None:
            self.action_score_clear(axis)

        if floor:
            if end == "lo":
                clear()
            else:
                self.action_score_set(axis, vals[-1])
            return
        # ceilings
        if end == "lo":
            self.action_score_set(axis, vals[0])
        else:
            clear()

    def action_score_clear(self, axis: str | None = None) -> None:
        """Clear one axis, or all (X / reset)."""
        if axis in (None, "", "all"):
            self.score_lo_min = None
            self.score_z_min = None
            self.score_snp_min = None
            self.score_n_min = None
            self.score_se_max = None
            self.score_y = "all"
            self.score_coords = False
            self.score_bp_max = None
        elif axis == "lo":
            self.score_lo_min = None
        elif axis == "z":
            self.score_z_min = None
        elif axis == "snp":
            self.score_snp_min = None
        elif axis == "n":
            self.score_n_min = None
        elif axis == "se":
            self.score_se_max = None
        elif axis == "age":
            self.score_bp_max = None
        else:
            return
        self._refresh_score_views()

    def action_score_y(self, mode: str) -> None:
        if mode not in ("all", "line", "same"):
            mode = "all"
        self.score_y = mode
        self._refresh_score_views()

    def action_score_coords(self, on: bool = True) -> None:
        self.score_coords = bool(on)
        self._refresh_score_views()

    def action_filter_tab(self, key: str) -> None:
        if key not in FILTER_TABS or key == self.filter_tab:
            return
        self.filter_tab = key
        self.refresh_score_filter()

    def action_epoch(self, key: str) -> None:
        self.epoch = key
        self.map_focus = None
        self._seshat_cache_key = None
        self._seshat_cache = []
        if key == "all":
            self.playing = False
            if self._play:
                self._play.pause()
        self._refresh_list_views(bars=True)

    def action_region(self, key: str) -> None:
        keys = ["all", *self.present_regions()]
        if key not in keys:
            key = "all"
        self.area = key
        self._refresh_list_views(bars=True)

    def action_region_next(self) -> None:
        keys = ["all", *self.present_regions()]
        if self.area not in keys:
            self.area = "all"
        i = keys.index(self.area) if self.area in keys else 0
        self.action_region(keys[(i + 1) % len(keys)])

    def action_epoch_prev(self) -> None:
        self.action_epoch_step(-1)

    def action_epoch_next(self) -> None:
        self.action_epoch_step(1)

    def action_epoch_step(self, d: int) -> None:
        keys = self.present_epochs()
        if not keys:
            return
        if self.epoch == "all" or self.epoch not in keys:
            self.action_epoch(keys[0 if int(d) >= 0 else -1])
            return
        i = keys.index(self.epoch)
        self.action_epoch(keys[(i + int(d)) % len(keys)])

    def action_toggle_play(self) -> None:
        self.playing = not self.playing
        need_list = False
        if self.playing:
            if self.epoch == "all":
                keys = self.present_epochs()
                if keys:
                    self.epoch = keys[0]
                    need_list = True
            if self._play:
                self._play.resume()
        elif self._play:
            self._play.pause()
        if need_list:
            self._refresh_list_views(bars=True)
        else:
            self._refresh_bars()
            self.update_status()

    def action_toggle_y(self) -> None:
        if self.source != "aadr":
            self.app.notify("Y line is AADR-only", timeout=4)
            return
        self.y_only = not self.y_only
        self._refresh_list_views(bars=False)
        if self.y_only and self.y_branch:
            off = sum(1 for h in self.hits.values() if h.relation == "other")
            self.app.notify(f"Y branch {self.y_branch.tip} · {off} other Y still listed")

    def action_toggle_people(self) -> None:
        if self.source == "kg" and not self.kg_people_groups:
            self.app.notify("1000G people appear after an f3 scan", timeout=4)
        self.people_mode = not self.people_mode
        # Region set can change in KG people mode.
        self._refresh_list_views(bars=True)

    def action_toggle_quest(self) -> None:
        if self.source != "aadr":
            self.app.notify("Questionable is AADR-only", timeout=4)
            return
        self.questionable = not self.questionable
        self.load_results()
        self._refresh_list_views(bars=True)
        if self.questionable and not self.results:
            self.app.notify("Questionable on — press F to rescan including them", timeout=6)

    def action_toggle_world(self) -> None:
        self.world = not self.world
        self.refresh_map()

    def action_score(self) -> None:
        if not self.genome:
            self.app.notify("Load a genotype file in F1 first", severity="warning")
            return
        gp, store = self.genome, self.store
        ws = getattr(self.app, "genome_ws", None)
        loaded = ws.genome if ws and genome_db.same_path(ws.genome, gp) else None
        if self.source == "kg":
            if self.kg_problem:
                self.app.notify(self.kg_problem, severity="warning")
                return
            if (job := JOBS.get(JOB_KG)) and job.running:
                self.app.notify("1000G f3 is already running (C cancels it)", severity="warning")
                return
            JOBS.start(JOB_KG, f"1000G · outgroup-f3 (YRI) · {gp.name}",
                       lambda j: _kg().scan_f3(store, gp, j, loaded))
            self.update_status()
            return
        if self.loading_msg:
            self.app.notify(self.loading_msg, severity="warning")
            return
        if self.problem:
            self.app.notify(self.problem, severity="warning")
            return
        if not self.cat or not self.cat.files.get("geno"):
            self.app.notify("AADR .geno is missing", severity="warning")
            return
        if (job := JOBS.get(JOB)) and job.running:
            self.app.notify("f3 scan is already running (C cancels it)", severity="warning")
            return
        q = self.questionable
        JOBS.start(JOB, f"AADR · outgroup-f3 · {gp.name}",
                   lambda j, q=q: _fstats().scan_f3(store, gp, j, loaded, q))
        self.update_status()

    def action_cancel(self) -> None:
        stopped = 0
        for k in JOBS_ALL:
            job = JOBS.get(k)
            # paused also holds the slot (JOBS.start checks .active).
            if job and job.active:
                if k == JOB_SUMMARY:
                    chat.abort_send(job)
                job.cancel()
                stopped += 1
        if stopped:
            self.app.notify(f"Cancelling {stopped} job(s)")
        else:
            self.app.notify("Nothing is running")

    def action_source(self, key: str) -> None:
        if key not in ("aadr", "kg") or key == self.source:
            return
        self.source = key
        self.epoch = "all"
        self.area = "all"
        self.people_mode = False
        self.y_only = False
        self.reload()

    def action_toggle_source(self) -> None:
        self.action_source("kg" if self.source == "aadr" else "aadr")

    def action_tab(self, key: str) -> None:
        if key not in TABS or key == self.tab:
            return
        self.tab = key
        self.refresh_all()
        self.query_one("#map-scroll").scroll_home(animate=False)

    def action_toggle_tab(self) -> None:
        i = TABS.index(self.tab) if self.tab in TABS else 0
        self.action_tab(TABS[(i + 1) % len(TABS)])

    def action_card_tab(self, key: str) -> None:
        if key not in CARD_TABS or key == self.card_tab:
            return
        self.card_tab = key
        self.refresh_card()
        self.query_one("#card-scroll").scroll_home(animate=False)

    def action_open_doi(self, doi: str) -> None:
        doi = (doi or "").strip()
        if not doi:
            return
        url = doi if doi.lower().startswith("http") else f"https://doi.org/{doi}"
        try:
            webbrowser.open(url)
            self.app.notify(f"Open DOI · {doi}")
        except Exception as exc:  # noqa: BLE001
            self.app.notify(f"Could not open DOI: {exc}", severity="error")

    def _wait_summary_slot(self, timeout: float = 2.5) -> bool:
        """After cancel, wait until ref-summary is free (paused/running → cancelled)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = JOBS.get(JOB_SUMMARY)
            if not job or not job.active:
                return True
            time.sleep(0.05)
        job = JOBS.get(JOB_SUMMARY)
        return not (job and job.active)

    def action_ask_summary(self, refresh: bool = False) -> None:
        """SUMMARY tab: ask Chat model for a scientific brief grounded in ANNO."""
        g, sample, key = self._card_subject()
        if g is None or not key:
            self.app.notify("Select a group first", severity="warning")
            return
        if not chat.api_key(self.store):
            self.app.notify("Set the Gemini API key in F2 (Google Gemini · P)",
                            severity="warning")
            return
        if not refresh and key in self.summary_cache and self.summary_cache[key].get("text"):
            self.card_tab = "summary"
            self.refresh_card()
            return
        # Paused jobs still occupy the slot (.active); replace with the new ask.
        if (old := JOBS.get(JOB_SUMMARY)) and old.active:
            old.cancel()
            if not self._wait_summary_slot():
                self.app.notify("Summary still stopping · C / try again",
                                severity="warning")
                return
        keep: list[aadr.Sample] = []
        if self.cat and self.source == "aadr":
            keep = [s for s in (self.cat.samples[i] for i in g.samples)
                    if s.keep or (self.questionable and s.questionable)]
        prompt = summary_prompt(g, keep, sample=sample)
        title = f"Summary · {key}"[:48]
        store = self.store
        subject = key

        def work(job) -> str:
            # One shared chat for all F5 asks; fresh Gemini turn (no prior group context).
            t = chat.ensure_reference_thread()
            # Label the turn in F6 history.
            headed = f"[F5 · {subject}]\n\n{prompt}"
            t.messages.append(chat.Message("user", headed))
            chat.save(t)
            chat.send(store, t, job, continue_chat=False)
            text = next((m.text for m in reversed(t.messages) if m.role == "model"), "")
            return (text or job.draft or "").strip() or "(empty reply)"

        self.summary_job_key = subject
        self.card_tab = "summary"
        try:
            JOBS.start(JOB_SUMMARY, title, work)
        except RuntimeError as exc:
            self.app.notify(str(exc), severity="warning")
            return
        self.refresh_card()
        self.update_status()

    def _finish_summary_job(self, job) -> None:
        key = self.summary_job_key
        self.summary_job_key = None
        if not key:
            self.refresh_card()
            return
        if job.status == "done":
            # JOBS stores work() return value in job.message.
            text = (job.message or job.draft or "").strip()
            if text in ("done",):
                text = (job.draft or "").strip()
            model = chat.model_label(self.store)
            ts = chat.now()
            entry = {
                "text": text or "(empty reply)",
                "model": model,
                "ts": ts,
            }
            self.summary_cache[key] = entry
            try:
                ref_db.save_summary(
                    key, text=entry["text"], model=model,
                    thread_id=chat.REF_THREAD_ID, ts=ts,
                )
            except Exception as exc:  # noqa: BLE001
                self.app.notify(f"Summary saved in memory only: {exc}",
                                severity="warning", timeout=8)
            self.app.notify(f"Summary · {key}", severity="information", timeout=6)
        elif job.status == "failed":
            self.app.notify(f"Summary failed: {job.message}", severity="error", timeout=10)
        else:
            self.app.notify(f"Summary: {job.message or job.status}", severity="warning", timeout=8)
        self.card_tab = "summary"
        self.refresh_card()

    def action_layer(self, kind: str) -> None:
        if not self.genome:
            self.app.notify("Load a genotype file in F1 first", severity="warning")
            return
        if self.loading_msg:
            self.app.notify(self.loading_msg, severity="warning")
            return
        gp, store = self.genome, self.store
        ws = getattr(self.app, "genome_ws", None)
        loaded = ws.genome if ws and genome_db.same_path(ws.genome, gp) else None
        q = self.questionable
        if kind == "pca":
            if (job := JOBS.get(JOB_PCA)) and job.running:
                self.app.notify("smartpca is already running", severity="warning")
                return
            JOBS.start(JOB_PCA, f"AADR · smartpca · {gp.name}",
                       lambda j, q=q: _admix().run_smartpca(store, gp, j, loaded, q))
        elif kind == "qpadm":
            if (job := JOBS.get(JOB_QPADM)) and job.running:
                self.app.notify("qpAdm is already running", severity="warning")
                return
            JOBS.start(JOB_QPADM, f"AADR · qpAdm · {gp.name}",
                       lambda j, q=q: _admix().run_qpadm(store, gp, j, loaded, q))
        elif kind == "impute":
            if (job := JOBS.get(JOB_IMPUTE)) and job.running:
                self.app.notify("imputation is already running", severity="warning")
                return
            JOBS.start(JOB_IMPUTE, f"Beagle · impute · {gp.name}",
                       lambda j, q=q: _admix().run_impute(store, gp, j, loaded, q))
        elif kind == "paint":
            if (job := JOBS.get(JOB_PAINT)) and job.running:
                self.app.notify("ChromoPainter is already running", severity="warning")
                return
            JOBS.start(JOB_PAINT, f"1000G · ChromoPainter · {gp.name}",
                       lambda j: _admix().run_paint_job(store, gp, j, loaded))
        else:
            self.app.notify(f"Unknown layer {kind}", severity="warning")
            return
        self.tab = "layers"
        self.refresh_all()

    def action_map_focus(self, key: str) -> None:
        """Toggle DEFCON radar for an ocean-legend culture / Seshat row."""
        key = (key or "").strip()
        if not key:
            return
        self.map_focus = None if self.map_focus == key else key
        self.refresh_map()

    def action_select(self, name: str) -> None:
        ol = self.query_one("#groups", OptionList)
        for i in range(ol.option_count):
            if ol.get_option_at_index(i).id == name:
                ol.highlighted = i
                ol.focus()
                return
