"""F6 Reference screen for textual-wgpu — groups · map/tree/layers · card · f3 jobs."""

from __future__ import annotations

import ast
import re
import time
import webbrowser
from pathlib import Path
from typing import Any

from rich.text import Text

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
    Vertical,
    VerticalScroll,
)

from ..core import aadr, chat, genome_db, ref_db
from ..core.jobs import JOBS
from ..core.store import Store
from ..core.ylink import YBranch, active_genome
from ..workspaces.base import MODE_LABELS, fit
from ..workspaces.data.workspace import job_line
from ..workspaces.reference.view import (
    CARD_TABS,
    FILTER_TABS,
    card,
    card_tab_bar,
    cliopatria_layer_for_epoch,
    draw_map,
    draw_y_map,
    epoch_bar,
    group_prompt,
    layers_view,
    map_points,
    region_bar,
    score_filter_panel,
    seshat_layer_for_groups,
    source_bar,
    summary_prompt,
    tab_bar,
    tree_view,
    y_map_points,
)
from ..workspaces.reference.workspace import (
    AADR_LIKE,
    JOB,
    JOB_HO,
    JOB_IMPUTE,
    JOB_KG,
    JOB_PAINT,
    JOB_PCA,
    JOB_QPADM,
    JOB_SUMMARY,
    JOBS_ALL,
    SRC_CYCLE,
    TABS,
    ReferenceWorkspace,
    _admix,
    _fstats,
    _kg,
)
from .wgpu_jobs import _text_lines

# Pixel → terminal cell (RichText / map braille). Tighter than Textual CSS
# padding so braille/overlays pack closer to terminal MapScreen density.
_MAP_FONT = 12
_CELL_W = 7
_CELL_H = 14
_ACTION_RE = re.compile(r"^screen\.(\w+)(?:\((.*)\))?$", re.DOTALL)
_CLICKABLE = (
    "source", "epochs", "regions", "view-tabs", "card-tabs", "score-filter", "map", "card",
)


def _borrow(name: str):
    """Bind an unbound ReferenceWorkspace method onto ReferenceScreen."""
    return getattr(ReferenceWorkspace, name)


class ReferenceScreen(Screen):
    """Same F6 Reference logic as Textual, hosted on wgpu widgets."""

    DEFAULT_CSS = """
    #main { height: 1fr; }
    #left-col { width: 58; height: 1fr; }
    #groups-box { height: 1fr; border: round $primary; }
    #card-box { height: 1fr; border: round $primary; }
    #map-box { width: 1fr; border: round $primary; }
    #groups-box:focus-within, #map-box:focus-within, #card-box:focus-within { border: round $accent; }
    #groups { height: 1fr; border: none; background: transparent; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """

    BINDINGS = [
        ("left", "action_epoch_prev"),
        ("right", "action_epoch_next"),
        ("space", "action_toggle_play"),
        ("f", "action_score"),
        ("y", "action_toggle_y"),
        ("p", "action_toggle_people"),
        ("u", "action_toggle_quest"),
        ("w", "action_toggle_world"),
        ("o", "action_toggle_map_overlay"),
        ("r", "action_region_next"),
        ("g", "action_toggle_source"),
        ("v", "action_toggle_tab"),
        ("c", "action_cancel"),
        ("x", "action_score_clear"),
        ("s", "action_ask_summary"),
        ("a", "action_ask_summary"),
        ("t", "action_cycle_card_tab"),
    ]

    def __init__(self, store: Store | None = None) -> None:
        super().__init__()
        self.store = store or Store()
        self.cat: aadr.Catalog | None = None
        self.problem = ""
        self.genome: Path | None = None
        self.results: dict[str, aadr.F3] = {}
        self.people: dict[str, aadr.F3] = {}
        self.ranks: dict[str, float] = {}
        self.people_ranks: dict[str, float] = {}
        # Never name this ``hits`` — shadows Widget.hits() used for mouse.
        self.y_hits: dict[str, aadr.Hit] = {}
        self.hits_people: dict[str, aadr.Hit] = {}
        self.y_branch: YBranch | None = None
        self.y_ours_other: set[str] = set()
        self.check: dict | None = None
        self.epoch = "all"
        self.y_only = False
        self.mt_only = False
        self.people_mode = False
        self.questionable = False
        self.world = True
        self.map_habitat = False
        self.map_polity = False
        self.map_uysd = False
        self.map_kin = False
        self.map_amtdb = False
        self.mt_tip: str | None = None
        self.mt_tip_score: float | None = None
        self.playing = False
        self.source = "aadr"
        self.area = "all"
        self.tab = "map"
        # Never ``self.paint`` — shadows Widget.paint() used every frame.
        self.paint_data: dict | None = None
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
        self.ui_ready: bool = True
        self.last_load_s: float = 0.0
        self.last_apply_s: float = 0.0
        self.last_paint_s: float = 0.0
        self.last_fill_list_s: float = 0.0
        self.last_refresh_map_s: float = 0.0
        self.last_refresh_card_s: float = 0.0
        self.score_lo_min: float | None = None
        self.score_z_min: float | None = None
        self.score_snp_min: int | None = None
        self.score_n_min: int | None = None
        self.score_se_max: float | None = None
        self.score_y: str = "all"
        self.score_coords: bool = False
        self.score_bp_max: float | None = None
        self.filter_tab: str = "floors"
        self.card_tab: str = "our"
        self.summary_cache: dict[str, dict] = {}
        self.summary_job_key: str | None = None
        self._sel_blink: bool = True
        self.map_focus: str | None = None
        self._seshat_cache_key: tuple | None = None
        self._seshat_cache: list[dict] = []
        self._clio_cache_key: tuple | None = None
        self._clio_cache: list[dict] = []
        self._map_sel_timer = None
        self._list_syncing: bool = False
        self._last_vis: list[aadr.Group] | None = None
        self._layers_shown_sig: tuple | None = None
        self._gcount_cache: dict[str, int] = {}
        self._gcount_q: bool | None = None
        self._people_by_gid: dict[str, aadr.Group] = {}
        self._groups_by_epoch: dict[str, list[aadr.Group]] = {}
        self._samples_by_epoch: dict[str, list[aadr.Sample]] = {}
        self._list_ids: list[str] = []
        self._list_prompt_sigs: dict[str, str] = {}
        self._map_blink_key: tuple | None = None
        self._map_blink_frames: dict[bool, Text] = {}
        self._map_points_key: tuple | None = None
        self._map_points: list[dict] | None = None
        self._stamp_tick: int = 0
        self._load_gen = 0
        self._labels = list(MODE_LABELS)
        self._groups_box: Panel | None = None
        self._map_box: Panel | None = None
        self._card_box: Panel | None = None
        self._tick_handle = None
        self._blink_handle = None

    # ── compose ──────────────────────────────────────────────────────
    def compose(self):
        from .app_api import make_mode_bar

        yield make_mode_bar(self, "reference", self._labels)

        for cid in ("source", "epochs", "regions"):
            bar = RichText(id=cid, can_focus=False)
            bar.layout_hint_h = 22
            bar.on_action = self._run_action
            yield bar

        groups = OptionList(id="groups", on_highlight=self._on_highlight, row_lines=2)
        self._groups_box = Panel(groups, title="GROUPS", id="groups-box")

        card_tabs = RichText(id="card-tabs", can_focus=False)
        card_tabs.layout_hint_h = 22
        card_tabs.on_action = self._run_action
        card_body = RichText(id="card", can_focus=False)
        card_body.on_action = self._run_action
        card_scroll = VerticalScroll(card_body, id="card-scroll")
        card_inner = Vertical(card_tabs, card_scroll, id="card-inner")
        self._card_box = Panel(card_inner, title="GROUP", id="card-box")

        left = Vertical(self._groups_box, self._card_box, id="left-col")

        view_tabs = RichText(id="view-tabs", can_focus=False)
        view_tabs.layout_hint_h = 22
        view_tabs.on_action = self._run_action
        map_body = RichText(id="map", can_focus=False)
        map_body.on_action = self._run_action
        map_body.fill_bg = False
        map_body.FONT_SIZE = _MAP_FONT
        map_body.LINE_H = _CELL_H
        map_scroll = VerticalScroll(map_body, id="map-scroll")
        score = RichText(id="score-filter", can_focus=False)
        score.layout_hint_h = 110
        score.on_action = self._run_action
        map_inner = Vertical(view_tabs, map_scroll, score, id="map-inner")
        self._map_box = Panel(map_inner, title="MAP", id="map-box")

        yield Horizontal(left, self._map_box, id="main")
        from .app_api import focus_next

        yield KeyBar([
            ("←→", "Epoch", lambda: self.action_epoch_step(1)),
            ("Space", "Play", lambda: self.action_toggle_play()),
            ("F", "Scan f3", lambda: self.action_score()),
            ("G", "AADR/1000G/HO", lambda: self.action_toggle_source()),
            ("V", "View", lambda: self.action_toggle_tab()),
            ("Y", "Y/mt", lambda: self.action_toggle_y()),
            ("P", "People", lambda: self.action_toggle_people()),
            ("U", "Q", lambda: self.action_toggle_quest()),
            ("W", "World", lambda: self.action_toggle_world()),
            ("O", "Overlays", lambda: self.action_toggle_map_overlay()),
            ("R", "Region", lambda: self.action_region_next()),
            ("S", "Summary", lambda: self.action_ask_summary()),
            ("T", "Card tab", lambda: self.action_cycle_card_tab()),
            ("X", "Clear filter", lambda: self.action_score_clear()),
            ("C", "Cancel", lambda: self.action_cancel()),
            ("Tab", "Panel", lambda: focus_next(self)),
            ("F1–F10", "Modes", None),
            ("Q", "Quit", lambda: self.app.ask_quit() if self.app else None),
        ])
        yield StatusBar(id="status")

    def on_screen_mount(self) -> None:
        self._wire_click_actions()
        try:
            chat.ensure_reference_thread()
            self.summary_cache = ref_db.load_summaries()
        except Exception:  # noqa: BLE001
            self.summary_cache = {}
        self.reload()
        ol = self.query_one("#groups", OptionList)
        if self.app is not None:
            self.app.set_focus(ol)
            self._tick_handle = self.app.set_interval(0.5, self._tick)
            self._blink_handle = self.app.set_interval(0.4, self._sel_blink_tick)
            self._play = self.app.set_interval(2.4, self._play_tick)
            if self._play is not None:
                self._play.pause()

    def on_screen_resume(self) -> None:
        self._wire_app_ws()
        genome = self._active()
        if self.source in AADR_LIKE:
            folder = self.store.path(self.source)
            folder_changed = folder != (self.cat.folder if self.cat else None)
            if folder_changed or self.cat is None:
                self.reload()
                return
        if genome != self.genome:
            self.refresh_genome()
            return
        self._prewarm_world_coast()

    def on_resize(self) -> None:
        self._prewarm_world_coast()
        self.refresh_map()

    # ── helpers ──────────────────────────────────────────────────────
    def _wire_app_ws(self) -> None:
        app = self.app
        if app is None:
            return
        screens = getattr(app, "_screens", {}) or {}
        app.genome_ws = screens.get("genome")
        app.tree_ws = screens.get("tree")

    def _genome_ws(self):
        if self.app is None:
            return None
        return getattr(self.app, "_screens", {}).get("genome")

    def _notify(self, msg: str, **_kw) -> None:
        if self.app is not None and hasattr(self.app, "notify"):
            self.app.notify(msg)

    def _show_busy(self, title: str, subtitle: str = "") -> None:
        if self.app is not None:
            self.app.show_busy(title, subtitle)

    def _busy_progress(self, msg: str, *, frac: float | None = None) -> None:
        if self.app is not None:
            self.app.busy_progress(msg, frac=frac)

    def _hide_busy(self) -> None:
        if self.app is not None:
            self.app.hide_busy()

    def _cells(self, widget) -> tuple[int, int]:
        from textual_wgpu.fonts import cell_width

        w = getattr(getattr(widget, "rect", None), "w", 0) or 0
        h = getattr(getattr(widget, "rect", None), "h", 0) or 0
        cw = max(_CELL_W, cell_width(_MAP_FONT))
        # Minimal chrome: map RichText fills the scroll pane (fill_bg=False).
        cols = max(48, (w // cw) - 2) if w else 80
        rows = max(16, (h // _CELL_H) - 2) if h else 24
        return cols, rows

    def _active(self) -> Path | None:
        gws = self._genome_ws()
        if gws is not None:
            active = getattr(gws, "active", None)
            if active is not None and Path(active).exists():
                return Path(active)
        try:
            return active_genome()
        except Exception:  # noqa: BLE001
            return None

    def _source_label(self, source: str | None = None) -> str:
        src = source or self.source
        return {"aadr": "AADR", "kg": "1000G", "aadr_ho": "Human Origins"}.get(src, src)

    def _load_msg(self) -> str:
        return f"Loading {self._source_label()}…"

    def _set_groups_title(self) -> None:
        box = self._groups_box
        if box is None:
            return
        if self.loading_msg:
            box.title = f"GROUPS · {self.loading_msg}"
        elif self.genome:
            box.title = f"GROUPS · for {self.genome.name}"
        else:
            box.title = "GROUPS · no genome (F1)"

    def _set_status(self, line: str) -> None:
        try:
            self.query_one("#status", StatusBar).update(line)
        except Exception:  # noqa: BLE001
            pass

    def _wire_click_actions(self) -> None:
        for wid in _CLICKABLE:
            try:
                self.query_one(f"#{wid}", RichText).on_action = self._run_action
            except Exception:  # noqa: BLE001
                pass

    def _update_rich(self, wid: str, text: Text) -> None:
        try:
            w = self.query_one(f"#{wid}", RichText)
            w.on_action = self._run_action
            w.update(text)
        except Exception:  # noqa: BLE001
            pass

    def _run_action(self, action: str) -> None:
        """Dispatch Rich ``ydna_click`` actions (``screen.epoch(...)``, bare ``screen.score``)."""
        m = _ACTION_RE.match((action or "").strip())
        if not m:
            return
        name, args_src = m.group(1), (m.group(2) or "").strip()
        fn = getattr(self, f"action_{name}", None) or getattr(self, name, None)
        if not callable(fn):
            return
        try:
            args = ast.literal_eval(f"({args_src},)") if args_src else ()
        except Exception:  # noqa: BLE001
            return
        if not isinstance(args, tuple):
            args = (args,)
        fn(*args)

    # ── load pipeline ────────────────────────────────────────────────
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
        self._groups_by_epoch = {}
        self._samples_by_epoch = {}
        self._list_ids = []
        self._list_prompt_sigs = {}
        self._map_blink_key = None
        self._map_blink_frames = {}
        self._map_points_key = None
        self._map_points = None
        self.loading_msg = msg
        self.genome = self._active()
        self._set_groups_title()
        self.update_status()
        self._show_busy(title, msg)
        self._busy_progress(msg, frac=0.02)
        return self._load_gen

    def reload(self) -> None:
        self._wire_app_ws()
        gen = self._begin_load(self._load_msg(), title="Reference")
        if self.app is not None:
            self.app.run_worker(lambda: self._reload_work(gen), name="ref-load", exclusive=True, group="ref-load")
        else:
            self._reload_work(gen)

    def refresh_genome(self) -> None:
        self._wire_app_ws()
        g = self._active()
        msg = f"Updating Y for {g.name}…" if g else "Updating…"
        gen = self._begin_load(msg, title="Reference")
        if self.app is not None:
            self.app.run_worker(lambda: self._genome_work(gen), name="ref-load", exclusive=True, group="ref-load")
        else:
            self._genome_work(gen)

    def _load_progress(self, gen: int, msg: str, frac: float = 0.1) -> None:
        if gen != self._load_gen:
            return
        self.loading_msg = msg
        self._set_groups_title()
        self.update_status()
        self._busy_progress(msg, frac=frac)

    def _reload_work(self, gen: int) -> None:
        try:
            t0 = time.perf_counter()
            store = self.store
            source = self.source
            questionable = self.questionable
            genome = self._active()
            cat = None
            aadr_problem = ""
            if source in AADR_LIKE:
                folder = store.path(source)
                label = self._source_label(source)
                if not folder:
                    aadr_problem = (
                        f"Set {label} in F2 (download still running, or P to the folder)."
                        if source == "aadr_ho"
                        else "Set the AADR folder in F2."
                    )
                else:
                    try:
                        cat = aadr.load_catalog(folder, panel=aadr.snp_panel_for(source))
                    except (OSError, RuntimeError, ValueError) as exc:
                        aadr_problem = str(exc)
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
            if genome and self.app is not None:
                self.app.call_from_thread(
                    self._load_progress, gen, f"Updating Y for {genome.name}…", 0.45)
            results = self._read_results(genome, source, questionable)
            if genome and self.app is not None:
                self.app.call_from_thread(
                    self._load_progress, gen, f"Updating Y for {genome.name}…", 0.7)
            y = self._compute_y(cat, genome, store)
            if source in AADR_LIKE:
                problem = aadr_problem
            else:
                problem = kg_problem or ("No 1000 Genomes pops." if not kg_groups else "")
            load_s = time.perf_counter() - t0
            apply = {
                "genome": genome, "cat": cat, "problem": problem,
                "kg_groups": kg_groups, "kg_people_groups": kg_people,
                "kg_problem": kg_problem, "load_s": load_s,
                **results, **y,
            }
            if self.app is not None:
                self.app.call_from_thread(self._apply_reload, gen, apply)
            else:
                self._apply_reload(gen, apply)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            if self.app is not None:
                self.app.call_from_thread(self._load_failed, gen, str(exc))
            else:
                self._load_failed(gen, str(exc))

    def _genome_work(self, gen: int) -> None:
        try:
            t0 = time.perf_counter()
            genome = self._active()
            source = self.source
            questionable = self.questionable
            cat = self.cat
            msg = f"Updating Y for {genome.name}…" if genome else "Updating…"
            if self.app is not None:
                self.app.call_from_thread(self._load_progress, gen, msg, 0.3)
            results = self._read_results(genome, source, questionable)
            if self.app is not None:
                self.app.call_from_thread(self._load_progress, gen, msg, 0.65)
            y = self._compute_y(cat, genome, self.store)
            load_s = time.perf_counter() - t0
            apply = {"genome": genome, "load_s": load_s, **results, **y}
            if self.app is not None:
                self.app.call_from_thread(self._apply_genome, gen, apply)
            else:
                self._apply_genome(gen, apply)
        except Exception as exp:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            if self.app is not None:
                self.app.call_from_thread(self._load_failed, gen, str(exp))
            else:
                self._load_failed(gen, str(exp))

    def _load_failed(self, gen: int, msg: str) -> None:
        if gen != self._load_gen:
            return
        self.loading_msg = None
        self.ui_ready = True
        self._hide_busy()
        self._notify(f"Reference load failed: {msg}")
        self.update_status()

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
            self._busy_progress("apply · layers…", frac=0.88)
            t = time.perf_counter()
            self._apply_results_y(data)
            self.last_apply_s = time.perf_counter() - t
            self._finish_paint()
        except Exception:
            self.loading_msg = None
            self._hide_busy()
            self.ui_ready = True
            raise

    def _apply_genome(self, gen: int, data: dict) -> None:
        if gen != self._load_gen:
            return
        self.last_load_s = float(data.get("load_s") or 0.0)
        try:
            self.genome = data["genome"]
            if self.source in AADR_LIKE:
                if self.cat:
                    self.problem = ""
                elif self.source == "aadr_ho":
                    self.problem = "Set Human Origins in F2 (download still running, or P)."
                else:
                    self.problem = "Set the AADR folder in F2."
            else:
                self.problem = self.kg_problem or (
                    "No 1000 Genomes pops." if not self.kg_groups else "")
            self._busy_progress("apply · layers…", frac=0.88)
            t = time.perf_counter()
            self._apply_results_y(data)
            self.last_apply_s = time.perf_counter() - t
            self._finish_paint()
        except Exception:
            self.loading_msg = None
            self._hide_busy()
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
        self.paint_data = data["paint"]
        self.phase = data["phase"]
        self.stamp = data["stamp"]
        self.y_hits = data["hits"]
        self.hits_people = data["hits_people"]
        self.y_branch = data["y_branch"]
        self.y_ours_other = data["y_ours_other"]
        self.layer_stamp = None
        self._load_layer_bundles(self.genome, self.questionable,
                                 reuse=data.get("reuse_layers"))

    def load_results(self) -> None:
        """Same as Textual load_results — writes paint_data, not paint."""
        data = self._read_results(self.genome, self.source, self.questionable)
        self.results = data["results"]
        self.people = data["people"]
        self.ranks = data["ranks"]
        self.people_ranks = data["people_ranks"]
        self.check = data["check"]
        self.pca = data["pca"]
        self.qpadm = data["qpadm"]
        self.imputed = data["imputed"]
        self.paint_data = data["paint"]
        self.phase = data["phase"]
        self.stamp = data["stamp"]
        self._load_layer_bundles(self.genome, self.questionable,
                                 reuse=data.get("reuse_layers"))

    def _finish_paint(self) -> None:
        self._busy_progress("GROUPS · MAP…", frac=0.92)
        self.loading_msg = None
        self._set_groups_title()
        t0 = time.perf_counter()
        t = time.perf_counter()
        self.fill_list()
        self.last_fill_list_s = time.perf_counter() - t
        self._refresh_bars()
        self._refresh_mt_tip()
        self._prewarm_world_coast()
        t = time.perf_counter()
        self.refresh_map()
        self.last_refresh_map_s = time.perf_counter() - t
        t = time.perf_counter()
        self.refresh_card()
        self.last_refresh_card_s = time.perf_counter() - t
        shown = len(self._last_vis) if self._last_vis is not None else None
        self.refresh_score_filter(shown=shown)
        self.update_status()
        self.last_paint_s = time.perf_counter() - t0
        self._hide_busy()
        self.ui_ready = True

    # Borrowed data helpers (no query_one / no self.hits).
    _panel_stamp = _borrow("_panel_stamp")
    _load_layer_bundles = _borrow("_load_layer_bundles")
    _read_results = _borrow("_read_results")
    _compute_y = _borrow("_compute_y")
    _on_mt = _borrow("_on_mt")
    _y_rank = _borrow("_y_rank")
    _kept = _borrow("_kept")
    _rebuild_gcount_cache = _borrow("_rebuild_gcount_cache")
    _rebuild_people_cache = _borrow("_rebuild_people_cache")
    _refresh_mt_tip = _borrow("_refresh_mt_tip")
    _uysd_map_discs = _borrow("_uysd_map_discs")
    _ancibd_map_edges = _borrow("_ancibd_map_edges")
    _amtdb_map_anchors = _borrow("_amtdb_map_anchors")
    _person_group = _borrow("_person_group")
    _gcount = _borrow("_gcount")
    _scores = _borrow("_scores")
    _ranks = _borrow("_ranks")
    _in_region = _borrow("_in_region")
    _filter_group = _borrow("_filter_group")
    _passes_score_filter = _borrow("_passes_score_filter")
    _row_key = _borrow("_row_key")
    _cap_rows = _borrow("_cap_rows")
    _list_meta = _borrow("_list_meta")
    _map_blink_content_key = _borrow("_map_blink_content_key")
    _store_map_blink_frame = _borrow("_store_map_blink_frame")
    present_epochs = _borrow("present_epochs")
    present_regions = _borrow("present_regions")
    _epoch_labels = _borrow("_epoch_labels")
    _card_subject = _borrow("_card_subject")
    _layers_paint_sig = _borrow("_layers_paint_sig")
    _wait_summary_slot = _borrow("_wait_summary_slot")
    _summary_extras = _borrow("_summary_extras")
    _finish_summary_job = _borrow("_finish_summary_job")

    def _on_line(self, name: str) -> bool:
        return name in (self.hits_people if self.people_mode else self.y_hits)

    def _hit_of(self, name: str) -> aadr.Hit | None:
        return (self.hits_people if self.people_mode else self.y_hits).get(name)

    def visible(self) -> list[aadr.Group]:
        """Same as Textual visible(), using y_hits instead of hits."""
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
            if self.epoch == "all":
                sample_pool: list[aadr.Sample] = list(self.cat.samples)
            else:
                sample_pool = list(self._samples_by_epoch.get(self.epoch, ()))
            og = self.cat.outgroup
            for s in sample_pool:
                if not self._kept(s) or s.group == og:
                    continue
                g = self._person_group(s)
                if not self._in_region(g):
                    continue
                if self.y_only and not self._on_line(s.gid):
                    continue
                if self.mt_only and not self._on_mt(s.gid):
                    continue
                if (self.y_only or self.mt_only or s.gid in self.hits_people
                        or s.lat is not None or s.gid in self.people):
                    if self._passes_score_filter(s.gid, scores):
                        rows.append(g)
        else:
            if self._gcount_q != self.questionable or not self._gcount_cache:
                self._rebuild_gcount_cache()
            if self.epoch == "all":
                group_pool: list[aadr.Group] = [
                    g for ep in self._groups_by_epoch.values() for g in ep]
            else:
                group_pool = list(self._groups_by_epoch.get(self.epoch, ()))
            for g in group_pool:
                if not self._in_region(g):
                    continue
                if self.y_only and not self._on_line(g.name):
                    continue
                if self.mt_only and not self._on_mt(g.name):
                    continue
                if (self.y_only or self.mt_only or g.name in self.y_hits
                        or g.lat is not None or g.name in self.results):
                    if self._passes_score_filter(g.name, scores):
                        rows.append(g)
        rows.sort(key=lambda g: self._row_key(g, scores))
        if (self.epoch == "all" and self.area == "all"
                and not self.y_only and not self.mt_only
                and len(rows) > 250):
            rows = self._cap_rows(rows, scores)
        return rows

    def _prewarm_world_coast(self) -> None:
        try:
            from ..workspaces.tree.tabs.worldmap import prewarm_world_base
            body = self.query_one("#map", RichText)
            cols, rows_n = self._cells(body)
            prewarm_world_base(cols, rows_n)
            for dcols in (-8, 8, -16, 16):
                prewarm_world_base(max(40, cols + dcols), rows_n)
        except Exception:  # noqa: BLE001
            pass

    # ── list / chrome ────────────────────────────────────────────────
    @property
    def current(self) -> aadr.Group | None:
        ol = self.query_one("#groups", OptionList)
        if not ol.options:
            return None
        opt = ol.highlighted_option
        if opt is None or not opt.id:
            return None
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
        new_ids = [m["g"].name for m in meta]
        opts: list[Option] = []
        for m in meta:
            g = m["g"]
            prompt = group_prompt(
                g, m["r"], m["hit"], ranks.get(g.name),
                place=m["place"], decide=m["decide"],
                outside_head=m["outside_head"],
            )
            opts.append(Option(g.name, id=g.name, lines=_text_lines(prompt)))
        self._list_syncing = True
        try:
            ol.set_options(opts, keep_id=keep if keep in new_ids else None)
        finally:
            self._list_syncing = False
        self._list_ids = new_ids
        self._fill_list_chrome(rows, update_filter=update_filter)
        return rows

    def _fill_list_chrome(self, rows: list[aadr.Group], *, update_filter: bool) -> None:
        bits = [f"{len(rows)} {'people' if self.people_mode else 'groups'}",
                "lo › Z › SNP › Y"]
        if self.y_only:
            bits.append("Y-only")
        if self.mt_only:
            bits.append("mt-only")
        if self.questionable:
            bits.append("Q")
        if self._groups_box is not None:
            self._groups_box.subtitle = " · ".join(bits)
        self._set_groups_title()
        if update_filter:
            self.refresh_score_filter(shown=len(rows))

    def _refresh_bars(self) -> None:
        self._update_rich("source", source_bar(self.source))
        self._update_rich(
            "epochs",
            epoch_bar(self.epoch, self.present_epochs(), self.playing, self._epoch_labels()),
        )
        self._update_rich("regions", region_bar(self.area, self.present_regions()))
        self._update_rich("view-tabs", tab_bar(self.tab))
        title = {"map": "MAP", "y": "Y", "tree": "TREE", "layers": "LAYERS"}.get(self.tab, "MAP")
        if self._map_box is not None:
            self._map_box.title = title

    def _refresh_list_views(self, *, bars: bool = False) -> None:
        rows = self.visible()
        self.fill_list(rows, update_filter=False)
        if bars:
            self._refresh_bars()
        self.refresh_map(vis=rows)
        self.refresh_card()
        self.refresh_score_filter(shown=len(rows))
        self.update_status()

    def _refresh_score_views(self) -> None:
        self._refresh_list_views(bars=False)

    def refresh_score_filter(self, shown: int | None = None) -> None:
        scores = self._scores()
        if shown is None:
            shown = len(self.visible())
        total = sum(1 for r in scores.values() if r.ok)
        box = self.query_one("#score-filter", RichText)
        box.on_action = self._run_action
        width = max(52, ((box.rect.w or 400) // _CELL_W) - 2)
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

    def refresh_all(self) -> None:
        self._refresh_bars()
        self.refresh_map(vis=self._last_vis)
        self.refresh_card()
        shown = len(self._last_vis) if self._last_vis is not None else None
        self.refresh_score_filter(shown=shown)
        self.update_status()

    def refresh_map(self, vis: list[aadr.Group] | None = None) -> None:
        body = self.query_one("#map", RichText)
        body.on_action = self._run_action
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
        cols, rows_n = self._cells(body)

        if self.tab == "y":
            if self.source not in AADR_LIKE:
                self._map_blink_key = None
                self._map_blink_frames = {}
                self._map_points_key = None
                self._map_points = None
                body.update(Text("Y map is AADR/HO-only. Switch with G.", style="#AEA5B8"))
                return
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
            hits = self.hits_people if self.people_mode else self.y_hits
            scores, ranks = self._scores(), self._ranks()
            if self._map_points_key == key and self._map_points is not None:
                points = self._map_points
            else:
                points = y_map_points(
                    vis, scores, ranks, hits, self.y_ours_other, sel)
                self._map_points_key = key
                self._map_points = points
            text = draw_y_map(
                vis, scores, ranks, hits,
                self.y_ours_other, sel,
                title, self.world, cols, rows_n,
                selected_blink=self._sel_blink, points=points)
            self._store_map_blink_frame(key, text)
            body.update(text)
            return

        if self.tab == "tree":
            self._map_blink_key = None
            self._map_blink_frames = {}
            self._map_points_key = None
            self._map_points = None
            body.update(tree_view(
                vis, self._scores(), self._ranks(),
                self.hits_people if self.people_mode else self.y_hits,
                self.qpadm, self.present_epochs() if self.epoch == "all" else [self.epoch],
                labels, self.current.name if self.current else None,
                paint=self.paint_data))
            return

        if self.tab == "layers":
            self._map_blink_key = None
            self._map_blink_frames = {}
            self._map_points_key = None
            self._map_points = None
            self._load_layer_bundles(self.genome, self.questionable)
            aadr_b, aadr_imp = self.layer_aadr, self.layer_aadr_imp
            kg_b, kg_imp = self.layer_kg, self.layer_kg_imp
            if self.source == "aadr_ho":
                aadr_b = aadr.Bundle(
                    groups=dict(self.results), people=dict(self.people),
                    check=self.check, pca=self.pca, qpadm=None,
                    snps=0, outgroup=(self.cat.outgroup if self.cat else "") or "",
                )
                aadr_imp = aadr.Bundle(groups=dict(self.imputed)) if self.imputed else None
            rows = _admix().layer_state(self.store, self.genome, self.source,
                                        aadr_b, kg_b, aadr_imp, kg_imp)
            if self.source in AADR_LIKE:
                chip = aadr_b.groups if aadr_b else {}
                imp = aadr_imp.groups if aadr_imp else {}
            else:
                chip = kg_b.groups if kg_b else {}
                imp = kg_imp.groups if kg_imp else {}
            body.update(layers_view(rows, chip, imp))
            self._layers_shown_sig = self._layers_paint_sig()
            return

        if self.epoch == "all":
            title = {
                "aadr": "ALL EPOCHS",
                "aadr_ho": "HUMAN ORIGINS",
                "kg": "1000 GENOMES",
            }.get(self.source, self.source.upper())
        else:
            title = labels.get(self.epoch, self.epoch)
        if self.people_mode:
            title += " · people"
        seshat: list[dict] = []
        clio: list[dict] = []
        want_polity = self.map_polity or (
            bool(self.map_focus) and str(self.map_focus).startswith("p:"))
        if self.epoch != "all" and self.source in AADR_LIKE:
            skey = (self.epoch, tuple(g.name for g in vis))
            if skey != self._seshat_cache_key:
                self._seshat_cache_key = skey
                self._seshat_cache = seshat_layer_for_groups(
                    vis, epoch=self.epoch, store=self.store)
            seshat = self._seshat_cache
            if want_polity:
                ckey = (self.epoch, self.world, tuple(g.name for g in vis[:80]))
                if ckey != self._clio_cache_key:
                    self._clio_cache_key = ckey
                    self._clio_cache = cliopatria_layer_for_epoch(
                        self.epoch, store=self.store, world=self.world, groups=vis)
                clio = self._clio_cache
            if seshat:
                title += f" · §{len(seshat)}"
        freq_discs: list[dict] = []
        kin_edges: list[dict] = []
        amtdb_anchors: list[dict] = []
        want_freq = self.map_uysd or (
            bool(self.map_focus) and str(self.map_focus).startswith("u:"))
        want_kin = self.map_kin or (
            bool(self.map_focus) and str(self.map_focus).startswith("k:"))
        want_amtdb = self.map_amtdb or (
            bool(self.map_focus) and str(self.map_focus).startswith("m:"))
        if want_freq:
            freq_discs = self._uysd_map_discs()
            if freq_discs:
                title += f" · %{len(freq_discs)}"
        if want_kin and self.source in AADR_LIKE and self.cat:
            kin_edges = self._ancibd_map_edges(vis)
            if kin_edges:
                title += f" · ≈{len(kin_edges)}"
        if want_amtdb:
            amtdb_anchors = self._amtdb_map_anchors()
            if amtdb_anchors:
                title += f" · m{len(amtdb_anchors)}"
        anchors = list(seshat or []) + amtdb_anchors
        sel = self.current.name if self.current else None
        key = self._map_blink_content_key(
            cols=cols, rows_n=rows_n, title=title, sel=sel, vis=vis)
        scores, ranks = self._scores(), self._ranks()
        if self._map_points_key == key and self._map_points is not None:
            points = self._map_points
        else:
            points = map_points(vis, scores, ranks, sel)
            self._map_points_key = key
            self._map_points = points
        text = draw_map(
            vis, scores, ranks, sel, title,
            self.world, cols, rows_n,
            culture_labels=(self.epoch != "all"),
            seshat_anchors=anchors or None,
            cliopatria_polities=clio or None,
            show_habitat=self.map_habitat,
            show_polity=self.map_polity,
            freq_discs=freq_discs or None,
            kin_edges=kin_edges or None,
            show_freq=self.map_uysd,
            show_kin=self.map_kin,
            show_amtdb=self.map_amtdb,
            selected_blink=self._sel_blink,
            map_focus=self.map_focus,
            points=points)
        self._store_map_blink_frame(key, text)
        body.update(text)

    def refresh_card(self) -> None:
        g, sample, key = self._card_subject()
        if g is None:
            title = "GROUP"
        elif sample is not None:
            title = f"PERSON · {sample.gid}"
        else:
            title = f"GROUP · {g.name}"
        if self._card_box is not None:
            self._card_box.title = fit(title, 40)
        self._update_rich("card-tabs", card_tab_bar(self.card_tab))
        info = self._meta_by_name.get(g.name) if g else None
        ranks = self._ranks()
        busy = bool(key and self.summary_job_key == key
                    and (job := JOBS.get(JOB_SUMMARY)) and job.active)
        draft = ""
        model_hint = ""
        if busy and (job := JOBS.get(JOB_SUMMARY)):
            draft = job.draft or ""
            model_hint = chat.wait_line(job)
        card_w = self.query_one("#card", RichText)
        card_w.on_action = self._run_action
        md_w = max(36, ((card_w.rect.w or 400) // _CELL_W) - 2)
        t = card(
            g, sample=sample,
            cat=self.cat if self.source in AADR_LIKE else None,
            hit=self._hit_of(g.name) if g else None,
            branch=self.y_branch.tip if self.y_branch else None,
            questionable=self.questionable,
            qpadm=self.qpadm if self.source == "aadr" else None,
            paint=self.paint_data,
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
            y_hits=self.hits_people,
            store=self.store,
            mt_tip=self.mt_tip,
            mt_tip_score=self.mt_tip_score,
        )
        card_w.update(t)

    def update_status(self) -> None:
        if self.loading_msg:
            g = f"    GENOME: {self.genome.name}" if self.genome else "    GENOME: —"
            self._set_status(f"{self.loading_msg}{g}")
            return
        scored = sum(1 for r in self.results.values() if r.ok)
        if self.source in AADR_LIKE:
            n = self.cat.n_pass if self.cat else 0
            g = len(self.cat.groups) if self.cat else 0
            og = self.cat.outgroup if self.cat else "—"
            people = sum(1 for r in self.people.values() if r.ok)
            tag = "HO" if self.source == "aadr_ho" else "AADR"
            line = f"{tag}: {n:,} samples · {g:,} groups · outgroup {og}    f3: {scored}g/{people}p"
            if self.y_branch:
                line += f"    Y {self.y_branch.tip}"
            if self.mt_tip:
                line += f"    mt {self.mt_tip}"
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
            if self.paint_data and self.paint_data.get("pops"):
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
        self._set_status(line)

    # ── highlight / ticks ────────────────────────────────────────────
    def _on_highlight(self, _opt: Option | None) -> None:
        if self._list_syncing:
            return
        self._sel_blink = True
        self.refresh_card()
        if self.tab not in ("map", "y"):
            return
        if self.app is not None:
            self.app.set_timer(0.05, self._flush_map_selection)

    def _flush_map_selection(self) -> None:
        if self.tab not in ("map", "y"):
            return
        self.refresh_map(vis=self._last_vis)

    def _sel_blink_tick(self) -> None:
        if self.loading_msg or self.tab not in ("map", "y"):
            return
        if not self.current:
            return
        self._sel_blink = not self._sel_blink
        cached = self._map_blink_frames.get(self._sel_blink)
        key = self._map_blink_key
        if cached is not None and key is not None:
            body = self.query_one("#map", RichText)
            cols, rows_n = self._cells(body)
            sel = self.current.name if self.current else None
            if (key[0] == self.tab
                    and key[5] == self.map_habitat and key[6] == self.map_polity
                    and key[7] == self.map_uysd and key[8] == self.map_kin
                    and key[9] == self.map_amtdb
                    and key[10] == self.map_focus
                    and key[11] == sel and key[12] == cols and key[13] == rows_n):
                body.update(cached)
                return
        self.refresh_map(vis=self._last_vis)

    def _play_tick(self) -> None:
        if self.playing:
            self.action_epoch_step(1)

    def _poll_db_stamps(self) -> None:
        genome = self.genome
        if not genome:
            return
        q = self.questionable if self.source in AADR_LIKE else False
        stamp = ref_db.panel_stamps(genome, self.source, q)
        layer_stamp = ref_db.layer_stamps(genome, q if self.source in AADR_LIKE else False)
        if stamp != self.stamp or layer_stamp != self.layer_stamp:
            self.load_results()
            self._refresh_list_views(bars=False)

    def _tick(self) -> None:
        active = [j for k in JOBS_ALL if (j := JOBS.get(k)) and j.active]
        summary = JOBS.get(JOB_SUMMARY)
        if active or self.loading_msg:
            self.update_status()
        if self.tab == "layers" and not self.loading_msg:
            sig = self._layers_paint_sig()
            if sig != self._layers_shown_sig:
                self.refresh_map()
        if self.card_tab == "summary" and summary and summary.active:
            self.refresh_card()
        if self.genome and not self.loading_msg:
            self._stamp_tick += 1
            if active or self._stamp_tick % 4 == 0:
                self._poll_db_stamps()
        for k in JOBS_ALL:
            job = JOBS.get(k)
            if job and not job.active and id(job) not in self.seen_job:
                self.seen_job.add(id(job))
                if k == JOB_SUMMARY:
                    self._finish_summary_job(job)
                    continue
                self._notify(f"{job.title}: {job.message}")
                self.load_results()
                self._refresh_list_views(bars=True)

    # ── actions ──────────────────────────────────────────────────────
    def action_epoch(self, key: str) -> None:
        self.epoch = key
        self.map_focus = None
        self._seshat_cache_key = None
        self._seshat_cache = []
        self._clio_cache_key = None
        self._clio_cache = []
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
        if self.source not in AADR_LIKE:
            self._notify("Y/mt filter is AADR/HO-only")
            return
        if not self.y_only and not self.mt_only:
            self.y_only = True
            self.mt_only = False
            msg = "Y-only"
            if self.y_branch:
                off = sum(1 for h in self.y_hits.values() if h.relation == "other")
                msg = f"Y-only · {self.y_branch.tip} · {off} other Y still listed"
        elif self.y_only:
            self.y_only = False
            if not self.mt_tip:
                self.mt_only = False
                self._notify("mt filter needs Our mt tip (F2 PhyloTree + F1 MT calls)")
                self._refresh_list_views(bars=False)
                return
            self.mt_only = True
            msg = f"mt-only · Our {self.mt_tip}"
        else:
            self.y_only = False
            self.mt_only = False
            msg = "Y/mt filter off"
        self._refresh_list_views(bars=False)
        self._notify(msg)

    def action_toggle_people(self) -> None:
        if self.source == "kg" and not self.kg_people_groups:
            self._notify("1000G people appear after an f3 scan")
        self.people_mode = not self.people_mode
        self._refresh_list_views(bars=True)

    def action_toggle_quest(self) -> None:
        if self.source not in AADR_LIKE:
            self._notify("Questionable is AADR/HO-only")
            return
        self.questionable = not self.questionable
        self.load_results()
        self._refresh_list_views(bars=True)
        if self.questionable and not self.results:
            self._notify("Questionable on — press F to rescan including them")

    def action_toggle_world(self) -> None:
        self.world = not self.world
        self.refresh_map()

    def action_toggle_map_overlay(self) -> None:
        state = (self.map_habitat, self.map_polity,
                 self.map_uysd, self.map_kin, self.map_amtdb)
        cycle = (
            (False, False, False, False, False),
            (True, False, False, False, False),
            (False, True, False, False, False),
            (True, True, False, False, False),
            (False, False, True, False, False),
            (False, False, False, True, False),
            (False, False, False, False, True),
        )
        try:
            i = cycle.index(state)
        except ValueError:
            i = 0
        (self.map_habitat, self.map_polity,
         self.map_uysd, self.map_kin, self.map_amtdb) = cycle[(i + 1) % len(cycle)]
        label = {
            (False, False, False, False, False): "sites only (overlays off)",
            (True, False, False, False, False): "⌂ habitat ranges",
            (False, True, False, False, False): "¶ Cliopatria empires",
            (True, True, False, False, False): "⌂ habitat + ¶ empires",
            (False, False, True, False, False): "% UYSD Y frequencies",
            (False, False, False, True, False): "≈ ancIBD kinship",
            (False, False, False, False, True): "m AmtDB ancient mt",
        }.get((self.map_habitat, self.map_polity,
               self.map_uysd, self.map_kin, self.map_amtdb), "overlays")
        self._notify(f"O · {label}")
        self.refresh_map(vis=self._last_vis)

    def action_score(self) -> None:
        if not self.genome:
            self._notify("Load a genotype file in F1 first")
            return
        gp, store = self.genome, self.store
        ws = self._genome_ws()
        loaded = ws.genome if ws and genome_db.same_path(ws.genome, gp) else None
        if self.source == "kg":
            if self.kg_problem:
                self._notify(self.kg_problem)
                return
            if (job := JOBS.get(JOB_KG)) and job.running:
                self._notify("1000G f3 is already running (C cancels it)")
                return
            JOBS.start(JOB_KG, f"1000G · outgroup-f3 (YRI) · {gp.name}",
                       lambda j: _kg().scan_f3(store, gp, j, loaded))
            self.update_status()
            return
        if self.loading_msg:
            self._notify(self.loading_msg)
            return
        if self.problem:
            self._notify(self.problem)
            return
        if not self.cat or not self.cat.files.get("geno"):
            miss = "Human Origins" if self.source == "aadr_ho" else "AADR"
            self._notify(f"{miss} .geno is missing")
            return
        job_key = JOB_HO if self.source == "aadr_ho" else JOB
        if (job := JOBS.get(job_key)) and job.running:
            self._notify("f3 scan is already running (C cancels it)")
            return
        q = self.questionable
        panel_key = self.source
        label = self._source_label(panel_key)
        JOBS.start(job_key, f"{label} · outgroup-f3 · {gp.name}",
                   lambda j, q=q, pk=panel_key: _fstats().scan_f3(
                       store, gp, j, loaded, q, panel_key=pk))
        self.update_status()

    def action_cancel(self) -> None:
        stopped = 0
        for k in JOBS_ALL:
            job = JOBS.get(k)
            if job and job.active:
                if k == JOB_SUMMARY:
                    chat.abort_send(job)
                job.cancel()
                stopped += 1
        if stopped:
            self._notify(f"Cancelling {stopped} job(s)")
        else:
            self._notify("Nothing is running")

    def action_source(self, key: str) -> None:
        if key not in SRC_CYCLE or key == self.source:
            return
        self.source = key
        self.epoch = "all"
        self.area = "all"
        self.people_mode = False
        self.y_only = False
        self.mt_only = False
        self.reload()

    def action_toggle_source(self) -> None:
        i = SRC_CYCLE.index(self.source) if self.source in SRC_CYCLE else 0
        self.action_source(SRC_CYCLE[(i + 1) % len(SRC_CYCLE)])

    def action_tab(self, key: str) -> None:
        if key not in TABS or key == self.tab:
            return
        self.tab = key
        self.refresh_all()
        try:
            self.query_one("#map-scroll", VerticalScroll).scroll_home()
        except Exception:  # noqa: BLE001
            pass

    def action_toggle_tab(self) -> None:
        i = TABS.index(self.tab) if self.tab in TABS else 0
        self.action_tab(TABS[(i + 1) % len(TABS)])

    def action_card_tab(self, key: str) -> None:
        if key not in CARD_TABS or key == self.card_tab:
            return
        self.card_tab = key
        self.refresh_card()
        try:
            self.query_one("#card-scroll", VerticalScroll).scroll_home()
        except Exception:  # noqa: BLE001
            pass

    def action_cycle_card_tab(self) -> None:
        i = CARD_TABS.index(self.card_tab) if self.card_tab in CARD_TABS else 0
        self.action_card_tab(CARD_TABS[(i + 1) % len(CARD_TABS)])

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
        return sorted({float(v) for v in raw})

    @staticmethod
    def _log_step_size(i: int, n: int, *, dense_at_high: bool) -> int:
        if n <= 2:
            return 1
        if dense_at_high:
            dist = (n - 1 - i) / (n - 1)
        else:
            dist = i / (n - 1)
        return max(1, int(round(1 + dist * max(1.0, n / 20.0))))

    def action_score_step(self, axis: str, end: str) -> None:
        """‹ / › — one log-aware step along observed values."""
        vals = self._score_axis_values(axis)
        if not vals:
            return
        end = "hi" if end in ("hi", "right", "strong") else "lo"
        floor = axis in ("lo", "z", "snp", "n")
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
            if floor and end == "lo":
                i = 0
            elif floor and end == "hi":
                i = max(0, n - 1 - self._log_step_size(n - 1, n, dense_at_high=dense_at_high))
            elif not floor and end == "lo":
                i = min(n - 1, self._log_step_size(0, n, dense_at_high=dense_at_high))
            else:
                i = n - 1
        else:
            i = min(range(n), key=lambda k: (abs(vals[k] - float(cur)), k))
            direction = -1 if end == "lo" else 1
            step = self._log_step_size(i, n, dense_at_high=dense_at_high)
            i = max(0, min(n - 1, i + direction * step))
        self.action_score_set(axis, vals[i])

    def action_score_edge(self, axis: str, end: str) -> None:
        """« / » — jump to bar left (lo) or right (hi) extreme."""
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
        elif axis == "y":
            self.score_y = "all"
        elif axis == "coords":
            self.score_coords = False
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

    def action_map_focus(self, key: str) -> None:
        """Toggle DEFCON radar for ocean-legend culture / Cliopatria / Seshat."""
        key = (key or "").strip()
        if not key:
            return
        self.map_focus = None if self.map_focus == key else key
        self.refresh_map()

    def action_select(self, name: str) -> None:
        try:
            ol = self.query_one("#groups", OptionList)
        except KeyError:
            return
        for i, opt in enumerate(ol.options):
            if opt.id == name:
                ol.highlighted = i
                ol._ensure_visible()
                ol._emit_highlight()
                if self.app is not None:
                    self.app.set_focus(ol)
                return

    def action_ask_summary(self, refresh: bool = False) -> None:
        g, sample, key = self._card_subject()
        if g is None or not key:
            self._notify("Select a group first")
            return
        err = chat.can_send(self.store)
        if err:
            self._notify(err)
            return
        if not refresh and key in self.summary_cache and self.summary_cache[key].get("text"):
            self.card_tab = "summary"
            self.refresh_card()
            return
        if (old := JOBS.get(JOB_SUMMARY)) and old.active:
            old.cancel()
            if not self._wait_summary_slot():
                self._notify("Summary still stopping · C / try again")
                return
        keep: list[aadr.Sample] = []
        if self.cat and self.source in AADR_LIKE:
            keep = [s for s in (self.cat.samples[i] for i in g.samples)
                    if s.keep or (self.questionable and s.questionable)]
        extras = self._summary_extras(g, sample, keep)
        prompt = summary_prompt(g, keep, sample=sample, **extras)
        title = f"Summary · {key}"[:48]
        store = self.store
        subject = key

        def work(job) -> str:
            t = chat.ensure_reference_thread()
            headed = f"[F6 · {subject}]\n\n{prompt}"
            t.messages.append(chat.Message("user", headed))
            chat.save(t)
            chat.send(
                store, t, job, continue_chat=False, tier="reasoning", use_saved=False,
            )
            text = next((m.text for m in reversed(t.messages) if m.role == "model"), "")
            return (text or job.draft or "").strip() or "(empty reply)"

        self.summary_job_key = subject
        self.card_tab = "summary"
        try:
            JOBS.start(JOB_SUMMARY, title, work)
        except RuntimeError as exc:
            self._notify(str(exc))
            return
        self.refresh_card()
        self.update_status()

    def action_layer(self, kind: str) -> None:
        if not self.genome:
            self._notify("Load a genotype file in F1 first")
            return
        if self.loading_msg:
            self._notify(self.loading_msg)
            return
        gp, store = self.genome, self.store
        ws = self._genome_ws()
        loaded = ws.genome if ws and genome_db.same_path(ws.genome, gp) else None
        q = self.questionable
        if kind == "pca":
            if (job := JOBS.get(JOB_PCA)) and job.running:
                self._notify("smartpca is already running")
                return
            JOBS.start(JOB_PCA, f"AADR · smartpca · {gp.name}",
                       lambda j, q=q: _admix().run_smartpca(store, gp, j, loaded, q))
        elif kind == "qpadm":
            if (job := JOBS.get(JOB_QPADM)) and job.running:
                self._notify("qpAdm is already running")
                return
            JOBS.start(JOB_QPADM, f"AADR · qpAdm · {gp.name}",
                       lambda j, q=q: _admix().run_qpadm(store, gp, j, loaded, q))
        elif kind == "impute":
            if (job := JOBS.get(JOB_IMPUTE)) and job.running:
                self._notify("imputation is already running")
                return
            JOBS.start(JOB_IMPUTE, f"Beagle · impute · {gp.name}",
                       lambda j, q=q: _admix().run_impute(store, gp, j, loaded, q))
        elif kind == "paint":
            if (job := JOBS.get(JOB_PAINT)) and job.running:
                self._notify("ChromoPainter is already running")
                return
            JOBS.start(JOB_PAINT, f"1000G · ChromoPainter · {gp.name}",
                       lambda j: _admix().run_paint_job(store, gp, j, loaded))
        else:
            self._notify(f"Unknown layer {kind}")
            return
        self.tab = "layers"
        self.refresh_all()

    def action_open_doi(self, doi: str) -> None:
        doi = (doi or "").strip()
        if not doi:
            return
        url = doi if doi.lower().startswith("http") else f"https://doi.org/{doi}"
        try:
            webbrowser.open(url)
            self._notify(f"Open DOI · {doi}")
        except Exception as exc:  # noqa: BLE001
            self._notify(f"Could not open DOI: {exc}")
