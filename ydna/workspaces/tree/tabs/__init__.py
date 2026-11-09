"""Вкладки инспектора.

Новая вкладка — это функция `render(ctx, doc, node)` с декоратором `@tab("Название")`
в любом модуле этого пакета (модуль нужно импортировать внизу файла).
Порядок регистрации = порядок вкладок; клавиши 1–9,0,-,= строятся из реестра.
Если вкладке нужны свои клавиши: `@tab("Название", keys={"x": handler})`, handler(ctx).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from rich.text import Text

from ...base import click
from ..data import clean


@dataclass
class Tab:
    title: str
    render: Callable[..., None]
    keys: dict[str, Callable[[Any], None]] = field(default_factory=dict)


TABS: list[Tab] = []


def tab(title: str, keys: dict[str, Callable[[Any], None]] | None = None):
    def deco(fn):
        TABS.append(Tab(title, fn, keys or {}))
        return fn
    return deco


class Doc:
    """Rich Text инспектора · Magazine: секция → воздух → тело.

    title() даёт ровно одну пустую строку перед секцией (не в начале документа).
    Карточки сами оставляют один воздух снизу — title это учитывает.
    """

    def __init__(self) -> None:
        self.t = Text()

    def _ensure_section_gap(self) -> None:
        """Ровно одна пустая строка перед новым блоком (idempotent)."""
        plain = self.t.plain
        if not plain:
            return
        if plain.endswith("\n\n"):
            return
        if plain.endswith("\n"):
            self.t.append("\n")
        else:
            self.t.append("\n\n")

    def title(self, s: str) -> None:
        self._ensure_section_gap()
        self.t.append(f"{s}\n", style="bold #FBF6F0")

    def head(self, s: str, style: str = "bold #D4AD72") -> None:
        self._ensure_section_gap()
        self.t.append(s + "\n", style=style)
        self.t.append("━" * min(len(s) + 8, 60) + "\n", style="#5A5361")

    def line(self, s: str, style: str = "", indent: int = 1) -> None:
        self.t.append("  " * indent + s + "\n", style=style)

    def link(self, name: str, style: str = "") -> None:
        """Имя гаплогруппы, по клику на которое дерево переходит к узлу."""
        self.t.append(name, style=click(f"screen.goto({name!r})", style))

    def kv(self, key: str, value: Any, indent: int = 1) -> None:
        value = clean(value)
        self.t.append("  " * indent + f"{key}: ", style="#AEA5B8")
        self.t.append(f"{value}\n")

    def sub(self, s: str, indent: int = 1) -> None:
        """Подзаголовок внутри секции (без лишнего воздуха и без рамки)."""
        self.t.append("  " * indent + s + "\n", style="bold #D4AD72")


def _scalar(v: Any) -> bool:
    return not isinstance(v, (dict, list))


def dump(doc: Doc, value: Any, indent: int = 1, skip: tuple[str, ...] = ()) -> None:
    """Выводит всё содержимое JSON-структуры без потерь (HTML в строках очищается)."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k in skip or v in (None, "", [], {}):
                continue
            if _scalar(v):
                doc.kv(str(k), v, indent)
            elif isinstance(v, list) and all(_scalar(x) for x in v):
                doc.kv(str(k), ", ".join(str(clean(x)) for x in v), indent)
            else:
                doc.line(f"{k}:", "#AEA5B8", indent)
                dump(doc, v, indent + 1)
    elif isinstance(value, list):
        for item in value:
            if _scalar(item):
                doc.line(f"• {clean(item)}", "", indent)
            else:
                doc.line("•", "#8A8294", indent)
                dump(doc, item, indent + 1)
    elif value not in (None, ""):
        doc.line(str(clean(value)), "", indent)



class Ctx:
    """Общий контекст вкладок: данные дерева и блоки, которые нужны нескольким вкладкам."""

    def __init__(self, nodes: dict, children: dict, folder: Path, stats: dict,
                 store=None) -> None:
        self.nodes = nodes
        self.children = children
        self.folder = folder
        self.stats = stats
        self.store = store  # тот же Store, что у TreeWorkspace (без нового connect)
        self.ours: set[str] = set()
        self.tip: str | None = None
        self.raw_full = False
        # Активный fetch Discover: {title, stage, done, total, fraction} или None.
        self.fetch: dict[str, Any] | None = None
        # YFull / TheYtree overlay: None = ещё не грузили, False = нет данных.
        self._overlays: Any = None

    def overlays(self):
        """Ленивый индекс YFull+TheYtree (ages / SNP / crosswalk)."""
        if self._overlays is False:
            return None
        if self._overlays is not None:
            return self._overlays
        try:
            from ....core.overlays import load_overlays
            idx = load_overlays(self.store)
        except Exception:  # noqa: BLE001
            idx = None
        self._overlays = idx if idx is not None else False
        return idx

    def drop_overlays(self) -> None:
        self._overlays = None

    def lineage(self, name: str) -> list[str]:
        chain, seen = [], set()
        cur: str | None = name
        while cur and cur in self.nodes and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = self.nodes[cur].get("parent")
        return chain[::-1]


    def render(self, name: str, section: int) -> Text:
        node = self.nodes[name]
        doc = Doc()
        doc.head(name)
        if node.get("placeholder"):
            self.skeleton_overview(doc, name, node)
            return doc.t
        TABS[section].render(self, doc, node)
        return doc.t

    def _discover_json(self, name: str) -> Path:
        from ....core.ylink import safe_stem
        return self.folder / f"{safe_stem(name)}.json"

    def missing_discover(self, name: str) -> list[str]:
        """Узел и потомки: haplotree-скелет БЕЗ файла Discover на диске (нужен сетевой fetch)."""
        out: list[str] = []
        stack = [name]
        seen: set[str] = set()
        while stack:
            n = stack.pop()
            if n in seen or n not in self.nodes:
                continue
            seen.add(n)
            node = self.nodes[n]
            if (node.get("placeholder") and node.get("haplotree")
                    and not self._discover_json(n).is_file()):
                out.append(n)
            for k in reversed(self.children.get(n, [])):
                stack.append(k)
        return out

    def local_discover(self, name: str) -> list[str]:
        """Скелеты, у которых JSON уже на диске, но ещё не влит в дерево."""
        out: list[str] = []
        stack = [name]
        seen: set[str] = set()
        while stack:
            n = stack.pop()
            if n in seen or n not in self.nodes:
                continue
            seen.add(n)
            node = self.nodes[n]
            if (node.get("placeholder") and node.get("haplotree")
                    and self._discover_json(n).is_file()):
                out.append(n)
            for k in reversed(self.children.get(n, [])):
                stack.append(k)
        return out

    def discover_fetch_block(self, doc: Doc, name: str) -> None:
        """Кнопки докачки только если есть работа; иначе статус. Во время fetch — прогресс."""
        from ....core.ylink import fetch_cost

        node = self.nodes.get(name) or {}
        missing = self.missing_discover(name)
        local = self.local_discover(name)
        doc.title("DISCOVER")
        fetch = self.fetch
        if fetch:
            title = str(fetch.get("title") or "Fetching Discover")
            stage = str(fetch.get("stage") or "…")
            done = int(fetch.get("done") or 0)
            total = fetch.get("total")
            frac = fetch.get("fraction")
            doc.line(f"IN PROGRESS · {title}", "bold #D4AD72", 1)
            doc.line(stage, "#C3BCCC", 1)
            if isinstance(total, int) and total > 0:
                width = 28
                filled = int((frac or 0) * width) if frac is not None else int(done / total * width)
                filled = max(0, min(width, filled))
                bar = "█" * filled + "░" * (width - filled)
                pct = f"{(frac if frac is not None else done / total):.0%}"
                doc.t.append("  ")
                doc.t.append(bar, style="#D4AD72")
                doc.t.append(f"  {pct}  {done:,}/{total:,}\n", style="#AEA5B8")
            doc.t.append("  ")
            doc.t.append(" Cancel ", style=click("screen.cancel_fetch", "bold reverse #B04A4A"))
            doc.t.append("  ")
            doc.t.append("C", style=click("screen.cancel_fetch", "bold #B04A4A"))
            doc.t.append("\n")
            doc.line("Fetch buttons are disabled until this job finishes.", "#8A8294", 1)
            return
        if node.get("placeholder") and not node.get("haplotree"):
            doc.line("Cannot fetch: name is not a haplotree node.", "#8A8294", 1)
            return
        if not self.folder.is_dir():
            doc.line("Discover folder is not set — open F2 → P.", "#8A8294", 1)
            return

        shown = False
        if name in local:
            doc.t.append("  ")
            doc.t.append(
                " Apply local JSON ",
                style=click("screen.apply_local", "bold reverse #53A94B"),
            )
            doc.t.append("\n")
            doc.line("File is already on disk; load it into this card.", "#8A8294", 1)
            shown = True
        if name in missing:
            doc.t.append("  ")
            doc.t.append(
                " Fetch this node ",
                style=click("screen.fetch_node", "bold reverse #D4AD72"),
            )
            doc.t.append("  ")
            doc.t.append("D", style=click("screen.fetch_node", "bold #D4AD72"))
            doc.t.append("\n")
            doc.line("Downloads dates, countries, ancient DNA and the rest of the card.", "#8A8294", 1)
            shown = True
        # Ветка: есть что качать, и это не единственная кнопка «Fetch this node».
        only_self = node.get("placeholder") and missing == [name]
        if missing and not only_self:
            n = len(missing)
            label = f" Fetch branch ↓ · {n:,} missing · {fetch_cost(n)} "
            doc.t.append("  ")
            doc.t.append(label, style=click("screen.fetch_branch", "bold reverse #53A94B"))
            doc.t.append("  ")
            doc.t.append("Shift+D", style=click("screen.fetch_branch", "bold #53A94B"))
            doc.t.append("\n")
            doc.line("This haplogroup and all descendants still without Discover JSON.",
                     "#8A8294", 1)
            shown = True
        if not shown:
            if node.get("placeholder"):
                doc.line("Nothing to download for this node.", "#8A8294", 1)
            else:
                doc.line("Branch complete — every descendant has Discover JSON.", "#8A8294", 1)

    def skeleton_overview(self, doc: Doc, name: str, node: dict) -> None:
        """Только то, что есть без Discover: топология + докачка."""
        if node.get("haplotree"):
            doc.line("FROM HAPLOTREE", "bold #53A94B")
            doc.line("Topology from the public FTDNA Y tree. Discover card is not downloaded yet.",
                     "#AEA5B8")
        else:
            doc.line("MISSING RESOURCE", "bold #C3BCCC")
            doc.line("Mentioned only as a parent; not in the current haplotree index.", "#AEA5B8")

        parent = node.get("parent")
        pnode = self.nodes.get(parent) if parent else None
        if parent and pnode and pnode.get("placeholder"):
            psuffix = "  [haplotree]" if pnode.get("haplotree") else "  [missing]"
        else:
            psuffix = ""
        doc.title("HAPLOGROUP")
        doc.kv("Name", name)
        if parent:
            doc.t.append("  Parent: ", style="#AEA5B8")
            if parent in self.nodes:
                doc.link(parent, "#D4AD72")
                doc.t.append(psuffix + "\n", style="#8A8294")
            else:
                doc.t.append(f"{parent}{psuffix}\n")
        else:
            doc.kv("Parent", "— (root)")
        if node.get("root") is not None:
            doc.kv("Root", node["root"])

        self.branch_block(doc, node)

        markers = node.get("markers") or []
        if markers:
            doc.title("SEARCH MARKERS")
            doc.line(", ".join(markers), "", 1)

        self.children_block(doc, name)
        self.lineage_block(doc, name)
        self.discover_fetch_block(doc, name)

    # — разделение «в ветке» / «от предков» —
    def above_set(self, node: dict) -> set[str]:
        names = set(self.lineage(node["name"])[:-1])
        names |= {a.get("name") for a in node["ancestors"]
                  if isinstance(a, dict) and a.get("name") and a.get("name") != node["name"]}
        return names

    def split_mrca(self, items: list, node: dict) -> tuple[list, list]:
        above, here, up = self.above_set(node), [], []
        for it in items:
            m = it.get("haplogroup_mrca") if isinstance(it, dict) else None
            (up if m in above else here).append(it)
        return here, up

    # — общие блоки —
    def class_label(self, node: dict) -> str:
        s, c = self.stats, node.get("rich", "normal")

        def thr(key: str) -> str:
            v = s.get(key) if isinstance(s, dict) else None
            if isinstance(v, (int, float)):
                return f"{int(v):,}"
            return "?"

        return {
            "hub": f"HUB · ≥ {thr('hub_thr')} descendants (top 1%)",
            "rich": f"RICH · ≥ {thr('rich_thr')} descendants (top 5%)",
            "normal": "normal",
            "dead": "dead end · ≤ 1 descendant",
        }.get(c, str(c or "normal"))

    def branch_block(self, doc: Doc, node: dict) -> None:
        doc.title("BRANCH SIZE")
        doc.kv("Class", self.class_label(node))
        doc.kv("Children", node.get("children_count", 0))
        doc.kv("Descendants", f"{node.get('descendants_count', 0):,}")
        rank = node.get("rank")
        doc.kv("Rank by descendants",
               f"#{rank:,} of {self.stats.get('branching', 0):,} branches" if rank else "— (leaf)")
        deep = "  · deep (top 5%)" if node.get("deep") else ""
        doc.kv("Height", f"{node.get('height', 0)} level(s) below{deep}")
        if node.get("crown"):
            doc.kv("Crown", "yes — rich node without rich children (B jumps here)")

    def children_block(self, doc: Doc, name: str) -> None:
        kids = self.children.get(name, [])
        declared = [c for c in self.nodes[name].get("declared_children", []) if c not in self.nodes]
        disc_kids = sum(1 for k in kids if not self.nodes[k].get("placeholder"))
        title = f"CHILDREN · {len(kids)}"
        if disc_kids and disc_kids != len(kids):
            title += f" · {disc_kids} Discover"
        if declared:
            title += f" · {len(declared)} declared only"
        doc.title(title)
        for k in kids[:60]:
            kn = self.nodes[k]
            doc.t.append("  └─ ")
            doc.link(k, "#D4AD72")
            mark = "  [haplotree]" if kn.get("placeholder") and kn.get("haplotree") else ""
            doc.t.append(f"    {kn['date']}{mark}    +{kn.get('descendants_count', 0):,}\n")
        if len(kids) > 60:
            doc.line(f"+{len(kids) - 60} more", "#8A8294", 1)
        for c in declared[:60]:
            doc.line(f"└─ {c}    [declared only]", "#8A8294", 1)
        if len(declared) > 60:
            doc.line(f"+{len(declared) - 60} more", "#8A8294", 1)

    def lineage_block(self, doc: Doc, name: str) -> None:
        doc.title("LINEAGE")
        for i, n in enumerate(self.lineage(name)):
            node = self.nodes[n]
            if node.get("placeholder") and node.get("haplotree"):
                mark = "  [haplotree]"
            elif node.get("placeholder"):
                mark = "  [missing resource]"
            else:
                mark = ""
            if n == name:
                style = "bold #53A94B" if n in self.ours else "bold #D4AD72"
            elif n in self.ours:
                style = "#53A94B"
            else:
                style = "#D4AD72"
            doc.t.append("  " + ("   " * (i - 1) + "└─ " if i else ""))
            doc.link(n, style)
            doc.t.append(mark + "\n", style="#8A8294")


from . import node as _node_tabs  # noqa: E402,F401  (регистрирует вкладки узла)

# Порядок для пользователя: кто узел → путь вверх → мутации → где люди/древние
# → матчи → маршруты → лор → сырьё. Блоки внутри вкладок не трогаем.
_TAB_ORDER = (
    "Overview",
    "Ancestors",
    "SNPs",
    "Geography",
    "Ancient DNA",
    "Matches",
    "Migration Map",
    "Famous",
    "Projects",
    "Raw JSON",
)
_by_title = {t.title: t for t in TABS}
TABS[:] = [_by_title[name] for name in _TAB_ORDER if name in _by_title]
# на случай новых вкладок, ещё не перечисленных в _TAB_ORDER
TABS.extend(t for t in _by_title.values() if t not in TABS)
