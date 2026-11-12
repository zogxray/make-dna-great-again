"""F5 GWAS match texts: stats strip, trait/assoc prompts, detail card."""

from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text

from ...core.gwas_curated import THEME_LABEL
from ...core.gwas_match import Assoc, MatchResult, dose_label, effect_dose
from ...theme.styles import DIM, OK
from ..base import click, fit

# Investigated-genome genotype vs Catalog effect allele.
_DOSE2 = "bold #53A94B"       # two copies
_DOSE1 = "bold #D4AD72"       # one copy
_DOSE0 = "#AEA5B8"            # zero copies
_DOSE_UNK = DIM               # indeterminate


def dose_style(dose: int | None) -> str:
    if dose == 2:
        return _DOSE2
    if dose == 1:
        return _DOSE1
    if dose == 0:
        return _DOSE0
    return _DOSE_UNK


def concordance_line(a: Assoc) -> str:
    dose = effect_dose(a.you, a.risk)
    risk = a.risk or "—"
    you = a.you or "—"
    if dose is None:
        return (f"Catalog effect {risk} · genome {you} · dosage ? "
                f"(alleles unclear — no strand flip)")
    copies = {0: "zero", 1: "one", 2: "two"}[dose]
    return (f"Catalog effect {risk} · genome {you} · dosage {dose} "
            f"({copies} cop{'y' if dose == 1 else 'ies'} of effect allele)")

_ASK_MD_CACHE: dict[tuple[int, int], Text] = {}
_ASK_MD_CACHE_MAX = 32
_ASK_W = 68


def _bake_md(body: str, *, width: int = _ASK_W) -> Text:
    text = (body or "").rstrip() or " "
    key = (hash(text), max(28, width))
    cached = _ASK_MD_CACHE.get(key)
    if cached is not None:
        return cached
    console = Console(
        width=max(28, width),
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        legacy_windows=False,
    )
    with console.capture() as cap:
        console.print(Padding(Markdown(text), (0, 0, 0, 0)), end="")
    out = Text.from_ansi(cap.get())
    if len(_ASK_MD_CACHE) >= _ASK_MD_CACHE_MAX:
        _ASK_MD_CACHE.pop(next(iter(_ASK_MD_CACHE)), None)
    _ASK_MD_CACHE[key] = out
    return out

TRAIT_TABS = (("curated", "CUR"), ("browse", "ALL"))


def trait_tab_bar(tab: str) -> Text:
    t = Text(no_wrap=True)
    for key, lab in TRAIT_TABS:
        style = "bold reverse #D4AD72" if tab == key else "#AEA5B8"
        t.append(f" {lab} ", style=click(f"screen.list_tab({key!r})", style))
    t.append(" L", style="#8A8294")
    return t


def theme_header(theme: str, n: int) -> Text:
    lab = THEME_LABEL.get(theme, theme)
    t = Text(no_wrap=True)
    t.append(f"── {lab} ", style="#8A8294")
    t.append(f"{n}", style="#D4AD72")
    t.append(" ──", style="#8A8294")
    return t


def p_cut_label(p_max: float | None) -> str:
    if p_max is None:
        return "any p"
    if p_max >= 1e-3:
        return f"p≤{p_max:g}"
    return f"p≤{p_max:.0e}"


def fmt_p(p: float | None) -> str:
    if p is None:
        return "n/a"
    if p == 0:
        return "0"
    if p < 1e-3:
        return f"{p:.1e}"
    return f"{p:g}"


def stats_text(result: MatchResult | None, *, genome_name: str | None,
               catalog_date: str, problem: str = "") -> Text:
    t = Text()
    if problem:
        t.append(problem, style="#B04A4A")
        return t
    if result is None:
        t.append("Matching genome to GWAS Catalog…", style=DIM)
        return t
    gname = genome_name or Path_name(result.genome)
    t.append(f"{gname}", style="bold #FBF6F0")
    if catalog_date:
        t.append(f"  ·  catalog {catalog_date}", style=DIM)
    t.append("\n")
    pct = result.chip_pct
    pct_s = f"{pct:.1f}%" if pct is not None else "—"
    cells = [
        (f"{result.catalog_rs:,}", "catalog SNP"),
        (f"{result.matched_rs:,}", "in genome"),
        (f"{result.traits_hit:,}", "traits hit"),
        (pct_s, "of chip rsIDs"),
    ]
    for i, (val, lab) in enumerate(cells):
        if i:
            t.append("   ", style=DIM)
        t.append(val, style="bold #D4AD72")
        t.append(f" {lab}", style=DIM)
    t.append("\n")
    t.append(f"filter {p_cut_label(result.p_max)}  ·  {result.genome_rs:,} rsIDs on chip  ·  "
             f"{len(result.assocs):,} association rows", style=DIM)
    return t


def Path_name(s: str) -> str:
    from pathlib import Path
    try:
        return Path(s).name
    except (TypeError, ValueError):
        return s or "—"


def trait_prompt(trait: str, n: int, width: int = 36) -> Text:
    t = Text(no_wrap=True)
    t.append(fit(trait, max(8, width - 8)), style="bold")
    t.append(f" {n:>5,}", style="#D4AD72")
    return t


def assoc_prompt(a: Assoc, width: int = 52) -> Text:
    """rsID · genome gt (colored) · Catalog risk · dose · p · gene."""
    dose = effect_dose(a.you, a.risk)
    t = Text(no_wrap=True)
    t.append(f"{a.rsid:<11} ", style="")
    you = (a.you or "—")[:4].ljust(4)
    t.append(you, style=dose_style(dose))
    t.append(f" {(a.risk or '—'):<4} ", style=DIM)
    t.append(f"×{dose_label(dose)}", style=dose_style(dose))
    t.append(f" {fmt_p(a.p):>8}", style="")
    used = 11 + 1 + 4 + 1 + 4 + 1 + 2 + 1 + 8  # approx columns before gene
    room = max(6, width - used - 1)
    t.append(" " + fit(a.gene or a.mapped_trait or a.trait, room), style=DIM)
    return t


def assoc_legend() -> Text:
    t = Text(no_wrap=True)
    t.append("you ", style=DIM)
    t.append("×2", style=_DOSE2)
    t.append(" ", style=DIM)
    t.append("×1", style=_DOSE1)
    t.append(" ", style=DIM)
    t.append("×0", style=_DOSE0)
    t.append(" ", style=DIM)
    t.append("×?", style=_DOSE_UNK)
    t.append("  = copies of Catalog effect allele", style=DIM)
    return t


DETAIL_TABS = ("hit", "ask")


def detail_tab_bar(tab: str) -> Text:
    t = Text(no_wrap=True)
    for key, lab in (("hit", "HIT"), ("ask", "ASK")):
        style = "bold reverse #D4AD72" if tab == key else "#AEA5B8"
        t.append(f" {lab} ", style=click(f"screen.detail_tab({key!r})", style))
        t.append(" ")
    return t


def detail_text(a: Assoc | None, result: MatchResult | None) -> Text:
    t = Text()
    if a is None:
        t.append("Select a trait, then an association.\n", style=DIM)
        return t
    t.append(f"{a.rsid}", style="bold #FBF6F0")
    if a.gene:
        t.append(f"  ·  {a.gene}", style="#D4AD72")
    t.append("\n")
    loc = " · ".join(x for x in (f"chr{a.chrom}" if a.chrom else "", a.pos) if x)
    if loc:
        t.append(f"{loc}\n", style=DIM)
    dose = effect_dose(a.you, a.risk)
    t.append("genome ", style=DIM)
    t.append(a.you or "—", style=dose_style(dose))
    t.append("   Catalog effect ", style=DIM)
    t.append(a.risk or "—", style="bold")
    t.append("   dose ", style=DIM)
    t.append(f"×{dose_label(dose)}", style=dose_style(dose))
    t.append("\n")
    t.append(concordance_line(a) + "\n", style=DIM)
    t.append("p ", style=DIM)
    t.append(fmt_p(a.p), style=OK if a.p is not None and a.p <= 5e-8 else "")
    if a.or_beta:
        t.append(f"   OR/β {a.or_beta}", style=DIM)
    t.append("\n\n")
    t.append("Trait\n", style="bold #D4AD72")
    t.append(f"  {a.trait}\n")
    if a.mapped_trait and a.mapped_trait.lower() != a.trait.lower():
        t.append(f"  mapped: {a.mapped_trait}\n", style=DIM)
    t.append("\nStudy\n", style="bold #D4AD72")
    bits = [x for x in (a.study, a.author, a.date, f"PMID {a.pubmed}" if a.pubmed else "") if x]
    t.append("  " + " · ".join(bits) + "\n")
    t.append("\n")
    t.append("[C] ", style=click("screen.copy_rsid", "bold #D4AD72"))
    t.append("copy rsID\n", style=click("screen.copy_rsid"))
    if a.pubmed:
        t.append("[B] ", style=click("screen.open_pubmed", "bold #D4AD72"))
        t.append("open PubMed\n", style=click("screen.open_pubmed"))
    t.append("\n")
    t.append("[A] ", style=click("screen.ask_chat", "bold #D4AD72"))
    t.append("scientific note (ASK)\n", style=click("screen.ask_chat"))
    return t


def ask_tab_text(
    a: Assoc | None,
    result: MatchResult | None,
    *,
    trait: str | None,
    genome_name: str | None = None,
    cache: dict | None = None,
    busy: bool = False,
    draft: str = "",
    model_hint: str = "",
    width: int = _ASK_W,
) -> Text:
    """ASK tab: run in shared GWAS chat; show brief inline (like F6 SUMMARY)."""
    t = Text()
    if result is None:
        t.append("Match the genome first, then ask.\n", style=DIM)
        return t
    w = max(28, width)
    if busy:
        t.append(" Generating note…\n", style="bold #D4AD72")
        t.append(f" {(model_hint or 'waiting for Gemini…')}\n", style=DIM)
        if not draft:
            t.append(" First token can take 10–60s.\n", style="#71697A")
        else:
            t.append("\n")
            t.append_text(_bake_md(draft, width=w))
        return t
    if cache and (cache.get("text") or "").strip():
        model = cache.get("model") or "model"
        ts = (cache.get("ts") or "")[:10]
        head = " · ".join(x for x in (model, ts, "GWAS · F5 chat") if x)
        t.append(f" {head}\n", style=DIM)
        t.append(" ")
        t.append(" Refresh ", style=click("screen.ask_chat(True)", "bold reverse #D4AD72"))
        t.append("\n\n", style=DIM)
        t.append_text(_bake_md(str(cache.get("text") or ""), width=w))
        return t

    t.append(" Scientific note\n\n", style="bold #FBF6F0")
    t.append(" ")
    t.append(" Ask ", style=click("screen.ask_chat()", "bold reverse #D4AD72"))
    t.append("  → shared chat «GWAS · F5»\n\n", style=DIM)
    t.append("Grounding\n", style="bold #D4AD72")
    t.append(f"  · Catalog {result.catalog_date or '—'} · {p_cut_label(result.p_max)}\n",
             style=DIM)
    focus = trait or (a.trait if a else None)
    if focus:
        t.append(f"  · trait {focus}\n", style=DIM)
    if a:
        t.append(f"  · Catalog {a.rsid} effect {a.risk or '—'} p {fmt_p(a.p)}\n",
                 style=DIM)
        t.append(f"  · investigated genome gt {a.you or '—'}\n", style=DIM)
    else:
        t.append("  · select an association for a focal row\n", style="#71697A")
    t.append("\nA = Catalog row · B = investigated genome · facts first.\n",
             style="#8A8294")
    t.append("\n[A] ", style=click("screen.ask_chat", "bold #D4AD72"))
    t.append("ask\n", style=click("screen.ask_chat"))
    return t

def empty_state(kind: str) -> Text:
    messages = {
        "genome": "No active genome.\n\nOpen a genotype file in F1.",
        "catalog": "GWAS Catalog is not ready.\n\nIn F2 · GWAS Catalog press D to download\n"
                   "(or P to an existing folder).",
        "empty": "No associations matched under this filter.",
    }
    return Text(messages.get(kind, kind), style="#AEA5B8")


__all__ = [
    "DETAIL_TABS", "TRAIT_TABS", "ask_tab_text", "assoc_legend", "assoc_prompt",
    "concordance_line", "detail_tab_bar", "detail_text", "dose_style",
    "empty_state", "fmt_p", "p_cut_label", "stats_text", "theme_header",
    "trait_prompt", "trait_tab_bar",
]
