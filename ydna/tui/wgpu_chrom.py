"""Chromosome browser for textual-wgpu — same data logic as Textual ChromView.

Paint path: ``ScrollView.render_lines`` → Content→Strip compositor.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from rich.text import Text

from textual_wgpu.fonts import cell_height
from textual_wgpu.geometry import Rect
from textual_wgpu.strip import Segment, Strip
from textual_wgpu.widgets.scroll_view import ScrollView

from ..core.genome import Chrom, Genome, gt_class
from ..core.ylink import DER, Mark, YLink
from ..workspaces.genome.summary import CLASS_STYLE, STATE_LABEL, STATE_STYLE, chrom_detail, pct, span
from ..workspaces.genome.view import (
    ALLELE_W,
    COUNT_W,
    HEAD,
    INFO,
    NAME_W,
    NODE_W,
    SNP,
    SPAN_W,
)
from ..workspaces.base import fit
from .wgpu_jobs import _text_lines

if TYPE_CHECKING:
    from PIL import ImageDraw

# Theme hexes matching Textual styles used in row_text
_HI = (0x72, 0x9F, 0xCF, 255)
_BRIGHT = (0xFB, 0xF6, 0xF0, 255)
_MUTED = (0xAE, 0xA5, 0xB8, 255)
_SOFT = (0xC3, 0xBC, 0xCC, 255)
_WARN = (0x72, 0x9F, 0xCF, 255)
_GHOST = (0x71, 0x69, 0x7A, 255)
_OK = (0x53, 0xA9, 0x4B, 255)
_DIM = (0x8A, 0x82, 0x94, 255)
_FAINT = (0x71, 0x69, 0x7A, 255)
# Textual MAP select_on / select_dim (row bg — not accent cream).
_SELECT_ON = (0x45, 0x3B, 0x52, 255)
_SELECT_DIM = (0x35, 0x2D, 0x3D, 255)


def _style_rgba(style: str) -> tuple[int, int, int, int]:
    """Best-effort Rich style string → rgba (hex tokens)."""
    if not style:
        return _SOFT
    for part in style.replace(",", " ").split():
        if part.startswith("#") and len(part) >= 7:
            h = part[1:7]
            try:
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
            except ValueError:
                pass
    return _SOFT


class WgpuChromView(ScrollView):
    """Virtualized chrom/SNP list with fold/expand (ScrollView + Strip)."""

    FONT_SIZE = 12
    LINE_H = cell_height(FONT_SIZE)
    RENDER_FONT_SIZE = FONT_SIZE

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.genome: Genome | None = None
        self.rows: list[tuple[int, str, int]] = []
        self.cursor = 0
        self.expanded: set[str] = set()
        self.details: dict[str, list[Text]] = {}
        self.link: YLink | None = None
        self.filter = ""
        self.filter_matches = 0
        self.on_moved: Callable[[], None] | None = None
        self.on_filter_changed: Callable[[str, int], None] | None = None
        self._focused = False
        self.layout_hint_h: int | None = None

    def set_genome(self, genome: Genome | None) -> None:
        self.genome = genome
        self.link = None
        self.expanded.clear()
        self.details.clear()
        self.filter = ""
        self.filter_matches = 0
        self.cursor = 0
        self.scroll_y = 0
        self.rebuild()

    def set_filter(self, query: str) -> None:
        q = (query or "").strip()
        if q == self.filter:
            return
        self.filter = q
        self.cursor = 0
        self.scroll_y = 0
        self.rebuild()

    def set_link(self, link: YLink | None) -> None:
        cur = self.current
        self.link = link
        self.details.pop("Y", None)
        if cur and cur[1] == "Y" and cur[0] != HEAD:
            kind, _, i = cur
            self.rebuild()
            self.cursor = self.row_index("Y", kind, i)
        else:
            self.rebuild()

    def mark(self, chrom: str, i: int) -> Mark | None:
        return self.link.marks.get(i) if self.link and chrom == "Y" else None

    def row_index(self, chrom: str, kind: int, i: int) -> int:
        for idx, (k, name, j) in enumerate(self.rows):
            if name == chrom and k == kind and j == i:
                return idx
        return min(self.cursor, max(0, len(self.rows) - 1))

    def _ensure_calls(self, name: str) -> Chrom:
        assert self.genome
        c = self.genome.chroms[name]
        if c.calls_loaded:
            return c
        from ..core import genome_db
        return genome_db.ensure_chrom(self.genome, name)

    def goto_snp(self, chrom: str, i: int) -> None:
        if not self.genome or chrom not in self.genome.chroms:
            return
        self._ensure_calls(chrom)
        if chrom not in self.expanded:
            self.expanded.add(chrom)
            self.rebuild()
        self.move(self.row_index(chrom, SNP, i))
        # Center cursor ~1/3 from top (same as Textual ChromView).
        vis = self._visible()
        max_s = self._max_scroll()
        self.scroll_y = max(0, min(max_s, self.cursor - vis // 3))
        self.refresh()

    def next_derived(self) -> None:
        if not self.link or not self.link.derived:
            if self.app is not None and hasattr(self.app, "notify"):
                self.app.notify(
                    "No derived Y markers" if self.link else "Y markers are not linked to the haplotree",
                )
            return
        cur = self.current
        at = cur[2] if cur and cur[1] == "Y" and cur[0] == SNP else -1
        ders = self.link.derived
        nxt = next((i for i in ders if i > at), ders[0])
        self.goto_snp("Y", nxt)
        if self.app is not None and hasattr(self.app, "notify"):
            self.app.notify(f"Derived {ders.index(nxt) + 1}/{len(ders)}")

    def _snp_hits(self, c: Chrom, q: str) -> list[int]:
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
            filter_chroms: set[str] | None = None
            if q:
                from ..core import genome_db
                try:
                    filter_chroms = set(genome_db.chroms_matching_query(self.genome.path, q))
                except Exception:  # noqa: BLE001
                    filter_chroms = None
            for name, c in self.genome.chroms.items():
                chrom_hit = bool(q) and q in name.casefold()
                open_ = name in self.expanded or (bool(q) and (chrom_hit or (
                    filter_chroms is not None and name in filter_chroms)))
                if open_ or (q and filter_chroms is None and not chrom_hit):
                    try:
                        c = self._ensure_calls(name)
                    except Exception:  # noqa: BLE001
                        pass
                if q and not chrom_hit:
                    if filter_chroms is not None and name not in filter_chroms and not c.calls_loaded:
                        continue
                    snps = self._snp_hits(c, q) if c.calls_loaded else []
                    if not snps:
                        continue
                elif q and chrom_hit:
                    snps = self._snp_hits(c, q) if c.calls_loaded else list(range(c.n))
                    if not snps:
                        snps = list(range(c.n))
                else:
                    snps = list(range(c.n)) if open_ and c.calls_loaded else (
                        list(range(c.n)) if open_ else [])
                matches += len(snps) if q else 0
                if name == keep:
                    target = len(rows)
                rows.append((HEAD, name, 0))
                if open_:
                    if name not in self.details:
                        b = self.genome.chrom_build(name)
                        votes = self.genome.chrom_votes.get(name)
                        self.details[name] = chrom_detail(
                            c, b, self.link, votes=votes)
                    rows.extend((INFO, name, i) for i in range(len(self.details[name])))
                    if c.calls_loaded:
                        rows.extend((SNP, name, i) for i in snps)
        self.filter_matches = matches
        self.rows = rows
        self.cursor = target if keep else min(self.cursor, max(0, len(rows) - 1))
        self._ensure_visible()
        if self.on_moved:
            self.on_moved()
        if self.on_filter_changed:
            self.on_filter_changed(self.filter, matches)
        if self.app is not None:
            self.app.request_draw()

    @property
    def current(self) -> tuple[int, str, int] | None:
        return self.rows[self.cursor] if self.rows else None

    def chrom(self, name: str) -> Chrom:
        assert self.genome
        return self.genome.chroms[name]

    @staticmethod
    def header() -> Text:
        t = Text(no_wrap=True, style="bold #AEA5B8")
        t.append("  " + "CHR".ljust(NAME_W - 2))
        t.append("SNPs".rjust(COUNT_W))
        t.append(" BLD")
        t.append("  " + "POSITION RANGE".ljust(SPAN_W))
        t.append("  HET    NO-CALL  NAMED")
        return t

    @staticmethod
    def header_plain() -> str:
        return WgpuChromView.header().plain

    def row_text(self, idx: int) -> Text:
        kind, name, i = self.rows[idx]
        c = self.chrom(name)
        t = Text(no_wrap=True, overflow="ellipsis")
        if kind == HEAD:
            assert self.genome
            b = self.genome.chrom_build(name)
            maj = self.genome.build_detected or self.genome.build
            t.append("▾ " if name in self.expanded else "▸ ", style="#729fcf")
            t.append(name.ljust(NAME_W - 2), style="bold #FBF6F0")
            t.append(f"{c.n:,}".rjust(COUNT_W), style="bold")
            tag = b if b in ("37", "38") else ("?" if b == "uncertain" else "—")
            t.append(f" {tag:>3}", style="#729fcf" if b and maj and b != maj else "#AEA5B8")
            t.append("  " + span(c).ljust(SPAN_W), style="#C3BCCC")
            dip = c.classes["het"] + c.classes["hom"]
            t.append(f"  {pct(c.classes['het'], dip):>6}" if dip else "       —", style="#729fcf")
            t.append(f"  {pct(c.nocall, c.n, 2):>8}", style="#AEA5B8")
            t.append(f"  {pct(c.named, c.n, 0):>5}", style="#AEA5B8")
        elif kind == INFO:
            t.append("  │ ", style="#71697A")
            t.append_text(self.details[name][i])
        else:
            gt = c.gts[i]
            t.append("  │ ", style="#71697A")
            rid = c.ids[i]
            t.append(f"{rid:<16}", style="#FBF6F0" if not rid.startswith("rs") else "")
            t.append(f"{c.pos[i]:>13,}", style="#AEA5B8")
            t.append("   ")
            t.append(f"{gt or '∅':<4}", style=CLASS_STYLE[gt_class(gt)])
            m = self.mark(name, i)
            if m:
                t.append(f" {STATE_LABEL[m.state]:<3} ", style=STATE_STYLE[m.state])
                t.append(fit(f"{m.anc}→{m.der}", ALLELE_W).ljust(ALLELE_W), style="#AEA5B8")
                t.append(" " + fit(m.node, NODE_W).ljust(NODE_W),
                         style="#53A94B" if m.state == DER else "")
                t.append(" ●" if m.downloaded else " ○", style="#53A94B" if m.downloaded else "#8A8294")
            elif self.link and name == "Y" and not rid.startswith("rs"):
                t.append(" " + "not in haplotree".ljust(5 + ALLELE_W + NODE_W + 3), style="#71697A")
            shown = f"{i + 1:,}/{c.n:,}"
            if self.filter:
                shown = f"@{c.pos[i]:,}"
            t.append(f"  {shown}", style="#71697A")
        return t

    def get_content_height(self) -> int:
        return len(self.rows)

    def _visible(self) -> int:
        return self._visible_lines()

    def _ensure_visible(self) -> None:
        self.ensure_visible_line(self.cursor)

    def move(self, to: int) -> None:
        if not self.rows:
            return
        to = max(0, min(len(self.rows) - 1, to))
        if to != self.cursor:
            self.cursor = to
            self._ensure_visible()
            if self.on_moved:
                self.on_moved()
            if self.app is not None:
                self.app.request_draw()

    def _set(self, name: str, expand: bool) -> None:
        if (name in self.expanded) == expand:
            return
        if expand:
            try:
                self._ensure_calls(name)
            except Exception:  # noqa: BLE001
                pass
            self.details.pop(name, None)
        (self.expanded.add if expand else self.expanded.discard)(name)
        self.rebuild(keep=name)
        self._ensure_visible()

    def toggle(self) -> None:
        cur = self.current
        if cur:
            self._set(cur[1], cur[1] not in self.expanded)

    def expand(self) -> None:
        cur = self.current
        if not cur:
            return
        if cur[0] == HEAD and cur[1] not in self.expanded:
            self._set(cur[1], True)
        else:
            self.move(self.cursor + 1)

    def collapse(self) -> None:
        cur = self.current
        if not cur:
            return
        if cur[0] == HEAD:
            self._set(cur[1], False)
        else:
            self.move(next(i for i in range(self.cursor, -1, -1) if self.rows[i][0] == HEAD))

    def collapse_all(self) -> None:
        cur = self.current
        self.expanded.clear()
        self.rebuild(keep=cur[1] if cur else None)
        self._ensure_visible()

    def on_key(self, key: str) -> bool:
        if key == "up":
            self.move(self.cursor - 1)
            return True
        if key == "down":
            self.move(self.cursor + 1)
            return True
        if key == "pageup":
            self.move(self.cursor - self._visible())
            return True
        if key == "pagedown":
            self.move(self.cursor + self._visible())
            return True
        if key == "home":
            self.move(0)
            return True
        if key == "end":
            self.move(len(self.rows) - 1)
            return True
        if key == "right":
            self.expand()
            return True
        if key == "left":
            self.collapse()
            return True
        if key in ("space", "enter", "return"):
            self.toggle()
            return True
        if key == "z":
            self.collapse_all()
            return True
        return False

    def _cursor_bg(self) -> tuple[int, int, int, int]:
        """Focused = Textual select_on; blurred = select_dim (not OptionList cream)."""
        return _SELECT_ON if self._focused else _SELECT_DIM

    def render_lines(self) -> list[Strip]:
        t = self.theme
        if not self.rows:
            return [Strip([Segment(
                "No genome loaded · O add a file · Enter to activate",
                t.rgba("muted"),
            )])]
        sel_bg = self._cursor_bg()
        out: list[Strip] = []
        for i in range(len(self.rows)):
            hi = i == self.cursor
            try:
                rich = self.row_text(i)
                lines = _text_lines(rich)
                runs = lines[0] if lines else [("", _SOFT)]
            except Exception:  # noqa: BLE001
                runs = [(f"row {i}", _SOFT)]
            segs: list[Segment] = []
            for text, fill in runs:
                if not text:
                    continue
                if hi:
                    # Keep row fg; only swap background (Textual stylize select_*).
                    segs.append(Segment(text, fill, sel_bg))
                else:
                    segs.append(Segment(text, fill))
            out.append(Strip(segs) if segs else Strip.from_text(" ", _SOFT))
        return out

    def paint_row_backgrounds(self, draw: ImageDraw.ImageDraw, region: Rect) -> None:
        if not self.rows:
            return
        y = self.line_y(self.cursor, region)
        if y is None:
            return
        hy1 = min(y + self.LINE_H - 1, region.y1 - 1)
        if hy1 >= y:
            draw.rectangle(
                [region.x, y, region.x1 - 1, hy1],
                fill=self._cursor_bg(),
            )

    def hits(self) -> list[tuple[Rect, Callable[[int, int], None]]]:
        out = list(super().hits())
        if not self.rows:
            return out
        region = self.content_region()
        vis = self._visible()
        start = self.scroll_y
        end = min(len(self.rows), start + vis + 1)

        def _pick(_x: int, _y: int, idx: int = 0) -> None:
            self.focus()
            self.move(idx)
            if self.rows[idx][0] == HEAD:
                self.toggle()

        for i in range(start, end):
            y = self.line_y(i, region)
            if y is None or y >= region.y1:
                break
            out.append((Rect(region.x, y, region.w, self.LINE_H),
                        lambda x, y, idx=i: _pick(x, y, idx)))
        return out
