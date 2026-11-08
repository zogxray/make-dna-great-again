"""Средняя панель: хромосомы (свёрнуты, со сводкой в строке), под раскрытой — подробности и SNP."""

from __future__ import annotations

from rich.segment import Segment
from rich.text import Text
from textual.binding import Binding
from textual.geometry import Size
from textual.message import Message
from textual.scroll_view import ScrollView
from textual.strip import Strip

from ...core.genome import Chrom, Genome, gt_class
from ...core.ylink import DER, Mark, YLink
from ...theme.styles import (BRIGHT, BRIGHT_BOLD, DIM, FAINT, GHOST, HI, INK, MAP, MUTED,
                             OK, SHADOW, SOFT, WARN, bold)
from ..base import alt_keys, fit
from .summary import CLASS_STYLE, STATE_LABEL, STATE_STYLE, chrom_detail, pct, span

HEAD, INFO, SNP = 0, 1, 2
NAME_W, COUNT_W, SPAN_W = 6, 10, 27
ALLELE_W, NODE_W = 7, 20


class ChromView(ScrollView, can_focus=True):
    BINDINGS = [
        Binding("up", "cursor(-1)", "Up", show=False),
        Binding("down", "cursor(1)", "Down", show=False),
        Binding("pageup", "page(-1)", "PgUp", show=False),
        Binding("pagedown", "page(1)", "PgDn", show=False),
        Binding("home", "goto(0)", "Top", show=False),
        Binding("end", "goto(1)", "Bottom", show=False),
        Binding("right", "expand", "Expand", show=False),
        Binding("left", "collapse", "Collapse", show=False),
        Binding("space", "toggle", "Toggle", show=False),
        Binding("enter", "toggle", "Toggle", show=False),
        Binding("escape", "clear_filter", "Clear filter", show=False),
        Binding(alt_keys("z"), "collapse_all", "Collapse all", show=False),
    ]

    class Moved(Message):
        pass

    class FilterChanged(Message):
        """Фильтр сменился — workspace обновляет subtitle."""

        def __init__(self, query: str, matches: int) -> None:
            super().__init__()
            self.query = query
            self.matches = matches

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.genome: Genome | None = None
        self.rows: list[tuple[int, str, int]] = []
        self.cursor = 0
        self.expanded: set[str] = set()
        self.details: dict[str, list[Text]] = {}
        self.link: YLink | None = None
        self.filter = ""
        self.filter_matches = 0

    # ── данные ──

    def set_genome(self, genome: Genome | None) -> None:
        self.genome = genome
        self.link = None
        self.expanded.clear()
        self.details.clear()
        self.filter = ""
        self.filter_matches = 0
        self.cursor = 0
        self.rebuild()
        self.scroll_to(y=0, animate=False)

    def set_filter(self, query: str) -> None:
        q = (query or "").strip()
        if q == self.filter:
            return
        self.filter = q
        self.cursor = 0
        self.rebuild()
        self.scroll_to(y=0, animate=False)

    def action_clear_filter(self) -> None:
        if self.filter:
            self.set_filter("")
            self.post_message(self.FilterChanged("", 0))

    def set_link(self, link: YLink | None) -> None:
        cur = self.current
        self.link = link
        self.details.pop("Y", None)
        if cur and cur[1] == "Y" and cur[0] != HEAD:
            kind, _, i = cur
            self.rebuild()
            self.cursor = self.row_index("Y", kind, i)
            self.refresh()
        else:
            self.rebuild()

    def mark(self, chrom: str, i: int) -> Mark | None:
        return self.link.marks.get(i) if self.link and chrom == "Y" else None

    def row_index(self, chrom: str, kind: int, i: int) -> int:
        for idx, (k, name, j) in enumerate(self.rows):
            if name == chrom and k == kind and j == i:
                return idx
        return min(self.cursor, max(0, len(self.rows) - 1))

    def goto_snp(self, chrom: str, i: int) -> None:
        if not self.genome or chrom not in self.genome.chroms:
            return
        if chrom not in self.expanded:
            self.expanded.add(chrom)
            self.rebuild()
        self.move(self.row_index(chrom, SNP, i))
        h = self.size.height
        self.scroll_to(y=max(0, self.cursor - h // 3), animate=False)

    def next_derived(self) -> None:
        if not self.link or not self.link.derived:
            self.app.notify("No derived Y markers" if self.link else "Y markers are not linked to the haplotree")
            return
        cur = self.current
        at = cur[2] if cur and cur[1] == "Y" and cur[0] == SNP else -1
        ders = self.link.derived
        nxt = next((i for i in ders if i > at), ders[0])
        self.goto_snp("Y", nxt)
        self.app.notify(f"Derived {ders.index(nxt) + 1}/{len(ders)}", timeout=2)

    def _snp_hits(self, c: Chrom, q: str) -> list[int]:
        """Индексы SNP, где rsID / позиция / генотип содержат q (casefold)."""
        if not q:
            return list(range(c.n))
        out: list[int] = []
        for i, rid in enumerate(c.ids):
            if (q in rid.casefold()
                    or q in str(c.pos[i])
                    or q in (c.gts[i] or "").casefold()):
                out.append(i)
        return out

    def rebuild(self, keep: str | None = None) -> None:
        rows: list[tuple[int, str, int]] = []
        target = 0
        matches = 0
        q = self.filter.casefold()
        if self.genome:
            for name, c in self.genome.chroms.items():
                chrom_hit = bool(q) and q in name.casefold()
                snps = self._snp_hits(c, q) if q else list(range(c.n))
                if q and not chrom_hit and not snps:
                    continue
                if chrom_hit and not snps:
                    snps = list(range(c.n))
                matches += len(snps) if q else 0
                if name == keep:
                    target = len(rows)
                rows.append((HEAD, name, 0))
                open_ = name in self.expanded or bool(q)
                if open_:
                    if name not in self.details:
                        b = self.genome.chrom_build(name)
                        votes = self.genome.chrom_votes.get(name)
                        self.details[name] = chrom_detail(
                            c, b, self.link, votes=votes)
                    rows.extend((INFO, name, i) for i in range(len(self.details[name])))
                    rows.extend((SNP, name, i) for i in snps)
        self.filter_matches = matches
        self.rows = rows
        self.cursor = target if keep else min(self.cursor, max(0, len(rows) - 1))
        self.virtual_size = Size(max(self.size.width, 1), len(rows))
        self.refresh()
        self.post_message(self.Moved())
        self.post_message(self.FilterChanged(self.filter, matches))

    @property
    def current(self) -> tuple[int, str, int] | None:
        return self.rows[self.cursor] if self.rows else None

    def chrom(self, name: str) -> Chrom:
        assert self.genome
        return self.genome.chroms[name]

    # ── отрисовка ──

    @staticmethod
    def header() -> Text:
        t = Text(no_wrap=True, style="bold #AEA5B8")
        t.append("  " + "CHR".ljust(NAME_W - 2))
        t.append("SNPs".rjust(COUNT_W))
        t.append(" BLD")
        t.append("  " + "POSITION RANGE".ljust(SPAN_W))
        t.append("  HET    NO-CALL  NAMED")
        return t

    def row_text(self, idx: int) -> Text:
        kind, name, i = self.rows[idx]
        c = self.chrom(name)
        t = Text(no_wrap=True, overflow="ellipsis")
        if kind == HEAD:
            assert self.genome
            b = self.genome.chrom_build(name)
            maj = self.genome.build_detected or self.genome.build
            t.append("▾ " if name in self.expanded else "▸ ", style=HI)
            t.append(name.ljust(NAME_W - 2), style=BRIGHT_BOLD)
            t.append(f"{c.n:,}".rjust(COUNT_W), style="bold")
            tag = b if b in ("37", "38") else ("?" if b == "uncertain" else "—")
            t.append(f" {tag:>3}", style=WARN if b and maj and b != maj else MUTED)
            t.append("  " + span(c).ljust(SPAN_W), style=SOFT)
            dip = c.classes["het"] + c.classes["hom"]
            t.append(f"  {pct(c.classes['het'], dip):>6}" if dip else "       —", style=WARN)
            t.append(f"  {pct(c.nocall, c.n, 2):>8}", style=MUTED)
            t.append(f"  {pct(c.named, c.n, 0):>5}", style=MUTED)
        elif kind == INFO:
            t.append("  │ ", style=GHOST)
            t.append_text(self.details[name][i])
        else:
            gt = c.gts[i]
            t.append("  │ ", style=GHOST)
            rid = c.ids[i]
            t.append(f"{rid:<16}", style=BRIGHT if not rid.startswith("rs") else "")
            t.append(f"{c.pos[i]:>13,}", style=MUTED)
            t.append("   ")
            t.append(f"{gt or '∅':<4}", style=CLASS_STYLE[gt_class(gt)])
            m = self.mark(name, i)
            if m:
                t.append(f" {STATE_LABEL[m.state]:<3} ", style=STATE_STYLE[m.state])
                t.append(fit(f"{m.anc}→{m.der}", ALLELE_W).ljust(ALLELE_W), style=MUTED)
                t.append(" " + fit(m.node, NODE_W).ljust(NODE_W),
                         style=OK if m.state == DER else "")
                t.append(" ●" if m.downloaded else " ○", style=OK if m.downloaded else DIM)
            elif self.link and name == "Y" and not rid.startswith("rs"):
                t.append(" " + "not in haplotree".ljust(5 + ALLELE_W + NODE_W + 3), style=FAINT)
            shown = f"{i + 1:,}/{c.n:,}"
            if self.filter:
                # i — индекс в хромосоме; порядковый в фильтре не считаем (дешево).
                shown = f"@{c.pos[i]:,}"
            t.append(f"  {shown}", style=GHOST)
        return t

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        idx = y + int(self.scroll_offset.y)
        if idx >= len(self.rows) or width <= 0:
            return Strip.blank(width, self.rich_style)
        t = self.row_text(idx)
        t.truncate(width, pad=True)
        if idx == self.cursor:
            t.stylize(MAP["select_on"] if self.has_focus else MAP["select_dim"])
        segments = Segment.apply_style(t.render(self.app.console), self.rich_style)
        return Strip(list(segments)).adjust_cell_length(width)

    def on_resize(self) -> None:
        self.virtual_size = Size(max(self.size.width, 1), len(self.rows))

    def watch_has_focus(self, value: bool) -> None:
        self.refresh()

    # ── навигация ──

    def ensure_visible(self) -> None:
        h, top = self.size.height, int(self.scroll_offset.y)
        if self.cursor < top:
            self.scroll_to(y=self.cursor, animate=False)
        elif self.cursor >= top + h:
            self.scroll_to(y=self.cursor - h + 1, animate=False)

    def move(self, to: int) -> None:
        if not self.rows:
            return
        to = max(0, min(len(self.rows) - 1, to))
        if to != self.cursor:
            self.cursor = to
            self.ensure_visible()
            self.refresh()
            self.post_message(self.Moved())

    def action_cursor(self, d: int) -> None:
        self.move(self.cursor + d)

    def action_page(self, d: int) -> None:
        self.move(self.cursor + d * max(1, self.size.height - 1))

    def action_goto(self, end: int) -> None:
        self.move(len(self.rows) - 1 if end else 0)

    def _set(self, name: str, expand: bool) -> None:
        if (name in self.expanded) == expand:
            return
        (self.expanded.add if expand else self.expanded.discard)(name)
        self.rebuild(keep=name)
        self.ensure_visible()

    def action_toggle(self) -> None:
        cur = self.current
        if cur:
            self._set(cur[1], cur[1] not in self.expanded)

    def action_expand(self) -> None:
        cur = self.current
        if not cur:
            return
        if cur[0] == HEAD and cur[1] not in self.expanded:
            self._set(cur[1], True)
        else:
            self.action_cursor(1)

    def action_collapse(self) -> None:
        cur = self.current
        if not cur:
            return
        if cur[0] == HEAD:
            self._set(cur[1], False)
        else:
            self.move(next(i for i in range(self.cursor, -1, -1) if self.rows[i][0] == HEAD))

    def action_collapse_all(self) -> None:
        cur = self.current
        self.expanded.clear()
        self.rebuild(keep=cur[1] if cur else None)
        self.ensure_visible()

    def on_click(self, event) -> None:
        idx = int(self.scroll_offset.y) + event.y
        if not (0 <= idx < len(self.rows)):
            return
        self.focus()
        self.move(idx)
        if self.rows[idx][0] == HEAD and event.chain == 1:
            self.action_toggle()
