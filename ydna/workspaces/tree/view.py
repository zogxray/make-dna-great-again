"""Левая панель: дерево гаплогрупп (BranchView), стили богатства веток и легенда."""

from __future__ import annotations

import math

from rich.segment import Segment
from rich.text import Text
from textual.binding import Binding
from textual.geometry import Size
from textual.message import Message
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import Static

from ...theme.styles import (
    BRIGHT, BRIGHT_BOLD, DATE_STYLES, DIM, FAINT, GHOST, HI_REV, MAP, MUTED,
    NAME_STYLE, OK, OK_BOLD, SIZE_STYLE, SOFT, WARN, WARN_BOLD, bold,
)
from .data import date_class, year_class, year_label


# ── богатство веток ──
# hub    — верхний 1% по числу потомков
# rich   — верхние 5%
# normal — всё остальное
# dead   — лист или узел максимум с одним потомком («брошенная» ветка)

MARKER = {
    "hub": "◉",
    "rich": "●",
    "normal": "●",
    "dead": "○"
}

CONNECT = {
    "hub": ("┣━", "┗━"),
    "rich": ("┣━", "┗━"),
    "normal": ("├─", "└─"),
    "dead": ("├╴", "└╴"),
}

SORT_MODES = ("date", "richness", "depth")

SORT_LABELS = {
    "date": "oldest → newest",
    "richness": "richest first",
    "depth": "deepest first"
}

SHORT_CLASS = {
    "hub": "HUB",
    "rich": "RICH",
    "normal": "normal",
    "dead": "dead end"
}

DATE_W = 13
SIZE_W = 18
GEO_W = 24

LEGEND = (
    ("< 4000 BCE", "deep"),
    ("4000–2000 BCE", "purple"),
    ("2000–1 BCE", "blue"),
    ("1–1000 CE", "cyan"),
    ("> 1000 CE", "green"),
    ("unknown", "unknown"),
)


def legend_text() -> Text:
    t = Text(no_wrap=True, overflow="ellipsis")

    for label, cls in LEGEND:
        t.append("● ", style=DATE_STYLES[cls])
        t.append(label + "   ", style=MUTED)

    return t


def glyph_legend(ours: str | None = None) -> Text:
    t = Text(no_wrap=True, overflow="ellipsis")

    for glyph, label, style in (
        ("◉", "hub", WARN_BOLD),
        ("┣━●", "rich", WARN),
        ("├─●", "normal", MUTED),
        ("├╴○", "dead end", FAINT),
    ):
        t.append(glyph + " ", style=style)
        t.append(label + "   ", style=MUTED)

    t.append("█░ ", style=WARN)
    t.append("size (log)   ↓ height", style=MUTED)

    if ours:
        t.append("   ● ", style=OK_BOLD)
        t.append(f"our Y ({ours})", style=MUTED)

    return t


def no_haplotree_guide(path: str | None = None) -> Text:
    """Empty-state F3: haplotree не задан / файл отсутствует — онбординг, не «поломка»."""
    t = Text()
    t.append(" TREE ", style=HI_REV)
    t.append("  needs Haplotree\n", style=BRIGHT_BOLD)
    if path:
        t.append(f"  Path is set, but the file is missing:\n", style=DIM)
        t.append(f"  {path}\n\n", style=WARN)
    else:
        t.append("  F3 builds the phylogenetic tree from the FTDNA Haplotree JSON.\n\n",
                 style=DIM)
    t.append("  1 · F2 Data\n", style=BRIGHT_BOLD)
    t.append("  P         point to y-dna-haplotree.json (required)\n", style=BRIGHT)
    t.append("  D / path  Discover folder — optional enrichment for cards\n\n", style=DIM)
    t.append("  2 · Back here\n", style=BRIGHT_BOLD)
    t.append("  F3        tree loads automatically once Haplotree is set\n\n", style=BRIGHT)
    t.append("  Our Y (O) needs a genotype in F1 after the tree is up.\n", style=DIM)
    return t


class BranchView(ScrollView, can_focus=True):
    """Левая панель: граф веток слева, затем FORMED, SIZE и GEOGRAPHY."""

    BINDINGS = [
        Binding("up", "cursor(-1)", "Up", show=False),
        Binding("down", "cursor(1)", "Down", show=False),
        Binding("pageup", "page(-1)", "PgUp", show=False),
        Binding("pagedown", "page(1)", "PgDn", show=False),
        Binding("home", "goto(0)", "Top", show=False),
        Binding("end", "goto(1)", "Bottom", show=False),

        Binding("right", "expand_or_child", "Expand", show=False),
        Binding("left", "collapse_or_parent", "Collapse", show=False),

        Binding("space", "toggle", "Toggle", show=False),
        Binding("enter", "select", "Details", show=False),

        Binding("p", "parent", "Parent", show=False),
        Binding("r", "root", "Root", show=False),

        # ── Копирование ──
        Binding("y", "copy_name", "Copy name", show=False),
        Binding("ctrl+c", "copy_branch", "Copy branch", show=False),
        Binding(
            "ctrl+shift+c",
            "copy_tree",
            "Copy tree",
            show=False
        ),

        Binding("e", "expand_all", "Expand all", show=False),
        Binding("z", "collapse_all", "Collapse all", show=False),

        Binding(
            "shift+right",
            "expand_subtree",
            "Expand subtree",
            show=False
        ),

        Binding(
            "asterisk",
            "expand_subtree",
            "Expand subtree",
            show=False
        ),
    ]

    class Highlighted(Message):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    class Selected(Message):
        pass

    class Changed(Message):
        pass

    def __init__(self, **kw) -> None:
        super().__init__(**kw)

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

    def set_source(
        self,
        nodes: dict,
        children: dict,
        roots: list[str],
        max_desc: int = 0
    ) -> None:
        self.nodes = nodes
        self.hg_children = children
        self.hg_roots = roots
        self.max_desc = max_desc

    # ── данные ──

    def sorted_names(self, names: list[str]) -> list[str]:
        mode, nd = self.sort_mode, self.nodes

        if mode == "richness":
            return sorted(
                names,
                key=lambda n: (
                    -nd[n].get("descendants_count", 0),
                    -nd[n].get("height", 0),
                    n.lower()
                )
            )

        if mode == "depth":
            return sorted(
                names,
                key=lambda n: (
                    -nd[n].get("height", 0),
                    -nd[n].get("descendants_count", 0),
                    n.lower()
                )
            )

        return list(names)

    def set_data(self, vis: set[str] | None) -> None:
        self.vkids = {
            n: self.sorted_names([
                c for c in kids
                if vis is None or c in vis
            ])
            for n, kids in self.hg_children.items()
            if vis is None or n in vis
        }

        self.vroots = self.sorted_names([
            r for r in self.hg_roots
            if vis is None or r in vis
        ])

        self.order, seen, stack = (
            [],
            set(),
            list(reversed(self.vroots))
        )

        while stack:
            n = stack.pop()

            if n in seen:
                continue

            seen.add(n)
            self.order.append(n)
            stack.extend(reversed(self.vkids.get(n, [])))

        self.order_set = seen

        # Полный haplotree (~100k) нельзя раскрывать целиком — только корни;
        # Our Y / стрелки раскрывают нужный путь. Маленькое Discover-подмножество — как раньше.
        if len(seen) > 8_000:
            self.expanded = set()
        else:
            self.expanded = {
                n for n in self.order
                if self.vkids.get(n)
            }

        self.cursor = 0

        self.rebuild()
        self.scroll_to(y=0, animate=False)
        self.update_header()
        self.notify_cursor()

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
            (
                r,
                0,
                (),
                i == n_roots - 1,
                i == n_roots - 1,
                self.root_tone(r)
            )
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

                # Адаптивная ширина отступов:
                # глубина 1–3: 4 символа
                # глубина 4–7: 2 символа
                # глубина 8+: 1 символ

                indent = (
                    4 if depth <= 3
                    else 2 if depth <= 7
                    else 1
                )

                lanes = "".join(
                    (
                        " " if last
                        else ("┃" if heavy else "│")
                    ) + " " * (indent - 1)
                    for last, heavy in anc
                )

                guide = (
                    lane
                    + "   "
                    + lanes
                    + CONNECT[cls][1 if is_last else 0]
                    + mk
                    + " "
                )

            rows.append((name, guide, tone, depth))

            kids = self.vkids.get(name, [])

            if kids and name in self.expanded:
                heavy = cls in ("rich", "hub")

                child_anc = (
                    anc + (((is_last, heavy),) if depth >= 1 else ())
                )

                for j in range(len(kids) - 1, -1, -1):
                    stack.append((
                        kids[j],
                        depth + 1,
                        child_anc,
                        j == len(kids) - 1,
                        root_last,
                        tone
                    ))

        self.rows = rows

        if keep is not None:
            for i, row in enumerate(rows):
                if row[0] == keep:
                    self.cursor = i
                    break

        self.cursor = max(
            0,
            min(self.cursor, len(rows) - 1)
        )

        self.virtual_size = Size(
            max(self.size.width, 1),
            len(rows)
        )

        self.refresh()

    @property
    def current(self) -> str | None:
        return self.rows[self.cursor][0] if self.rows else None

    def notify_cursor(self) -> None:
        if self.current:
            self.post_message(self.Highlighted(self.current))

        self.post_message(self.Changed())

    def marker_pos(self, idx: int) -> int:
        _, guide, _, depth = self.rows[idx]

        return 0 if depth == 0 else len(guide) - 2

    # ── колонки ──

    @staticmethod
    def columns(
        width: int
    ) -> tuple[int | None, int | None, int | None]:
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

        limit = (
            date_x if date_x is not None else width
        ) - 1

        label = (
            f"PHYLOGENETIC BRANCHES "
            f"({SORT_LABELS[self.sort_mode]})"
        )

        t = Text(label, no_wrap=True, overflow="crop")
        t.truncate(max(limit, 1), overflow="ellipsis", pad=True)

        if date_x is not None:
            t.append(" " + "FORMED".ljust(DATE_W))

        if size_x is not None:
            t.append("SIZE  desc ↓h".ljust(SIZE_W))

        if geo_x is not None:
            t.append("GEOGRAPHY")

        t.stylize(bold(MUTED))

        return t

    def update_header(self) -> None:
        try:
            self.app.query_one(
                "#colhead",
                Static
            ).update(self.header(self.size.width))
        except Exception:
            pass

    def on_resize(self) -> None:
        self.virtual_size = Size(
            max(self.size.width, 1),
            len(self.rows)
        )

        self.update_header()

    # ── отрисовка ──

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
            style=(bold(BRIGHT) if node.get("deep") else DIM),
        )

        return t

    def row_text(self, idx: int, width: int) -> Text:
        name, guide, tone, depth = self.rows[idx]

        node = self.nodes[name]
        ph = bool(node.get("placeholder"))

        cls = node.get("rich", "normal")
        color = DATE_STYLES[tone]
        on_path = name in self.mine
        is_tip = name == self.tip

        rich = cls in ("rich", "hub")

        if on_path:
            line_style = OK_BOLD if is_tip else OK
            marker_style = OK_BOLD
        else:
            line_style = color if rich else f"dim {color}"
            marker_style = (
                bold(color)
                if cls != "dead"
                else f"dim {color}"
            )

        date_x, size_x, geo_x = self.columns(width)

        left = Text(no_wrap=True, overflow="crop")

        i = self.marker_pos(idx)

        left.append(guide[:i], style=line_style)
        left.append(guide[i], style=marker_style)
        left.append(guide[i + 1:], style=line_style)

        if on_path:
            name_style = OK_BOLD if is_tip else OK
        elif ph:
            name_style = MUTED
        else:
            name_style = NAME_STYLE[cls]

        if ph:
            left.append(name, style=name_style)
            left.append(
                "  [haplotree]" if node.get("haplotree") else "  [MISSING RESOURCE]",
                style=f"{FAINT} italic",
            )
        else:
            left.append(name, style=name_style)

        if self.vkids.get(name) and name not in self.expanded:
            left.append(
                f"  ▸ +{node.get('descendants_count', 0)}",
                style=DIM,
            )

        t = left

        if date_x is not None:
            left.truncate(
                date_x - 1,
                overflow="ellipsis",
                pad=True
            )

            t.append(" ")

            if ph:
                bd = node.get("branch_date")

                t.append(
                    (
                        year_label(bd)
                        if bd is not None else ""
                    ).ljust(DATE_W),
                    style=GHOST,
                )

            else:
                parent = self.nodes.get(
                    node.get("parent") or ""
                )

                same = (
                    parent is not None
                    and not parent.get("placeholder")
                    and node.get("date_mean") is not None
                    and parent.get("date_mean")
                    == node.get("date_mean")
                )

                t.append(
                    node["date"].ljust(DATE_W),
                    style=(
                        GHOST
                        if same
                        else DATE_STYLES[date_class(node)]
                    )
                )

        if size_x is not None:
            t.append_text(self.size_text(node, cls))

        if geo_x is not None:
            geo = (
                " · ".join(node.get("flags") or [])
                if not ph else ""
            )

            if len(geo) > GEO_W - 2:
                geo = geo[:GEO_W - 3] + "…"

            t.append(
                geo.ljust(GEO_W),
                style=SOFT,
            )

        t.truncate(
            width,
            overflow="crop",
            pad=True
        )

        if idx == self.cursor:
            t.stylize(
                MAP["select_on"]
                if self.has_focus
                else MAP["select_dim"]
            )

        return t

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        idx = y + int(self.scroll_offset.y)

        if idx >= len(self.rows) or width <= 0:
            return Strip.blank(width, self.rich_style)

        return Strip(
            list(
                Segment.apply_style(
                    self.row_text(idx, width).render(self.app.console),
                    self.rich_style,
                )
            )
        ).adjust_cell_length(width)

    def watch_has_focus(self, value: bool) -> None:
        self.refresh()

    # ── навигация ──

    def ensure_visible(self) -> None:
        h = self.size.height
        top = int(self.scroll_offset.y)

        if self.cursor < top:
            self.scroll_to(
                y=self.cursor,
                animate=False
            )

        elif self.cursor >= top + h:
            self.scroll_to(
                y=self.cursor - h + 1,
                animate=False
            )

    def move(self, to: int) -> None:
        if not self.rows:
            return

        to = max(
            0,
            min(len(self.rows) - 1, to)
        )

        if to != self.cursor:
            self.cursor = to
            self.ensure_visible()
            self.refresh()
            self.notify_cursor()

    def action_cursor(self, d: int) -> None:
        self.move(self.cursor + d)

    def action_page(self, d: int) -> None:
        self.move(
            self.cursor
            + d * max(1, self.size.height - 1)
        )

    def action_goto(self, end: int) -> None:
        self.move(
            len(self.rows) - 1 if end else 0
        )

    def _toggle(self, expand: bool | None) -> bool:
        name = self.current

        if not name or not self.vkids.get(name):
            return False

        is_open = name in self.expanded

        if expand is None:
            expand = not is_open

        if expand == is_open:
            return False

        (
            self.expanded.add
            if expand
            else self.expanded.discard
        )(name)

        self.rebuild(keep=name)
        self.ensure_visible()
        self.post_message(self.Changed())

        return True

    def action_toggle(self) -> None:
        self._toggle(None)

    def action_expand_or_child(self) -> None:
        if (
            not self._toggle(True)
            and self.current
            and self.vkids.get(self.current)
        ):
            self.move(self.cursor + 1)

    def action_expand_all(self) -> None:
        cur = self.current

        if len(self.order) > 8_000:
            # Два уровня от корней — иначе UI зависнет на полном haplotree.
            expanded: set[str] = set()
            for r in self.vroots:
                kids = self.vkids.get(r) or []
                if kids:
                    expanded.add(r)
                    for c in kids:
                        if self.vkids.get(c):
                            expanded.add(c)
            self.expanded = expanded
        else:
            self.expanded = {
                n for n in self.order
                if self.vkids.get(n)
            }

        self.rebuild(keep=cur)
        self.ensure_visible()
        self.post_message(self.Changed())

    def _visible_root_of(
        self,
        name: str | None
    ) -> str | None:
        cur, seen = name, set()

        while cur and cur not in seen:
            seen.add(cur)

            parent = self.nodes[cur].get("parent")

            if parent and parent in self.order_set:
                cur = parent
            else:
                break

        return cur

    def action_collapse_all(self) -> None:
        target = self._visible_root_of(self.current)

        self.expanded = set()

        self.rebuild(keep=target)
        self.ensure_visible()
        self.notify_cursor()

    def action_expand_subtree(self) -> None:
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
        self.ensure_visible()
        self.post_message(self.Changed())

    def action_parent(self) -> None:
        if not self.current:
            return

        parent = self.nodes[self.current].get("parent")

        for i in range(self.cursor - 1, -1, -1):
            if self.rows[i][0] == parent:
                self.move(i)
                return

    def action_collapse_or_parent(self) -> None:
        if not self._toggle(False):
            self.action_parent()

    def action_root(self) -> None:
        for i in range(self.cursor, -1, -1):
            if self.rows[i][3] == 0:
                self.move(i)
                return

    # ── копирование ──

    def _copy_text(self, text: str) -> bool:
        """Системный буфер (wl-copy/…) + внутренний/OSC 52. True, если ОС приняла текст."""
        try:
            self.app.copy_to_clipboard(text)
        except Exception:
            return False
        return bool(getattr(self.app, "_clip_system_ok", False))

    def _format_tree(
        self,
        roots: list[str]
    ) -> tuple[str, int]:
        """
        Формирует текстовое дерево из переданных корней.

        Учитывает текущую фильтрацию и сортировку.
        Не зависит от раскрытия узлов на экране.

        Используется итеративный обход, подходящий
        для глубоких деревьев и большого количества узлов.
        """

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
                    connector = (
                        "└─ " if is_last else "├─ "
                    )

                    lines.append(
                        prefix + connector + name
                    )

                kids = self.vkids.get(name, [])

                child_prefix = (
                    prefix
                    if is_root
                    else prefix + (
                        "   " if is_last else "│  "
                    )
                )

                for index in range(len(kids) - 1, -1, -1):
                    child = kids[index]

                    stack.append((
                        child,
                        child_prefix,
                        index == len(kids) - 1,
                        False
                    ))

        return "\n".join(lines), len(seen)

    def action_copy_name(self) -> None:
        if not self.current:
            return

        success = self._copy_text(self.current)

        self.app.notify(
            f"Copied {self.current}"
            if success
            else "Clipboard command sent; terminal support required",
            severity=(
                "information" if success else "warning"
            )
        )

    def action_copy_branch(self) -> None:
        """Копирует выбранную ветку со всеми потомками."""

        name = self.current

        if not name:
            self.app.notify(
                "No branch selected",
                severity="warning"
            )
            return

        tree, count = self._format_tree([name])

        if not tree:
            return

        success = self._copy_text(tree)

        self.app.notify(
            f"Copied branch: {count:,} nodes"
            if success
            else (
                "System clipboard unavailable; "
                "OSC 52 fallback used"
            ),
            severity=(
                "information" if success else "warning"
            )
        )

    def action_copy_tree(self) -> None:
        """Копирует всё дерево с учётом фильтров и сортировки."""

        if not self.vroots:
            self.app.notify(
                "Tree is empty",
                severity="warning"
            )
            return

        tree, count = self._format_tree(self.vroots)

        if not tree:
            self.app.notify(
                "Tree is empty",
                severity="warning"
            )
            return

        success = self._copy_text(tree)

        self.app.notify(
            f"Copied tree: {count:,} nodes"
            if success
            else (
                "System clipboard unavailable; "
                "OSC 52 fallback used"
            ),
            severity=(
                "information" if success else "warning"
            )
        )

    # ── выбор ──

    def action_select(self) -> None:
        self.post_message(self.Selected())

    # ── мышь ──

    def on_click(self, event) -> None:
        idx = int(self.scroll_offset.y) + event.y

        if not (0 <= idx < len(self.rows)):
            return

        self.focus()
        self.move(idx)

        name, guide, _, _ = self.rows[idx]
        node = self.nodes[name]

        hit = event.x == self.marker_pos(idx)

        if self.vkids.get(name) and name not in self.expanded:
            start = (
                len(guide)
                + len(name)
                + (
                    len("  [MISSING RESOURCE]")
                    if node.get("placeholder")
                    else 0
                )
                + 2
            )

            hit = hit or (
                start
                <= event.x
                < start + len(
                    f"▸ +{node.get('descendants_count', 0)}"
                )
            )

        if (hit and event.chain == 1) or (not hit and event.chain == 2):
            self._toggle(None)

    # ── переход к узлу ──

    def reveal(self, name: str) -> None:
        if name not in self.order_set:
            return

        cur = self.nodes[name].get("parent")
        seen = set()

        while cur and cur in self.nodes and cur not in seen:
            seen.add(cur)

            if self.vkids.get(cur):
                self.expanded.add(cur)

            cur = self.nodes[cur].get("parent")

        self.rebuild(keep=name)
        self.call_after_refresh(self.ensure_visible)
        self.notify_cursor()

    def focus_path(self, path: list[str], collapse: bool = True) -> str | None:
        """Раскрывает предков пути и ставит курсор на самый глубокий видимый узел."""
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
        self.call_after_refresh(self.ensure_visible)
        self.notify_cursor()
        return tip