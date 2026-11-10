"""Модальные экраны дерева (фильтр, help, issues, поиск). Загрузка — общий BusyModal."""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from .data import date_class, parse_count, parse_year
from ..base import MODE_LABELS, ModalDialog
from .tabs import TABS
from .view import DATE_STYLES, SIZE_STYLE, glyph_legend, legend_text


class FilterScreen(ModalDialog):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]
    DEFAULT_CSS = """
    FilterScreen { align: center middle; }
    #box { width: 56; height: auto; border: round $accent; background: $surface; padding: 1 2; }
    #box Input { margin-bottom: 1; }
    #btns { height: 3; align-horizontal: right; }
    #btns Button { margin-left: 1; }
    """

    def __init__(self, current: dict) -> None:
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        c = self.current
        with Vertical(id="box"):
            yield Label("FILTER  (empty field = no filter)")
            yield Label("Country code (e.g. GB, ENG, UA):")
            yield Input(c.get("country", ""), id="f-country")
            yield Label("Formed from (e.g. -3000 or 3000 BCE):")
            yield Input(c.get("lo_raw", ""), id="f-lo")
            yield Label("Formed to (e.g. 500 or 500 CE):")
            yield Input(c.get("hi_raw", ""), id="f-hi")
            yield Label("Min descendants (e.g. 20):")
            yield Input(c.get("mind_raw", ""), id="f-mind")
            yield Label("Min height — levels below (e.g. 4):")
            yield Input(c.get("minh_raw", ""), id="f-minh")
            with Horizontal(id="btns"):
                yield Button("Reset", id="reset")
                yield Button("Apply", id="apply", variant="primary")

    @on(Button.Pressed, "#reset")
    def _reset(self) -> None:
        self.dismiss({})

    @on(Button.Pressed, "#apply")
    @on(Input.Submitted)
    def _apply(self) -> None:
        country = self.query_one("#f-country", Input).value.strip().upper()
        lo_raw = self.query_one("#f-lo", Input).value.strip()
        hi_raw = self.query_one("#f-hi", Input).value.strip()
        mind_raw = self.query_one("#f-mind", Input).value.strip()
        minh_raw = self.query_one("#f-minh", Input).value.strip()
        try:
            lo, hi = parse_year(lo_raw), parse_year(hi_raw)
            mind, minh = parse_count(mind_raw), parse_count(minh_raw)
        except ValueError as exc:
            self.notify(f"Bad value: {exc}", severity="error")
            return
        self.dismiss({"country": country, "lo": lo, "hi": hi, "lo_raw": lo_raw, "hi_raw": hi_raw,
                      "mind": mind, "minh": minh, "mind_raw": mind_raw, "minh_raw": minh_raw})


class HelpScreen(ModalDialog):
    BINDINGS = [Binding("escape,question_mark,q", "dismiss", "Close")]
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    #help-box { width: 76; height: auto; max-height: 90%; border: round $accent;
                background: $surface; padding: 1 2; }
    """

    def compose(self) -> ComposeResult:
        t = Text()
        rows = [("NAVIGATION", None),
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
                ("", "click section tabs, mode bar F1–F9 and the hint line; wheel scrolls"),
                ("", "click outside a dialog to close it"),
                ("RICH BRANCHES", None),
                ("S", "sort children: oldest first → richest first → deepest first"),
                ("B", "jump to the next rich branch (rich node without rich children)"),
                ("O", "Our Y tip; tied/near-tie cycle · ? uncertain · ○ pruned"),
                ("A", "Ask GPT: compact prompt for this tab → F6 Chat"),
                ("FIND", None),
                ("/", "search haplogroup name, SNP or alias (list of matches)"),
                ("N", "next match"),
                ("F", "filter: country, dates, min descendants, min height"),
                ("INSPECTOR", None)]
        # Клавиши как в TreeWorkspace.SECTION_LABELS: 1–9, 0, -, =
        _tab_keys = "1234567890-="
        for i, tab in enumerate(TABS):
            k = _tab_keys[i] if i < len(_tab_keys) else "·"
            rows.append((k, tab.title))
        rows += [("G / W", "Geography: regions · world DB; Migration Map: W = fit/world"),
                 ("M", "fullscreen Migration Map (map + Contact log / routes)"),
                 ("X", "Raw JSON: show all lines / collapse"),
                 ("Y", "copy haplogroup name"),
                 ("OTHER", None),
                 ("F1–F9", "switch mode: " + " · ".join(title for _, title in MODE_LABELS)),
                 ("I", "data issues found while loading"),
                 ("?  /  Q", "this help  /  quit")]
        for k, v in rows:
            if v is None:
                t.append(f"\n{k}\n", style="bold #FBF6F0")
            else:
                t.append(f"  {k:<26}", style="#D4AD72")
                t.append(f"{v}\n")
        t.append("\n")
        t.append_text(glyph_legend())
        t.append("\n")
        t.append_text(legend_text())
        with VerticalScroll(id="help-box"):
            yield Static(t)


class IssuesScreen(ModalDialog):
    BINDINGS = [Binding("escape,i,q", "dismiss", "Close")]
    DEFAULT_CSS = """
    IssuesScreen { align: center middle; }
    #issues-box { width: 84; height: 80%; border: round $accent; background: $surface; padding: 1 2; }
    """

    def __init__(self, issues: dict) -> None:
        super().__init__()
        self.issues = issues

    def compose(self) -> ComposeResult:
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
        for key, items in self.issues.items():
            if not items:
                continue
            any_row = True
            title = titles.get(key, key)
            t.append(f"\n{title} · {len(items)}\n", style="bold #D4AD72")
            for x in items[:300]:
                t.append(f"  {fmt(x)}\n")
            if len(items) > 300:
                t.append(f"  … +{len(items) - 300} more\n", style="#8A8294")
        if not any_row:
            t.append("\n  (none)\n", style="#AEA5B8")
        with VerticalScroll(id="issues-box"):
            yield Static(t)


class SearchResults(ModalDialog):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]
    DEFAULT_CSS = """
    SearchResults { align: center middle; }
    #res-box { width: 84; height: 70%; border: round $accent; background: $surface; padding: 1 2; }
    #res-box OptionList { height: 1fr; }
    """

    def __init__(self, hits: list[str], nodes: dict, query: str) -> None:
        super().__init__()
        self.hits, self.nodes, self.query_text = hits, nodes, query

    def compose(self) -> ComposeResult:
        opts = []
        for name in self.hits[:300]:
            node = self.nodes[name]
            t = Text(no_wrap=True, overflow="ellipsis")
            t.append(name.ljust(18), style="bold #D4AD72")
            if node.get("placeholder"):
                label = "[haplotree]" if node.get("haplotree") else "[missing resource]"
                t.append(label, style="#8A8294")
            else:
                t.append(str(node.get("date") or "").ljust(14),
                         style=DATE_STYLES[date_class(node)])
                t.append(f"+{node.get('descendants_count', 0):,}".ljust(9),
                         style=SIZE_STYLE[node.get("rich", "normal")])
                t.append(" · ".join(node.get("flags") or []), style="#AEA5B8")
            opts.append(Option(t))
        if len(self.hits) > 300:
            opts.append(Option(Text(f"… +{len(self.hits) - 300} more — refine the search",
                                    style="#8A8294"), disabled=True))
        with Vertical(id="res-box"):
            yield Label(f'{len(self.hits):,} matches for "{self.query_text}"   (Enter: go · Esc: cancel)')
            yield OptionList(*opts)

    @on(OptionList.OptionSelected)
    def _pick(self, event: OptionList.OptionSelected) -> None:
        if event.option_index < len(self.hits):
            self.dismiss(self.hits[event.option_index])
