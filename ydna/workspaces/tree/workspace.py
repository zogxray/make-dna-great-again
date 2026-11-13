"""Режим Tree: дерево Y-DNA, инспектор узла, поиск и фильтр."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, Static

from ...core import chat, genome_db
from ...core.jobs import JOBS
from ...core.store import Store
from ...core.ylink import (YBranch, YLink, active_genome, fetch_cost, fetch_resources,
                           link_genome, load_haplotree, our_branch, safe_stem)
from ..base import KeyBar, ModeBar, Workspace, busy_progress, click, hide_busy, show_busy  # KeyBar used for updates
from .ask import (ask_job_title, ask_subject, build_ask_prompt, load_cached, save_cached,
                  tab_title_at)
from .data import (STAGES, _apply_discover, graft_path, hydrate_node, load_or_build,
                   load_resource, node_codes, relink_children, search_text)
from .screens import FilterScreen, HelpScreen, IssuesScreen, SearchResults
from .tabs import TABS, Ctx
from .view import (SHORT_CLASS, SORT_LABELS, SORT_MODES, BranchView, glyph_legend,
                   legend_text, no_haplotree_guide, our_y_styles)

FETCH_JOB = "tree_discover_fetch"  # отдельно от F2 ftdna_discover, иначе джобы блокируют друг друга
ASK_JOB = "tree-ask"


SECTION_KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "minus", "equals_sign"]
SECTION_LABELS = "1234567890-="


def _no_branch_reason(link: YLink, genome) -> str:
    """Почему our_branch вернул None — для notify, без спама."""
    y = getattr(genome, "chroms", {}).get("Y") if genome is not None else None
    if y is not None and y.n:
        nc = 0
        if hasattr(y, "classes") and isinstance(y.classes, dict):
            nc = int(y.classes.get("nocall", 0) or 0)
        if nc >= y.n:
            return f"Y has {y.n:,} SNPs, all no-call (--)"
    states = link.states if isinstance(link.states, Counter) else Counter()
    der = int(states.get("der", 0) or 0)
    noc = int(states.get("nocall", 0) or 0)
    named = int(link.named or 0)
    if named and der == 0 and noc >= named:
        return f"{named:,} named Y SNPs, all no-call (--)"
    if named and der == 0:
        return f"{named:,} named Y SNPs · 0 derived (anc/nocall/mismatch only)"
    if not named:
        return "no named Y SNPs matched the haplotree"
    return "no derived Y path on the haplotree"


class TreeWorkspace(Workspace):
    mode_key = "tree"
    DEFAULT_CSS = """
    #top { height: 3; padding: 0 1; border: round $primary; }
    #stats { width: 1fr; content-align: left middle; }
    #search { width: 34; border: none; height: 1; margin-top: 1; }
    #help { height: 1; padding: 0 1; color: $text-muted; }
    #main { height: 1fr; }
    #tree-box { width: 1fr; border: round $primary; }
    #insp-box { width: 36%; min-width: 38; border: round $primary; }
    #tree-box:focus-within, #insp-box:focus-within { border: round $accent; }
    #crumbs { height: 1; padding: 0; }
    #colhead { height: 1; padding: 0; }
    BranchView { height: 1fr; background: transparent; scrollbar-size-vertical: 1; }
    #legend { height: 2; padding: 0; }
    #tabs { height: auto; padding: 0 1; }
    #inspector-scroll {
        height: 1fr; overflow-y: scroll; scrollbar-size-vertical: 2;
        scrollbar-color: $accent; scrollbar-background: $boost;
    }
    #inspector { padding: 0 1; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """
    BINDINGS = [
        Binding("slash", "focus_search", "Search"),
        Binding("f", "filter", "Filter"),
        Binding("s", "sort", "Sort"),
        Binding("b", "next_rich", "Rich branch"),
        Binding("question_mark", "help", "Help"),
        Binding("i", "issues", "Issues"),
        Binding("tab", "toggle_panel", "Panel", priority=True),
        Binding("escape", "back", "Back"),
        Binding("n", "next_match", "Next match", show=False),
        Binding("o", "our_y", "Our Y", show=False),
        Binding("d", "fetch_node", "Fetch Discover", show=False),
        Binding("shift+d", "fetch_branch", "Fetch branch", show=False),
        Binding("l", "apply_local", "Apply local", show=False),
        Binding("c", "cancel_fetch", "Cancel fetch", show=False),
        Binding("m", "map_fullscreen", "Map fullscreen", show=False),
        Binding("a", "ask_gpt", "Ask", show=False),
        *[Binding(k, f"section({i})", t.title, show=False)
          for i, (k, t) in enumerate(zip(SECTION_KEYS, TABS))],
        Binding("left_square_bracket", "section_step(-1)", "Prev section", show=False),
        Binding("right_square_bracket", "section_step(1)", "Next section", show=False),
        *[Binding(k, f"tab_key('{k}')", show=False) for k in sorted({k for t in TABS for k in t.keys})],
    ]

    def __init__(self, store: Store, folder: Path | None = None) -> None:
        super().__init__()
        self.store = store
        self.folder = folder  # Discover folder; optional enrichment over haplotree
        self.hg_nodes: dict = {}
        self.hg_children: dict = {}
        self.hg_roots: list[str] = []
        self.hg_stats: dict = {}
        self.hg_issues: dict = {}
        self.haystack: dict = {}
        self.ctx: Ctx | None = None
        self.ready = False
        self.stale = False
        self.vis_set: set[str] | None = None
        self.flt: dict = {}
        self.section = 0
        self.current: str | None = None
        self.last_query = ""
        self.hits: list[str] = []
        self.hit_i = -1
        self.branch: YBranch | None = None
        self.branch_genome: Path | None = None
        self.branch_busy: str | None = None   # статус Linking Y… (лоадер до _apply_branch)
        self.our_i: int = 0                  # активный кандидат Our Y при ничьей
        self._fetch_names: list[str] = []
        self._fetch_seen: set[int] = set()
        # Inspector re-paint during fetch only when stage changes (status carries %).
        self._fetch_paint_sig: str | None = None
        self._insp_timer = None  # debounce ↑↓ inspector (hydrate + Migration Map)
        self.ask_cache: dict[str, dict] = {}
        self.ask_job_key: str | None = None
        self.ask_ground_tab: str = "Overview"
        self._ask_seen: set[int] = set()
        self._ask_section: int | None = None

    @property
    def view(self) -> BranchView:
        return self.query_one(BranchView)

    def _active_genome(self) -> Path | None:
        gws = getattr(self.app, "genome_ws", None)
        if gws and gws.active and gws.active.exists():
            return gws.active
        return active_genome()

    def compose(self) -> ComposeResult:
        yield ModeBar(self.mode_key)
        with Horizontal(id="top"):
            yield Static(id="stats")
            yield Input(placeholder="/ search haplogroup or SNP", id="search")
        yield KeyBar(self._keybar_items(), id="help")
        with Horizontal(id="main"):
            with Vertical(id="tree-box"):
                yield Static(id="crumbs")
                yield Static(id="colhead")
                yield BranchView(id="tree")
                legend = legend_text()
                legend.append("\n")
                legend.append_text(glyph_legend())
                yield Static(legend, id="legend")
            with Vertical(id="insp-box"):
                yield Static(id="tabs")
                with VerticalScroll(id="inspector-scroll"):
                    yield Static(id="inspector")
        yield Static(id="status")

    def on_mount(self) -> None:
        self.query_one("#tree-box").border_title = "PHYLOGENETIC TREE"
        box = self.query_one("#insp-box")
        box.border_title = "NODE INSPECTOR"
        box.border_subtitle = "Tab · ↑↓ PgUp/PgDn scroll"
        self.update_tabs()
        self.set_interval(0.5, self._tick_fetch)
        self.start_load()

    # — загрузка —
    def set_folder(self, folder: Path) -> None:
        """Новый путь Discover из режима Data: перезагрузка при следующем показе экрана."""
        if folder != self.folder:
            self.folder = folder
            self.stale = True

    def mark_stale(self) -> None:
        """Haplotree или Discover сменились в F2."""
        self.stale = True

    def on_screen_resume(self) -> None:
        if self.stale:
            self.start_load()
        elif self.ready:
            # Смена генома — только Our Y. start_branch сам no-op, если этот
            # path уже линковали (в т.ч. промах) — иначе hide_busy→resume→loop.
            if self.branch_busy:
                return
            self.start_branch()

    def on_resize(self, event) -> None:
        # Пересчитать ширину инспектора для Map/Geography при ресайзе терминала.
        if self.ready and self.current and self.ctx:
            try:
                self.ctx.panel_width = max(0, int(self.query_one("#insp-box").size.width) - 4)
            except Exception:
                pass
            # Migration Map зависит от cols — перерисовать при ресайзе.
            if 0 <= self.section < len(TABS) and TABS[self.section].title == "Migration Map":
                self.refresh_inspector(keep_scroll=True)

    def start_load(self) -> None:
        self.stale = False
        ht = self.store.path("ftdna_haplotree")
        if not ht or not Path(ht).is_file():
            self._show_no_haplotree(None if not ht else str(ht))
            return
        disc = self.folder if self.folder and Path(self.folder).is_dir() else None
        show_busy(self.app, "Y tree",
                  f"Building / loading haplotree skeleton"
                  + (f"\n+ Discover {disc}" if disc else "\n(Discover not set — skeleton only)"))
        self.run_worker(self._load, thread=True, exclusive=True, name="loader")

    def _show_no_haplotree(self, missing_path: str | None) -> None:
        """Онбординг вместо пустого дерева / error notify, когда Haplotree не готов."""
        hide_busy(self.app)
        self.ready = False
        self.hg_nodes, self.hg_children, self.hg_roots = {}, {}, []
        self.hg_stats, self.hg_issues, self.haystack = {}, {}, {}
        self.ctx = None
        self.vis_set, self.flt = None, {}
        self.current, self.hits, self.hit_i = None, [], -1
        self.branch, self.branch_genome, self.branch_busy = None, None, None
        self.view.set_source({}, {}, [])
        self.view.set_data(None)
        self.view.mine = set()
        self.view.tip = None
        self.view.our_styles = {}
        self.view.our_uncertain = set()
        self.view.our_pruned = set()
        self.our_i = 0
        self.query_one("#tree-box").border_title = "TREE · needs Haplotree"
        self.query_one("#stats", Static).update(
            "Haplotree not set · F2 → P" if missing_path is None
            else "Haplotree file missing · F2 → P")
        self.query_one("#colhead", Static).update("")
        self.query_one("#legend", Static).update(
            Text("Discover is optional · Our Y needs a genome in F1 after the tree loads",
                 style="#71697A"))
        self.query_one("#inspector", Static).update(no_haplotree_guide(missing_path))
        self.update_tabs()
        self.update_status()

    def _load(self) -> None:
        names = [n for n, _ in STAGES]

        def prog(stage: str, done: int, total: int) -> None:
            frac = done / total if total else 1.0
            if stage in names:
                idx = names.index(stage)
                before = sum(w for _, w in STAGES[:idx])
                overall = (before + STAGES[idx][1] * frac) / 100.0
                label = f"[{idx + 1}/{len(STAGES)}] {stage}"
            else:
                overall, label = frac, stage
            self.app.call_from_thread(
                busy_progress, self.app, label, frac=overall, done=done, total=total)

        ht = self.store.path("ftdna_haplotree")
        disc = self.folder if self.folder and Path(self.folder).is_dir() else None
        try:
            if not ht or not Path(ht).is_file():
                # Гонка с F2: путь пропал после start_load — тихий empty-state, не error.
                self.app.call_from_thread(
                    self._show_no_haplotree, None if not ht else str(ht))
                return
            data, cached = load_or_build(Path(ht), disc, prog)
        except Exception as exc:  # noqa: BLE001
            msg = f"Failed to load data: {exc}"
            self.app.call_from_thread(self._failed, msg)
            return
        self.app.call_from_thread(self._loaded, data, cached)

    def _failed(self, msg: str) -> None:
        hide_busy(self.app)
        if not self.ready:
            self.query_one("#tree-box").border_title = "TREE · load failed"
            self.query_one("#inspector", Static).update(
                Text(f"{msg}\n\nCheck the Haplotree path in F2 → P, then reopen F3.",
                     style="#8BAB4A"))
            self.query_one("#stats", Static).update("Load failed · see inspector")
        self.app.notify(msg, severity="error", timeout=10)

    def _loaded(self, data: dict, cached: bool) -> None:
        self.vis_set, self.flt, self.current, self.hits = None, {}, None, []
        self.hg_nodes, self.hg_children, self.hg_roots = data["nodes"], data["children"], data["roots"]
        self.hg_stats, self.hg_issues, self.haystack = data["stats"], data["issues"], data["haystack"]
        folder = self._discover_folder() or Path(".")
        self.folder = folder if folder != Path(".") else self.folder
        self.ctx = Ctx(self.hg_nodes, self.hg_children, folder, self.hg_stats,
                       store=self.store)
        self.ctx.workspace = self
        self.ctx.ask_cache = self.ask_cache
        self.ctx.ask_ground_tab = self.ask_ground_tab
        self.view.set_source(self.hg_nodes, self.hg_children, self.hg_roots,
                             self.hg_stats.get("max_desc", 0))
        try:
            chat.ensure_tree_thread()
        except Exception:
            pass
        self.query_one("#tree-box").border_title = "PHYLOGENETIC TREE"
        self.ready = True
        # BusyModal держим до Our Y — иначе дерево мигает без подсветки ветки.
        self.branch = None
        self.branch_genome = None
        self.our_i = 0
        self.view.mine = set()
        self.view.tip = None
        self.view.our_styles = {}
        self.view.our_uncertain = set()
        self.view.our_pruned = set()
        if self.ctx:
            self.ctx.ours = set()
            self.ctx.tip = None
        self.populate()
        self.update_legend()
        self.view.on_resize()
        disc_n = self.hg_stats.get("discover_nodes", self.hg_stats.get("real_nodes", 0))
        total = self.hg_stats.get("nodes", len(self.hg_nodes))
        if cached:
            self.app.notify(f"Y tree from cache · {total:,} nodes · {disc_n:,} Discover")
        else:
            self.app.notify(f"Y tree built · {total:,} nodes · {disc_n:,} Discover")
        if self.hg_stats.get("issues"):
            self.app.notify(f"{self.hg_stats['issues']} data issue(s) — press I", severity="warning")
        self.start_branch()

    def populate(self) -> None:
        view = self.view
        view.set_data(self.vis_set)
        s = self.hg_stats
        flt = " · FILTER ON" if self.vis_set is not None else ""
        warn = f"   ⚠ {s['issues']} issues (I)" if s.get("issues") else ""
        total = s.get("nodes", s.get("real_nodes", 0))
        disc = s.get("discover_nodes", s.get("real_nodes", 0))
        skel = s.get("skeleton_nodes", s.get("placeholder_nodes", 0))
        self.query_one("#stats", Static).update(
            f"{total:,} nodes   {disc:,} Discover   {skel:,} skeleton   "
            f"{s['roots']} roots   {s.get('rich_nodes', 0):,} rich   shown {len(view.rows):,}   "
            f"tree {s.get('tree_version') or '?'}{flt}{warn}")
        if not view.rows:
            self.current = None
            self.query_one("#inspector", Static).update("Nothing matches the filter.")
            self.update_status()

    def update_legend(self) -> None:
        legend = legend_text()
        legend.append("\n")
        n = self.branch.n_candidates if self.branch else 1
        noise = bool(self.branch and (
            self.branch.uncertain or self.branch.pruned or self.branch.uncertain_marks))
        legend.append_text(glyph_legend(
            self.branch.tip if self.branch else None,
            n_candidates=n, show_noise=noise,
            tie_label=self.branch.tie_label if self.branch else ""))
        self.query_one("#legend", Static).update(legend)

    def start_branch(self, force: bool = False) -> None:
        """Пересчёт Our Y. Лоадер закрывается только в _apply_branch.

        Уже посчитанный геном (успех или «нет ветки») не трогаем снова —
        иначе hide_busy → on_screen_resume → бесконечный Our Y / notify spam
        на файлах без usable Y (сплошные --).
        """
        if not self.ready:
            return
        path = self._active_genome()
        # Тот же path уже линковали (branch или явный промах) — не крутить снова.
        if not force and path is not None and path == self.branch_genome:
            hide_busy(self.app)
            self.view.focus()
            return
        if not force and path is None and self.branch_genome is None and self.branch is None:
            hide_busy(self.app)
            self.view.focus()
            return
        if not path:
            self.branch_busy = None
            self._apply_branch(None, None)
            return
        show_busy(self.app, "Our Y", f"Linking branch for {path.name}")
        busy_progress(self.app, "Starting…", frac=0.0)
        self.branch_busy = f"Linking Y for {path.name}…"
        self.update_status()
        self.run_worker(self._load_branch, thread=True, exclusive=True, group="ybranch")

    def _branch_progress(self, path: Path, stage: str, done: int = 0, total: int = 1) -> None:
        if path != self._active_genome():
            return
        bit = f"{done}/{total}" if total > 1 else ""
        self.branch_busy = f"Linking Y for {path.name}… {stage}" + (f" {bit}" if bit else "")
        busy_progress(self.app, stage, done=done, total=total,
                      title="Our Y", subtitle=f"Linking branch for {path.name}")
        self.update_status()

    def _load_branch(self) -> None:
        store = getattr(self.app, "store", None)
        if store is None:
            return
        path = self._active_genome()
        if not path:
            self.app.call_from_thread(self._apply_branch, None, None)
            return

        def report(stage: str, done: int = 0, total: int = 1) -> None:
            self.app.call_from_thread(self._branch_progress, path, stage, done, total)

        gws = getattr(self.app, "genome_ws", None)
        try:
            from ...core import tree_db
            tree_path = store.path("ftdna_haplotree")
            disc = store.path("ftdna_discover")
            # Кэш ветки первым: Y/link только при промахе.
            report("loading branch cache")
            branch = tree_db.load_branch(path, tree_path, disc)
            if branch is not None:
                names: set[str] = set()
                for p in (branch.candidates or [branch.path]):
                    names.update(p)
                names.update(branch.uncertain or {})
                names.update(branch.pruned or [])
                # Full haplotree only if Our Y names are missing from the F3 skeleton.
                if any(n not in self.hg_nodes for n in names):
                    report("loading haplotree")
                    tree = (load_haplotree(tree_path)
                            if tree_path and Path(tree_path).is_file() else None)
                    if tree is None:
                        self.app.call_from_thread(
                            self._apply_branch, None, path, None, "haplotree not loaded")
                        return
                else:
                    tree = None
                self.app.call_from_thread(self._apply_branch, branch, path, tree)
                return
            report("loading genome")
            g = genome_db.get_y(path, gws.genome if gws else None)
            if gws and isinstance(getattr(gws, "link", None), YLink) and gws.genome is g:
                link: YLink | str | None = gws.link
            else:
                report("linking named Y markers")
                link = link_genome(g, store)
            report("loading haplotree")
            tree = (load_haplotree(tree_path)
                    if tree_path and Path(tree_path).is_file() else None)
            if not isinstance(link, YLink) or not tree:
                why = link if isinstance(link, str) else (
                    "haplotree not loaded" if not tree else "no usable Y link")
                self.app.call_from_thread(self._apply_branch, None, path, None, why)
                return
            branch = our_branch(link, tree, g, disc, progress=report)
            if branch:
                try:
                    tree_db.save_branch(path, branch, tree_path, disc)
                except Exception:
                    pass
            why = None if branch else _no_branch_reason(link, g)
            self.app.call_from_thread(self._apply_branch, branch, path, tree, why)
        except Exception:
            self.app.call_from_thread(
                self._apply_branch, None, path, None, "Our Y link failed")

    def _apply_branch(self, branch: YBranch | None, genome: Path | None,
                      tree=None, reason: str | None = None) -> None:
        if not self.ready:
            return
        # Устаревший воркер после смены генома — не трогаем текущий Linking Y / лоадер.
        if genome != self._active_genome():
            return
        self.branch = branch
        # Помечаем геном обработанным даже при промахе — стоп resume→retry loop.
        self.branch_genome = genome
        self.our_i = 0
        if not branch:
            hide_busy(self.app)
            self.branch_busy = None
            self.view.mine = set()
            self.view.tip = None
            self.view.our_styles = {}
            self.view.our_uncertain = set()
            self.view.our_pruned = set()
            if self.ctx:
                self.ctx.ours = set()
                self.ctx.tip = None
            self.update_legend()
            self.update_status()
            self.view.focus()
            if genome:
                detail = reason or "no derived Y path"
                self.app.notify(
                    f"No Y branch for {genome.name} · {detail}",
                    severity="warning", timeout=8)
            return
        branch.select(0)
        cands = list(branch.candidates or [branch.path])
        # Подсветка: graft всех ничьих путей, не только primary.
        # tree may be None when every name is already in the F3 skeleton (cached branch).
        added = 0
        all_names: set[str] = set()
        for path in cands:
            if tree is not None:
                added += graft_path(self.hg_nodes, tree, path)
            all_names.update(path)
        all_names.update(branch.uncertain or {})
        all_names.update(branch.pruned or [])
        rebuilt = False
        if added:
            self.hg_children, self.hg_roots = relink_children(self.hg_nodes)
            self.view.set_source(self.hg_nodes, self.hg_children, self.hg_roots,
                                 self.hg_stats.get("max_desc", 0))
            if self.ctx:
                self.ctx.nodes = self.hg_nodes
                self.ctx.children = self.hg_children
            for name in all_names:
                if name not in self.haystack and name in self.hg_nodes:
                    self.haystack[name] = search_text(self.hg_nodes[name])
            rebuilt = True
        if self.vis_set is not None:
            extra = {n for n in all_names if n in self.hg_nodes} - self.vis_set
            if extra:
                self.vis_set.update(extra)
                rebuilt = True
        # Keep branch_busy through populate/focus: set_data resets cursor to a
        # root and would otherwise trigger a full-tree Discover scan.
        if rebuilt:
            self.populate()
        self._paint_our_y()
        self.update_legend()
        shown = self.view.focus_path(branch.path)
        # focus_path posts Highlighted async — set current now so keybar/inspector
        # don't scan the previous root while the message is still queued.
        if shown:
            self.current = shown
        hide_busy(self.app)
        self.branch_busy = None
        self.update_keybar()
        self.update_status()
        if self.current:
            self.refresh_inspector(keep_scroll=True)
        gname = genome.name if genome else "?"
        self.view.focus()
        if shown:
            grafted = f" · {added} from haplotree" if added else ""
            if branch.n_candidates > 1:
                tie = f" · {branch.n_candidates} {branch.tie_label or 'tied'} · O cycles"
            else:
                tie = ""
            noise = ""
            if branch.uncertain_marks:
                noise = f" · {branch.uncertain_marks} uncertain"
            if branch.pruned:
                noise += f" · {len(branch.pruned)} pruned"
            self.app.notify(
                f"Our Y: {branch.tip} · {gname}  "
                f"({branch.derived_marks} DER on path{grafted}{tie}{noise})")
        else:
            self.app.notify(f"Our Y {branch.tip} · {gname} is hidden by the filter",
                            severity="warning")

    def _paint_our_y(self) -> None:
        """mine / цвета / tip / uncertain·pruned / ctx из активной ничьей Our Y."""
        branch = self.branch
        if not branch:
            self.view.mine = set()
            self.view.tip = None
            self.view.our_styles = {}
            self.view.our_uncertain = set()
            self.view.our_pruned = set()
            if self.ctx:
                self.ctx.ours = set()
                self.ctx.tip = None
            return
        cands = list(branch.candidates or [branch.path])
        mine, styles = our_y_styles(cands)
        mine = {n for n in mine if n in self.hg_nodes}
        self.view.mine = mine
        self.view.our_styles = {n: s for n, s in styles.items() if n in mine}
        self.view.our_uncertain = {n for n in (branch.uncertain or {}) if n in self.hg_nodes}
        self.view.our_pruned = {n for n in (branch.pruned or []) if n in self.hg_nodes}
        tip = branch.tip if branch.tip in mine else (branch.path[-1] if branch.path else None)
        self.view.tip = tip if tip in mine else None
        if self.ctx:
            # Inspector: все ничьи пути — «наши»; tip — активный кандидат.
            self.ctx.ours = mine
            self.ctx.tip = self.view.tip
        try:
            self.view.refresh()
        except Exception:
            pass

    def action_our_y(self) -> None:
        if not self.ready:
            return
        if self.branch_busy:
            self.app.notify(self.branch_busy)
            return
        if not self.branch:
            path = self._active_genome()
            if not path:
                self.app.notify("Load a genotype file in F1 first", severity="warning")
                return
            self.start_branch(force=True)
            return
        n = self.branch.n_candidates
        # Уже на активном tip → следующий кандидат; иначе сначала вернуть к текущему.
        if n > 1 and self.current == self.branch.tip:
            self.our_i = (self.our_i + 1) % n
            self.branch.select(self.our_i)
            self._paint_our_y()
            self.update_legend()
            self.update_keybar()
            self.update_status()
        shown = self.view.focus_path(self.branch.path)
        self.view.focus()
        gname = self.branch_genome.name if self.branch_genome else "?"
        if shown:
            if n > 1:
                self.app.notify(f"Our Y {self.our_i + 1}/{n}: {shown} · {gname}")
            else:
                self.app.notify(f"Our Y: {shown} · {gname}")
        else:
            self.app.notify(f"Our Y {self.branch.tip} · {gname} is hidden by the filter",
                            severity="warning")

    # — события дерева —
    @on(BranchView.Highlighted)
    def _highlighted(self, event: BranchView.Highlighted) -> None:
        if event.name == self.current:
            return
        self.current = event.name
        # Status updates immediately; heavy inspector waits until arrows settle.
        self.update_status()
        self._schedule_inspector()

    def _schedule_inspector(self) -> None:
        if self._insp_timer is not None:
            self._insp_timer.stop()
        self._insp_timer = self.set_timer(0.08, self._flush_inspector)

    def _flush_inspector(self) -> None:
        self._insp_timer = None
        self.refresh_inspector()

    def _cancel_inspector_timer(self) -> None:
        if self._insp_timer is not None:
            self._insp_timer.stop()
            self._insp_timer = None

    @on(BranchView.Changed)
    def _changed(self) -> None:
        self.update_status()

    @on(BranchView.Selected)
    def _selected(self) -> None:
        self._cancel_inspector_timer()
        self.refresh_inspector(keep_scroll=True)
        self.query_one("#inspector-scroll").focus()

    def _keybar_items(self) -> list[tuple[str, str, str | None]]:
        items: list[tuple[str, str, str | None]] = [
            ("↑↓", "Navigate", None), ("←→", "Fold", None), ("/", "Search", "screen.focus_search"),
            ("F", "Filter", "screen.filter"), ("S", "Sort", "screen.sort"),
            ("B", "Rich", "screen.next_rich"),
            ("O", "Our Y" + (" · cycle" if self.branch and self.branch.n_candidates > 1 else ""),
             "screen.our_y"),
            ("A", "Ask", "screen.ask_gpt"),
            ("E", "Expand all", "screen.tree('expand_all')"),
            ("Z", "Collapse all", "screen.tree('collapse_all')"), ("Tab", "Panel", "screen.toggle_panel"),
            ("[", "Prev", "screen.section_step(-1)"), ("]", "Next section", "screen.section_step(1)"),
        ]
        if self._fetch_running():
            items.append(("C", "Cancel fetch", "screen.cancel_fetch"))
        elif self.ready and self.current and self.ctx and not self.branch_busy:
            cur = self.current
            n_miss, _, self_miss, self_local = self.ctx.discover_subtree_stats(cur)
            if self_local:
                items.append(("L", "Apply local", "screen.apply_local"))
            if self_miss:
                items.append(("D", "Fetch node", "screen.fetch_node"))
            if n_miss and not (self_miss and n_miss == 1):
                items.append(("⇧D", "Fetch branch", "screen.fetch_branch"))
        items += [("I", "Issues", "screen.issues"), ("?", "Help", "screen.help"),
                  ("Q", "Quit", "app.quit")]
        return items

    def update_keybar(self) -> None:
        try:
            self.query_one("#help", KeyBar).set_items(self._keybar_items())
        except Exception:
            pass

    def refresh_inspector(self, *, keep_scroll: bool = False) -> None:
        if not self.current or not self.ctx:
            return
        self._cancel_inspector_timer()
        # Under Our Y BusyModal the cursor often sits on a root; Discover
        # subtree scans there are invisible and very large — skip until linked.
        if self.branch_busy:
            return
        # JSON уже на диске, а узел ещё skeleton — подтянуть без сети.
        node = self.hg_nodes.get(self.current)
        if (node and node.get("placeholder") and node.get("haplotree")
                and not self._fetch_running()
                and self.ctx.has_discover_file(self.current)):
            self._ingest_discover([self.current], quiet=True)
            node = self.hg_nodes.get(self.current)
        # slim DuckDB-узел → тяжёлые Discover-поля с диска
        if node and self.folder:
            hydrate_node(node, Path(self.folder))
        if node and node.get("placeholder") and self.section != 0:
            self.section = 0
        self._sync_fetch_ctx()
        self._sync_ask_ctx()
        # Ширина инспектора для Map / Geography (иначе fallback ~60).
        try:
            self.ctx.panel_width = max(0, int(self.query_one("#insp-box").size.width) - 4)
        except Exception:
            self.ctx.panel_width = 0
        self.update_tabs()
        self.update_keybar()
        self.query_one("#inspector", Static).update(self.ctx.render(self.current, self.section))
        if not keep_scroll:
            self.query_one("#inspector-scroll").scroll_home(animate=False)
        self.update_status()

    def _sync_fetch_ctx(self) -> None:
        """Пробросить состояние JOBS → ctx.fetch для инспектора."""
        if not self.ctx:
            return
        job = JOBS.get(FETCH_JOB)
        if self._fetch_names and job and job.running:
            self.ctx.fetch = {
                "title": job.title,
                "stage": job.stage,
                "done": job.done,
                "total": job.total,
                "fraction": job.fraction,
            }
        else:
            self.ctx.fetch = None

    def update_tabs(self) -> None:
        t = Text()
        node = self.hg_nodes.get(self.current) if self.current else None
        if node and node.get("placeholder"):
            t.append(" 1 Overview ", style="bold reverse #D4AD72")
            t.append("· skeleton — other tabs after Discover", style="#8A8294")
        else:
            for i, tb in enumerate(TABS):
                if i and i % 4 == 0:
                    t.append("\n")
                style = "bold reverse #D4AD72" if i == self.section else "#AEA5B8"
                key = SECTION_LABELS[i] if i < len(SECTION_LABELS) else "·"
                t.append(f" {key} {tb.title} ", style=click(f"screen.section({i})", style))
                t.append(" ")
        self.query_one("#tabs", Static).update(t)

    def _discover_folder(self) -> Path | None:
        path = self.store.path("ftdna_discover") or self.folder
        return Path(path) if path and Path(path).is_dir() else None

    def _fetch_running(self) -> bool:
        job = JOBS.get(FETCH_JOB)
        return bool(self._fetch_names and job and job.running)

    def _start_fetch(self, names: list[str], title: str) -> None:
        if not names:
            self.app.notify("Nothing to fetch in this branch")
            return
        folder = self._discover_folder()
        if not folder:
            self.app.notify("Set the Discover folder in F2 (Data → P) first", severity="warning")
            return
        if self.ctx:
            self.ctx.folder = folder
        job = JOBS.get(FETCH_JOB)
        if job and job.running:
            self.app.notify(f"Discover fetch already running: {job.stage}", severity="warning")
            return
        try:
            JOBS.start(FETCH_JOB, title,
                       lambda j, ns=list(names), f=folder: fetch_resources(f, ns, j))
        except RuntimeError as exc:
            self.app.notify(str(exc), severity="warning")
            return
        self._fetch_names = list(names)
        self.app.notify(f"{title} · {fetch_cost(len(names))}")
        self._sync_fetch_ctx()
        self.update_keybar()
        if self.current and self.ctx:
            self.query_one("#inspector", Static).update(
                self.ctx.render(self.current, self.section))
        job2 = JOBS.get(FETCH_JOB)
        self._fetch_paint_sig = (job2.stage or "") if job2 else ""
        self.update_status()

    def action_fetch_node(self) -> None:
        """Докачать Discover JSON для текущего skeleton-узла."""
        if self._fetch_running():
            self.app.notify("Fetch already in progress — press C to cancel", severity="warning")
            return
        name = self.current
        node = self.hg_nodes.get(name) if name else None
        if not name or not node:
            self.app.notify("Select a haplogroup first", severity="warning")
            return
        if not node.get("placeholder"):
            self.app.notify(f"{name}: Discover card already loaded")
            return
        if not node.get("haplotree"):
            self.app.notify(f"{name}: not a haplotree node — cannot fetch", severity="warning")
            return
        if self.ctx and self.ctx.has_discover_file(name):
            self.action_apply_local()
            return
        self._start_fetch([name], f"Fetch {name}")

    def action_fetch_branch(self) -> None:
        """Докачать Discover для текущего узла и всех потомков без карточки."""
        if self._fetch_running():
            self.app.notify("Fetch already in progress — press C to cancel", severity="warning")
            return
        name = self.current
        if not name or not self.ctx:
            self.app.notify("Select a haplogroup first", severity="warning")
            return
        missing = self.ctx.missing_discover(name)
        if not missing:
            local = self.ctx.local_discover(name)
            if local:
                self._ingest_discover(local)
                return
            self.app.notify(f"{name}: nothing to download in this branch")
            return
        self._start_fetch(missing, f"Fetch branch {name} · {len(missing):,}")

    def action_apply_local(self) -> None:
        """Влить уже скачанный JSON текущего узла / потомков с файлами на диске."""
        name = self.current
        if not name or not self.ctx:
            self.app.notify("Select a haplogroup first", severity="warning")
            return
        local = self.ctx.local_discover(name)
        if not local:
            self.app.notify(f"{name}: no local Discover JSON on disk", severity="warning")
            return
        self._ingest_discover(local)

    def action_cancel_fetch(self) -> None:
        job = JOBS.get(FETCH_JOB)
        if not job or not job.running or not self._fetch_names:
            self.app.notify("No Discover fetch to cancel")
            return
        job.cancel()
        self.app.notify("Cancelling Discover fetch…")

    def _ask_section_index(self) -> int:
        if self._ask_section is not None:
            return self._ask_section
        idx = next((i for i, t in enumerate(TABS) if t.title == "ASK"), -1)
        self._ask_section = idx if idx >= 0 else 0
        return self._ask_section

    def _sync_ask_ctx(self) -> None:
        if not self.ctx:
            return
        self.ctx.workspace = self
        self.ctx.ask_cache = self.ask_cache
        self.ctx.ask_ground_tab = self.ask_ground_tab
        self.ctx.ask_job = JOBS.get(ASK_JOB)

    def _tick_fetch(self) -> None:
        ask = JOBS.get(ASK_JOB)
        if ask:
            if ask.active:
                if self.ask_job_key and self.section == self._ask_section_index():
                    self._sync_ask_ctx()
                    if self.current and self.ctx:
                        self.query_one("#inspector", Static).update(
                            self.ctx.render(self.current, self.section))
                self.update_status()
            elif id(ask) not in self._ask_seen:
                self._ask_seen.add(id(ask))
                if self.ask_job_key:
                    self._finish_ask_job(ask)

        job = JOBS.get(FETCH_JOB)
        if self._fetch_names and job and job.running:
            self._sync_fetch_ctx()
            # Live % stays in the status line. Full inspector (Migration Map etc.)
            # only when Discover stage text changes — not every 0.5s progress tick.
            stage = job.stage or ""
            if stage != self._fetch_paint_sig:
                self._fetch_paint_sig = stage
                if self.current and self.ctx:
                    self.query_one("#inspector", Static).update(
                        self.ctx.render(self.current, self.section))
            self.update_status()
            return
        if not self._fetch_names:
            if self.ctx and self.ctx.fetch:
                self.ctx.fetch = None
                self._fetch_paint_sig = None
                self.update_keybar()
                if self.current:
                    self.query_one("#inspector", Static).update(
                        self.ctx.render(self.current, self.section))
            return
        # Имена есть, джоба уже не running — завершить или снять залипание.
        self._fetch_paint_sig = None
        if job is None or id(job) in self._fetch_seen:
            self._fetch_names = []
            self._sync_fetch_ctx()
            self.update_keybar()
            if self.current and self.ctx:
                self.query_one("#inspector", Static).update(
                    self.ctx.render(self.current, self.section))
            self.update_status()
            return
        self._fetch_seen.add(id(job))
        names = self._fetch_names
        self._fetch_names = []
        self._sync_fetch_ctx()
        sev = {"done": "information", "failed": "error", "cancelled": "warning"}.get(
            job.status, "warning")
        self.app.notify(f"{job.title}: {job.message}", severity=sev, timeout=8)
        if job.status == "done":
            self._ingest_discover(names)
        else:
            self.update_keybar()
            if self.current and self.ctx:
                self.query_one("#inspector", Static).update(
                    self.ctx.render(self.current, self.section))
            self.update_status()

    def _ingest_discover(self, names: list[str] | str, *, quiet: bool = False) -> int:
        """Подмешать Discover JSON в узлы. Возвращает число обогащённых узлов."""
        if isinstance(names, str):
            names = [names]
        folder = self._discover_folder()
        if not folder:
            if not quiet:
                self.app.notify("Discover folder not set", severity="warning")
            return 0
        if self.ctx:
            self.ctx.folder = folder
            self.ctx.invalidate_discover_index()
        loaded = skipped = 0
        for name in names:
            if name not in self.hg_nodes:
                continue
            path = folder / f"{safe_stem(name)}.json"
            if not path.is_file():
                skipped += 1
                continue
            data, err = load_resource(path)
            if err or not data:
                skipped += 1
                continue
            was = self.hg_nodes[name].get("placeholder")
            _apply_discover(self.hg_nodes[name], data, path.name)
            self.haystack[name] = search_text(self.hg_nodes[name])
            if was:
                loaded += 1
        if loaded:
            disc = self.hg_stats.get("discover_nodes", 0) + loaded
            skel = max(0, self.hg_stats.get("skeleton_nodes", 0) - loaded)
            self.hg_stats["discover_nodes"] = disc
            self.hg_stats["real_nodes"] = disc
            self.hg_stats["skeleton_nodes"] = skel
            self.hg_stats["placeholder_nodes"] = skel
            self.section = 0
            self.populate()
            if self.current:
                self.view.reveal(self.current)
            self._sync_fetch_ctx()
            self.update_tabs()
            self.update_keybar()
            if self.current and self.ctx:
                self.query_one("#inspector", Static).update(
                    self.ctx.render(self.current, self.section))
            self.update_status()
        if not quiet:
            msg = f"Loaded {loaded:,} Discover card(s)"
            if skipped:
                msg += f" · {skipped:,} missing on disk (alias / not in Discover)"
            self.app.notify(msg, severity="warning" if not loaded else "information")
        return loaded

    def update_crumbs(self) -> None:
        crumbs = self.query_one("#crumbs", Static)
        if not self.ready:
            crumbs.update(Text("Set FTDNA Haplotree in F2 → P, then return to F3.",
                               style="#8A8294"))
            return
        if not self.current or not self.ctx:
            crumbs.update("")
            return
        chain = self.ctx.lineage(self.current)
        width = max(12, self.view.size.width)
        cut = False
        while len(chain) > 1 and sum(len(n) for n in chain) + 3 * (len(chain) - 1) + (4 if cut else 0) > width:
            chain, cut = chain[1:], True
        t = Text(no_wrap=True, overflow="ellipsis")
        if cut:
            t.append("… › ", style="#8A8294")
        for i, n in enumerate(chain):
            if i:
                t.append(" › ", style="#8A8294")
            missing = self.hg_nodes[n].get("placeholder")
            our = self.view.our_styles.get(n) if n in self.view.mine else None
            if n == self.current:
                if our:
                    style = f"bold {our}"
                else:
                    style = "bold #53A94B" if n in self.view.mine else "bold #D4AD72"
            elif our:
                style = our
            elif n in self.view.mine:
                style = "#53A94B"
            else:
                style = "#8A8294" if missing else "#C3BCCC"
            t.append(n, style=click(f"screen.goto({n!r})", style))
            if n in self.view.our_uncertain:
                t.append("?", style="bold #B04A4A")
        crumbs.update(t)

    def update_status(self) -> None:
        view = self.view
        name = self.current or "—"
        node = self.hg_nodes.get(self.current) if self.current else None
        depth = len(self.ctx.lineage(name)) - 1 if (self.current and self.ctx) else 0
        pos = f"{view.cursor + 1}/{len(view.rows)}" if view.rows else "0/0"
        size = ""
        if node:
            rank = node.get("rank")
            rank_s = f"#{rank:,}/{self.hg_stats.get('branching', 0):,}" if rank else "leaf"
            size = (f"    {SHORT_CLASS[node.get('rich', 'normal')]}"
                    f"  desc {node.get('descendants_count', 0):,} ({rank_s})  ↓{node.get('height', 0)}")
        if self.branch_busy:
            y = f"    {self.branch_busy}"
        elif self.branch:
            gname = self.branch_genome.name if self.branch_genome else "?"
            n = self.branch.n_candidates
            slot = f"{self.our_i + 1}/{n} " if n > 1 else ""
            bits = [f"{self.branch.derived_marks} DER"]
            if self.branch.uncertain_marks:
                u = self.branch.uncertain_marks
                het = self.branch.uncertain_het
                bits.append(f"{u} uncertain" + (f" ({het} het)" if het else ""))
            if self.branch.pruned:
                bits.append(f"{len(self.branch.pruned)} pruned")
            if n > 1:
                bits.append(self.branch.tie_label or "tied")
                bits.append("O cycles")
            y = f"    Y: {slot}{self.branch.tip} · {gname} ({' · '.join(bits)})"
        else:
            active = self._active_genome()
            y = f"    Y: — · {active.name}" if active else "    Y: —"
        fetch = ""
        job = JOBS.get(FETCH_JOB)
        if self._fetch_names and job and job.running:
            bit = f"{job.done}/{job.total}" if job.total else str(job.done or "…")
            pct = f"{job.fraction:.0%}" if job.fraction is not None else ""
            fetch = f"    ⇣ {job.title}: {job.stage} {pct} {bit}".rstrip()
        self.query_one("#status", Static).update(
            f"SELECTED: {name}{size}    DEPTH: {depth}    VISIBLE: {len(view.rows):,}    "
            f"POS: {pos}{y}{fetch}")
        self.update_crumbs()

    # — действия —
    def action_section(self, i: int) -> None:
        if not self.ready or i >= len(TABS):
            return
        node = self.hg_nodes.get(self.current) if self.current else None
        if node and node.get("placeholder") and i != 0:
            self.app.notify("Only Overview until Discover JSON is fetched (D)", severity="information")
            i = 0
        self.section = i
        self.update_tabs()
        self.refresh_inspector()

    def action_section_step(self, d: int) -> None:
        if not self.ready:
            return
        node = self.hg_nodes.get(self.current) if self.current else None
        if node and node.get("placeholder"):
            self.action_section(0)
            return
        self.action_section((self.section + d) % len(TABS))

    def action_tab_key(self, key: str) -> None:
        node = self.hg_nodes.get(self.current) if self.current else None
        if node and node.get("placeholder"):
            return
        handler = TABS[self.section].keys.get(key) if self.ready else None
        if handler:
            handler(self.ctx)
            self.refresh_inspector()

    def action_ask_gpt_refresh(self) -> None:
        self.action_ask_gpt(refresh=True)

    def action_ask_gpt(self, refresh: bool = False) -> None:
        """ASK tab: brief via pinned Tree · F3 chat; show inline."""
        if not self.ready or not self.ctx or not self.current:
            self.app.notify("Select a haplogroup first", severity="warning")
            return
        node = self.hg_nodes.get(self.current)
        if not node:
            self.app.notify("No node loaded", severity="warning")
            return
        err = chat.can_send(self.store)
        if err:
            self.app.notify(err, severity="warning")
            return
        # Ground on the tab we came from (not ASK itself).
        if TABS[self.section].title != "ASK":
            self.ask_ground_tab = tab_title_at(self.section)
        ground = self.ask_ground_tab or "Overview"
        # Pull Discover fields so the prompt is not a skeleton.
        if self.folder:
            try:
                hydrate_node(node, self.folder)
            except Exception:  # noqa: BLE001
                pass
        name = str(node.get("name") or self.current)
        key = ask_subject(name, ground)
        ask_i = self._ask_section_index()
        if self.section != ask_i:
            self.section = ask_i
        if not refresh:
            hit = self.ask_cache.get(key) or load_cached(key)
            if hit:
                self.ask_cache[key] = hit
                self._sync_ask_ctx()
                self.refresh_inspector()
                return
        if (old := JOBS.get(ASK_JOB)) and old.active:
            old.cancel()
            import time
            for _ in range(40):
                j = JOBS.get(ASK_JOB)
                if not j or not j.active:
                    break
                time.sleep(0.05)
            if (j := JOBS.get(ASK_JOB)) and j.active:
                self.app.notify("Ask still stopping · try again", severity="warning")
                self.refresh_inspector()
                return

        prompt = build_ask_prompt(self.ctx, node, ground)
        title = ask_job_title(name, ground)
        store = self.store
        subject = key
        label = f"{name} · {ground}"

        def work(job) -> str:
            t = chat.ensure_tree_thread()
            headed = f"[F3 · {label}]\n\n{prompt}"
            t.messages.append(chat.Message("user", headed))
            chat.save(t)
            chat.send(
                store, t, job, continue_chat=False, tier="reasoning", use_saved=False,
            )
            text = next((m.text for m in reversed(t.messages) if m.role == "model"), "")
            return (text or job.draft or "").strip() or "(empty reply)"

        self.ask_job_key = subject
        self._sync_ask_ctx()
        try:
            JOBS.start(ASK_JOB, title, work)
        except RuntimeError as exc:
            self.ask_job_key = None
            self.app.notify(str(exc), severity="warning")
            self.refresh_inspector()
            return
        self.refresh_inspector()
        self.update_status()

    def _finish_ask_job(self, job) -> None:
        key = self.ask_job_key
        self.ask_job_key = None
        if not key:
            self.refresh_inspector()
            return
        if job.status == "done":
            text = (job.message or job.draft or "").strip()
            if text in ("done",):
                text = (job.draft or "").strip()
            model = chat.model_label(self.store)
            ts = chat.now()
            parts = key.split("|")
            node = parts[1] if len(parts) > 1 else ""
            tab = parts[2] if len(parts) > 2 else ""
            entry = {"text": text or "(empty reply)", "model": model, "ts": ts,
                     "node": node, "tab": tab}
            self.ask_cache[key] = entry
            try:
                save_cached(key, text=entry["text"], model=model, ts=ts,
                            node=node, tab=tab)
            except OSError:
                pass
            self.app.notify(f"Tree note · {node or key}", timeout=5)
        elif job.status == "failed":
            self.app.notify(f"Tree ask failed: {job.message}", severity="error",
                            timeout=10)
        else:
            self.app.notify(f"Tree ask: {job.message or job.status}",
                            severity="warning", timeout=8)
        self.section = self._ask_section_index()
        self._sync_ask_ctx()
        self.refresh_inspector()
        self.update_status()

    def action_map_fullscreen(self) -> None:
        """M — полноэкранная Migration Map: карта + Contact log / Migration справа."""
        if not self.ready or not self.current:
            return
        node = self.hg_nodes.get(self.current)
        if not node:
            return
        if node.get("placeholder"):
            self.app.notify("Fetch Discover JSON first (D) — map needs migration data",
                            severity="information")
            return
        if self.folder:
            hydrate_node(node, Path(self.folder))
        if not node.get("migrationmap"):
            self.app.notify("No migration map on this node", severity="information")
            return
        from .tabs.map_screen import MapScreen
        world = bool(self.ctx and getattr(self.ctx, "map_world", False))
        self.app.push_screen(MapScreen(node, world=world))

    def action_tree(self, name: str) -> None:
        """Действие левой панели (expand_all, collapse_all, …) — для кликов по строке подсказок."""
        if self.ready:
            getattr(self.view, f"action_{name}")()
            self.view.focus()

    def action_goto(self, name: str) -> None:
        """Переход к узлу по клику на его имя (крошки, CHILDREN, LINEAGE)."""
        if not self.ready or name not in self.hg_nodes:
            return
        if name not in self.view.order_set:
            self.app.notify(f"{name} is hidden by the filter (F)", severity="warning")
            return
        self.view.reveal(name)
        self.view.focus()

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def action_issues(self) -> None:
        if self.ready:
            self.app.push_screen(IssuesScreen(self.hg_issues))

    def action_toggle_panel(self) -> None:
        if not self.ready:
            return
        if self.view.has_focus:
            self.query_one("#inspector-scroll").focus()
        else:
            self.view.focus()

    def action_back(self) -> None:
        if self.ready and (self.query_one("#search", Input).has_focus
                           or self.query_one("#inspector-scroll").has_focus):
            self.view.focus()

    def action_focus_search(self) -> None:
        if not self.ready:
            return
        inp = self.query_one("#search", Input)
        inp.value = ""
        inp.focus()

    def action_next_match(self) -> None:
        if self.ready and self.hits:
            self.hit_i = (self.hit_i + 1) % len(self.hits)
            self.jump_to_hit()

    def jump_to_hit(self) -> None:
        self.view.reveal(self.hits[self.hit_i])
        self.view.focus()
        self.app.notify(f"{self.hits[self.hit_i]}  ({self.hit_i + 1}/{len(self.hits)})  — N: next")

    def action_sort(self) -> None:
        if not self.ready:
            return
        view, cur = self.view, self.current
        view.sort_mode = SORT_MODES[(SORT_MODES.index(view.sort_mode) + 1) % len(SORT_MODES)]
        self.populate()
        if cur and cur in view.order_set:
            view.reveal(cur)
        view.focus()
        self.app.notify(f"Sort: {SORT_LABELS[view.sort_mode]}")

    def action_next_rich(self) -> None:
        """Прыжок к следующей «короне» — богатому узлу без богатых детей — в порядке отображения."""
        if not self.ready:
            return
        order = self.view.order
        if not order:
            return
        start = order.index(self.current) if self.current in order else -1
        n = len(order)
        for step in range(1, n + 1):
            name = order[(start + step) % n]
            if self.hg_nodes[name].get("crown"):
                node = self.hg_nodes[name]
                self.view.reveal(name)
                self.view.focus()
                self.app.notify(f"{name}  {SHORT_CLASS[node['rich']]} · +{node['descendants_count']:,} "
                            f"· ↓{node['height']}  — B: next")
                return
        self.app.notify("No rich branches in the current view", severity="warning")

    @on(Input.Submitted, "#search")
    def _search(self, event: Input.Submitted) -> None:
        q = event.value.strip().lower()
        if not q:
            self.view.focus()
            return
        self.last_query = q
        order = self.view.order
        exact = [n for n in order if n.lower() == q]
        rest = [n for n in order if q in self.haystack[n] and n not in exact]
        self.hits = exact + rest
        if not self.hits:
            self.app.notify(f"Not found: {event.value}", severity="warning")
            return
        if len(self.hits) == 1:
            self.hit_i = 0
            self.jump_to_hit()
            return

        def picked(name: str | None) -> None:
            if name:
                self.hit_i = self.hits.index(name)
                self.jump_to_hit()
            else:
                self.view.focus()
        self.app.push_screen(SearchResults(self.hits, self.hg_nodes, event.value.strip()), picked)

    def action_filter(self) -> None:
        if not self.ready:
            return

        def done(result: dict | None) -> None:
            if result is not None:
                self.apply_filter(result)
        self.app.push_screen(FilterScreen(self.flt), done)

    def apply_filter(self, flt: dict) -> None:
        self.flt = flt
        code, lo, hi = flt.get("country"), flt.get("lo"), flt.get("hi")
        mind, minh = flt.get("mind"), flt.get("minh")
        if not flt or not (code or lo is not None or hi is not None
                           or mind is not None or minh is not None):
            self.flt, self.vis_set = {}, None
            self.populate()
            return
        matched = set()
        for name, node in self.hg_nodes.items():
            if node.get("placeholder"):
                continue
            if code and code not in node_codes(node):
                continue
            if lo is not None or hi is not None:
                m = node.get("date_mean")
                if m is None or (lo is not None and m < lo) or (hi is not None and m > hi):
                    continue
            if mind is not None and node.get("descendants_count", 0) < mind:
                continue
            if minh is not None and node.get("height", 0) < minh:
                continue
            matched.add(name)
        if not matched:
            self.app.notify("No haplogroups match this filter", severity="warning")
            self.flt = {}
            return
        visible = set(matched)
        for name in matched:
            visible.update(self.ctx.lineage(name))
        self.vis_set = visible
        self.populate()
        self.app.notify(f"{len(matched):,} haplogroups match (ancestors shown for context)")

