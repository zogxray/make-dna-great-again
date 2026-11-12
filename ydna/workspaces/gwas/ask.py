"""Scientific ASK for F5 GWAS: shared chat thread + on-disk brief cache."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from ...core.gwas_match import (
    Assoc, MatchResult, assocs_for_trait, dose_label, effect_dose,
    sort_assocs_by_dose, traits_sorted,
)
from ...core.store import cache_dir
from .view import concordance_line, fmt_p, p_cut_label

_MAX_CHARS = 3800


def ask_subject(a: Assoc | None, trait: str | None, result: MatchResult) -> str:
    """Stable cache / job key for the focused Catalog row (or trait overview)."""
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
            "v2",  # prompt revision — bump to invalidate old briefs
        ))
    return "|".join((
        "trait",
        trait or "",
        str(result.p_max),
        Path(result.genome).name,
        result.catalog_date or "",
        "v2",
    ))


def ask_title(a: Assoc | None, trait: str | None) -> str:
    if a:
        return f"GWAS · {a.rsid}"[:48]
    if trait:
        return f"GWAS · {trait[:40]}"[:48]
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


def build_ask_prompt(
    result: MatchResult,
    *,
    trait: str | None,
    assoc: Assoc | None,
    genome_name: str | None = None,
) -> str:
    """Scientific brief: Catalog stats + computed dosage + forced gene explanation."""
    del genome_name  # never put the file path/name in the model prompt
    lines = [
        "Task: scientific note on one NHGRI-EBI GWAS Catalog association versus the "
        "diploid genotype in the investigated genome at the same rsID.",
        "",
        "Sources (keep distinct):",
        "- GWAS Catalog = published association row (trait, effect allele, OR/β, p, gene, study).",
        "- Investigated genome = observed genotype + the precomputed effect-allele dosage below.",
        "Use the computed dosage as given — do not re-derive or flip strand.",
        "",
        "Output: Markdown (## headings, bullets, **bold** key terms). Third person. "
        "Researchers’ note — no clinical framing, no file names, no apologies.",
        "Spend ~90% of the text on numbers and gene biology; Limits = one sentence at the end.",
        "",
        "Required sections (in order):",
        "1) ## Catalog association — trait, effect allele, OR/β, p-value, locus, "
        "study accession / PMID. Quote the numbers from the data block.",
        "2) ## Gene — mandatory if a mapped gene is listed. Name the symbol; state "
        "chromosomal locus from the data; explain what this gene product does "
        "(established molecular / developmental / physiological role). Then tie it "
        "to the Catalog trait: how variation at this locus is discussed for that "
        "phenotype in the GWAS literature (mechanism hypotheses OK if labelled as "
        "such; do not invent p-values or OR/β). If gene is missing, say so in one line "
        "and skip mechanism.",
        "3) ## Investigated genome — restate observed genotype and the computed "
        "dosage (×0/×1/×2/×?); interpret effect direction using Catalog OR/β when present.",
        "4) ## Limits — exactly one sentence (LD / chip / ancestry). No other caveats.",
        "",
        "Hard rules:",
        "- Do not invent statistics, alleles, sample sizes, PMIDs, or study accessions.",
        "- Gene function may use standard biology; association stats only from the block.",
        "- Do not pad with generic GWAS sermons; every paragraph must use a fact from below "
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
        f"- Retained Catalog association rows: {len(result.assocs):,}",
    ]

    focus_trait = trait or (assoc.trait if assoc else None)
    if focus_trait:
        n = result.trait_counts.get(focus_trait, 0)
        lines.append(f"- Focused trait label: {focus_trait} ({n} retained Catalog rows)")

    if assoc:
        lines.extend([
            "",
            "A) GWAS Catalog (published association):",
            f"- rsID: {assoc.rsid}",
            f"- DISEASE/TRAIT: {assoc.trait}",
        ])
        if assoc.mapped_trait and assoc.mapped_trait.lower() != assoc.trait.lower():
            lines.append(f"- MAPPED_TRAIT: {assoc.mapped_trait}")
        lines.extend([
            f"- Reported effect/risk allele: {assoc.risk or '—'}",
            f"- P-value: {fmt_p(assoc.p)}",
            f"- OR or β: {assoc.or_beta or '—'}",
            f"- Mapped gene (explain this gene in section 2): {assoc.gene or '—'}",
            (f"- Locus: chr{assoc.chrom}:{assoc.pos}"
             if assoc.chrom or assoc.pos else "- Locus: —"),
        ])
        bits = [x for x in (assoc.study, assoc.author, assoc.date) if x]
        if bits:
            lines.append(f"- Study: {' · '.join(bits)}")
        if assoc.pubmed:
            lines.append(f"- PubMed: {assoc.pubmed}")
        lines.append("")
        lines.append("B) Investigated genome (observed call + computed dosage):")
        lines.append(f"- rsID: {assoc.rsid}")
        lines.extend(_dose_block(assoc))

    if focus_trait:
        rows = sort_assocs_by_dose(assocs_for_trait(result, focus_trait))[:8]
        extras = []
        for a in rows:
            if assoc and a.rsid == assoc.rsid and a.study == assoc.study and a.p == assoc.p:
                continue
            d = effect_dose(a.you, a.risk)
            extras.append(
                f"- {a.rsid} Catalog effect={a.risk or '—'} p={fmt_p(a.p)} "
                f"OR/β={a.or_beta or '—'} gene={a.gene or '—'} {a.study or ''} · "
                f"genome={a.you or '—'} dose=×{dose_label(d)}"
            )
        if extras:
            lines.append("")
            lines.append("Additional retained rows (sorted by dosage, high→low):")
            lines.extend(extras)

    if not focus_trait and result.trait_counts:
        lines.append("")
        lines.append("Top traits by retained Catalog rows:")
        for t, n in traits_sorted(result)[:10]:
            lines.append(f"- {t}: {n}")

    text = "\n".join(lines)
    if len(text) <= _MAX_CHARS:
        return text
    return text[: _MAX_CHARS - 20].rstrip() + "\n…(truncated)"
