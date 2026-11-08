"""Тексты режима PGS: строка модели в списке, карточка модели, обзор результатов генома."""

from __future__ import annotations

from rich.text import Text

from ...core.pgs import HIST_BINS, POPS, SUPERPOPS, THEME_LABEL, THEMES, GroupStat, Model, Pair, Result
from ...theme.harmony import BrandAnchor, DIATONIC_HUE_STEP, muted_fill
from ...theme.styles import A_STYLE, B_STYLE, BAD, DIM, OK, WARN
from ..base import click, fit

_ANCHOR = BrandAnchor.from_brand()
GRADIENT = [
    muted_fill(_ANCHOR, dark_ui=True, hue_offset=DIATONIC_HUE_STEP * (i / 5.0), s_extra=0.02, v_extra=0.04)
    for i in range(16)
]
SPARK = " ▁▂▃▄▅▆▇█"
LIST_W = 40
BIG_DELTA = 25
SORTS = {"theme": "theme", "delta": "Δ", "a": "A", "b": "B"}


def pct_style(p: float) -> str:
    return GRADIENT[min(int(p / 100 * len(GRADIENT)), len(GRADIENT) - 1)]


def z_style(z: float | None) -> str:
    if z is None:
        return DIM
    a = abs(z)
    return OK if a < 1 else WARN if a < 2 else WARN if a < 3 else BAD


def z_words(z: float | None) -> str:
    if z is None:
        return ""
    a, side = abs(z), "above" if z > 0 else "below"
    if a < 0.25:
        return "very close to the mean"
    if a < 1:
        return f"slightly {side} the mean"
    if a < 2:
        return f"noticeably {side} the mean"
    return f"at the edge of the distribution ({side})"


def bar(t: Text, p: float, width: int) -> None:
    pos = min(int(round(p / 100 * (width - 1))), width - 1)
    t.append("█" * pos, style=pct_style(p))
    t.append("◆", style="bold #FBF6F0")
    t.append("░" * (width - pos - 1), style="#433F47")


def z_text(z: float | None) -> str:
    return f"{z:+.2f}" if z is not None else "n/a"


# ───────────────────────── список моделей ─────────────────────────

def theme_header(theme: str, total: int, done: int) -> Text:
    t = Text(no_wrap=True)
    t.append(THEME_LABEL.get(theme, theme).upper(), style="bold #D4AD72")
    t.append(f"  {done}/{total}", style="#8A8294")
    return t


def model_prompt(m: Model, r: Result | None) -> Text:
    t = Text(no_wrap=True)
    if r and not r.error and r.eur:
        t.append("✔ ", style="#53A94B")
    elif r and r.error:
        t.append("✖ ", style="#8BAB4A")
    else:
        t.append("· ", style="#71697A")
    t.append(fit(m.trait, LIST_W - 2) + "\n", style="bold" if r else "")
    t.append(f"  {m.pgs_id} ", style="#8A8294")
    if r and r.error:
        t.append("failed", style="#8BAB4A")
    elif r and r.eur:
        t.append(f"{r.eur.pct:5.1f}% ", style=pct_style(r.eur.pct))
        bar(t, r.eur.pct, LIST_W - 20)
    elif m.status and m.status not in ("ok", ""):
        t.append(m.status.replace("_", " "), style="#B04A4A")
    else:
        t.append("not scored", style="#71697A")
    return t


# ───────────────────────── карточка ─────────────────────────

class _Doc:
    def __init__(self) -> None:
        self.t = Text()

    def title(self, s: str) -> None:
        self.t.append(f"\n{s}\n", style="bold #D4AD72")

    def kv(self, key: str, value: object, style: str = "") -> None:
        self.t.append(f"  {key}: ", style="#AEA5B8")
        self.t.append(f"{value}\n", style=style)


def hist_line(r: Result) -> Text:
    t = Text()
    top = max(r.hist) or 1
    t.append("  EUR distribution  ", style="#AEA5B8")
    for i, n in enumerate(r.hist):
        ch = SPARK[min(len(SPARK) - 1, round(n / top * (len(SPARK) - 1)))]
        t.append(ch, style="bold #FBF6F0" if i == r.hist_at else "#AEA5B8")
    t.append("\n" + " " * 20 + " " * (r.hist_at or 0) + "▲ you\n", style="#FBF6F0")
    return t


def group_row(t: Text, code: str, name: str, g: GroupStat, bold: bool = False) -> None:
    t.append(f"  {code:<9}", style="bold" if bold else "")
    t.append(f"{fit(name, 26):<26}", style="#C3BCCC" if bold else "#AEA5B8")
    t.append(f"{g.n:>5}", style="#8A8294")
    t.append(f"  {g.pct:6.1f}", style=f"bold {pct_style(g.pct)}")
    t.append(f"  {z_text(g.z):>6}  ", style=z_style(g.z))
    bar(t, g.pct, 20)
    t.append("\n")


def _other_line(t: Text, other: tuple[str, Result | None], eur: GroupStat | None) -> None:
    name, rb = other
    t.append("  B ", style=f"bold {B_STYLE}")
    t.append(f"{fit(name, 12):<12} ", style=B_STYLE)
    if not (rb and not rb.error and rb.eur):
        t.append("not scored\n", style="#8A8294")
        return
    t.append(f"{rb.eur.pct:5.1f}  ", style=f"bold {pct_style(rb.eur.pct)}")
    bar(t, rb.eur.pct, 30)
    t.append(f"  z {z_text(rb.eur.z)}", style=z_style(rb.eur.z))
    if eur:
        diff = rb.eur.pct - eur.pct
        t.append(f"  Δ {diff:+.1f}", style=delta_style(abs(diff)))
    t.append("\n")


def card(m: Model, r: Result | None, genome: str | None,
         other: tuple[str, Result | None] | None = None) -> Text:
    d = _Doc()
    d.t.append(f"{m.trait}\n", style="bold #FBF6F0")
    d.t.append(" · ".join(x for x in (m.pgs_id, THEME_LABEL.get(m.theme, m.theme), m.method, m.build,
                                       f"{m.publication} ({m.pub_date})" if m.publication else "") if x) + "\n",
               style="#AEA5B8")
    if m.efo and m.efo.lower() != m.trait.lower():
        d.t.append(f"EFO: {m.efo}\n", style="#8A8294")

    d.title("MODEL")
    d.kv("Variants in the model", f"{m.variants:,}  (autosomal SNPs with rsID: {m.usable:,})")
    if m.eur_pct is not None:
        d.kv("European share of the training sample", f"{m.eur_pct:.0f}%")
    if m.cov_w is not None:
        d.kv("Coverage by |weight| (catalog genomes)", f"{m.cov_w:.0%}")

    d.title(f"RESULT · {genome}" if genome else "RESULT")
    if not genome:
        d.t.append("  Load a genotype file in F1.\n", style="#AEA5B8")
        return d.t
    if r is None:
        d.t.append("  Not scored yet. ", style="#AEA5B8")
        d.t.append("[Enter] score this model", style=click("screen.score_current", "bold #D4AD72"))
        d.t.append("\n")
        if other:
            _other_line(d.t, other, None)
        return d.t
    if r.error:
        d.t.append(f"  {r.error}\n", style="#8BAB4A")
        d.t.append("  [R] try again", style=click("screen.rescore", "bold #D4AD72"))
        d.t.append("\n")
        return d.t
    d.kv("Variants", f"weights {r.weights:,} · matched in the genome {r.matched:,} "
                     f"({r.matched / max(1, r.weights):.0%}) · used {r.used:,} ({r.used / max(1, r.weights):.0%})")
    d.kv("Score (mean per allele)", f"{r.score:.6g}")
    eur = r.eur
    if eur:
        d.t.append("  EUR percentile ", style="#AEA5B8")
        d.t.append(f"{eur.pct:5.1f}  ", style=f"bold {pct_style(eur.pct)}")
        bar(d.t, eur.pct, 30)
        d.t.append(f"  z {z_text(eur.z)}", style=z_style(eur.z))
        d.t.append(f"  {z_words(eur.z)}\n", style="#AEA5B8")
    if other:
        _other_line(d.t, other, eur)
    if r.hist and r.hist_at is not None:
        d.t.append_text(hist_line(r))
    d.kv("Computed", r.computed.replace("T", " "), "#8A8294")

    d.title("COMPARISON WITH 1000 GENOMES")
    d.t.append(f"  {'Group':<9}{'Description':<26}{'N':>5}  {'Pct':>6}  {'z':>6}  {'0 → 100':<20}\n", style="#8A8294")
    sps = sorted((k for k in r.groups if "/" not in k and k != "ALL"), key=lambda k: (k != "EUR", k))
    for sp in sps:
        group_row(d.t, sp + (" ★" if sp == "EUR" else ""), SUPERPOPS.get(sp, sp), r.groups[sp], bold=True)
        pops = sorted(k for k in r.groups if k.startswith(sp + "/"))
        for i, k in enumerate(pops):
            code = k.split("/", 1)[1]
            group_row(d.t, ("└─ " if i == len(pops) - 1 else "├─ ") + code, POPS.get(code, ""), r.groups[k])
    if "ALL" in r.groups:
        group_row(d.t, "ALL", "All 1000 Genomes samples", r.groups["ALL"], bold=True)
    d.t.append("\n  ★ primary reference · z: ", style="#8A8294")
    for lbl, st in (("|z|<1", "#53A94B"), ("1–2", "#B04A4A"), ("2–3", "#B04A4A"), (">3", "#8BAB4A")):
        d.t.append(lbl + " ", style=st)
    d.t.append("\n\n  A PGS percentile is not a percentile of the trait itself. Models trained on Europeans: "
               "differences between superpopulations mostly reflect allele frequencies, not the trait.\n",
               style="#B04A4A")
    return d.t


# ───────────────────────── обзор ─────────────────────────

def overview(models: list[Model], results: dict[str, Result], genome: str | None, panel: str) -> Text:
    d = _Doc()
    if not genome:
        d.t.append("No active genome.\n\nOpen a genotype file in F1 — scoring needs a loaded sample.",
                   style="#AEA5B8")
        return d.t
    ok = [r for r in results.values() if not r.error and r.eur]
    failed = sum(1 for r in results.values() if r.error)
    d.t.append(f"Results for {genome}\n", style="bold #FBF6F0")
    d.t.append(f"scored {len(ok)} of {len(models)}", style="#53A94B" if ok else "#AEA5B8")
    if failed:
        d.t.append(f" · {failed} failed", style="#8BAB4A")
    d.t.append(f"\nreference panel: {panel}\n", style="#8A8294")
    if not ok:
        d.t.append("\nEnter scores the highlighted model, G its theme, A all models.\n", style="#AEA5B8")
        return d.t

    far = sorted(ok, key=lambda r: -abs(r.eur.z or 0))[:8]
    d.title("FURTHEST FROM THE EUR MEAN")
    for r in far:
        _ov_row(d.t, r)
    by_id = {m.pgs_id: m for m in models}
    for theme, label in THEMES:
        rows = sorted((r for r in ok if by_id.get(r.pgs_id) and by_id[r.pgs_id].theme == theme),
                      key=lambda r: -r.eur.pct)
        if rows:
            d.title(label.upper())
            for r in rows:
                _ov_row(d.t, r)
    return d.t


def _ov_row(t: Text, r: Result) -> None:
    act = f"screen.select({r.pgs_id!r})"
    t.append(f"  {r.eur.pct:5.1f} ", style=click(act, f"bold {pct_style(r.eur.pct)}"))
    pos = min(int(r.eur.pct / 100 * 10), 9)
    t.append("█" * pos, style=click(act, pct_style(r.eur.pct)))
    t.append("░" * (10 - pos), style=click(act, "#433F47"))
    t.append(f" {z_text(r.eur.z):>6} ", style=click(act, z_style(r.eur.z)))
    t.append(fit(r.trait, 34) + "\n", style=click(act))


# ───────────────────────── сравнение двух геномов ─────────────────────────

def delta_style(d: float) -> str:
    return "bold #B04A4A" if d >= BIG_DELTA else "#B04A4A" if d >= 10 else "#AEA5B8"


def pair_bar(t: Text, a: float, b: float, width: int, act: str) -> None:
    """Шкала 0 → 100 с метками A и B; отрезок между ними окрашен цветом того, кто выше."""
    pa, pb = (min(int(round(p / 100 * (width - 1))), width - 1) for p in (a, b))
    lo, hi = sorted((pa, pb))
    span = A_STYLE if a > b else B_STYLE
    for i in range(width):
        if i == pa == pb:
            t.append("◆", style=click(act, "bold #FBF6F0"))
        elif i == pa:
            t.append("A", style=click(act, f"bold {A_STYLE}"))
        elif i == pb:
            t.append("B", style=click(act, f"bold {B_STYLE}"))
        elif lo < i < hi:
            t.append("━", style=click(act, span))
        else:
            t.append("·" if i == width // 2 else "░", style=click(act, "#433F47"))


def _pair_row(t: Text, p: Pair, width: int) -> None:
    act = f"screen.select({p.model.pgs_id!r})"
    t.append(f"  {fit(p.model.trait, width - 20):<{width - 20}}", style=click(act, "#C3BCCC"))
    t.append(f" {p.model.pgs_id:<10}", style=click(act, "#8A8294"))
    t.append(f" Δ{p.delta:5.1f}\n", style=click(act, delta_style(p.delta)))
    t.append("  ")
    t.append(f"{p.a.pct:5.1f} ", style=click(act, f"{'bold ' if p.a.pct >= p.b.pct else ''}{A_STYLE}"))
    pair_bar(t, p.a.pct, p.b.pct, width - 14, act)
    t.append(f" {p.b.pct:5.1f}\n", style=click(act, f"{'bold ' if p.b.pct >= p.a.pct else ''}{B_STYLE}"))


R_MIN_N = 30


def r_words(r: float, n: int) -> str:
    if n < R_MIN_N:
        return f"rough: only {n} models, a verdict needs {R_MIN_N}+"
    if r >= 0.8:
        return "nearly identical (same person or twin?)"
    if r >= 0.35:
        return "similar (close relatives?)"
    return "no sign of relatedness"


def _who(t: Text, tag: str, style: str, name: str | None, act: str | None = None) -> None:
    t.append(f"{tag} ● ", style=f"bold {style}")
    t.append(name or "—", style=click(act, f"bold {style}") if act else f"bold {style}")


def compare_view(models: list[Model], ra: dict[str, Result], rb: dict[str, Result], a: str | None,
                 b: str | None, n_choices: int, sort: str, only_big: bool, stats: tuple[list[Pair], int, int],
                 r: float | None, width: int) -> Text:
    d = _Doc()
    if not a:
        d.t.append("No active genome.\n\nAdd and load a genotype file in F1.", style="#AEA5B8")
        return d.t
    _who(d.t, "A", A_STYLE, a)
    d.t.append("  active genome (F1)\n", style="#8A8294")
    _who(d.t, "B", B_STYLE, b, "screen.next_other" if n_choices > 1 else None)
    if n_choices > 1:
        d.t.append("  [B] next", style=click("screen.next_other", "#AEA5B8"))
    d.t.append("\n")
    if not b:
        d.t.append("\nNothing to compare with: add another genotype file in F1 "
                   "(any file you have scored here also shows up).\n", style="#AEA5B8")
        return d.t

    pairs, only_a, only_b = stats
    ok_b = sum(1 for x in rb.values() if not x.error and x.eur)
    d.t.append(f"\nscored for both: {len(pairs)}", style="#53A94B" if pairs else "#AEA5B8")
    if only_a or only_b:
        d.t.append(f" · only A {only_a} · only B {only_b}", style="#AEA5B8")
    d.t.append("\n")
    missing_b = [m for m in models if (x := ra.get(m.pgs_id)) and not x.error and m.pgs_id not in rb]
    if missing_b:
        d.t.append(f"[X] score the {len(missing_b)} model(s) A has for B",
                   style=click("screen.score_other", "bold #D4AD72"))
        d.t.append("\n")
    elif not ok_b:
        d.t.append("B has no results yet: score models for A first, then X scores them for B.\n",
                   style="#AEA5B8")
    if not pairs:
        return d.t

    big = sum(1 for p in pairs if p.delta >= BIG_DELTA)
    a_up = sum(1 for p in pairs if p.a.pct > p.b.pct)
    mean_d = sum(p.delta for p in pairs) / len(pairs)
    d.t.append(f"mean |Δ| {mean_d:.1f} pct points · Δ ≥ {BIG_DELTA}: {big}\n", style="#C3BCCC")
    d.t.append("higher: ", style="#AEA5B8")
    d.t.append(f"A in {a_up}", style=A_STYLE)
    d.t.append(" · ", style="#8A8294")
    d.t.append(f"B in {len(pairs) - a_up}\n", style=B_STYLE)
    if r is not None:
        d.t.append(f"profile correlation r = {r:+.2f}", style="bold")
        d.t.append(f"  {r_words(r, len(pairs))}\n", style="#AEA5B8")

    d.t.append("\nsort [S]: ", style="#AEA5B8")
    for key, label in SORTS.items():
        d.t.append(f" {label} ", style=click(f"screen.sort({key!r})",
                                               "bold reverse #D4AD72" if key == sort else "#AEA5B8"))
    d.t.append("   [D] ", style="#AEA5B8")
    d.t.append(f" Δ ≥ {BIG_DELTA} ", style=click("screen.toggle_big", "bold reverse yellow" if only_big else "#AEA5B8"))
    d.t.append("\n")

    rows = [p for p in pairs if p.delta >= BIG_DELTA] if only_big else pairs
    if not rows:
        d.t.append(f"\nNo models with Δ ≥ {BIG_DELTA}.\n", style="#AEA5B8")
        return d.t
    if sort == "theme":
        if not only_big:
            d.title("BIGGEST DIFFERENCES")
            for p in sorted(rows, key=lambda p: -p.delta)[:8]:
                _pair_row(d.t, p, width)
        for theme, label in THEMES:
            group = [p for p in rows if p.model.theme == theme]
            if group:
                d.title(label.upper())
                for p in group:
                    _pair_row(d.t, p, width)
    else:
        key = {"delta": lambda p: -p.delta, "a": lambda p: -p.a.pct, "b": lambda p: -p.b.pct}[sort]
        d.title(f"BY {SORTS[sort]}")
        for p in sorted(rows, key=key):
            _pair_row(d.t, p, width)
    d.t.append("\n  Percentiles against 1000G Europeans. Δ is the gap between A and B in percentile points.\n",
               style="#8A8294")
    return d.t


__all__ = ["card", "compare_view", "model_prompt", "overview", "theme_header", "HIST_BINS"]
