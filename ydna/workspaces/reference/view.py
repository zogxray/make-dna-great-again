"""Тексты F5: строка группы, карточка, карта по эпохам."""

from __future__ import annotations

import math
import re

from rich.text import Text

from ...core.aadr import (EPOCH_LABEL, F3, Catalog, Group, Hit, ON_BRANCH, Sample,
                         date_range, hapconx_label)
from ...core.lore_codex import extract_culture
from ..base import click, fit
from ..tree.tabs.worldmap import scatter_map

# Tokens that are country / dating / QC suffixes — not culture names on the map.
_MAP_EPOCH_TOK = frozenset({
    "n", "c", "ba", "eba", "mba", "lba", "mlba", "ia", "eia", "mia", "lia",
    "en", "mn", "ln", "ca", "hg", "msh", "up", "mp", "lp", "mesolithic",
    "neolithic", "chalcolithic", "eneolithic", "bronze", "iron", "medieval",
    "historic", "modern", "imperial", "republic", "period", "age", "early",
    "late", "middle", "old", "young",
})
_MAP_SKIP_TOK = frozenset({
    "o", "lc", "contam", "rel", "mother", "father", "son", "daughter",
    "family", "duplicate", "published",
})

GRADIENT = [27, 33, 39, 45, 49, 48, 41, 77, 113, 149, 185, 220, 214, 208, 202, 196]
LIST_W = 52
REL_MARK = {"same": "●", "ancestor": "▲", "downstream": "▼", "other": "○"}
REL_STYLE = {"same": "bold #53A94B", "ancestor": "#D4AD72",
             "downstream": "#B04A4A", "other": "#C3BCCC"}
class _Doc:
    def __init__(self) -> None:
        self.t = Text()

    def title(self, s: str) -> None:
        self.t.append(f"\n{s}\n", style="bold #D4AD72")

    def kv(self, key: str, value: object, style: str = "") -> None:
        self.t.append(f"  {key}: ", style="#AEA5B8")
        self.t.append(f"{value}\n", style=style)


def pct_style(p: float) -> str:
    return f"color({GRADIENT[min(int(p / 100 * len(GRADIENT)), len(GRADIENT) - 1)]})"


def fmt_lo(lo: float) -> str:
    """lo with enough digits that near-ties do not look identical (.4f did)."""
    return f"{lo:.6f}"


def f3_pct(r: F3 | None, ranks: dict[str, float]) -> float | None:
    if r is None or not r.ok:
        return None
    return ranks.get(r.name)


def ranks_of(results: dict[str, F3], field: str = "cluster") -> dict[str, float]:
    """Colour scale 0…100 for list + map (best → 100 hot, worse → 0 cool).

    Real AADR lo is skewed: almost everyone sits in a tight band near the max,
    with a few outgroup outliers far below — so a log on raw lo collapses the
    map to one colour. We bend by *rank place* instead (same order as the list:
    higher lo first), so the top stretches into hot shades and the bottom stays
    cool without the outliers owning the scale.
    """
    del field
    ok = [r for r in results.values() if r.ok]
    if not ok:
        return {}
    # Same direction as sort_tuple / _row_key: higher lo = better.
    ordered = sorted(ok, key=lambda r: r.est - 2 * r.se, reverse=True)
    n = max(1, len(ordered) - 1)
    c = 9.0
    denom = math.log1p(c)
    return {
        r.name: 100.0 * (1.0 - math.log1p(c * i / n) / denom)
        for i, r in enumerate(ordered)
    }


def epoch_bar(epoch: str, present: list[str], playing: bool,
              labels: dict[str, str] | None = None) -> Text:
    t = Text(no_wrap=True)
    keys = ["all", *present]
    lab = labels or EPOCH_LABEL
    for key in keys:
        if key == "all":
            label = "all"
        else:
            raw = lab.get(key, key)
            label = raw.split("(")[0].strip()
        style = "bold reverse #D4AD72" if key == epoch else "#AEA5B8"
        t.append(f" {label} ", style=click(f"screen.epoch({key!r})", style))
        t.append(" ")
    t.append("◀▶", style=click("screen.epoch_step(1)", "#8A8294"))
    if playing:
        t.append("  ■", style=click("screen.toggle_play", "bold #8BAB4A"))
    else:
        t.append("  ▶", style=click("screen.toggle_play", "bold #53A94B"))
    return t


def source_bar(source: str) -> Text:
    t = Text(no_wrap=True)
    for key, lab in (("aadr", "AADR"), ("kg", "1000G")):
        style = "bold reverse #D4AD72" if source == key else "#AEA5B8"
        t.append(f" {lab} ", style=click(f"screen.source({key!r})", style))
        t.append(" ")
    return t


def region_bar(region: str, present: list[str]) -> Text:
    t = Text(no_wrap=True)
    for key in ["all", *present]:
        label = key if key != "all" else "all regions"
        style = "bold reverse #D4AD72" if key == region else "#AEA5B8"
        t.append(f" {label} ", style=click(f"screen.region({key!r})", style))
        t.append(" ")
    return t


def tab_bar(tab: str) -> Text:
    t = Text(no_wrap=True)
    for key, lab in (("map", "MAP"), ("y", "Y"), ("tree", "TREE"), ("layers", "LAYERS")):
        style = "bold reverse #D4AD72" if tab == key else "#AEA5B8"
        t.append(f" {lab} ", style=click(f"screen.tab({key!r})", style))
        t.append(" ")
    t.append("V", style="#8A8294")
    return t


def _snp(n: int) -> str:
    return f"{n / 1000:.0f}k" if n >= 10_000 else f"{n:,}"


_FILTER_BAR_W = 12
_FILTER_COL_W = 40
_FILTER_VALUE_W = 12  # fixed "≥0.074032×" / "(off)" — no row jump


def _filter_axis_bar(
    t: Text, *,
    lo: float, hi: float,
    keep_lo: float, keep_hi: float,
    action: str,
    active: bool,
    width: int = _FILTER_BAR_W,
) -> None:
    """Clickable ░/█ band — value interpolated by cell."""
    if hi <= lo:
        hi = lo + 1e-9
    width = max(6, width)
    for i in range(width):
        x0 = lo + (hi - lo) * i / width
        x1 = lo + (hi - lo) * (i + 1) / width
        mid = 0.5 * (x0 + x1)
        filled = active and keep_lo - 1e-15 <= mid <= keep_hi + 1e-15
        ch = "█" if filled else "░"
        sty = "bold #D4AD72" if filled else "#8A8294"
        t.append(ch, style=click(f"screen.score_set({action!r},{mid!r})", sty))


def _filter_value_slot(
    t: Text, *,
    value: float | None,
    fmt,
    mode: str,
    axis: str,
    width: int = _FILTER_VALUE_W,
) -> None:
    """Fixed-width value/clear slot so enabling an axis does not shift neighbors."""
    start = len(t.plain)
    if value is None:
        t.append("(off)", style="#5A5361")
    else:
        op = "≤" if mode == "ceil" else "≥"
        body = f"{op}{fmt(value)}"
        # keep × visible; trim numeric body if the column is tight
        room = max(2, width - 2)
        if len(body) > room:
            body = body[:room]
        t.append(body, style="bold #D4AD72")
        t.append(" ")
        t.append("×", style=click(f"screen.score_clear({axis!r})", "bold #B04A4A"))
    pad = width - (len(t.plain) - start)
    if pad > 0:
        t.append(" " * pad)


def _filter_band_row(
    *,
    label: str,
    axis: str,
    vmin: float,
    vmax: float,
    value: float | None,
    fmt,
    weak: str,
    strong: str,
    mode: str = "floor",
    col_width: int = _FILTER_COL_W,
) -> Text:
    """One axis row sized to col_width so two columns never wrap under the map.

    mode=floor → keep ≥ value; mode=ceil → keep ≤ value.
    « » jump to bar ends; ‹ › log-aware steps (dense end = finer).
    Hints (far/close) drop first when the column is narrow.
    """
    t = Text(no_wrap=True)
    value_w = _FILTER_VALUE_W
    # Fixed chrome: "lo  «‹›» " + value_slot
    # label4 + controls5 + gap1 + value12 = 22
    fixed = 4 + 5 + 1 + value_w
    hint_w = 4  # "far " or " clo"
    # Prefer: label «‹›» weak ░░ strong value — else drop strong — else drop both hints.
    if col_width >= fixed + 2 * hint_w + 6:
        show_weak = show_strong = True
        bar_w = col_width - fixed - 2 * hint_w
    elif col_width >= fixed + hint_w + 6:
        show_weak, show_strong = True, False
        bar_w = col_width - fixed - hint_w
    else:
        show_weak = show_strong = False
        bar_w = max(6, col_width - fixed)

    t.append(f"{label:<4}", style="#AEA5B8")
    t.append("«", style=click(f"screen.score_edge({axis!r},'lo')", "bold #AEA5B8"))
    t.append("‹", style=click(f"screen.score_step({axis!r},'lo')", "bold #D4AD72"))
    t.append("›", style=click(f"screen.score_step({axis!r},'hi')", "bold #D4AD72"))
    t.append("»", style=click(f"screen.score_edge({axis!r},'hi')", "bold #AEA5B8"))
    t.append(" ")
    if show_weak:
        t.append(f"{weak[:3]:<3} ", style="#5A5361")
    if mode == "ceil":
        keep_lo, keep_hi = vmin, (value if value is not None else vmax)
    else:
        keep_lo, keep_hi = (value if value is not None else vmin), vmax
    _filter_axis_bar(t, lo=vmin, hi=vmax, keep_lo=keep_lo, keep_hi=keep_hi,
                     action=axis, active=value is not None, width=bar_w)
    if show_strong:
        t.append(f" {strong[:3]:<3}", style="#5A5361")
    t.append(" ")
    _filter_value_slot(t, value=value, fmt=fmt, mode=mode, axis=axis, width=value_w)
    # Fit column without stripping click meta (plain rebuild used to kill all hits).
    return _pad_col(t, col_width)


def _filter_chip_row(label: str, chips: list[tuple[str, str, bool, str]],
                     col_width: int = _FILTER_COL_W) -> Text:
    """label + clickable chips. chips: (key, lab, on, action)."""
    t = Text(no_wrap=True)
    t.append(f"{label:<4}", style="#AEA5B8")
    for _key, lab, on, action in chips:
        if len(t.plain) + len(lab) + 3 > col_width:
            break
        sty = "bold reverse #D4AD72" if on else "#AEA5B8"
        t.append(f" {lab} ", style=click(action, sty))
        t.append(" ")
    return _pad_col(t, col_width)


def _pad_col(row: Text, width: int) -> Text:
    """Pad or truncate to width, preserving Rich span meta (clicks)."""
    out = Text(no_wrap=True)
    plain = row.plain
    if len(plain) > width:
        out.append_text(row[:width])
        return out
    out.append_text(row)
    if width > len(plain):
        out.append(" " * (width - len(plain)))
    return out


FILTER_TABS = ("floors", "tips")


def _filter_tab_bar(tab: str) -> Text:
    t = Text(no_wrap=True)
    for key, lab in (("floors", "FLOORS"), ("tips", "TIPS")):
        style = "bold reverse #D4AD72" if tab == key else "#AEA5B8"
        t.append(f" {lab} ", style=click(f"screen.filter_tab({key!r})", style))
        t.append(" ")
    return t


def _filter_tips_panel(col_width: int) -> Text:
    """Compact how-to · exactly 5 body lines (panel height 6 with header)."""
    t = Text(no_wrap=True)
    cw = max(24, col_width)
    rows = [
        (Text("floors", style="bold #8A8294"), Text("context", style="bold #8A8294")),
        (Text("lo  ≥ closer shared drift", style="#C3BCCC"),
         Text("SE  ≤ tighter estimate", style="#C3BCCC")),
        (Text("Z   ≥ sharper · SNP denser", style="#C3BCCC"),
         Text("Y   on-line = Our Y branch", style="#C3BCCC")),
        (Text("n   ≥ larger group", style="#C3BCCC"),
         Text("map xy · age ≤ younger BP", style="#C3BCCC")),
        (Text("«» ends · ‹› log step · × off", style="#8A8294"),
         Text("X / reset clears all", style="#8A8294")),
    ]
    for L, R in rows:
        t.append_text(_pad_col(L, cw))
        t.append("│ ", style="#5A5361")
        t.append_text(_pad_col(R, cw))
        t.append("\n")
    return t


def score_filter_panel(
    scores: dict[str, F3],
    *,
    lo_min: float | None,
    z_min: float | None,
    snp_min: int | None,
    n_min: int | None,
    se_max: float | None,
    y_mode: str,
    coords_only: bool,
    bp_max: float | None,
    bp_values: list[float],
    shown: int,
    total: int,
    bar_width: int = _FILTER_BAR_W,
    col_width: int = _FILTER_COL_W,
    tab: str = "floors",
) -> Text:
    """Two columns under the map: score floors | context (SE / Y / coords / age).

    Height budget: 1 header + 5 body rows (CSS #score-filter height: 6).
    Tab TIPS swaps body for a compact how-to — same 5 lines, no wrap.
    """
    t = Text(no_wrap=True)
    ok = [r for r in scores.values() if r.ok]
    active = (lo_min is not None or z_min is not None or snp_min is not None
              or n_min is not None or se_max is not None or y_mode != "all"
              or coords_only or bp_max is not None)
    tab = tab if tab in FILTER_TABS else "floors"
    t.append("FILTER", style="bold #D4AD72")
    t.append(f"  {shown} / {total}  ", style="#AEA5B8")
    t.append_text(_filter_tab_bar(tab))
    if active:
        t.append(" ")
        t.append(" reset ", style=click("screen.score_clear()", "bold reverse #B04A4A"))
    t.append("\n")

    cw = max(24, col_width)
    if tab == "tips":
        t.append_text(_filter_tips_panel(cw))
        return t

    if not ok:
        t.append("  no scored groups yet — run F / Scan f3\n", style="#8A8294")
        return t

    # bar_width kept for call-site compat; band sizes itself from col_width.
    _ = bar_width
    los = [r.est - 2 * r.se for r in ok]
    zs = [r.z for r in ok]
    snps = [float(r.snps) for r in ok]
    ns = [float(max(1, r.n)) for r in ok]
    ses = [r.se for r in ok if r.se > 0]

    left = [
        Text("floors", style="bold #8A8294"),
        _filter_band_row(label="lo", axis="lo", vmin=min(los), vmax=max(los),
                         value=lo_min, fmt=fmt_lo, weak="far", strong="close",
                         mode="floor", col_width=cw),
        _filter_band_row(label="Z", axis="z", vmin=min(zs), vmax=max(zs),
                         value=z_min, fmt=lambda v: f"{v:.1f}",
                         weak="weak", strong="sharp", mode="floor", col_width=cw),
        _filter_band_row(label="SNP", axis="snp", vmin=min(snps), vmax=max(snps),
                         value=float(snp_min) if snp_min is not None else None,
                         fmt=lambda v: _snp(int(v)),
                         weak="thin", strong="dense", mode="floor", col_width=cw),
        _filter_band_row(label="n", axis="n", vmin=min(ns), vmax=max(ns),
                         value=float(n_min) if n_min is not None else None,
                         fmt=lambda v: f"{int(round(v))}",
                         weak="small", strong="large", mode="floor", col_width=cw),
    ]

    right: list[Text] = [Text("context", style="bold #8A8294")]
    if ses:
        right.append(_filter_band_row(
            label="SE", axis="se", vmin=min(ses), vmax=max(ses),
            value=se_max, fmt=lambda v: f"{v:.4f}",
            weak="tight", strong="noisy", mode="ceil", col_width=cw))
    else:
        right.append(Text("SE   (n/a)", style="#5A5361"))
    right.append(_filter_chip_row("Y", [
        ("all", "all", y_mode == "all", "screen.score_y('all')"),
        ("line", "on-line", y_mode == "line", "screen.score_y('line')"),
        ("same", "same", y_mode == "same", "screen.score_y('same')"),
    ], col_width=cw))
    right.append(_filter_chip_row("map", [
        ("any", "any", not coords_only, "screen.score_coords(False)"),
        ("xy", "xy", coords_only, "screen.score_coords(True)"),
    ], col_width=cw))
    if bp_values:
        right.append(_filter_band_row(
            label="age", axis="age", vmin=0.0, vmax=max(bp_values),
            value=bp_max, fmt=lambda v: f"{int(round(v))}BP",
            weak="now", strong="old", mode="ceil", col_width=cw))
    else:
        right.append(Text("age  (n/a)", style="#5A5361"))

    for i in range(max(len(left), len(right))):
        L = left[i] if i < len(left) else Text()
        R = right[i] if i < len(right) else Text()
        t.append_text(_pad_col(L, cw))
        t.append("│ ", style="#5A5361")
        t.append_text(_pad_col(R, cw))
        t.append("\n")
    return t


CARD_W = 52
ACCENT = "bold #D4AD72"
MUTED = "#8A8294"
BODY = "#C3BCCC"
Y_RANK = {"same": 0, "ancestor": 1, "downstream": 2, "other": 3}


def _filled(s: str | None) -> str:
    t = (s or "").strip().strip('"')
    low = t.lower()
    if not t or low in (".", "..", "n/a", "na") or low.startswith("n/a"):
        return ""
    return t


def _uniq(xs: list[str], n: int = 8) -> str:
    out: list[str] = []
    for x in xs:
        x = _filled(x)
        if x and x not in out:
            out.append(x)
    if not out:
        return ""
    extra = len(out) - n
    body = ", ".join(out[:n])
    return f"{body} +{extra}" if extra > 0 else body


def _minmax(vals: list[float], fmt) -> str:
    if not vals:
        return ""
    a, b = min(vals), max(vals)
    return fmt(a) if abs(a - b) < 1e-9 else f"{fmt(a)}–{fmt(b)}"


def _doi(s: str) -> str:
    s = _filled(s)
    for p in ("https://doi.org/", "http://doi.org/"):
        if s.lower().startswith(p):
            return s[len(p):]
    return s


def _y_mark(hit: Hit | None) -> tuple[str, str]:
    if hit and hit.relation in REL_MARK:
        return REL_MARK[hit.relation], REL_STYLE[hit.relation]
    return "·", "#71697A"


def _y_rank(hit: Hit | None) -> int:
    if not hit:
        return 4
    return Y_RANK.get(hit.relation, 4)


def sort_tuple(g: Group, r: F3 | None, hit: Hit | None) -> tuple:
    """Ключи списка: ok · lo · Z · SNP · Y · name."""
    yr = _y_rank(hit)
    if r and r.ok:
        return (0, -(r.est - 2 * r.se), -r.z, -r.snps, yr, g.name)
    if r and r.snps:
        return (1, -r.snps, yr, g.name)
    return (2, yr, -g.n, g.name)


def decide_key(prev: tuple | None, cur: tuple, r: F3 | None) -> str:
    """Какой ключ решил место относительно предыдущей строки."""
    if not (r and r.ok):
        return "too few" if (r and r.snps) else "not scored"
    if prev is None or prev[0] != 0:
        return "lo"
    # cur/prev: (0, -lo, -Z, -SNP, yr, name)
    if cur[1] != prev[1]:
        return "lo"
    if cur[2] != prev[2]:
        return "Z"
    if cur[3] != prev[3]:
        return "SNP"
    if cur[4] != prev[4]:
        return "Y"
    return "name"


def _bar(p: float | None) -> tuple[str, str]:
    if p is None:
        return "", MUTED
    style = pct_style(p)
    pos = min(5, max(0, int(p / 100 * 5 + 0.5)))
    return "█" * pos + "░" * (5 - pos), style


def group_prompt(g: Group, r: F3 | None, hit: Hit | None, rank: float | None,
                 *, place: int | None = None, decide: str = "lo",
                 outside_head: bool = False) -> Text:
    t = Text(no_wrap=True)
    if outside_head:
        t.append("── outside ranking ──\n", style="bold #8BAB4A")
    mark, msty = _y_mark(hit)
    if r and r.ok:
        lo = r.est - 2 * r.se
        bar, bsty = _bar(rank)
        num = f"{place}" if place is not None else "·"
        name = fit(g.name, 22)
        lo_s = fmt_lo(lo)
        t.append(f"{num:>2} ", style=MUTED)
        t.append(f"{mark} ", style=msty)
        t.append(f"{name:<22} ", style="bold")
        if decide == "lo":
            t.append(f"⟦lo {lo_s}⟧ ", style=ACCENT)
        else:
            t.append(f"lo {lo_s} ", style=MUTED)
        if decide == "Z":
            t.append(f"⟦Z {r.z:.1f}⟧ ", style=ACCENT)
        if decide == "SNP":
            t.append(f"⟦{_snp(r.snps)} SNP⟧ ", style=ACCENT)
        if decide == "Y":
            t.append(f"⟦{mark}⟧ ", style=ACCENT)
        if decide == "name":
            t.append("⟦name⟧ ", style=ACCENT)
        t.append(bar + "\n", style=bsty)
        bits = [f"{r.est:.4f}±{r.se:.4f}"]
        if decide != "Z":
            bits.append(f"Z {r.z:.1f}")
        if decide != "SNP":
            bits.append(f"{_snp(r.snps)} SNP")
        bits.append(f"n={g.n}")
        t.append("   " + " · ".join(bits), style=MUTED)
    elif r and r.snps:
        t.append("— ", style=MUTED)
        t.append(f"{mark} ", style=msty)
        t.append(fit(g.name, 28) + " ", style="#AEA5B8")
        t.append("⟦too few⟧\n", style="bold #8BAB4A")
        t.append(f"   {r.snps:,} SNP", style=MUTED)
    else:
        t.append("— ", style=MUTED)
        t.append(f"{mark} ", style=msty)
        t.append(fit(g.name, 28) + " ", style="#AEA5B8")
        t.append("⟦not scored⟧\n", style="bold #8BAB4A")
        t.append("   no f3 estimate", style=MUTED)
    return t


def _sec(d: Text, title: str, *, major: bool = True) -> None:
    fill = "═" if major else "─"
    sty = "bold #D4AD72" if major else MUTED
    head = f"{fill * 2} {title} "
    d.append(head + fill * max(0, CARD_W - len(head)) + "\n", style=sty)


def _row(d: Text, key: str, value: str, *, accent: bool = False,
         style: str = BODY) -> None:
    if not value:
        return
    d.append(f" {key:<8} ", style=MUTED)
    d.append(fit(value, CARD_W - 10) + "\n", style=ACCENT if accent else style)


def _anno_get(s: Sample, key: str) -> str:
    return _filled((s.anno or {}).get(key) or "")


def _qpadm_bits(name: str, qpadm: list | None) -> str:
    best = None
    for row in qpadm or []:
        if not row.get("feasible"):
            continue
        for w in row.get("weights") or []:
            if w.get("pop") != name:
                continue
            p = row.get("p") or 0
            if best is None or p > best[0]:
                best = (p, w.get("w") or 0, w.get("se") or 0)
    if not best:
        return ""
    line = f"{best[1]:.0%} ± {best[2]:.0%}"
    if best[0]:
        line += f"   p={best[0]:.3g}"
    return line


def _paint_bits(name: str, paint: dict | None) -> str:
    pops = (paint or {}).get("pops") or {}
    bits = []
    if name in pops:
        bits.append(f"{pops[name]:.0%}")
    near = next((row for row in (paint or {}).get("nearest") or []
                 if row.get("iid") == name), None)
    if near:
        bits.append(f"nearest {near.get('pop', '')}")
    return " · ".join(bits)


CARD_TABS = ("our", "catalog", "summary")


def card_tab_bar(tab: str) -> Text:
    t = Text(no_wrap=True)
    for key, lab in (("our", "OUR"), ("catalog", "CATALOG"), ("summary", "SUMMARY")):
        style = "bold reverse #D4AD72" if tab == key else "#AEA5B8"
        t.append(f" {lab} ", style=click(f"screen.card_tab({key!r})", style))
        t.append(" ")
    return t


def _our_block(d: Text, r: F3 | None, hit: Hit | None, tip: str | None,
               name: str, n: int, qpadm: list | None, paint: dict | None,
               *, place: int | None, ok_total: int, decide: str,
               rank: float | None) -> None:
    if r and r.ok:
        lo = r.est - 2 * r.se
        where = f"#{place} of {ok_total} · decided by {decide}" if place else f"decided by {decide}"
        _row(d, "rank", where)
        bar, _ = _bar(rank)
        _row(d, "lo",
             f"{fmt_lo(lo)} = {r.est:.6f} − 2×{r.se:.6f}   {bar}".rstrip(),
             accent=(decide == "lo"))
        _row(d, "f3", f"{r.est:.6f} ± {r.se:.6f}")
        zline = f"{r.z:.1f}"
        if r.f4_z:
            zline = f"{r.z:.1f}            f4    {r.f4_z:.1f}"
        _row(d, "Z", zline, accent=(decide == "Z"))
        _row(d, "SNP", f"{_snp(r.snps)}            n     {n}",
             accent=(decide == "SNP"))
    elif r and r.snps:
        _row(d, "rank", "outside ranking · too few", accent=True)
        _row(d, "SNP", f"{r.snps:,}", style=MUTED)
    else:
        _row(d, "rank", "outside ranking · not scored", accent=True)
    if hit:
        mark = REL_MARK.get(hit.relation, "·")
        yv = f"{mark} {hit.node}"
        if hit.relation == "same":
            yv += f" · same as {tip or hit.y}"
        elif hit.relation in ON_BRANCH:
            yv += f" · {hit.relation} of {tip or hit.y}"
        elif tip:
            yv += f" · not your {tip}"
        _row(d, "Y", yv, accent=(decide == "Y"),
             style=REL_STYLE.get(hit.relation, BODY))
    qp = _qpadm_bits(name, qpadm)
    if qp:
        _row(d, "qpAdm", qp)
    pt = _paint_bits(name, paint)
    if pt:
        _row(d, "paint", pt)


def _doi_row(d: Text, doi: str) -> None:
    if not doi:
        return
    d.append(f" {'doi':<8} ", style=MUTED)
    d.append(fit(doi, CARD_W - 18), style=BODY)
    d.append("  ")
    d.append("[open]", style=click(f"screen.open_doi({doi!r})", "bold #D4AD72"))
    d.append("\n")


def _catalog_pubs(d: Text, keep: list[Sample], *, sample: Sample | None = None) -> None:
    if sample is not None:
        pubs = _filled(_anno_get(sample, "pub") or sample.pub)
        firsts = _filled(_anno_get(sample, "pub_first"))
        dois = [_doi(_anno_get(sample, "doi"))] if _anno_get(sample, "doi") else []
        repos = _filled(_anno_get(sample, "repo"))
        ids = " · ".join(x for x in (
            _anno_get(sample, "pid") and f"pid {_anno_get(sample, 'pid')}",
            _anno_get(sample, "iid") and f"iid {_anno_get(sample, 'iid')}",
        ) if x)
    else:
        pubs = _uniq([_anno_get(s, "pub") or s.pub for s in keep], 4)
        firsts = _uniq([_anno_get(s, "pub_first") for s in keep], 3)
        dois = []
        for s in keep:
            doi = _doi(_anno_get(s, "doi"))
            if doi and doi not in dois:
                dois.append(doi)
            if len(dois) >= 4:
                break
        repos = _uniq([_anno_get(s, "repo") for s in keep], 2)
        ids = ""
    if not (pubs or firsts or dois or repos or ids):
        return
    _sec(d, "PUBLICATIONS", major=False)
    if pubs:
        _row(d, "pub", pubs, style=MUTED)
    if firsts and firsts != pubs:
        _row(d, "first", firsts, style=MUTED)
    for doi in dois:
        _doi_row(d, doi)
    if repos:
        _row(d, "repo", repos, style=MUTED)
    if ids:
        _row(d, "ids", ids, style=MUTED)


def _catalog_qc_sample(d: Text, s: Sample) -> None:
    hx = hapconx_label(s.hapconx) or _anno_get(s, "hapconx")
    assess = _anno_get(s, "assess")
    qc_keys = (
        ("roh", "ROH"), ("damage", "damage"), ("sex_ratio", "sex ratio"),
        ("angsd", "ANGSD"), ("family", "family"), ("libraries", "libraries"),
        ("endogenous", "endogenous"), ("assess_warn", "warn"),
    )
    has = hx or assess or any(_anno_get(s, k) for k, _ in qc_keys)
    if not has:
        return
    _sec(d, "QC", major=False)
    if hx or assess:
        _row(d, "hapConX", "  ".join(x for x in (hx, assess) if x), style=MUTED)
    for key, lab in qc_keys:
        v = _anno_get(s, key)
        if v:
            _row(d, lab, v, style=MUTED)


def _catalog_qc_group(d: Text, keep: list[Sample]) -> None:
    if not keep:
        return
    bits: list[str] = []
    for s in keep[:6]:
        hx = hapconx_label(s.hapconx) or _anno_get(s, "hapconx")
        assess = _anno_get(s, "assess")
        damage = _anno_get(s, "damage")
        roh = _anno_get(s, "roh")
        piece = " · ".join(x for x in (assess, hx and f"hapConX {hx}",
                                       damage and f"dmg {damage}",
                                       roh and f"ROH {roh}") if x)
        if piece:
            bits.append(f"{fit(s.gid, 10)}  {piece}")
    if not bits:
        return
    _sec(d, "QC", major=False)
    for line in bits[:6]:
        d.append(" " + fit(line, CARD_W - 1) + "\n", style=MUTED)


def _y_on_our(hit: Hit | None) -> bool:
    return bool(hit and hit.relation in ON_BRANCH)


def _catalog_people_row(d: Text, s: Sample, hit: Hit | None) -> None:
    """One PEOPLE line; Our Y branch (same/ancestor/downstream) is highlighted."""
    y = _anno_get(s, "y_term") or s.y or "—"
    mt = _anno_get(s, "mt") or s.mt or "—"
    cov = f"{s.coverage:.2f}×" if s.coverage else ""
    assess = _anno_get(s, "assess")
    on = _y_on_our(hit)
    mark = REL_MARK.get(hit.relation, "·") if on and hit else " "
    y_sty = REL_STYLE.get(hit.relation, BODY) if on and hit else BODY
    line_sty = y_sty if on else BODY
    d.append(" ")
    d.append(f"{mark} ", style=y_sty if on else MUTED)
    # gid · sex stay readable; Y carries the Our-branch accent
    d.append(f"{fit(s.gid, 11)} ", style=line_sty)
    if s.sex:
        d.append(f"{s.sex} ", style=MUTED if not on else line_sty)
    d.append(fit(y, 14), style=y_sty)
    rest = "  ".join(x for x in (mt, cov, assess) if x)
    if rest:
        d.append("  " + fit(rest, max(8, CARD_W - 32)), style=MUTED if not on else line_sty)
    d.append("\n")


def _catalog_y_row(d: Text, keep: list[Sample], y_hits: dict[str, Hit] | None,
                   group_ys: list[str] | None = None) -> None:
    """GENETICS · Y — each haplogroup, Our-branch hits marked ●/▲/▼."""
    # Prefer sample order; fall back to group.y labels.
    entries: list[tuple[str, Hit | None]] = []
    seen: set[str] = set()
    for s in keep:
        y = _filled(_anno_get(s, "y_term") or s.y)
        if not y or y in seen:
            continue
        seen.add(y)
        entries.append((y, (y_hits or {}).get(s.gid)))
    if not entries and group_ys:
        for y in group_ys:
            y = _filled(y)
            if y and y not in seen:
                seen.add(y)
                entries.append((y, None))
    if not entries:
        return
    d.append(f" {'Y':<8} ", style=MUTED)
    room = CARD_W - 10
    used = 0
    for i, (y, hit) in enumerate(entries[:8]):
        on = _y_on_our(hit)
        mark = REL_MARK.get(hit.relation, "") if on and hit else ""
        piece = f"{mark}{y}" if mark else y
        sep = ", " if i else ""
        need = len(sep) + len(piece)
        if used and used + need > room:
            d.append("…", style=MUTED)
            break
        if sep:
            d.append(sep, style=BODY)
            used += len(sep)
        sty = REL_STYLE.get(hit.relation, BODY) if on and hit else BODY
        d.append(piece, style=sty)
        used += len(piece)
    d.append("\n")


def _catalog_group(d: Text, g: Group, keep: list[Sample],
                   *, y_hits: dict[str, Hit] | None = None) -> None:
    # 1 PLACE
    country = _filled(g.country)
    site = _filled(g.locality)
    if country or site or g.lat is not None:
        _sec(d, "PLACE")
        _row(d, "country", country)
        _row(d, "site", site)
        if g.lat is not None and g.lon is not None:
            _row(d, "coord", f"{g.lat:.2f} N  {g.lon:.2f} E")
        d.append("\n")
    # 2 TIME
    span = date_range(keep, g.bp)
    ep = EPOCH_LABEL.get(g.epoch, g.epoch)
    ages = _uniq([_anno_get(s, "age") for s in keep], 4)
    methods = _uniq([_anno_get(s, "date_method") for s in keep], 2)
    date_bits = []
    for s in keep:
        bp = _anno_get(s, "date")
        if not bp:
            continue
        sd = _anno_get(s, "date_sd")
        date_bits.append(f"{bp}" + (f" ± {sd}" if sd else "") + " BP")
        if len(date_bits) >= 2:
            break
    if (span and span != "?") or ep or ages or methods or date_bits:
        _sec(d, "TIME")
        when = " · ".join(x for x in (span if span and span != "?" else "", ep) if x)
        _row(d, "when", when)
        _row(d, "age", ages)
        if methods or date_bits:
            _row(d, "date", " · ".join(x for x in (methods, " · ".join(date_bits)) if x))
        d.append("\n")
    # 3 PEOPLE — Our Y branch highlighted
    if keep:
        on_n = sum(1 for s in keep if _y_on_our((y_hits or {}).get(s.gid)))
        title = f"PEOPLE · {len(keep)}"
        if on_n:
            title += f" · {on_n} on Our Y"
        _sec(d, title)
        for s in keep[:8]:
            _catalog_people_row(d, s, (y_hits or {}).get(s.gid))
        if len(keep) > 8:
            _row(d, "…", f"{len(keep) - 8} more", style=MUTED)
        d.append("\n")
    # 4 GENETICS
    isos = _uniq([_anno_get(s, "y_isogg") or s.y_isogg for s in keep])
    mts = _uniq([_anno_get(s, "mt") or s.mt for s in keep] or g.mt)
    has_y = any(_filled(_anno_get(s, "y_term") or s.y) for s in keep) or bool(g.y)
    if has_y or mts:
        _sec(d, "GENETICS")
        if has_y:
            _catalog_y_row(d, keep, y_hits, group_ys=list(g.y or []))
            if isos:
                _row(d, "ISOGG", isos, style=MUTED)
        if mts:
            _row(d, "mt", mts)
        d.append("\n")
    # 5 DATA
    snps = [float(s.snps) for s in keep if s.snps]
    covs = [s.coverage for s in keep if s.coverage]
    types = _uniq([_anno_get(s, "data_type") for s in keep], 3)
    libs = _uniq([_anno_get(s, "lib_type") for s in keep], 3)
    suf = _uniq([_anno_get(s, "suffix") for s in keep], 3)
    counts: dict[str, int] = {}
    for s in keep:
        k = _anno_get(s, "assess") or ("Questionable" if s.questionable else "Pass" if s.keep else "")
        if k:
            counts[k] = counts.get(k, 0) + 1
    if snps or covs or types or counts:
        _sec(d, "DATA", major=False)
        if snps or covs:
            bits = []
            if snps:
                bits.append(_minmax(snps, lambda v: f"{v / 1000:.0f}k"))
            if covs:
                bits.append(_minmax(covs, lambda v: f"{v:.2f}×"))
            _row(d, "1240k", " · ".join(bits), style=MUTED)
        tech = " · ".join(x for x in (suf, types, libs) if x)
        if tech:
            _row(d, "type", tech, style=MUTED)
        if counts:
            _row(d, "Pass", "  ".join(f"{k} {n}" for k, n in counts.items()), style=MUTED)
        d.append("\n")
    # 6 QC
    _catalog_qc_group(d, keep)
    if keep:
        d.append("\n")
    # 7 PUBLICATIONS
    _catalog_pubs(d, keep)


def _catalog_person(d: Text, s: Sample, *, hit: Hit | None = None) -> None:
    # 1 PLACE
    country = _filled(s.country)
    site = _filled(s.locality)
    if country or site or s.lat is not None:
        _sec(d, "PLACE")
        _row(d, "country", country)
        _row(d, "site", site)
        if s.lat is not None and s.lon is not None:
            _row(d, "coord", f"{s.lat} N  {s.lon} E")
        d.append("\n")
    # 2 TIME
    span = _filled(s.date_span) or _anno_get(s, "date_full")
    method = _anno_get(s, "date_method")
    date_bp = _anno_get(s, "date")
    date_sd = _anno_get(s, "date_sd")
    age = _anno_get(s, "age")
    if span or method or date_bp or age:
        _sec(d, "TIME")
        _row(d, "when", span)
        _row(d, "age", age)
        if date_bp:
            _row(d, "date",
                 f"{method + ' · ' if method else ''}{date_bp}"
                 + (f" ± {date_sd} BP" if date_sd else " BP"))
        elif method:
            _row(d, "method", method)
        d.append("\n")
    # 3 WHO (person)
    on = _y_on_our(hit)
    who_title = "WHO · on Our Y" if on else "WHO"
    _sec(d, who_title)
    grp = _anno_get(s, "group") or s.group
    _row(d, "group", grp)
    who = " · ".join(x for x in (_anno_get(s, "sex") or s.sex,
                                 _anno_get(s, "skel"),
                                 _anno_get(s, "skel_code")) if x)
    _row(d, "sex", who)
    _row(d, "age", age)
    d.append("\n")
    # 4 GENETICS — Our Y highlighted
    term = _anno_get(s, "y_term") or s.y
    iso = _anno_get(s, "y_isogg") or s.y_isogg
    man = _anno_get(s, "y_manual")
    mt = _anno_get(s, "mt") or s.mt
    if term or iso or man or mt:
        _sec(d, "GENETICS")
        if term or iso or man:
            d.append(f" {'Y':<8} ", style=MUTED)
            if term:
                if on and hit:
                    mark = REL_MARK.get(hit.relation, "·")
                    d.append(f"{mark} {term}", style=REL_STYLE.get(hit.relation, ACCENT))
                    if hit.node and hit.node != term:
                        d.append(f" · {hit.node}", style=MUTED)
                else:
                    d.append(term, style=BODY)
            bits = []
            if iso and iso != term:
                bits.append(f"ISOGG {iso}")
            if man and man not in (term, iso):
                bits.append(f"manual {man}")
            if bits:
                d.append(" · " + " · ".join(bits), style=MUTED)
            d.append("\n")
        if mt:
            mt_line = mt
            if _anno_get(s, "mt_cov"):
                mt_line += f"  {_anno_get(s, 'mt_cov')}×"
            if _anno_get(s, "mt_match"):
                mt_line += f"  match {_anno_get(s, 'mt_match')}"
            _row(d, "mt", mt_line)
        d.append("\n")
    # 5 DATA
    _sec(d, "DATA", major=False)
    if s.snps or s.coverage:
        bits = []
        if s.snps:
            bits.append(f"{s.snps:,}")
        if s.coverage:
            bits.append(f"{s.coverage:.2f}×")
        _row(d, "1240k", " · ".join(bits), style=MUTED)
    extra = []
    for key, lab in (("snps_ho", "HO"), ("snps_2m", "2M")):
        v = _anno_get(s, key)
        if v:
            try:
                extra.append(f"{lab} {float(v.replace(',', '')):,.0f}")
            except ValueError:
                extra.append(f"{lab} {v}")
    if extra:
        _row(d, "extra", " · ".join(extra), style=MUTED)
    off = _anno_get(s, "cov_off")
    if off:
        try:
            _row(d, "off-tgt", f"{float(off):.2f}×", style=MUTED)
        except ValueError:
            _row(d, "off-tgt", off, style=MUTED)
    tech = " · ".join(x for x in (_anno_get(s, "data_type"), _anno_get(s, "suffix"),
                                  _anno_get(s, "lib_type")) if x)
    if tech:
        _row(d, "libs", tech, style=MUTED)
    pull = _anno_get(s, "pulldown")
    if pull:
        _row(d, "pulldown", pull, style=MUTED)
    d.append("\n")
    # 6 QC
    _catalog_qc_sample(d, s)
    d.append("\n")
    # 7 PUBLICATIONS
    _catalog_pubs(d, [], sample=s)


def summary_prompt(g: Group, keep: list[Sample], *, sample: Sample | None = None) -> str:
    """Scientific Ask prompt for SUMMARY — ANNO facts only as grounding."""
    subject = sample.gid if sample is not None else g.name
    lines = [
        "Write a scientific brief on this Allen Ancient DNA Resource (AADR) assemblage.",
        "Use an academic tone. Structure the answer as:",
        "1) What this assemblage is (place, date, archaeological context).",
        "2) Key published studies that report or analyse these genomes — with specifics.",
        "3) What is new, contested, or still open — with details.",
        "Do not invent sample IDs, DOIs, or dates beyond the facts below.",
        "",
        f"Subject: {subject}",
    ]
    if sample is not None:
        lines.append(f"Group ID: {_anno_get(sample, 'group') or sample.group or g.name}")
        lines.append(f"Country: {_filled(sample.country) or '—'}")
        lines.append(f"Locality: {_filled(sample.locality) or '—'}")
        if sample.lat is not None and sample.lon is not None:
            lines.append(f"Coordinates: {sample.lat}, {sample.lon}")
        span = _filled(sample.date_span) or _anno_get(sample, "date_full")
        if span:
            lines.append(f"Date: {span}")
        bp = _anno_get(sample, "date")
        sd = _anno_get(sample, "date_sd")
        method = _anno_get(sample, "date_method")
        if bp:
            lines.append(f"Date BP: {bp}" + (f" ± {sd}" if sd else "")
                         + (f" ({method})" if method else ""))
        age = _anno_get(sample, "age")
        if age:
            lines.append(f"Age at death: {age}")
        y = _anno_get(sample, "y_term") or sample.y
        iso = _anno_get(sample, "y_isogg") or sample.y_isogg
        mt = _anno_get(sample, "mt") or sample.mt
        if y or iso:
            lines.append(f"Y: {y or '—'}" + (f" / ISOGG {iso}" if iso and iso != y else ""))
        if mt:
            lines.append(f"mt: {mt}")
        pub = _anno_get(sample, "pub") or sample.pub
        first = _anno_get(sample, "pub_first")
        doi = _doi(_anno_get(sample, "doi"))
        repo = _anno_get(sample, "repo")
        if pub:
            lines.append(f"Publication: {pub}")
        if first and first != pub:
            lines.append(f"First publication: {first}")
        if doi:
            lines.append(f"DOI: {doi}")
        if repo:
            lines.append(f"Repository: {repo}")
    else:
        lines.append(f"Country: {_filled(g.country) or '—'}")
        lines.append(f"Locality: {_filled(g.locality) or '—'}")
        if g.lat is not None and g.lon is not None:
            lines.append(f"Coordinates: {g.lat:.4f}, {g.lon:.4f}")
        span = date_range(keep, g.bp)
        ep = EPOCH_LABEL.get(g.epoch, g.epoch)
        if span and span != "?":
            lines.append(f"Date span: {span}")
        if ep:
            lines.append(f"Epoch: {ep}")
        ys = _uniq([_anno_get(s, "y_term") or s.y for s in keep] or g.y)
        mts = _uniq([_anno_get(s, "mt") or s.mt for s in keep] or g.mt)
        if ys:
            lines.append(f"Y haplogroups: {ys}")
        if mts:
            lines.append(f"mt haplogroups: {mts}")
        lines.append(f"Individuals (kept): {len(keep)}")
        for s in keep[:12]:
            bits = [s.gid, s.sex or "", _anno_get(s, "y_term") or s.y or "",
                    _anno_get(s, "mt") or s.mt or "",
                    f"{s.coverage:.2f}×" if s.coverage else "",
                    _anno_get(s, "pub") or s.pub or ""]
            lines.append("- " + " · ".join(x for x in bits if x))
        pubs = _uniq([_anno_get(s, "pub") or s.pub for s in keep], 6)
        firsts = _uniq([_anno_get(s, "pub_first") for s in keep], 4)
        dois = []
        for s in keep:
            doi = _doi(_anno_get(s, "doi"))
            if doi and doi not in dois:
                dois.append(doi)
        repos = _uniq([_anno_get(s, "repo") for s in keep], 3)
        if pubs:
            lines.append(f"Publications: {pubs}")
        if firsts:
            lines.append(f"First publications: {firsts}")
        if dois:
            lines.append("DOIs: " + "; ".join(dois[:6]))
        if repos:
            lines.append(f"Repositories: {repos}")
    return "\n".join(lines)


def _summary_tab(d: Text, *,
                 cache: dict | None,
                 busy: bool,
                 draft: str = "",
                 model_hint: str = "") -> None:
    if busy:
        d.append(" Generating summary…\n", style=ACCENT)
        if model_hint:
            d.append(f" {model_hint}\n", style=MUTED)
        if draft:
            d.append("\n")
            for line in draft.splitlines()[:40]:
                d.append(f" {line}\n", style=BODY)
        return
    if cache and (cache.get("text") or "").strip():
        model = cache.get("model") or "model"
        ts = (cache.get("ts") or "")[:10]
        head = " · ".join(x for x in (model, ts, "from ANNO") if x)
        d.append(f" {head}\n", style=MUTED)
        d.append(" ")
        d.append(" Refresh ", style=click("screen.ask_summary(True)", "bold reverse #D4AD72"))
        d.append("\n\n", style=MUTED)
        for line in str(cache.get("text") or "").splitlines():
            d.append(f" {line}\n", style=BODY)
        return
    d.append(" No summary yet.\n\n", style=MUTED)
    d.append(" ")
    d.append(" Ask summary ", style=click("screen.ask_summary()", "bold reverse #D4AD72"))
    d.append("  uses Chat model · scientific brief\n\n", style=MUTED)
    d.append(" Prompt sketch (what we send):\n", style="#8A8294")
    d.append("  · group + place/date/Y/mt from ANNO\n", style="#71697A")
    d.append("  · pubs + DOIs\n", style="#71697A")
    d.append("  · ask: what this assemblage is; key studies;\n", style="#71697A")
    d.append("    what is new / contested — with details\n", style="#71697A")


def card(g: Group | None, *, sample: Sample | None = None,
         cat: Catalog | None = None, hit: Hit | None = None,
         branch: str | None = None, questionable: bool = False,
         qpadm: list | None = None, paint: dict | None = None,
         result: F3 | None = None, place: int | None = None,
         ok_total: int = 0, decide: str = "lo",
         rank: float | None = None,
         tab: str = "our",
         summary: dict | None = None,
         summary_busy: bool = False,
         summary_draft: str = "",
         summary_model: str = "",
         y_hits: dict[str, Hit] | None = None) -> Text:
    d = Text()
    if g is None:
        d.append(" No selection.\n", style="#AEA5B8")
        return d
    tab = tab if tab in CARD_TABS else "our"
    # Tab bar lives outside the scroll (#card-tabs).
    keep: list[Sample] = []
    if cat:
        keep = [s for s in (cat.samples[i] for i in g.samples)
                if s.keep or (questionable and s.questionable)]
    if tab == "summary":
        _summary_tab(d, cache=summary, busy=summary_busy,
                     draft=summary_draft, model_hint=summary_model)
        return d
    if tab == "catalog":
        if sample is not None:
            person_hit = hit or (y_hits or {}).get(sample.gid)
            _catalog_person(d, sample, hit=person_hit)
        else:
            _catalog_group(d, g, keep, y_hits=y_hits)
        return d
    # OUR
    name = sample.gid if sample is not None else g.name
    n = 1 if sample is not None else (g.n if g.n else len(keep))
    _our_block(d, result, hit, branch, name, n, qpadm, paint,
               place=place, ok_total=ok_total, decide=decide, rank=rank)
    return d


def _rank_color(p: float) -> str:
    return f"color({GRADIENT[min(int(p / 100 * len(GRADIENT)), len(GRADIENT) - 1)]})"


def culture_map_label(group_name: str) -> str:
    """Short culture name for map labels: Srubnaya, Yamnaya — not Russia_…_MLBA."""
    raw = (group_name or "").strip()
    if not raw:
        return ""
    named = extract_culture(raw)
    # lore_codex hits look like "Srubnaya (Bronze Age)"
    if " (" in named and not named.startswith(("Archaeological",)):
        short = named.split(" (", 1)[0].strip()
        if short and " · " not in short:
            return fit(short, 14)
    base = raw.split(".", 1)[0]
    parts = [p for p in re.split(r"[_\s]+", base) if p]
    if len(parts) <= 1:
        return ""
    # Drop country (first token), epoch/QC tokens; keep culture-ish remainder.
    body = []
    for p in parts[1:]:
        low = p.lower()
        if low in _MAP_EPOCH_TOK or low in _MAP_SKIP_TOK:
            continue
        if re.fullmatch(r"\d+(\.\d+)?", p):
            continue
        body.append(p)
    if not body:
        return ""
    # Prefer a multi-word culture if two tokens look cultural (CordedWare already one).
    label = body[0]
    if len(body) >= 2 and body[1][0:1].isupper() and body[1].lower() not in _MAP_EPOCH_TOK:
        # e.g. Central_Steppe → keep first meaningful only to avoid clutter
        pass
    # CamelCase / glued: CordedWare → Corded Ware for readability? Keep compact.
    label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", label)
    return fit(label, 14)


def draw_map(groups: list[Group], results: dict[str, F3], ranks: dict[str, float],
             selected: str | None, title: str,
             world: bool, cols: int, rows: int,
             *, culture_labels: bool = True,
             seshat_anchors: list[dict] | None = None) -> Text:
    doc = _Doc()
    doc.t = Text(no_wrap=True, overflow="crop")
    points = []
    for g in groups:
        if g.lat is None or g.lon is None or (g.lat == 0 and g.lon == 0):
            continue
        r = results.get(g.name)
        p = f3_pct(r, ranks)
        if p is not None:
            color, glyph = _rank_color(p), None
        elif r and r.snps:
            color, glyph = "#555555", None
        elif g.name == selected:
            color, glyph = "#5A7CB3", None
        else:
            continue
        cult = culture_map_label(g.name)
        points.append({
            "id": g.name, "lon": g.lon, "lat": g.lat, "color": color,
            "glyph": glyph,
            "label": cult or g.name.split("_")[0],
            "culture": cult,
            "rank": float(p) if p is not None else -1.0,
        })
    scatter_map(doc, points, title, world=world, cols=cols, max_rows=rows,
                selected=selected, culture_labels=culture_labels,
                anchors=seshat_anchors)
    return doc.t


def seshat_layer_for_groups(groups: list[Group], *, epoch: str,
                            store=None, limit: int = 8) -> list[dict]:
    """Build Seshat map anchors from visible AADR groups + epoch year."""
    if not groups or epoch in ("", "all"):
        return []
    try:
        from ...core.seshat import load_index, map_anchors
    except Exception:  # noqa: BLE001
        return []
    idx = load_index(store=store)
    if idx is None:
        return []
    hints: list[str] = []
    aadr_xy: list[tuple[float, float, str]] = []
    for g in groups:
        cult = culture_map_label(g.name)
        blob = " ".join(x for x in (g.name, g.country, g.locality, cult) if x)
        hints.append(g.name)
        if g.country:
            hints.append(g.country)
        if cult:
            hints.append(cult)
        if g.lat is not None and g.lon is not None and not (g.lat == 0 and g.lon == 0):
            aadr_xy.append((float(g.lon), float(g.lat), blob))
    # Cap hint noise — match() tokenizes; unique keep order.
    seen: set[str] = set()
    uniq: list[str] = []
    for h in hints:
        if h and h not in seen:
            seen.add(h)
            uniq.append(h)
        if len(uniq) >= 40:
            break
    return map_anchors(idx, hints=uniq, epoch=epoch, aadr_xy=aadr_xy, limit=limit)


def draw_y_map(groups: list[Group], results: dict[str, F3], ranks: dict[str, float],
               hits: dict[str, Hit], ours_other: set[str], selected: str | None,
               title: str, world: bool, cols: int, rows: int) -> Text:
    doc = _Doc()
    doc.t = Text(no_wrap=True, overflow="crop")
    points = []
    for g in groups:
        if g.lat is None or g.lon is None or (g.lat == 0 and g.lon == 0):
            continue
        h = hits.get(g.name)
        if not h:
            continue
        r = results.get(g.name)
        p = f3_pct(r, ranks)
        if h.relation in ON_BRANCH:
            color, glyph = "#53A94B", REL_MARK[h.relation]
        elif g.name in ours_other and p is not None:
            color, glyph = _rank_color(p), "○"
        else:
            continue
        points.append({"id": g.name, "lon": g.lon, "lat": g.lat, "color": color,
                       "glyph": glyph, "label": g.name.split("_")[0]})
    scatter_map(doc, points, title, world=world, cols=cols, max_rows=rows, selected=selected)
    return doc.t


REGIONS = ("Americas", "Africa", "Europe", "W.Asia", "E.Asia", "Oceania")


def region_of(lat: float | None, lon: float | None) -> str | None:
    if lat is None or lon is None:
        return None
    if lon < -30:
        return "Americas"
    if lon < 40:
        return "Africa" if lat < 37 else "Europe"
    if lon < 90:
        return "W.Asia"
    if lon < 150:
        return "E.Asia"
    return "Oceania"


def tree_view(groups: list[Group], results: dict[str, F3], ranks: dict[str, float],
              hits: dict[str, Hit], qpadm: list | None, epochs: list[str],
              epoch_labels: dict[str, str], selected: str | None,
              paint: dict | None = None) -> Text:
    d = _Doc()
    d.t.append("Time ↓  ·  region branches  ·  qpAdm mergers  ·  Y branch / other Y\n",
               style="#AEA5B8")
    by: dict[tuple[str, str], list[Group]] = {}
    y_by_ep: dict[str, list[Group]] = {}
    y_off: dict[str, list[Group]] = {}
    for g in groups:
        h = hits.get(g.name)
        if h and h.relation in ON_BRANCH:
            y_by_ep.setdefault(g.epoch, []).append(g)
        elif h and h.relation == "other":
            y_off.setdefault(g.epoch, []).append(g)
        reg = region_of(g.lat, g.lon)
        if not reg:
            continue
        by.setdefault((g.epoch, reg), []).append(g)
    q_by_ep: dict[str, dict] = {}
    for row in qpadm or []:
        if row.get("feasible") and row.get("epoch") and row["epoch"] not in q_by_ep:
            q_by_ep[row["epoch"]] = row
    n_ep = len(epochs)
    for ei, ep in enumerate(epochs):
        last_ep = ei == n_ep - 1
        d.t.append(f"\n{'└' if last_ep else '├'}─ ", style="#8A8294")
        d.t.append(f"{epoch_labels.get(ep, ep)}\n", style="bold #D4AD72")
        regs = [r for r in REGIONS if by.get((ep, r))]
        for ri, reg in enumerate(regs):
            last_r = (ri == len(regs) - 1 and ep not in y_by_ep and ep not in y_off
                      and ep not in q_by_ep)
            rows = sorted(by[(ep, reg)], key=lambda g: -(results[g.name].est
                         if results.get(g.name) and results[g.name].ok else -1))
            branch = "    └─ " if last_r else "    ├─ "
            d.t.append(branch, style="#8A8294")
            d.t.append(f"{reg:<10} ", style="#AEA5B8")
            for i, g in enumerate(rows[:2]):
                r = results.get(g.name)
                star = "★" if r and r.ok and r.epoch_cluster == 0 else ""
                f3s = f"{r.est:.3f}" if r and r.ok else ""
                sty = "bold #FBF6F0" if g.name == selected else (
                    "#B04A4A" if star else "")
                if i:
                    d.t.append(" · ", style="#71697A")
                d.t.append(f"{star}{g.name[:20]} {f3s}".strip(),
                           style=click(f"screen.select({g.name!r})", sty))
            d.t.append("\n")
        if ep in y_by_ep:
            d.t.append("    ├─ ", style="#8A8294")
            d.t.append("Y branch  ", style="bold #53A94B")
            for i, g in enumerate(y_by_ep[ep][:3]):
                h = hits[g.name]
                mark = REL_MARK.get(h.relation, "●")
                if i:
                    d.t.append("  ", style="")
                d.t.append(f"{mark} {g.name[:18]}",
                           style=click(f"screen.select({g.name!r})",
                                       REL_STYLE.get(h.relation, "")))
            d.t.append("\n")
        if ep in y_off:
            d.t.append("    ├─ ", style="#8A8294")
            d.t.append("other Y   ", style="#C3BCCC")
            for i, g in enumerate(y_off[ep][:3]):
                h = hits[g.name]
                if i:
                    d.t.append("  ", style="")
                d.t.append(f"○ {g.name[:18]}",
                           style=click(f"screen.select({g.name!r})", "#C3BCCC"))
            d.t.append("\n")
        if ep in q_by_ep:
            row = q_by_ep[ep]
            left = " + ".join(row.get("left") or [])
            wts = " ".join(f"{w.get('pop')} {w.get('w', 0):.0%}"
                           for w in row.get("weights") or [])
            p = row.get("p")
            d.t.append("    └─ merge    ", style="#8A8294")
            d.t.append(left, style="bold")
            if wts:
                d.t.append(f"  {wts}", style="#C3BCCC")
            if p is not None:
                d.t.append(f"  p={p:.3g}",
                           style="#53A94B" if row.get("feasible") else "#B04A4A")
            d.t.append("\n")
    best = None
    for row in qpadm or []:
        if row.get("feasible"):
            best = row
            break
    if best is None and qpadm:
        best = qpadm[0]
    d.t.append("\nYOU", style="bold #53A94B")
    if best:
        left = " + ".join(best.get("left") or [])
        d.t.append("  ←  ", style="#8A8294")
        d.t.append(left, style="bold")
        p = best.get("p")
        if p is not None:
            d.t.append(f"   p={p:.3g}",
                       style="#53A94B" if best.get("feasible") else "#B04A4A")
        d.t.append("\n")
    else:
        d.t.append("  · no qpAdm yet  ", style="#AEA5B8")
        d.t.append("[LAYERS]", style=click("screen.tab('layers')", "bold #D4AD72"))
        d.t.append("\n")
    if paint and paint.get("pops"):
        d.t.append("\nChromoPainter  ", style="bold #D4AD72")
        bits = [f"{k} {v:.0%}" for k, v in list(paint["pops"].items())[:5]]
        d.t.append("  ".join(bits) + "\n", style="#C3BCCC")
        for row in (paint.get("nearest") or [])[:4]:
            d.t.append(f"  {row.get('iid')}  {row.get('pop')}  {row.get('chunks', 0):.1f}\n",
                       style="#AEA5B8")
    return d.t


def layers_view(rows: list[dict], chip: dict[str, F3], imputed: dict[str, F3]) -> Text:
    d = _Doc()
    d.t.append("Layers  ·  click a missing step to run it\n", style="#AEA5B8")
    for row in rows:
        mark = {"done": "●", "ready": "●", "running": "⚙", "missing": "○",
                "n/a": "·"}.get(row["status"], "○")
        color = {"done": "bold #53A94B", "ready": "#D4AD72",
                 "running": "#B04A4A", "missing": "#8A8294", "n/a": "#71697A"}.get(
            row["status"], "#8A8294")
        d.t.append(f"\n  {mark}  {row['id']:>3}  ", style=color)
        title_style = click(row["action"], "bold") if row.get("action") else "bold"
        d.t.append(row["title"], style=title_style)
        d.t.append(f"\n       {row.get('detail') or ''}\n", style="#AEA5B8")
        if row.get("action"):
            d.t.append("       ", style="")
            d.t.append("run", style=click(row["action"], "bold reverse #D4AD72"))
            d.t.append("\n")
    names = sorted({*chip, *imputed},
                   key=lambda n: -(chip[n].est if n in chip and chip[n].ok else -1))
    if names and imputed:
        d.title("CHIP vs IMPUTED  ·  outgroup-f3")
        d.t.append(f"  {'group':<24} {'chip':>12} {'imputed':>12} {'Δ':>9}\n", style="#8A8294")
        for name in names[:24]:
            a, b = chip.get(name), imputed.get(name)
            d.t.append(f"  {name:<24.24} ", style="#C3BCCC")
            d.t.append(f"{a.est:12.5f}" if a and a.ok else f"{'—':>12}", style="#C3BCCC")
            d.t.append(" ")
            d.t.append(f"{b.est:12.5f}" if b and b.ok else f"{'—':>12}", style="#C3BCCC")
            if a and b and a.ok and b.ok:
                d.t.append(f"  {a.est - b.est:+.5f}", style="#8A8294")
            d.t.append("\n")
    return d.t
