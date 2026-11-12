"""Scientific ASK for F5 GWAS: shared chat thread + on-disk brief cache."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

from ...core.gwas_match import (
    Assoc, MatchResult, assocs_for_trait, dose_label, effect_dose,
    sort_assocs_by_dose, traits_sorted,
)
from ...core.store import cache_dir
from .view import concordance_line, fmt_p, p_cut_label

# Trait-panel briefs need room for the full dosage table.
_MAX_CHARS = 7200
_PROMPT_REV = "v5"


def ask_subject(a: Assoc | None, trait: str | None, result: MatchResult) -> str:
    """Cache key: trait panel when a trait is focused; else single Catalog row."""
    focus = (trait or (a.trait if a else "") or "").strip()
    if focus:
        # One brief per trait×genome×p-cut — Oracle synthesizes the whole panel.
        return "|".join((
            "trait-panel",
            focus,
            str(result.p_max),
            Path(result.genome).name,
            result.catalog_date or "",
            _PROMPT_REV,
        ))
    if a:
        return "|".join((
            a.rsid,
            a.study or "",
            a.trait,
            a.you or "",
            a.risk or "",
            fmt_p(a.p),
            str(result.p_max),
            Path(result.genome).name,
            result.catalog_date or "",
            f"d{dose_label(effect_dose(a.you, a.risk))}",
            _PROMPT_REV,
        ))
    return "|".join((
        "trait",
        "",
        str(result.p_max),
        Path(result.genome).name,
        result.catalog_date or "",
        _PROMPT_REV,
    ))


def ask_title(a: Assoc | None, trait: str | None) -> str:
    if trait:
        return f"GWAS · {trait[:40]}"[:48]
    if a:
        return f"GWAS · {a.rsid}"[:48]
    return "GWAS · ask"


def _cache_path(subject: str) -> Path:
    digest = hashlib.sha1(subject.encode()).hexdigest()[:20]
    d = cache_dir() / "gwas_ask"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{digest}.json.gz"


def load_cached(subject: str) -> dict | None:
    path = _cache_path(subject)
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            raw = json.load(fh)
        text = (raw.get("text") or "").strip()
        if not text:
            return None
        return {
            "text": text,
            "model": raw.get("model") or "",
            "ts": raw.get("ts") or "",
            "subject": raw.get("subject") or subject,
        }
    except (OSError, ValueError, TypeError):
        return None


def save_cached(subject: str, *, text: str, model: str, ts: str) -> None:
    path = _cache_path(subject)
    payload = {"subject": subject, "text": text, "model": model, "ts": ts}
    tmp = path.with_suffix(path.suffix + ".part")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    tmp.replace(path)


def _dose_block(a: Assoc) -> list[str]:
    dose = effect_dose(a.you, a.risk)
    lines = [
        f"- Observed genotype: {a.you or '—'}",
        f"- Effect-allele dosage (computed, no strand flip): ×{dose_label(dose)}",
        f"- Concordance: {concordance_line(a)}",
    ]
    if dose == 2:
        lines.append(
            "- Reading: investigated genome is homozygous for the Catalog effect allele."
        )
    elif dose == 1:
        lines.append(
            "- Reading: investigated genome is heterozygous — one copy of the Catalog "
            "effect allele."
        )
    elif dose == 0:
        lines.append(
            "- Reading: investigated genome carries zero copies of the Catalog effect allele."
        )
    else:
        lines.append(
            "- Reading: dosage indeterminate (missing/non-ACGT alleles) — do not guess strand."
        )
    return lines


def _panel_rows(result: MatchResult, focus_trait: str) -> list[Assoc]:
    return sort_assocs_by_dose(assocs_for_trait(result, focus_trait))


def _dose_histogram(rows: list[Assoc]) -> Counter:
    c: Counter = Counter()
    for a in rows:
        c[dose_label(effect_dose(a.you, a.risk))] += 1
    return c


def _compact_row(a: Assoc) -> str:
    d = effect_dose(a.you, a.risk)
    return (
        f"{a.rsid}\tgenome={a.you or '—'}\teffect={a.risk or '—'}\t"
        f"dose=×{dose_label(d)}\tp={fmt_p(a.p)}\tOR/β={a.or_beta or '—'}\t"
        f"gene={a.gene or '—'}"
    )


def build_ask_prompt(
    result: MatchResult,
    *,
    trait: str | None,
    assoc: Assoc | None,
    genome_name: str | None = None,
) -> str:
    """Trait-panel scientific brief: all retained Catalog×genome rows for the trait."""
    del genome_name  # never put the file path/name in the model prompt
    focus_trait = (trait or (assoc.trait if assoc else None) or "").strip() or None
    panel = _panel_rows(result, focus_trait) if focus_trait else []
    hist = _dose_histogram(panel) if panel else Counter()
    n_panel = len(panel)

    lines = [
        "Task: write a scientific brief on how the INVESTIGATED GENOME sits on a TRAIT "
        "PANEL — every retained NHGRI-EBI GWAS Catalog association row for one trait that "
        "also has a genotype call (or missing call) in the investigated genome.",
        "",
        "Unit of analysis = the whole trait panel (all rows in TRAIT PANEL TABLE below). "
        "A single rsID is NEVER enough when the panel has more than one row.",
        "",
        "Sources (keep distinct):",
        "- GWAS Catalog = published association rows (trait, effect allele, OR/β, p, gene, study).",
        "- Investigated genome = observed genotypes + precomputed effect-allele dosages in the table.",
        "Use computed dosages as given — do not re-derive or flip strand.",
        "",
        "How to read dosage vs Catalog effect:",
        "- ×N = copies (0/1/2) of the Catalog effect/risk allele in the investigated genome.",
        "- When OR>1 or β>0 is listed for that row: more copies → trait in the direction "
        "the study coded (higher continuous value / higher odds of the reported outcome).",
        "- When OR<1 or β<0 is listed: more copies → opposite direction.",
        "- When OR/β is missing: state dosage only; do not invent direction.",
        "- Rows can disagree (LD, ancestry, coding). Report the pattern; do not hide conflicts.",
        "",
        "Output: Markdown (## headings, bullets, **bold** key terms). Third person. "
        "Researchers’ note — no clinical framing, no file names, no apologies.",
        "",
        "Length budget (strict — count words roughly):",
        "- ## Oracle — short (≈3–5 sentences). Not part of the 75%.",
        "- ~75% ## Investigated genome — detailed panel synthesis.",
        "- ~20% ## Catalog & gene — compact biology for the main loci.",
        "- ~5% ## Limits — one short sentence.",
        "",
        "Required sections (in this order):",
        "1) ## Oracle — plain human «so what?» for THIS TRAIT across THE ENTIRE PANEL. "
        "Must use the dose histogram (counts of ×2 / ×1 / ×0 / ×?). Must state a net "
        "tilt for the trait (toward / against / mixed relative to Catalog effect alleles "
        "where OR/β allows). Name the strongest locus cluster(s) by p-value AND by dose "
        "(gene symbols from the table). "
        f"HARD FAIL: if the panel has N>1 rows and Oracle discusses only one rsID — "
        f"rewrite. HARD FAIL: hedges, apologies, «not medical advice», «more research "
        f"needed», «cannot conclude». Commit. Heading text exactly: Oracle.",
        "2) ## Investigated genome (~75%) — longest section. Walk the PANEL, not one SNP:",
        "   (a) Restate histogram: n×2, n×1, n×0, n×? out of N.",
        "   (b) Group rows by mapped gene / locus when genes repeat; for each group give "
        "       dosages and the best p / OR/β from the table.",
        "   (c) Call out concordance and conflicts across rows (same trait, opposite tilt).",
        "   (d) Quantify: cite concrete rsIDs, dosages, OR/β, p from the table — many of them.",
        "   (e) If a CURSOR ROW is marked below, you may note it as the UI focus, but it must "
        "       not dominate; the panel does.",
        "3) ## Catalog & gene (~20%) — biology for the 2–4 most important loci in the panel "
        "(by p-value and by dose burden), not a novel for one SNP. Gene-product role vs "
        "the Catalog trait; hypotheses labelled as such. Stats only from the table.",
        "4) ## Limits (~5%) — exactly one sentence (LD / chip / ancestry / missing ×?). "
        "Nothing else.",
        "",
        "Hard rules:",
        "- Do not invent statistics, alleles, sample sizes, PMIDs, or study accessions.",
        "- Gene function may use standard biology; association stats only from the blocks.",
        "- Do not let Catalog & gene outgrow Investigated genome.",
        "- Oracle must not apologize or refuse; Investigated genome must not merely restate Oracle.",
        "- Do not collapse a multi-row panel into a single-rs essay.",
        "- Do not pad with generic GWAS sermons; every paragraph uses a fact from below "
        "or a concrete gene-function statement.",
        "",
        "Match context:",
        f"- GWAS Catalog release: {result.catalog_date or '—'}",
        f"- Association filter: {p_cut_label(result.p_max)}",
        (f"- Catalog SNPs also typed in the investigated genome: {result.matched_rs:,} "
         f"({result.chip_pct:.1f}% of {result.genome_rs:,} chip rsIDs)"
         if result.chip_pct is not None else
         f"- Catalog SNPs also typed in the investigated genome: {result.matched_rs:,} "
         f"/ {result.genome_rs:,} chip rsIDs"),
        f"- Traits with ≥1 retained Catalog row: {result.traits_hit:,}",
        f"- Retained Catalog association rows (all traits): {len(result.assocs):,}",
    ]

    if focus_trait:
        lines.append(
            f"- FOCUSED TRAIT: {focus_trait} — panel size N={n_panel} retained rows"
        )
        lines.append(
            f"- DOSE HISTOGRAM (mandatory for Oracle + Investigated genome): "
            f"×2={hist.get('2', 0)}  ×1={hist.get('1', 0)}  "
            f"×0={hist.get('0', 0)}  ×?={hist.get('?', 0)}  (of {n_panel})"
        )

    if assoc and focus_trait:
        lines.append("")
        lines.append(
            "CURSOR ROW (UI selection only — optional footnote, NOT the unit of analysis):"
        )
        lines.append(f"- rsID: {assoc.rsid}")
        lines.extend(_dose_block(assoc))
        if assoc.or_beta:
            lines.append(f"- Catalog OR/β: {assoc.or_beta}")
        lines.append(f"- Catalog p-value: {fmt_p(assoc.p)}")
        lines.append(f"- Mapped gene: {assoc.gene or '—'}")
        bits = [x for x in (assoc.study, assoc.author, assoc.date) if x]
        if bits:
            lines.append(f"- Study: {' · '.join(bits)}")
        if assoc.pubmed:
            lines.append(f"- PubMed: {assoc.pubmed}")
    elif assoc and not focus_trait:
        # Fallback: no trait panel — single-row brief.
        lines.append("")
        lines.append("PRIMARY DATA (single Catalog row — no trait panel available):")
        lines.append(f"- rsID: {assoc.rsid}")
        lines.extend(_dose_block(assoc))
        if assoc.or_beta:
            lines.append(f"- Catalog OR/β: {assoc.or_beta}")
        lines.append(f"- Catalog p-value: {fmt_p(assoc.p)}")
        lines.extend([
            f"- DISEASE/TRAIT: {assoc.trait}",
            f"- Reported effect/risk allele: {assoc.risk or '—'}",
            f"- Mapped gene: {assoc.gene or '—'}",
        ])

    if panel:
        lines.append("")
        lines.append(
            f"TRAIT PANEL TABLE — ALL {n_panel} retained rows for this trait "
            f"(sorted dosage high→low). THIS IS THE PRIMARY DATA. Cite many rows."
        )
        lines.append(
            "columns: rsID, genome genotype, Catalog effect allele, dose, p, OR/β, gene"
        )
        for a in panel:
            lines.append(_compact_row(a))

    if not focus_trait and result.trait_counts:
        lines.append("")
        lines.append("Top traits by retained Catalog rows:")
        for t, n in traits_sorted(result)[:10]:
            lines.append(f"- {t}: {n}")

    text = "\n".join(lines)
    if len(text) <= _MAX_CHARS:
        return text
    return text[: _MAX_CHARS - 20].rstrip() + "\n…(truncated)"
