"""F3 Tree screen for textual-wgpu — haplotree · Our Y · node inspector."""

from __future__ import annotations

import ast
import math
import re
import time
from pathlib import Path

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
    TextArea,
    Vertical,
)

_ACTION_RE = re.compile(r"^screen\.(\w+)(?:\((.*)\))?$", re.DOTALL)

from ..core import chat, genome_db
from ..core.jobs import JOBS
from ..core.store import Store
from ..core.ylink import (
    YBranch,
    YLink,
    active_genome,
    fetch_cost,
    fetch_resources,
    link_genome,
    load_haplotree,
    our_branch,
)
from ..theme.styles import (
    BAD,
    DATE_STYLES,
    DIM,
    FAINT,
    GHOST,
    MUTED,
    NAME_STYLE,
    OK,
    OK_BOLD,
    SIZE_STYLE,
    SOFT,
)
from ..workspaces.base import MODE_LABELS, click
from ..workspaces.tree.ask import (
    ask_job_title,
    ask_subject,
    build_ask_prompt,
    load_cached,
    save_cached,
    tab_title_at,
)
from ..workspaces.tree.data import (
    STAGES,
    date_class,
    graft_path,
    hydrate_node,
    load_or_build,
    node_codes,
    parse_count,
    parse_year,
    relink_children,
    search_text,
    year_class,
    year_label,
)
from ..workspaces.tree.tabs import TABS, Ctx, Doc
from ..workspaces.tree.tabs.node import render_migration
from ..workspaces.tree.tabs.worldmap import contact_log_lines, defcon_map
from ..workspaces.tree.view import (
    CONNECT,
    DATE_W as VIEW_DATE_W,
    GEO_W,
    MARKER,
    SHORT_CLASS,
    SIZE_W as VIEW_SIZE_W,
    SORT_LABELS,
    SORT_MODES,
    glyph_legend,
    legend_text,
    no_haplotree_guide,
    our_y_styles,
)
from ..workspaces.tree.workspace import (
    ASK_JOB,
    FETCH_JOB,
    SECTION_LABELS,
    _no_branch_reason,
)
from .app_api import focus_next
from .wgpu_jobs import _text_lines

TREE_W = 72
DATE_W = VIEW_DATE_W
SIZE_W = VIEW_SIZE_W


class TreeModel:
    """BranchView data/nav without Textual — feeds OptionList rows."""

    def __init__(self) -> None:
        self.nodes: dict = {}
        self.hg_children: dict = {}
        self.hg_roots: list[str] = []
        self.max_desc = 0
        self.sort_mode = "date"
        self.rows: list[tuple[str, str, str, int]] = []
        self.cursor = 0
        self.expanded: set[str] = set()
        self.vkids: dict[str, list[str]] = {}
        self.vroots: list[str] = []
        self.order: list[str] = []
        self.order_set: set[str] = set()
        self.mine: set[str] = set()
        self.tip: str | None = None
        self.our_styles: dict[str, str] = {}
        self.our_uncertain: set[str] = set()
        self.our_pruned: set[str] = set()

    def set_source(self, nodes: dict, children: dict, roots: list[str], max_desc: int = 0) -> None:
        self.nodes = nodes
        self.hg_children = children
        self.hg_roots = roots
        self.max_desc = max_desc

    def sorted_names(self, names: list[str]) -> list[str]:
        mode, nd = self.sort_mode, self.nodes
        if mode == "richness":
            return sorted(
                names,
                key=lambda n: (-nd[n].get("descendants_count", 0), -nd[n].get("height", 0), n.lower()),
            )
        if mode == "depth":
            return sorted(
                names,
                key=lambda n: (-nd[n].get("height", 0), -nd[n].get("descendants_count", 0), n.lower()),
            )
        return list(names)

    def set_data(self, vis: set[str] | None) -> None:
        self.vkids = {
            n: self.sorted_names([c for c in kids if vis is None or c in vis])
            for n, kids in self.hg_children.items()
            if vis is None or n in vis
        }
        self.vroots = self.sorted_names([r for r in self.hg_roots if vis is None or r in vis])
        self.order, seen, stack = [], set(), list(reversed(self.vroots))
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            self.order.append(n)
            stack.extend(reversed(self.vkids.get(n, [])))
        self.order_set = seen
        if len(seen) > 8_000:
            self.expanded = set()
        else:
            self.expanded = {n for n in self.order if self.vkids.get(n)}
        self.cursor = 0
        self.rebuild()

    def root_tone(self, name: str) -> str:
        node = self.nodes[name]
        year = node.get("date_mean")
        if year is None:
            year = node.get("branch_date")
        return year_class(year)

    def rebuild(self, keep: str | None = None) -> None:
        rows: list[tuple[str, str, str, int]] = []
        seen: set[str] = set()
        n_roots = len(self.vroots)
        stack = [
            (r, 0, (), i == n_roots - 1, i == n_roots - 1, self.root_tone(r))
            for i, r in reversed(list(enumerate(self.vroots)))
        ]
        while stack:
            name, depth, anc, is_last, root_last, tone = stack.pop()
            if name in seen:
                continue
            seen.add(name)
            cls = self.nodes[name].get("rich", "normal")
            mk = MARKER[cls]
            if depth == 0:
                guide = mk + "── "
            else:
                lane = " " if root_last else "│"
                indent = 4 if depth <= 3 else 2 if depth <= 7 else 1
                lanes = "".join(
                    (" " if last else ("┃" if heavy else "│")) + " " * (indent - 1)
                    for last, heavy in anc
                )
                guide = lane + "   " + lanes + CONNECT[cls][1 if is_last else 0] + mk + " "
            rows.append((name, guide, tone, depth))
            kids = self.vkids.get(name, [])
            if kids and name in self.expanded:
                heavy = cls in ("rich", "hub")
                child_anc = anc + (((is_last, heavy),) if depth >= 1 else ())
                for j in range(len(kids) - 1, -1, -1):
                    stack.append((
                        kids[j], depth + 1, child_anc,
                        j == len(kids) - 1, root_last, tone,
                    ))
        self.rows = rows
        if keep is not None:
            for i, row in enumerate(rows):
                if row[0] == keep:
                    self.cursor = i
                    break
        self.cursor = max(0, min(self.cursor, len(rows) - 1)) if rows else 0

    @property
    def current(self) -> str | None:
        return self.rows[self.cursor][0] if self.rows else None

    def marker_pos(self, idx: int) -> int:
        _, guide, _, depth = self.rows[idx]
        return 0 if depth == 0 else len(guide) - 2

    @staticmethod
    def columns(width: int) -> tuple[int | None, int | None, int | None]:
        """(x FORMED, x SIZE, x GEOGRAPHY)."""
        x = width
        geo_x = size_x = date_x = None
        if width >= 112:
            x -= GEO_W
            geo_x = x
        if width >= 84:
            x -= SIZE_W
            size_x = x
        if width >= 64:
            x -= DATE_W
            date_x = x
        return date_x, size_x, geo_x

    def header(self, width: int) -> Text:
        date_x, size_x, geo_x = self.columns(width)
        limit = (date_x if date_x is not None else width) - 1
        label = f"PHYLOGENETIC BRANCHES ({SORT_LABELS[self.sort_mode]})"
        t = Text(label, no_wrap=True, overflow="crop")
        t.truncate(max(limit, 1), overflow="ellipsis", pad=True)
        if date_x is not None:
            t.append(" " + "FORMED".ljust(DATE_W))
        if size_x is not None:
            t.append("SIZE  desc ↓h".ljust(SIZE_W))
        if geo_x is not None:
            t.append("GEOGRAPHY")
        t.stylize(f"bold {MUTED}")
        return t

    def size_text(self, node: dict, cls: str) -> Text:
        d = node.get("descendants_count", 0)
        if d <= 0:
            return Text(" " * SIZE_W)
        frac = (
            math.log1p(d) / math.log1p(self.max_desc)
            if self.max_desc > 0 else 0
        )
        k = max(1, min(5, round(frac * 5)))
        style = SIZE_STYLE[cls]
        t = Text(no_wrap=True)
        t.append("█" * k + "░" * (5 - k), style=style)
        t.append(" " + f"{d:,}".rjust(7), style=style)
        t.append(
            " ↓" + str(node.get("height", 0)).ljust(3),
            style=("bold #729fcf" if node.get("deep") else DIM),
        )
        return t

    def row_prompt(self, idx: int, width: int = TREE_W) -> Text:
        name, guide, tone, _depth = self.rows[idx]
        node = self.nodes[name]
        ph = bool(node.get("placeholder"))
        cls = node.get("rich", "normal")
        color = DATE_STYLES[tone]
        on_path = name in self.mine
        is_tip = name == self.tip
        our_color = self.our_styles.get(name) if on_path else None
        is_uncertain = name in self.our_uncertain
        is_pruned = (not on_path) and name in self.our_pruned
        rich = cls in ("rich", "hub")

        if on_path and our_color:
            line_style = f"bold {our_color}" if is_tip else our_color
            marker_style = f"bold {our_color}"
        elif on_path:
            line_style = OK_BOLD if is_tip else OK
            marker_style = OK_BOLD
        elif is_uncertain:
            line_style = BAD
            marker_style = f"bold {BAD}"
        elif is_pruned:
            line_style = FAINT
            marker_style = FAINT
        else:
            line_style = color if rich else f"dim {color}"
            marker_style = f"bold {color}" if cls != "dead" else f"dim {color}"

        if on_path and our_color:
            name_style = f"bold {our_color}" if is_tip else our_color
        elif on_path:
            name_style = OK_BOLD if is_tip else OK
        elif is_uncertain:
            name_style = f"bold {BAD}"
        elif is_pruned:
            name_style = FAINT
        else:
            name_style = MUTED if ph else NAME_STYLE[cls]

        date_x, size_x, geo_x = self.columns(width)
        left = Text(no_wrap=True, overflow="crop")
        mi = self.marker_pos(idx)
        if 0 <= mi < len(guide):
            left.append(guide[:mi], style=line_style)
            left.append(guide[mi], style=marker_style)
            left.append(guide[mi + 1 :], style=line_style)
        else:
            left.append(guide, style=line_style)

        if ph:
            left.append(name, style=name_style)
            left.append(
                "  [haplotree]" if node.get("haplotree") else "  [MISSING RESOURCE]",
                style=f"{FAINT} italic",
            )
        else:
            left.append(name, style=name_style)
        if is_uncertain:
            left.append(" ?", style=f"bold {BAD}")
        elif is_pruned:
            left.append(" ○", style=FAINT)
        if self.vkids.get(name) and name not in self.expanded:
            left.append(f"  ▸ +{node.get('descendants_count', 0)}", style=DIM)

        t = left
        if date_x is not None:
            left.truncate(date_x - 1, overflow="ellipsis", pad=True)
            t.append(" ")
            if ph:
                bd = node.get("branch_date")
                t.append(
                    (year_label(bd) if bd is not None else "").ljust(DATE_W),
                    style=GHOST,
                )
            else:
                parent = self.nodes.get(node.get("parent") or "")
                same = (
                    parent is not None
                    and not parent.get("placeholder")
                    and node.get("date_mean") is not None
                    and parent.get("date_mean") == node.get("date_mean")
                )
                t.append(
                    str(node.get("date") or "").ljust(DATE_W),
                    style=GHOST if same else DATE_STYLES[date_class(node)],
                )
        if size_x is not None:
            t.append_text(self.size_text(node, cls))
        if geo_x is not None:
            geo = " · ".join(node.get("flags") or []) if not ph else ""
            if len(geo) > GEO_W - 2:
                geo = geo[: GEO_W - 3] + "…"
            t.append(geo.ljust(GEO_W), style=SOFT)
        t.truncate(width, overflow="crop", pad=True)
        return t

    def toggle(self, expand: bool | None = None) -> bool:
        name = self.current
        if not name or not self.vkids.get(name):
            return False
        is_open = name in self.expanded
        if expand is None:
            expand = not is_open
        if expand == is_open:
            return False
        (self.expanded.add if expand else self.expanded.discard)(name)
        self.rebuild(keep=name)
        return True

    def expand_or_child(self) -> None:
        name = self.current
        if not name:
            return
        if self.vkids.get(name) and name not in self.expanded:
            self.toggle(True)
            return
        if self.cursor + 1 < len(self.rows):
            self.cursor += 1

    def jump_parent(self) -> None:
        if not self.current:
            return
        parent = self.nodes[self.current].get("parent")
        for i in range(self.cursor - 1, -1, -1):
            if self.rows[i][0] == parent:
                self.cursor = i
                return

    def jump_root(self) -> None:
        for i in range(self.cursor, -1, -1):
            if self.rows[i][3] == 0:
                self.cursor = i
                return

    def collapse_or_parent(self) -> None:
        name = self.current
        if not name:
            return
        if name in self.expanded:
            self.toggle(False)
            return
        self.jump_parent()

    def expand_subtree(self) -> None:
        name = self.current
        if not name:
            return
        stack = [name]
        while stack:
            n = stack.pop()
            kids = self.vkids.get(n)
            if kids:
                self.expanded.add(n)
                stack.extend(kids)
        self.rebuild(keep=name)

    def expand_all(self) -> None:
        if len(self.order_set) > 8_000:
            self.expanded = {n for n in self.vroots if self.vkids.get(n)}
        else:
            self.expanded = {n for n in self.order if self.vkids.get(n)}
        keep = self.current
        self.rebuild(keep=keep)

    def collapse_all(self) -> None:
        self.expanded = set()
        self.rebuild(keep=self.current)

    def reveal(self, name: str) -> None:
        if name not in self.order_set:
            return
        cur = self.nodes[name].get("parent")
        seen: set[str] = set()
        while cur and cur in self.nodes and cur not in seen:
            seen.add(cur)
            if self.vkids.get(cur):
                self.expanded.add(cur)
            cur = self.nodes[cur].get("parent")
        self.rebuild(keep=name)

    def focus_path(self, path: list[str], collapse: bool = True) -> str | None:
        visible = [n for n in path if n in self.order_set]
        if not visible:
            return None
        tip = visible[-1]
        if collapse:
            self.expanded = {n for n in path if self.vkids.get(n)}
        else:
            cur = self.nodes.get(tip, {}).get("parent")
            seen: set[str] = set()
            while cur and cur in self.nodes and cur not in seen:
                seen.add(cur)
                if self.vkids.get(cur):
                    self.expanded.add(cur)
                cur = self.nodes[cur].get("parent")
        self.rebuild(keep=tip)
        return tip

    def format_tree(self, roots: list[str]) -> tuple[str, int]:
        """Text tree from roots (filter/sort aware; ignores on-screen fold)."""
        lines: list[str] = []
        seen: set[str] = set()
        for root_index, root in enumerate(roots):
            if root in seen:
                continue
            if root_index > 0 and lines:
                lines.append("")
            stack = [(root, "", True, True)]
            while stack:
                name, prefix, is_last, is_root = stack.pop()
                if name in seen:
                    continue
                seen.add(name)
                if is_root:
                    lines.append(name)
                else:
                    connector = "└─ " if is_last else "├─ "
                    lines.append(prefix + connector + name)
                kids = self.vkids.get(name, [])
                child_prefix = (
                    prefix if is_root else prefix + ("   " if is_last else "│  ")
                )
                for index in range(len(kids) - 1, -1, -1):
                    stack.append((
                        kids[index],
                        child_prefix,
                        index == len(kids) - 1,
                        False,
                    ))
        return "\n".join(lines), len(seen)


class TreeScreen(Screen):
    """F3 Tree on wgpu — haplotree + Our Y + inspector tabs."""

    DEFAULT_CSS = """
    #main { height: 1fr; }
    #search { width: 34; border: none; height: 1; }
    #tree-box { width: 1fr; border: round $primary; }
    #insp-box { width: 36%; min-width: 38; border: round $primary; }
    #tree-box:focus-within, #insp-box:focus-within { border: round $accent; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """

    BINDINGS = [
        ("slash", "focus_search"),
        ("/", "focus_search"),
        ("f", "filter"),
        ("s", "sort"),
        ("b", "next_rich"),
        ("m", "map_fullscreen"),
        ("w", "toggle_map_world"),
        ("g", "tab_g"),
        ("x", "tab_x"),
        ("question_mark", "help"),
        ("i", "issues"),
        ("n", "next_match"),
        ("o", "our_y"),
        ("d", "fetch_node"),
        ("shift+d", "fetch_branch"),
        ("l", "apply_local"),
        ("c", "cancel_fetch"),
        ("a", "ask_gpt"),
        ("y", "copy_name"),
        ("ctrl+c", "copy_branch"),
        ("ctrl+shift+c", "copy_tree"),
        ("p", "jump_parent"),
        ("r", "jump_root"),
        ("e", "expand_all"),
        ("z", "collapse_all"),
        ("left", "collapse"),
        ("right", "expand"),
        ("shift+right", "expand_subtree"),
        ("asterisk", "expand_subtree"),
        ("*", "expand_subtree"),
        ("space", "toggle"),
        ("enter", "select_node"),
        ("return", "select_node"),
        ("left_square_bracket", "section_prev"),
        ("right_square_bracket", "section_next"),
        ("[", "section_prev"),
        ("]", "section_next"),
        ("1", "sec_1"), ("2", "sec_2"), ("3", "sec_3"), ("4", "sec_4"),
        ("5", "sec_5"), ("6", "sec_6"), ("7", "sec_7"), ("8", "sec_8"),
        ("9", "sec_9"), ("0", "sec_0"),
        ("minus", "sec_minus"), ("=", "sec_eq"),
    ]

    def __init__(self, store: Store | None = None, folder: Path | None = None) -> None:
        super().__init__()
        self.store = store or Store()
        self.folder = folder
        self.hg_nodes: dict = {}
        self.hg_children: dict = {}
        self.hg_roots: list[str] = []
        self.hg_stats: dict = {}
        self.hg_issues: dict = {}
        self.haystack: dict = {}
        self.ctx: Ctx | None = None
        self.ready = False
        self.stale = False
        self._map_focus: str | None = None
        self.vis_set: set[str] | None = None
        self.flt: dict = {}
        self.section = 0
        self.current: str | None = None
        self.last_query = ""
        self.search_hits: list[str] = []
        self.hit_i = -1
        self.branch: YBranch | None = None
        self.branch_genome: Path | None = None
        self.branch_busy: str | None = None
        self.our_i = 0
        self._fetch_names: list[str] = []
        self._fetch_paint_sig: str | None = None
        self.ask_cache: dict[str, dict] = {}
        self.ask_job_key: str | None = None
        self.ask_ground_tab: str = "Overview"
        self._ask_seen: set[int] = set()
        self._insp_gen = 0
        self._labels = list(MODE_LABELS)
        self._tick_handle = None
        self._keybar: KeyBar | None = None
        self._tree_box: Panel | None = None
        self._insp_box: Panel | None = None
        self._tab_hit_specs: list[tuple[int, int, int, int]] = []
        self.view = TreeModel()

    # ── compose ──────────────────────────────────────────────────────
    def compose(self):
        from .app_api import make_mode_bar

        yield make_mode_bar(self, "tree", self._labels)
        stats = RichText(id="stats", can_focus=False)
        stats.layout_hint_h = 28
        search = TextArea(
            "",
            id="search",
            placeholder="/ search haplogroup or SNP",
            single_line=True,
            on_submit=self._search_submit,
        )
        search.layout_hint_h = 28
        top = Horizontal(stats, search, id="top")
        top.layout_hint_h = 28

        crumbs = RichText(id="crumbs", can_focus=False)
        crumbs.layout_hint_h = 22
        crumbs.on_action = self._run_action
        colhead = RichText(id="colhead", can_focus=False)
        colhead.layout_hint_h = 20
        tree = OptionList(
            id="tree",
            on_highlight=self._tree_hi,
            click_activates=False,
            on_row_click=self._tree_row_click,
            row_lines=1,
        )
        legend = RichText(id="legend", can_focus=False)
        legend.layout_hint_h = 36
        tree_inner = Vertical(crumbs, colhead, tree, legend, id="tree-inner")
        self._tree_box = Panel(tree_inner, title="PHYLOGENETIC TREE", id="tree-box")

        tabs = RichText(id="tabs", can_focus=False)
        tabs.layout_hint_h = 44
        tabs.hit_builder = self._tab_hits
        inspector = RichText(id="inspector")
        inspector.on_action = self._run_action
        insp_inner = Vertical(tabs, inspector, id="insp-inner")
        self._insp_box = Panel(
            insp_inner, title="NODE INSPECTOR", subtitle="[ ] sections · Tab focus",
            id="insp-box",
        )

        # Screen layout: [0]=ModeBar · middles · [-2]=KeyBar · [-1]=Status.
        # Keep KeyBar immediately before StatusBar (same as F1/F2/F4/F5).
        yield top
        yield Horizontal(self._tree_box, self._insp_box, id="main")
        self._keybar = KeyBar(self._keybar_items())
        yield self._keybar
        yield StatusBar(id="status")

    def on_screen_mount(self) -> None:
        self.update_tabs()
        if self.app is not None and hasattr(self.app, "set_interval"):
            self._tick_handle = self.app.set_interval(0.5, self._tick_fetch)
        self.start_load()

    def on_screen_resume(self) -> None:
        if self.stale:
            self.start_load()
        elif self.ready:
            if self.branch_busy:
                return
            self.start_branch()

    def mark_stale(self) -> None:
        self.stale = True

    def set_folder(self, folder: Path) -> None:
        if folder != self.folder:
            self.folder = folder
            self.stale = True

    # ── helpers ──────────────────────────────────────────────────────
    def _notify(self, msg: str) -> None:
        if self.app is not None and hasattr(self.app, "notify"):
            self.app.notify(msg)

    def _path(self, key: str) -> Path | None:
        try:
            return self.store.path(key)
        except Exception:  # noqa: BLE001
            return None

    def _active_genome(self) -> Path | None:
        app = self.app
        if app is not None:
            gws = getattr(app, "_screens", {}).get("genome")
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

    # ── load ─────────────────────────────────────────────────────────
    def start_load(self) -> None:
        self.stale = False
        ht = self._path("ftdna_haplotree")
        if not ht or not Path(ht).is_file():
            self._show_no_haplotree(None if not ht else str(ht))
            return
        disc = self.folder if self.folder and Path(self.folder).is_dir() else None
        if disc is None:
            disc = self._discover_folder()
        if self.app is not None:
            self.app.show_busy(
                "Y tree",
                "Building / loading haplotree skeleton"
                + (f"\n+ Discover {disc}" if disc else "\n(Discover not set — skeleton only)"),
            )
            self.app.run_worker(self._load, name="tree-loader", exclusive=True, group="loader")
        else:
            self._load()

    def _show_no_haplotree(self, missing_path: str | None) -> None:
        if self.app is not None:
            self.app.hide_busy()
        self.ready = False
        self.hg_nodes, self.hg_children, self.hg_roots = {}, {}, []
        self.hg_stats, self.hg_issues, self.haystack = {}, {}, {}
        self.ctx = None
        self.vis_set, self.flt = None, {}
        self.current, self.search_hits, self.hit_i = None, [], -1
        self.branch, self.branch_genome, self.branch_busy = None, None, None
        self.view.set_source({}, {}, [])
        self.view.set_data(None)
        self.view.mine = set()
        self.view.tip = None
        self.view.our_styles = {}
        self.view.our_uncertain = set()
        self.view.our_pruned = set()
        self.our_i = 0
        if self._tree_box is not None:
            self._tree_box.title = "TREE · needs Haplotree"
        try:
            self.query_one("#stats", RichText).update(Text(
                "Haplotree not set · F2 → P" if missing_path is None
                else "Haplotree file missing · F2 → P",
            ))
            self.query_one("#colhead", RichText).update("")
            self.query_one("#legend", RichText).update(Text(
                "Discover is optional · Our Y needs a genome in F1 after the tree loads",
                style="#71697A",
            ))
            self.query_one("#inspector", RichText).update(no_haplotree_guide(missing_path))
            self.query_one("#tree", OptionList).set_options([])
        except KeyError:
            pass
        self.update_tabs()
        self.update_status()

    def _load(self) -> None:
        names = [n for n, _ in STAGES]
        app = self.app

        def prog(stage: str, done: int, total: int) -> None:
            if app is None:
                return
            frac = done / total if total else 1.0
            if stage in names:
                idx = names.index(stage)
                before = sum(w for _, w in STAGES[:idx])
                overall = (before + STAGES[idx][1] * frac) / 100.0
                label = f"[{idx + 1}/{len(STAGES)}] {stage}"
            else:
                overall, label = frac, stage
            app.call_from_thread(app.busy_progress, label, frac=overall)

        ht = self._path("ftdna_haplotree")
        disc = self.folder if self.folder and Path(self.folder).is_dir() else self._discover_folder()
        try:
            if not ht or not Path(ht).is_file():
                if app is not None:
                    app.call_from_thread(
                        self._show_no_haplotree, None if not ht else str(ht),
                    )
                return
            data, cached = load_or_build(Path(ht), disc, prog)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            if app is not None:
                app.call_from_thread(self._failed, f"Failed to load data: {exc}")
            return
        if app is not None:
            app.call_from_thread(self._loaded, data, cached)
        else:
            self._loaded(data, cached)

    def _failed(self, msg: str) -> None:
        print(f"Tree load failed: {msg}", flush=True)
        if self.app is not None:
            self.app.hide_busy()
        if not self.ready:
            if self._tree_box is not None:
                self._tree_box.title = "TREE · load failed"
            try:
                self.query_one("#inspector", RichText).update(Text(
                    f"{msg}\n\nCheck the Haplotree path in F2 → P, then reopen F3.",
                    style="#8BAB4A",
                ))
                self.query_one("#stats", RichText).update(Text("Load failed · see inspector"))
            except KeyError:
                pass
        self._notify(msg)

    def _loaded(self, data: dict, cached: bool) -> None:
        self.vis_set, self.flt, self.current, self.search_hits = None, {}, None, []
        self.hg_nodes = data["nodes"]
        self.hg_children = data["children"]
        self.hg_roots = data["roots"]
        self.hg_stats = data["stats"]
        self.hg_issues = data["issues"]
        self.haystack = data["haystack"]
        folder = self._discover_folder() or Path(".")
        self.folder = folder if folder != Path(".") else self.folder
        self.ctx = Ctx(self.hg_nodes, self.hg_children, folder, self.hg_stats, store=self.store)
        self.ctx.workspace = self
        self.ctx.ask_cache = self.ask_cache
        self.ctx.ask_ground_tab = self.ask_ground_tab
        self.view.set_source(
            self.hg_nodes, self.hg_children, self.hg_roots,
            self.hg_stats.get("max_desc", 0),
        )
        try:
            chat.ensure_tree_thread()
        except Exception:  # noqa: BLE001
            pass
        if self._tree_box is not None:
            self._tree_box.title = "PHYLOGENETIC TREE"
        self.ready = True
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
        disc_n = self.hg_stats.get("discover_nodes", self.hg_stats.get("real_nodes", 0))
        total = self.hg_stats.get("nodes", len(self.hg_nodes))
        if cached:
            self._notify(f"Y tree from cache · {total:,} nodes · {disc_n:,} Discover")
        else:
            self._notify(f"Y tree built · {total:,} nodes · {disc_n:,} Discover")
        if self.hg_stats.get("issues"):
            self._notify(f"{self.hg_stats['issues']} data issue(s) — press I")
        self.start_branch()

    def populate(self) -> None:
        self.view.set_data(self.vis_set)
        self._sync_tree_list(keep=self.current)
        s = self.hg_stats
        flt = " · FILTER ON" if self.vis_set is not None else ""
        warn = f"   ⚠ {s['issues']} issues (I)" if s.get("issues") else ""
        total = s.get("nodes", s.get("real_nodes", 0))
        disc = s.get("discover_nodes", s.get("real_nodes", 0))
        skel = s.get("skeleton_nodes", s.get("placeholder_nodes", 0))
        try:
            self.query_one("#stats", RichText).update(Text(
                f"{total:,} nodes   {disc:,} Discover   {skel:,} skeleton   "
                f"{s['roots']} roots   {s.get('rich_nodes', 0):,} rich   "
                f"shown {len(self.view.rows):,}   "
                f"tree {s.get('tree_version') or '?'}{flt}{warn}",
            ))
            self._update_colhead()
        except KeyError:
            pass
        if not self.view.rows:
            self.current = None
            try:
                self.query_one("#inspector", RichText).update(
                    Text("Nothing matches the filter.", style="#AEA5B8"),
                )
            except KeyError:
                pass
            self.update_status()

    def _tree_col_width(self) -> int:
        try:
            ol = self.query_one("#tree", OptionList)
        except KeyError:
            return TREE_W
        from textual_wgpu.fonts import cell_width
        if ol.rect.w <= 0:
            return TREE_W
        return max(40, (ol.rect.w - 12) // cell_width(ol.FONT_SIZE))

    def _update_colhead(self) -> None:
        try:
            self.query_one("#colhead", RichText).update(
                self.view.header(self._tree_col_width()),
            )
        except KeyError:
            pass

    def on_resize(self) -> None:
        """Re-measure FORMED / SIZE / GEOGRAPHY columns after layout."""
        if self.ready and self.view.rows:
            self._sync_tree_list(keep=self.view.current)

    def _sync_tree_list(self, keep: str | None = None) -> None:
        try:
            ol = self.query_one("#tree", OptionList)
        except KeyError:
            return
        width = self._tree_col_width()
        opts = [
            Option(
                name,
                id=f"n:{name}",
                lines=_text_lines(self.view.row_prompt(i, width)),
            )
            for i, (name, *_rest) in enumerate(self.view.rows)
        ]
        keep_id = f"n:{keep}" if keep else (f"n:{self.view.current}" if self.view.current else None)
        ol.set_options(opts, keep_id=keep_id)
        # keep TreeModel.cursor in sync with OptionList
        if keep_id:
            for i, o in enumerate(ol.options):
                if o.id == keep_id:
                    self.view.cursor = i
                    break
        elif ol.options:
            self.view.cursor = ol.highlighted
        self._update_colhead()

    def update_legend(self) -> None:
        legend = legend_text()
        legend.append("\n")
        n = self.branch.n_candidates if self.branch else 1
        noise = bool(self.branch and (
            self.branch.uncertain or self.branch.pruned or self.branch.uncertain_marks
        ))
        legend.append_text(glyph_legend(
            self.branch.tip if self.branch else None,
            n_candidates=n, show_noise=noise,
            tie_label=self.branch.tie_label if self.branch else "",
        ))
        try:
            self.query_one("#legend", RichText).update(legend)
        except KeyError:
            pass

    # ── Our Y ────────────────────────────────────────────────────────
    def start_branch(self, force: bool = False) -> None:
        if not self.ready:
            return
        path = self._active_genome()
        if not force and path is not None and path == self.branch_genome:
            if self.app is not None:
                self.app.hide_busy()
            return
        if not force and path is None and self.branch_genome is None and self.branch is None:
            if self.app is not None:
                self.app.hide_busy()
            return
        if not path:
            self.branch_busy = None
            self._apply_branch(None, None)
            return
        if self.app is not None:
            self.app.show_busy("Our Y", f"Linking branch for {path.name}")
            self.app.busy_progress("Starting…", frac=0.0)
        self.branch_busy = f"Linking Y for {path.name}…"
        self.update_status()
        if self.app is not None:
            self.app.run_worker(self._load_branch, name="ybranch", exclusive=True, group="ybranch")
        else:
            self._load_branch()

    def _branch_progress(self, path: Path, stage: str, done: int = 0, total: int = 1) -> None:
        if path != self._active_genome():
            return
        bit = f"{done}/{total}" if total > 1 else ""
        self.branch_busy = f"Linking Y for {path.name}… {stage}" + (f" {bit}" if bit else "")
        if self.app is not None:
            frac = (done / total) if total else None
            self.app.busy_progress(stage, frac=frac)
        self.update_status()

    def _load_branch(self) -> None:
        app = self.app
        store = self.store
        path = self._active_genome()
        if not path:
            if app is not None:
                app.call_from_thread(self._apply_branch, None, None)
            return

        def report(stage: str, done: int = 0, total: int = 1) -> None:
            if app is not None:
                app.call_from_thread(self._branch_progress, path, stage, done, total)

        gws = self._genome_ws()
        try:
            from ..core import tree_db
            tree_path = store.path("ftdna_haplotree")
            disc = store.path("ftdna_discover")
            report("loading branch cache")
            branch = tree_db.load_branch(path, tree_path, disc)
            if branch is not None:
                names: set[str] = set()
                for p in (branch.candidates or [branch.path]):
                    names.update(p)
                names.update(branch.uncertain or {})
                names.update(branch.pruned or [])
                if any(n not in self.hg_nodes for n in names):
                    report("loading haplotree")
                    tree = (
                        load_haplotree(tree_path)
                        if tree_path and Path(tree_path).is_file() else None
                    )
                    if tree is None:
                        if app is not None:
                            app.call_from_thread(
                                self._apply_branch, None, path, None, "haplotree not loaded",
                            )
                        return
                else:
                    tree = None
                if app is not None:
                    app.call_from_thread(self._apply_branch, branch, path, tree)
                else:
                    self._apply_branch(branch, path, tree)
                return
            report("loading genome")
            g = genome_db.get_y(path, gws.genome if gws else None)
            if gws and isinstance(getattr(gws, "link", None), YLink) and gws.genome is g:
                link: YLink | str | None = gws.link
            else:
                report("linking named Y markers")
                link = link_genome(g, store)
            report("loading haplotree")
            tree = (
                load_haplotree(tree_path)
                if tree_path and Path(tree_path).is_file() else None
            )
            if not isinstance(link, YLink) or not tree:
                why = link if isinstance(link, str) else (
                    "haplotree not loaded" if not tree else "no usable Y link"
                )
                if app is not None:
                    app.call_from_thread(self._apply_branch, None, path, None, why)
                return
            branch = our_branch(link, tree, g, disc, progress=report)
            if branch:
                try:
                    tree_db.save_branch(path, branch, tree_path, disc)
                except Exception:  # noqa: BLE001
                    pass
            why = None if branch else _no_branch_reason(link, g)
            if app is not None:
                app.call_from_thread(self._apply_branch, branch, path, tree, why)
            else:
                self._apply_branch(branch, path, tree, why)
        except Exception:  # noqa: BLE001
            if app is not None:
                app.call_from_thread(
                    self._apply_branch, None, path, None, "Our Y link failed",
                )

    def _apply_branch(
        self,
        branch: YBranch | None,
        genome: Path | None,
        tree=None,
        reason: str | None = None,
    ) -> None:
        if not self.ready:
            return
        if genome != self._active_genome():
            return
        self.branch = branch
        self.branch_genome = genome
        self.our_i = 0
        if not branch:
            if self.app is not None:
                self.app.hide_busy()
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
            if genome:
                self._notify(f"No Y branch for {genome.name} · {reason or 'no derived Y path'}")
            return
        branch.select(0)
        cands = list(branch.candidates or [branch.path])
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
            self.view.set_source(
                self.hg_nodes, self.hg_children, self.hg_roots,
                self.hg_stats.get("max_desc", 0),
            )
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
        if rebuilt:
            self.populate()
        self._paint_our_y()
        self.update_legend()
        shown = self.view.focus_path(branch.path)
        self._sync_tree_list(keep=shown)
        if shown:
            self.current = shown
        if self.app is not None:
            self.app.hide_busy()
        self.branch_busy = None
        self.update_keybar()
        self.update_status()
        if self.current:
            self.refresh_inspector(keep_scroll=True)
        gname = genome.name if genome else "?"
        if shown:
            grafted = f" · {added} from haplotree" if added else ""
            tie = ""
            if branch.n_candidates > 1:
                tie = f" · {branch.n_candidates} {branch.tie_label or 'tied'} · O cycles"
            noise = ""
            if branch.uncertain_marks:
                noise = f" · {branch.uncertain_marks} uncertain"
            if branch.pruned:
                noise += f" · {len(branch.pruned)} pruned"
            self._notify(
                f"Our Y: {branch.tip} · {gname}  "
                f"({branch.derived_marks} DER on path{grafted}{tie}{noise})",
            )
        else:
            self._notify(f"Our Y {branch.tip} · {gname} is hidden by the filter")

    def _paint_our_y(self) -> None:
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
            self.ctx.ours = mine
            self.ctx.tip = self.view.tip

    def our_y(self) -> None:
        if not self.ready:
            return
        if self.branch_busy:
            self._notify(self.branch_busy)
            return
        if not self.branch:
            path = self._active_genome()
            if not path:
                self._notify("Load a genotype file in F1 first")
                return
            self.start_branch(force=True)
            return
        n = self.branch.n_candidates
        if n > 1 and self.current == self.branch.tip:
            self.our_i = (self.our_i + 1) % n
            self.branch.select(self.our_i)
            self._paint_our_y()
            self.update_legend()
            self.update_keybar()
            self.update_status()
        shown = self.view.focus_path(self.branch.path)
        self._sync_tree_list(keep=shown)
        gname = self.branch_genome.name if self.branch_genome else "?"
        if shown:
            self.current = shown
            self.refresh_inspector()
            if n > 1:
                self._notify(f"Our Y {self.our_i + 1}/{n}: {shown} · {gname}")
            else:
                self._notify(f"Our Y: {shown} · {gname}")
        else:
            self._notify(f"Our Y {self.branch.tip} · {gname} is hidden by the filter")

    # ── tree events ──────────────────────────────────────────────────
    def _tree_hi(self, opt: Option | None) -> None:
        if opt is None or not opt.id or not opt.id.startswith("n:"):
            return
        name = opt.id[2:]
        try:
            ol = self.query_one("#tree", OptionList)
            self.view.cursor = ol.highlighted
        except KeyError:
            pass
        if name == self.current:
            return
        self.current = name
        self.update_status()
        self._schedule_inspector()

    def _tree_row_click(self, opt: Option, x: int, y: int, double: bool) -> None:
        """Marker / ▸ single-click or row double-click → fold (BranchView.on_click)."""
        if not self.ready or opt is None or not opt.id or not opt.id.startswith("n:"):
            return
        try:
            ol = self.query_one("#tree", OptionList)
        except KeyError:
            return
        idx = ol.highlighted
        if not (0 <= idx < len(self.view.rows)):
            return
        from textual_wgpu.fonts import cell_width
        col = max(0, (x - (ol.rect.x + 6)) // cell_width(ol.FONT_SIZE))
        name, guide, _, _ = self.view.rows[idx]
        node = self.view.nodes.get(name) or {}
        hit = col == self.view.marker_pos(idx)
        if self.view.vkids.get(name) and name not in self.view.expanded:
            start = (
                len(guide)
                + len(name)
                + (len("  [MISSING RESOURCE]") if node.get("placeholder") else 0)
                + 2
            )
            tip = f"▸ +{node.get('descendants_count', 0)}"
            hit = hit or (start <= col < start + len(tip))
        if (hit and not double) or (not hit and double):
            if self.view.toggle():
                self._sync_tree_list(keep=self.view.current)
                self.current = self.view.current

    def _schedule_inspector(self) -> None:
        self._insp_gen += 1
        gen = self._insp_gen
        app = self.app
        if app is not None and hasattr(app, "set_timer"):
            app.set_timer(0.08, lambda g=gen: self._flush_inspector(g))
        else:
            self._flush_inspector(gen)

    def _flush_inspector(self, gen: int) -> None:
        if gen != self._insp_gen:
            return
        self.refresh_inspector()

    # ── inspector ────────────────────────────────────────────────────
    def refresh_inspector(self, *, keep_scroll: bool = False) -> None:
        if not self.current or not self.ctx:
            return
        if self.branch_busy:
            return
        node = self.hg_nodes.get(self.current)
        if (
            node and node.get("placeholder") and node.get("haplotree")
            and not self._fetch_running()
            and self.ctx.has_discover_file(self.current)
        ):
            self._ingest_discover([self.current], quiet=True)
            node = self.hg_nodes.get(self.current)
        if node and self.folder:
            try:
                hydrate_node(node, Path(self.folder))
            except Exception:  # noqa: BLE001
                pass
        if node and node.get("placeholder") and self.section != 0:
            self.section = 0
        self._sync_fetch_ctx()
        self._sync_ask_ctx()
        self.ctx.panel_width = 48
        self.update_tabs()
        self.update_keybar()
        try:
            body = self.ctx.render(self.current, self.section)
            insp = self.query_one("#inspector", RichText)
            insp.on_action = self._run_action
            insp.update(body, home=not keep_scroll)
        except Exception as exc:  # noqa: BLE001
            insp = self.query_one("#inspector", RichText)
            insp.on_action = self._run_action
            insp.update(Text(f"Inspector error: {exc}", style="#B04A4A"))
        self.update_status()

    def _run_action(self, action: str) -> None:
        """Dispatch Rich ``ydna_click`` from inspector / crumbs (goto, Ask, fetch, …)."""
        m = _ACTION_RE.match((action or "").strip())
        if not m:
            return
        name, args_src = m.group(1), (m.group(2) or "").strip()
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

    def ask_gpt_refresh(self) -> None:
        self.ask_gpt(refresh=True)

    def action_ask_gpt(self, refresh: bool = False) -> None:
        """Alias for tab handlers (``_refresh_ask`` → workspace.action_ask_gpt)."""
        self.ask_gpt(refresh=refresh)

    def map_focus(self, key: str) -> None:
        """Toggle map/contact focus from inspector Migration Map clicks."""
        key = str(key or "")
        self._map_focus = None if self._map_focus == key else key
        if self._map_focus:
            self._notify(f"Map focus · {self._map_focus}")
        self.refresh_inspector(keep_scroll=True)

    def tree(self, name: str) -> None:
        """KeyBar-style tree actions from click meta (``screen.tree('expand_all')``)."""
        fn = getattr(self, name, None)
        if callable(fn):
            fn()

    def _sync_fetch_ctx(self) -> None:
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

    def _sync_ask_ctx(self) -> None:
        if not self.ctx:
            return
        self.ctx.ask_cache = self.ask_cache
        self.ctx.ask_ground_tab = self.ask_ground_tab
        self.ctx.ask_job = JOBS.get(ASK_JOB)

    def update_tabs(self) -> None:
        t = Text()
        # (col0, col1, row, tab_index) for click hits — cols in character cells.
        specs: list[tuple[int, int, int, int]] = []
        node = self.hg_nodes.get(self.current) if self.current else None
        if node and node.get("placeholder"):
            label = " 1 Overview "
            t.append(label, style="bold reverse #729fcf")
            t.append("· skeleton — other tabs after Discover", style="#8A8294")
            specs.append((0, len(label), 0, 0))
        else:
            col = 0
            row = 0
            for i, tb in enumerate(TABS):
                if i and i % 4 == 0:
                    t.append("\n")
                    row += 1
                    col = 0
                style = "bold reverse #729fcf" if i == self.section else "#AEA5B8"
                key = SECTION_LABELS[i] if i < len(SECTION_LABELS) else "·"
                label = f" {key} {tb.title} "
                t.append(label, style=style)
                t.append(" ")
                specs.append((col, col + len(label), row, i))
                col += len(label) + 1
        self._tab_hit_specs = specs
        try:
            self.query_one("#tabs", RichText).update(t)
        except KeyError:
            pass

    def _tab_hits(self):
        """Clickable inspector tab chips."""
        from textual_wgpu.fonts import cell_width
        from textual_wgpu.geometry import Rect

        try:
            tabs = self.query_one("#tabs", RichText)
        except KeyError:
            return []
        if tabs.rect.w < 8 or tabs.rect.h < 8:
            return []
        cw = cell_width(tabs.FONT_SIZE)
        out = []
        for col0, col1, row, idx in getattr(self, "_tab_hit_specs", ()):
            box = Rect(
                tabs.rect.x + 6 + col0 * cw,
                tabs.rect.y + 2 + row * tabs.LINE_H,
                max(8, (col1 - col0) * cw),
                tabs.LINE_H,
            )

            def _pick(_x: int, _y: int, i: int = idx) -> None:
                self.set_section(i)

            out.append((box, _pick))
        return out

    def _discover_folder(self) -> Path | None:
        path = self._path("ftdna_discover") or self.folder
        return Path(path) if path and Path(path).is_dir() else None

    def _fetch_running(self) -> bool:
        job = JOBS.get(FETCH_JOB)
        return bool(self._fetch_names and job and job.running)

    def _keybar_items(self) -> list:
        items = [
            ("↑↓", "Nav", None),
            ("←→", "Fold", None),
            ("/", "Search", lambda: self.focus_search()),
            ("F", "Filter", lambda: self.filter()),
            ("S", "Sort", lambda: self.sort()),
            ("B", "Rich", lambda: self.next_rich()),
            ("M", "Map", lambda: self.map_fullscreen()),
            ("W", "World/Fit", lambda: self.toggle_map_world()),
            (
                "O",
                "Our Y" + (" · cycle" if self.branch and self.branch.n_candidates > 1 else ""),
                lambda: self.our_y(),
            ),
            ("A", "Ask", lambda: self.ask_gpt()),
            ("E", "Expand", lambda: self.expand_all()),
            ("Z", "Collapse", lambda: self.collapse_all()),
            ("[", "Prev", lambda: self.section_prev()),
            ("]", "Next", lambda: self.section_next()),
        ]
        if self._fetch_running():
            items.append(("C", "Cancel", lambda: self.cancel_fetch()))
        elif self.ready and self.current and self.ctx and not self.branch_busy:
            cur = self.current
            try:
                n_miss, _, self_miss, self_local = self.ctx.discover_subtree_stats(cur)
            except Exception:  # noqa: BLE001
                n_miss, self_miss, self_local = 0, False, False
            if self_local:
                items.append(("L", "Apply local", lambda: self.apply_local()))
            if self_miss:
                items.append(("D", "Fetch node", lambda: self.fetch_node()))
            if n_miss and not (self_miss and n_miss == 1):
                items.append(("⇧D", "Fetch branch", lambda: self.fetch_branch()))
        items += [
            ("Tab", "Panel", lambda: focus_next(self)),
            ("Esc", "Back", lambda: self.back()),
            ("I", "Issues", lambda: self.issues()),
            ("?", "Help", lambda: self.help()),
            ("Q", "Quit", lambda: self.app.ask_quit() if self.app else None),
        ]
        return items

    def update_keybar(self) -> None:
        if self._keybar is not None:
            self._keybar.set_items(self._keybar_items())

    def update_crumbs(self) -> None:
        try:
            crumbs = self.query_one("#crumbs", RichText)
        except KeyError:
            return
        if not self.ready:
            crumbs.update(Text(
                "Set FTDNA Haplotree in F2 → P, then return to F3.",
                style="#8A8294",
            ))
            return
        if not self.current or not self.ctx:
            crumbs.update("")
            return
        chain = self.ctx.lineage(self.current)
        cut = False
        while len(chain) > 1 and sum(len(n) for n in chain) + 3 * (len(chain) - 1) + (4 if cut else 0) > 60:
            chain, cut = chain[1:], True
        t = Text(no_wrap=True)
        if cut:
            t.append("… › ", style="#8A8294")
        for i, n in enumerate(chain):
            if i:
                t.append(" › ", style="#8A8294")
            missing = self.hg_nodes[n].get("placeholder")
            our = self.view.our_styles.get(n) if n in self.view.mine else None
            if n == self.current:
                style = f"bold {our}" if our else (
                    "bold #53A94B" if n in self.view.mine else "bold #729fcf"
                )
            elif our:
                style = our
            elif n in self.view.mine:
                style = "#53A94B"
            else:
                style = "#8A8294" if missing else "#C3BCCC"
            t.append(n, style=click(f"screen.goto({n!r})", style))
            if n in self.view.our_uncertain:
                t.append("?", style="bold #B04A4A")
        crumbs.on_action = self._run_action
        crumbs.update(t)

    def update_status(self) -> None:
        name = self.current or "—"
        node = self.hg_nodes.get(self.current) if self.current else None
        depth = len(self.ctx.lineage(name)) - 1 if (self.current and self.ctx) else 0
        pos = f"{self.view.cursor + 1}/{len(self.view.rows)}" if self.view.rows else "0/0"
        size = ""
        if node:
            rank = node.get("rank")
            rank_s = f"#{rank:,}/{self.hg_stats.get('branching', 0):,}" if rank else "leaf"
            size = (
                f"    {SHORT_CLASS[node.get('rich', 'normal')]}"
                f"  desc {node.get('descendants_count', 0):,} ({rank_s})  ↓{node.get('height', 0)}"
            )
        if self.branch_busy:
            y = f"    {self.branch_busy}"
        elif self.branch:
            gname = self.branch_genome.name if self.branch_genome else "?"
            n = self.branch.n_candidates
            slot = f"{self.our_i + 1}/{n} " if n > 1 else ""
            bits = [f"{self.branch.derived_marks} DER"]
            if self.branch.uncertain_marks:
                bits.append(f"{self.branch.uncertain_marks} uncertain")
            if self.branch.pruned:
                bits.append(f"{len(self.branch.pruned)} pruned")
            if n > 1:
                bits.append(self.branch.tie_label or "tied")
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
        try:
            self.query_one("#status", StatusBar).update(
                f"SELECTED: {name}{size}    DEPTH: {depth}    VISIBLE: {len(self.view.rows):,}    "
                f"POS: {pos}{y}{fetch}",
            )
        except KeyError:
            pass
        self.update_crumbs()
        if self.app is not None:
            self.app.request_draw()

    # ── fetch / ingest ───────────────────────────────────────────────
    def _start_fetch(self, names: list[str], title: str) -> None:
        if not names:
            self._notify("Nothing to fetch in this branch")
            return
        folder = self._discover_folder()
        if not folder:
            self._notify("Set the Discover folder in F2 (Data → P) first")
            return
        if self.ctx:
            self.ctx.folder = folder
        job = JOBS.get(FETCH_JOB)
        if job and job.running:
            self._notify(f"Discover fetch already running: {job.stage}")
            return
        try:
            JOBS.start(
                FETCH_JOB, title,
                lambda j, ns=list(names), f=folder: fetch_resources(f, ns, j),
            )
        except RuntimeError as exc:
            self._notify(str(exc))
            return
        self._fetch_names = list(names)
        self._notify(f"{title} · {fetch_cost(len(names))}")
        self._sync_fetch_ctx()
        self.update_keybar()
        if self.current and self.ctx:
            try:
                self.query_one("#inspector", RichText).update(
                    self.ctx.render(self.current, self.section),
                )
            except Exception:  # noqa: BLE001
                pass
        job2 = JOBS.get(FETCH_JOB)
        self._fetch_paint_sig = (job2.stage or "") if job2 else ""
        self.update_status()

    def fetch_node(self) -> None:
        if self._fetch_running():
            self._notify("Fetch already in progress — press C to cancel")
            return
        name = self.current
        node = self.hg_nodes.get(name) if name else None
        if not name or not node:
            self._notify("Select a haplogroup first")
            return
        if not node.get("placeholder"):
            self._notify(f"{name}: Discover card already loaded")
            return
        if not node.get("haplotree"):
            self._notify(f"{name}: not a haplotree node — cannot fetch")
            return
        if self.ctx and self.ctx.has_discover_file(name):
            self.apply_local()
            return
        self._start_fetch([name], f"Fetch {name}")

    def fetch_branch(self) -> None:
        if self._fetch_running():
            self._notify("Fetch already in progress — press C to cancel")
            return
        name = self.current
        if not name or not self.ctx:
            self._notify("Select a haplogroup first")
            return
        missing = self.ctx.missing_discover(name)
        if not missing:
            local = self.ctx.local_discover(name)
            if local:
                self._ingest_discover(local)
                return
            self._notify(f"{name}: nothing to download in this branch")
            return
        self._start_fetch(missing, f"Fetch branch {name} · {len(missing):,}")

    def apply_local(self) -> None:
        name = self.current
        if not name or not self.ctx:
            self._notify("Select a haplogroup first")
            return
        local = self.ctx.local_discover(name)
        if not local:
            self._notify(f"{name}: no local Discover JSON on disk")
            return
        n = self._ingest_discover(local)
        if n:
            self.refresh_inspector()
            self._sync_tree_list(keep=self.current)
            self._notify(f"Applied local Discover · {n} node(s)")

    def cancel_fetch(self) -> None:
        job = JOBS.get(FETCH_JOB)
        if job and job.running:
            job.cancel()
            self._notify(f"Cancelling: {job.title}")
        else:
            self._notify("Nothing is fetching")

    def _ingest_discover(self, names: list[str] | str, *, quiet: bool = False) -> int:
        from ..core.ylink import safe_stem
        from ..workspaces.tree.data import _apply_discover, load_resource

        if isinstance(names, str):
            names = [names]
        folder = self._discover_folder()
        if not folder:
            if not quiet:
                self._notify("Discover folder not set")
            return 0
        if self.ctx:
            self.ctx.folder = folder
            if hasattr(self.ctx, "invalidate_discover_index"):
                self.ctx.invalidate_discover_index()
            else:
                self.ctx._subtree_stats_cache.clear()
                self.ctx._discover_stems = None
        loaded = skipped = 0
        for name in names:
            if name not in self.hg_nodes:
                continue
            path = folder / f"{safe_stem(name)}.json"
            if not path.is_file():
                skipped += 1
                continue
            try:
                data, err = load_resource(path)
            except Exception:  # noqa: BLE001
                skipped += 1
                continue
            if err or not data:
                skipped += 1
                continue
            was = self.hg_nodes[name].get("placeholder")
            try:
                _apply_discover(self.hg_nodes[name], data, path.name)
            except Exception:  # noqa: BLE001
                skipped += 1
                continue
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
                self._sync_tree_list(keep=self.current)
            self._sync_fetch_ctx()
            self.update_tabs()
            self.update_keybar()
            if self.current and self.ctx:
                try:
                    self.query_one("#inspector", RichText).update(
                        self.ctx.render(self.current, self.section),
                    )
                except Exception:  # noqa: BLE001
                    pass
            self.update_status()
        if not quiet:
            msg = f"Loaded {loaded:,} Discover card(s)"
            if skipped:
                msg += f" · {skipped:,} missing on disk"
            self._notify(msg)
        return loaded

    def _tick_fetch(self) -> None:
        if self.app is None or getattr(self.app, "mode_key", None) != "tree":
            return
        ask = JOBS.get(ASK_JOB)
        if ask:
            if ask.active:
                if self.ask_job_key and TABS[self.section].title == "ASK":
                    self.refresh_inspector(keep_scroll=True)
                self.update_status()
            elif id(ask) not in self._ask_seen:
                self._ask_seen.add(id(ask))
                if self.ask_job_key:
                    self._finish_ask_job(ask)
        job = JOBS.get(FETCH_JOB)
        if not self._fetch_names:
            return
        if job and job.running:
            sig = job.stage or ""
            if sig != self._fetch_paint_sig:
                self._fetch_paint_sig = sig
                self._sync_fetch_ctx()
                if self.current and self.ctx:
                    try:
                        self.query_one("#inspector", RichText).update(
                            self.ctx.render(self.current, self.section),
                        )
                    except Exception:  # noqa: BLE001
                        pass
            self.update_status()
            return
        # finished
        names = list(self._fetch_names)
        self._fetch_names = []
        self._fetch_paint_sig = None
        self._sync_fetch_ctx()
        if job and job.status == "done":
            n = self._ingest_discover(names)
            self._notify(f"Discover fetch done · applied {n}/{len(names)}")
            self.populate()
            if self.current:
                self.refresh_inspector(keep_scroll=True)
        elif job:
            self._notify(f"Discover fetch: {job.message or job.status}")
        self.update_keybar()
        self.update_status()

    # ── sections / ask ───────────────────────────────────────────────
    def set_section(self, i: int) -> None:
        """Switch inspector tab. Named set_section so it never shadows self.section index."""
        if not self.ready or i >= len(TABS):
            return
        node = self.hg_nodes.get(self.current) if self.current else None
        if node and node.get("placeholder") and i != 0:
            self._notify("Only Overview until Discover JSON is fetched (D)")
            i = 0
        self.section = i
        self.update_tabs()
        self.refresh_inspector()

    def section_prev(self) -> None:
        if not self.ready:
            return
        node = self.hg_nodes.get(self.current) if self.current else None
        if node and node.get("placeholder"):
            self.set_section(0)
            return
        self.set_section((self.section - 1) % len(TABS))

    def section_next(self) -> None:
        if not self.ready:
            return
        node = self.hg_nodes.get(self.current) if self.current else None
        if node and node.get("placeholder"):
            self.set_section(0)
            return
        self.set_section((self.section + 1) % len(TABS))

    def sec_1(self) -> None: self.set_section(0)
    def sec_2(self) -> None: self.set_section(1)
    def sec_3(self) -> None: self.set_section(2)
    def sec_4(self) -> None: self.set_section(3)
    def sec_5(self) -> None: self.set_section(4)
    def sec_6(self) -> None: self.set_section(5)
    def sec_7(self) -> None: self.set_section(6)
    def sec_8(self) -> None: self.set_section(7)
    def sec_9(self) -> None: self.set_section(8)
    def sec_0(self) -> None: self.set_section(9)
    def sec_minus(self) -> None: self.set_section(10)
    def sec_eq(self) -> None: self.set_section(11)

    def _ask_section_index(self) -> int:
        for i, tb in enumerate(TABS):
            if tb.title == "ASK":
                return i
        return 0

    def ask_gpt(self, refresh: bool = False) -> None:
        if not self.ready or not self.ctx or not self.current:
            self._notify("Select a haplogroup first")
            return
        node = self.hg_nodes.get(self.current)
        if not node:
            self._notify("No node loaded")
            return
        try:
            err = chat.can_send(self.store)
        except Exception as exc:  # noqa: BLE001
            self._notify(str(exc))
            return
        if err:
            self._notify(err)
            return
        if TABS[self.section].title != "ASK":
            self.ask_ground_tab = tab_title_at(self.section)
        ground = self.ask_ground_tab or "Overview"
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
            for _ in range(40):
                j = JOBS.get(ASK_JOB)
                if not j or not j.active:
                    break
                time.sleep(0.05)
            if (j := JOBS.get(ASK_JOB)) and j.active:
                self._notify("Ask still stopping · try again")
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
            self._notify(str(exc))
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
            try:
                model = chat.model_label(self.store)
                ts = chat.now()
            except Exception:  # noqa: BLE001
                model, ts = "", ""
            entry = {"text": text or "(empty reply)", "model": model, "ts": ts}
            self.ask_cache[key] = entry
            try:
                save_cached(key, text=entry["text"], model=model, ts=ts)
            except OSError:
                pass
            self._notify(f"Tree note · {key.split('|')[0]}")
        elif job.status == "failed":
            self._notify(f"Tree ask failed: {job.message}")
        else:
            self._notify(f"Tree ask: {job.message or job.status}")
        self.section = self._ask_section_index()
        self.refresh_inspector()
        self.update_status()

    # ── navigation actions ───────────────────────────────────────────
    def expand(self) -> None:
        if not self.ready:
            return
        self.view.expand_or_child()
        self._sync_tree_list(keep=self.view.current)
        self.current = self.view.current
        self._schedule_inspector()

    def collapse(self) -> None:
        if not self.ready:
            return
        self.view.collapse_or_parent()
        self._sync_tree_list(keep=self.view.current)
        self.current = self.view.current
        self._schedule_inspector()

    def toggle(self) -> None:
        if not self.ready:
            return
        if self.view.toggle():
            self._sync_tree_list(keep=self.view.current)

    def expand_subtree(self) -> None:
        if not self.ready:
            return
        self.view.expand_subtree()
        self._sync_tree_list(keep=self.view.current)
        self._notify("Subtree expanded")

    def jump_parent(self) -> None:
        if not self.ready:
            return
        self.view.jump_parent()
        self._sync_tree_list(keep=self.view.current)
        self.current = self.view.current
        self._schedule_inspector()

    def jump_root(self) -> None:
        """R: parent-root when tree focused; ASK refresh when inspector + ASK tab."""
        if not self.ready:
            return
        if self._insp_focused() and TABS[self.section].title == "ASK":
            self.tab_key("r")
            return
        self.view.jump_root()
        self._sync_tree_list(keep=self.view.current)
        self.current = self.view.current
        self._schedule_inspector()

    def expand_all(self) -> None:
        if not self.ready:
            return
        self.view.expand_all()
        self._sync_tree_list(keep=self.view.current)
        self._notify("Expanded")

    def collapse_all(self) -> None:
        if not self.ready:
            return
        self.view.collapse_all()
        self._sync_tree_list(keep=self.view.current)
        self._notify("Collapsed")

    def _insp_focused(self) -> bool:
        if self.app is None:
            return False
        try:
            return self.app._focus is self.query_one("#inspector", RichText)
        except KeyError:
            return False

    def _tree_focused(self) -> bool:
        if self.app is None:
            return False
        try:
            return self.app._focus is self.query_one("#tree", OptionList)
        except KeyError:
            return False

    def tab_key(self, key: str) -> bool:
        """Section shortcuts (Geography g/w, Raw x, LORE g/s/x, ASK r)."""
        if not self.ready or not self.ctx:
            return False
        node = self.hg_nodes.get(self.current) if self.current else None
        if node and node.get("placeholder"):
            return False
        handler = TABS[self.section].keys.get(key)
        if not handler:
            return False
        handler(self.ctx)
        self.refresh_inspector()
        return True

    def tab_g(self) -> None:
        self.tab_key("g")

    def tab_x(self) -> None:
        self.tab_key("x")

    def _copy_text(self, text: str) -> bool:
        from .clipboard import copy_text

        return copy_text(self.app, text)

    def copy_name(self) -> None:
        from .clipboard import copy_status

        if not self.ready or not self.view.current:
            return
        name = self.view.current
        ok = self._copy_text(name)
        self._notify(copy_status(ok, success=f"Copied {name}", label=name))

    def copy_branch(self) -> None:
        from .clipboard import copy_status

        if not self.ready:
            return
        name = self.view.current
        if not name:
            self._notify("No branch selected")
            return
        tree, count = self.view.format_tree([name])
        if not tree:
            return
        ok = self._copy_text(tree)
        self._notify(copy_status(
            ok,
            success=f"Copied branch: {count:,} nodes",
            label=f"branch {count:,} nodes",
        ))

    def copy_tree(self) -> None:
        from .clipboard import copy_status

        if not self.ready:
            return
        if not self.view.vroots:
            self._notify("Tree is empty")
            return
        tree, count = self.view.format_tree(self.view.vroots)
        if not tree:
            self._notify("Tree is empty")
            return
        ok = self._copy_text(tree)
        self._notify(copy_status(
            ok,
            success=f"Copied tree: {count:,} nodes",
            label=f"tree {count:,} nodes",
        ))

    def select_node(self) -> None:
        if self.ready and self.current:
            self.refresh_inspector(keep_scroll=True)
            if self.app is not None:
                self.app.set_focus(self.query_one("#inspector", RichText))

    def goto(self, name: str) -> None:
        if not self.ready or name not in self.hg_nodes:
            return
        if name not in self.view.order_set:
            self._notify(f"{name} is hidden by the filter (F)")
            return
        self.view.reveal(name)
        self._sync_tree_list(keep=name)
        self.current = name
        self.refresh_inspector()

    def focus_search(self) -> None:
        if not self.ready:
            return
        try:
            inp = self.query_one("#search", TextArea)
        except KeyError:
            return
        inp.load_text("")
        inp.focus()

    def clear_search(self) -> bool:
        """Esc while search focused → tree (ModeApp clear_search hook)."""
        return self.back()

    def back(self) -> bool:
        """Esc: leave search / inspector → tree (Textual action_back)."""
        if not self.ready or self.app is None:
            return False
        try:
            tree = self.query_one("#tree", OptionList)
            search = self.query_one("#search", TextArea)
            insp = self.query_one("#inspector", RichText)
        except KeyError:
            return False
        focus = self.app._focus
        if focus is search or focus is insp:
            self.app.set_focus(tree)
            return True
        return False

    def toggle_panel(self) -> bool:
        """Tab: cycle search ↔ tree ↔ inspector via ModeApp focus chain."""
        if not self.ready or self.app is None:
            return False
        self.app.focus_next()
        return True

    def _search_submit(self) -> None:
        try:
            q = self.query_one("#search", TextArea).text.strip().lower()
        except KeyError:
            return
        if not q or not self.ready:
            return
        self.last_query = q
        order = self.view.order
        exact = [n for n in order if n.lower() == q]
        rest = [n for n in order if q in self.haystack.get(n, "") and n not in exact]
        self.search_hits = exact + rest
        if not self.search_hits:
            self._notify(f"Not found: {q}")
            return
        if len(self.search_hits) == 1:
            self.hit_i = 0
            self.jump_to_hit()
            return
        from .app_api import require_app

        if require_app(self, what="search pick"):
            opts: list[tuple[str, str]] = []
            for n in self.search_hits[:300]:
                node = self.hg_nodes.get(n) or {}
                if node.get("placeholder"):
                    tag = "[haplotree]" if node.get("haplotree") else "[missing resource]"
                    label = f"{n.ljust(18)}{tag}"
                else:
                    date = str(node.get("date") or "").ljust(14)
                    desc = f"+{node.get('descendants_count', 0):,}".ljust(9)
                    flags = " · ".join(node.get("flags") or [])
                    label = f"{n.ljust(18)}{date}{desc}{flags}"
                opts.append((n, label))
            extra = len(self.search_hits) - 300
            title = f'{len(self.search_hits):,} matches for "{q}"   (Enter: go · Esc: cancel)'
            if extra > 0:
                title = f"{title}  · +{extra:,} more — refine the search"

            def picked(name: str | None) -> None:
                if name and name in self.search_hits:
                    self.hit_i = self.search_hits.index(name)
                    self.jump_to_hit()

            self.app.ask_pick(title, opts, picked)
        else:
            self.hit_i = 0
            self.jump_to_hit()

    def next_match(self) -> None:
        if self.ready and self.search_hits:
            self.hit_i = (self.hit_i + 1) % len(self.search_hits)
            self.jump_to_hit()

    def jump_to_hit(self) -> None:
        name = self.search_hits[self.hit_i]
        self.view.reveal(name)
        self._sync_tree_list(keep=name)
        self.current = name
        self.refresh_inspector()
        self._notify(f"{name}  ({self.hit_i + 1}/{len(self.search_hits)})  — N: next")

    def sort(self) -> None:
        if not self.ready:
            return
        if self._insp_focused() and "s" in TABS[self.section].keys:
            self.tab_key("s")
            return
        cur = self.current
        self.view.sort_mode = SORT_MODES[
            (SORT_MODES.index(self.view.sort_mode) + 1) % len(SORT_MODES)
        ]
        self.populate()
        if cur and cur in self.view.order_set:
            self.view.reveal(cur)
            self._sync_tree_list(keep=cur)
            self.current = cur
        self._notify(f"Sort: {SORT_LABELS[self.view.sort_mode]}")

    def next_rich(self) -> None:
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
                self._sync_tree_list(keep=name)
                self.current = name
                self.refresh_inspector()
                self._notify(
                    f"{name}  {SHORT_CLASS[node['rich']]} · +{node['descendants_count']:,} "
                    f"· ↓{node['height']}  — B: next",
                )
                return
        self._notify("No rich branches in the current view")

    def filter(self) -> None:
        """F — full FilterScreen (country / years / min descendants / height)."""
        from .app_api import require_app

        if not self.ready:
            return
        if not require_app(self, what="filter form"):
            return
        c = self.flt or {}

        def done(raw: dict[str, str] | None) -> None:
            if raw is None:
                return
            if not raw:
                self.apply_filter({})
                return
            country = (raw.get("country") or "").strip().upper()
            lo_raw = (raw.get("lo_raw") or "").strip()
            hi_raw = (raw.get("hi_raw") or "").strip()
            mind_raw = (raw.get("mind_raw") or "").strip()
            minh_raw = (raw.get("minh_raw") or "").strip()
            try:
                lo, hi = parse_year(lo_raw), parse_year(hi_raw)
                mind, minh = parse_count(mind_raw), parse_count(minh_raw)
            except ValueError as exc:
                self._notify(f"Bad value: {exc}")
                return
            self.apply_filter({
                "country": country,
                "lo": lo,
                "hi": hi,
                "lo_raw": lo_raw,
                "hi_raw": hi_raw,
                "mind": mind,
                "minh": minh,
                "mind_raw": mind_raw,
                "minh_raw": minh_raw,
            })

        self.app.ask_form(
            "FILTER  (empty field = no filter)",
            [
                ("country", "Country code (e.g. GB, ENG, UA)", c.get("country", "")),
                ("lo_raw", "Formed from (e.g. -3000 or 3000 BCE)", c.get("lo_raw", "")),
                ("hi_raw", "Formed to (e.g. 500 or 500 CE)", c.get("hi_raw", "")),
                ("mind_raw", "Min descendants (e.g. 20)", c.get("mind_raw", "")),
                ("minh_raw", "Min height — levels below (e.g. 4)", c.get("minh_raw", "")),
            ],
            done,
        )

    def apply_filter(self, flt: dict) -> None:
        self.flt = flt
        code, lo, hi = flt.get("country"), flt.get("lo"), flt.get("hi")
        mind, minh = flt.get("mind"), flt.get("minh")
        if not flt or not (
            code or lo is not None or hi is not None
            or mind is not None or minh is not None
        ):
            self.flt, self.vis_set = {}, None
            self.populate()
            self._notify("Filter cleared")
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
            self._notify("No haplogroups match this filter")
            self.flt = {}
            return
        visible = set(matched)
        for name in matched:
            if self.ctx:
                visible.update(self.ctx.lineage(name))
        self.vis_set = visible
        self.populate()
        self._notify(f"Filter · {len(matched):,} matches")

    def toggle_map_world(self) -> None:
        """W — tab handler (Geography / Migration Map) or map_world fallback."""
        if self.tab_key("w"):
            return
        if not self.ctx:
            return
        self.ctx.map_world = not bool(getattr(self.ctx, "map_world", False))
        mode = "world" if self.ctx.map_world else "fit"
        self._notify(f"Map · {mode}")
        self.refresh_inspector()

    def map_fullscreen(self) -> None:
        """M — MapScreen analogue: DEFCON map + Contact log / Migration."""
        if not self.ready or not self.current:
            return
        node = self.hg_nodes.get(self.current)
        if not node:
            return
        if node.get("placeholder"):
            self._notify("Fetch Discover JSON first (D) — map needs migration data")
            return
        if self.folder:
            hydrate_node(node, Path(self.folder))
        if not node.get("migrationmap"):
            self._notify("No migration map on this node")
            return
        from .app_api import require_app

        if not require_app(self, what="map overlay"):
            return

        state = {
            "node": node,
            "world": bool(self.ctx and getattr(self.ctx, "map_world", False)),
            "side_scroll": 0,
            "map_scroll_x": 0,
        }

        def paint(draw, w: int, h: int):
            return self._paint_map_modal(draw, w, h, state)

        def on_key(key: str) -> str | None:
            if key in ("escape", "q", "m"):
                return "close"
            if key == "w":
                state["world"] = not state["world"]
                if self.ctx is not None:
                    self.ctx.map_world = state["world"]
                return "handled"
            if key in ("left",):
                state["map_scroll_x"] = max(0, int(state["map_scroll_x"]) - 4)
                return "handled"
            if key in ("right",):
                state["map_scroll_x"] = int(state["map_scroll_x"]) + 4
                return "handled"
            if key in ("up", "pageup"):
                state["side_scroll"] = max(0, int(state["side_scroll"]) - (3 if key == "up" else 8))
                return "handled"
            if key in ("down", "pagedown"):
                state["side_scroll"] = int(state["side_scroll"]) + (3 if key == "down" else 8)
                return "handled"
            return None

        def on_wheel(steps: int) -> None:
            state["side_scroll"] = max(0, int(state["side_scroll"]) + steps)

        self.app.show_overlay(paint, on_key=on_key, on_wheel=on_wheel)

    def _paint_map_modal(self, draw, w: int, h: int, state: dict) -> list:
        """Fullscreen MapScreen layout: map pane + side Contact/Migration + help."""
        from textual_wgpu.fonts import cell_width, font as ui_font
        from textual_wgpu.geometry import Rect

        # Match Textual MapScreen CSS (#1E1923 / #241E2A / #302838 / #483A59).
        map_bg = (30, 25, 35, 255)       # #1E1923
        side_bg = (36, 30, 42, 255)      # #241E2A
        help_bg = (48, 40, 56, 255)      # #302838
        border = (72, 58, 89, 255)       # #483A59
        help_fg = (88, 130, 199, 255)    # #5882C7
        draw.rectangle([0, 0, w, h], fill=map_bg)

        help_h = 28
        # Side width mirrors MapScreen: 56 / 52 / share of width.
        cw12 = cell_width(12)
        if w >= 1200:
            side_w = 56 * cw12
        elif w >= 1000:
            side_w = 52 * cw12
        else:
            side_w = max(40 * cw12, min(52 * cw12, w // 3 + 4 * cw12))
        side_w = min(side_w, max(200, w // 2))
        map_w = max(200, w - side_w)
        map_h = max(120, h - help_h)

        # Terminal-ish cell aspect for braille (MapScreen is 1 cell tall).
        font_sz = 13
        line_h = max(14, font_sz + 2)
        cw = cell_width(font_sz)
        # Chrome around braille canvas (title, ═, axis, ═, symbols, LEGEND).
        map_chrome = 6
        pad_x, pad_y = 10, 4
        cols = max(36, min(140, (map_w - pad_x * 2) // cw))
        max_rows = max(12, (map_h - pad_y * 2) // line_h - map_chrome)

        class _MapDoc:
            def __init__(self) -> None:
                self.t = Text(no_wrap=True, overflow="crop")

        node = state["node"]
        doc = _MapDoc()
        centers = defcon_map(
            doc,
            node.get("migrationmap"),
            node.get("ancient") or [],
            node.get("name", ""),
            year_label,
            world=bool(state["world"]),
            cols=cols,
            max_rows=max_rows,
            with_log=False,
            show_path_legend=True,
        ) or []

        # Map pane (left) — braille runs on map_bg, no panel fill.
        map_rt = RichText(doc.t)
        map_rt.app = self.app
        map_rt.fill_bg = False
        map_rt.LINE_H = line_h
        map_rt.FONT_SIZE = font_sz
        map_rt.scroll_y = 0
        # Horizontal scroll when map wider than pane (MapScreen overflow-x).
        map_lines = map_rt._lines()
        max_cols = max((sum(len(t) for t, _ in row) for row in map_lines), default=cols)
        max_sx = max(0, max_cols - max(1, (map_w - pad_x * 2) // cw))
        state["map_scroll_x"] = max(0, min(int(state["map_scroll_x"]), max_sx))
        sx = int(state["map_scroll_x"])
        if sx:
            # Rebuild content with left crop for scroll_x (cell columns).
            cropped = Text(no_wrap=True, overflow="crop")
            raw = str(doc.t)
            # Prefer span-preserving crop via rich Text slicing when possible.
            try:
                for line in doc.t.split("\n"):
                    cropped.append_text(line[sx:])
                    cropped.append("\n")
            except Exception:  # noqa: BLE001
                for row in raw.split("\n"):
                    cropped.append(row[sx:] + "\n")
            map_rt.update(cropped, home=True)
        map_rt.layout(Rect(pad_x, pad_y, map_w - pad_x * 2, map_h - pad_y * 2))
        map_rt.paint(draw)

        # Side pane (right): Contact log + Migration — MapScreen #sidescroll.
        draw.rectangle([map_w, 0, w, map_h], fill=side_bg)
        draw.line([(map_w, 0), (map_w, map_h)], fill=border, width=2)
        side = Doc()
        side_inner = max(32, (side_w - 4 * cw) // cw)
        from ..workspaces.tree.tabs.node import _WIDTH
        _WIDTH[0] = side_inner
        for text, style in contact_log_lines(
            centers, year_label, limit=60, width=side_inner,
        ):
            if style.startswith("bold"):
                side.title(text)
            else:
                side.line(text, style, indent=0)
        render_migration(side, node, compact=True)
        side_rt = RichText(side.t)
        side_rt.app = self.app
        side_rt.fill_bg = False
        side_rt.LINE_H = line_h
        side_rt.FONT_SIZE = font_sz
        side_rt.scroll_y = int(state.get("side_scroll") or 0)
        max_s = max(0, len(side_rt._lines()) - max(1, (map_h - 8) // line_h))
        side_rt.scroll_y = max(0, min(side_rt.scroll_y, max_s))
        state["side_scroll"] = side_rt.scroll_y
        side_rt.layout(Rect(map_w + 10, 4, side_w - 18, map_h - 8))
        side_rt.paint(draw)

        # Help strip (#maphelp).
        draw.rectangle([0, h - help_h, w, h], fill=help_bg)
        mode = "world" if state["world"] else "fit"
        help_line = (
            f" W — {mode} · ←→ pan map · ◆/labels = path colors · "
            f"LEGEND under map · Esc/M/Q"
        )
        draw.text((12, h - help_h + 6), help_line, font=ui_font(12), fill=help_fg)
        return []

    def help(self) -> None:
        """? — HelpScreen modal (not inspector dump)."""
        from .app_api import require_app

        if not require_app(self, what="help modal"):
            return
        body = Text()
        rows: list[tuple[str, str | None]] = [
            ("NAVIGATION", None),
            ("↑ ↓  PgUp PgDn  Home End", "move through branches"),
            ("→ / ←", "expand (or go to first child) / collapse (or go to parent)"),
            ("Space", "expand or collapse"),
            ("E / Z", "expand all / collapse all"),
            ("Shift+→  or  *", "expand the whole subtree of the current node"),
            ("P / R", "jump to parent / to root of the branch"),
            ("Enter", "open details panel (scroll with ↑↓)"),
            ("Tab / Esc", "switch panel / back to the tree"),
            ("Mouse", "click a row to select; click the marker or ▸, or double-click, to expand"),
            ("", "click names in the breadcrumbs, CHILDREN and LINEAGE to jump there"),
            ("", "click section tabs, mode bar F1–F10 and the hint line; wheel scrolls"),
            ("", "click outside a dialog to close it"),
            ("RICH BRANCHES", None),
            ("S", "sort children: oldest first → richest first → deepest first"),
            ("B", "jump to the next rich branch (rich node without rich children)"),
            ("O", "Our Y tip; tied/near-tie cycle · ? uncertain · ○ pruned"),
            ("A", "Ask: brief for this haplogroup → ASK tab + Tree · F3 chat"),
            ("FIND", None),
            ("/", "search haplogroup name, SNP or alias (list of matches)"),
            ("N", "next match"),
            ("F", "filter: country, dates, min descendants, min height"),
            ("INSPECTOR", None),
        ]
        _tab_keys = "1234567890-="
        for i, tab in enumerate(TABS):
            k = _tab_keys[i] if i < len(_tab_keys) else "·"
            rows.append((k, tab.title))
        rows += [
            ("G / W", "Geography: regions · world DB; Migration Map: W = fit/world"),
            ("M", "fullscreen Migration Map (map + Contact log / routes)"),
            ("X", "Raw JSON: show all lines / collapse"),
            ("Y", "copy haplogroup name"),
            ("OTHER", None),
            ("F1–F10", "switch mode: " + " · ".join(title for _, title in MODE_LABELS)),
            ("I", "data issues found while loading"),
            ("?  /  Q", "this help  /  quit"),
        ]
        for k, v in rows:
            if v is None:
                body.append(f"\n{k}\n", style="bold #FBF6F0")
            else:
                body.append(f"  {k:<26}", style="#729fcf")
                body.append(f"{v}\n")
        body.append("\n")
        noise = bool(self.branch and (
            self.branch.uncertain or self.branch.pruned or self.branch.uncertain_marks
        ))
        body.append_text(glyph_legend(
            self.branch.tip if self.branch else None,
            n_candidates=self.branch.n_candidates if self.branch else 1,
            show_noise=noise,
            tie_label=self.branch.tie_label if self.branch else "",
        ))
        body.append("\n")
        body.append_text(legend_text())
        self.app.show_doc(
            "Tree help",
            body,
            close_keys=("escape", "q", "question_mark", "?"),
            hint="↑↓ / wheel scroll · Esc / ? / Q close",
        )

    def issues(self) -> None:
        """I — IssuesScreen modal (category dump, not inspector)."""
        if not self.ready:
            return
        n = self.hg_stats.get("issues", 0)
        if not n:
            self._notify("No data issues")
            return
        from .app_api import require_app

        if not require_app(self, what="issues modal"):
            return

        titles = {
            "unreadable": "Unreadable JSON",
            "no_name": "JSON without haplogroup.name",
            "duplicates": "Duplicate haplogroup names",
            "missing_parents": "Missing parents",
            "cycles": "Cycles",
            "csv": "progress.csv issues",
        }

        def fmt(x) -> str:
            if isinstance(x, (list, tuple)):
                return " · ".join(str(p) for p in x)
            return str(x)

        t = Text()
        t.append("DATA ISSUES\n", style="bold #FBF6F0")
        any_row = False
        issues = self.hg_issues if isinstance(self.hg_issues, dict) else {}
        for key, items in issues.items():
            if not items:
                continue
            any_row = True
            title = titles.get(key, key)
            t.append(f"\n{title} · {len(items)}\n", style="bold #729fcf")
            for x in list(items)[:300]:
                t.append(f"  {fmt(x)}\n")
            if len(items) > 300:
                t.append(f"  … +{len(items) - 300} more\n", style="#8A8294")
        if not any_row:
            t.append("\n  (none)\n", style="#AEA5B8")
        self.app.show_doc(
            f"Data issues · {n}",
            t,
            close_keys=("escape", "q", "i"),
            hint="↑↓ / wheel scroll · Esc / I / Q close",
            box_w=840,
        )
