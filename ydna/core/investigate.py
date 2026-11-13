"""Point queries for agent investigation (search → trait → rs).

Not a fixed profile pack: the host agent chooses the next query.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .gwas_match import Assoc, MatchResult, effect_dose, match_genome
from .store import Store


def _resolve_genome(store: Store, genome: Path | None) -> Path:
    from . import genome_db

    path = genome or genome_db.active_path()
    if path is None:
        raise RuntimeError("no_genome")
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"no_genome:{path}")
    return path


def _resolve_gwas(store: Store, gwas_dir: Path | None) -> Path:
    folder = gwas_dir or store.path("gwas_catalog")
    if folder is None or not Path(folder).is_dir():
        raise RuntimeError("no_gwas")
    return Path(folder).resolve()


def ensure_match(
    store: Store,
    *,
    genome: Path | None = None,
    gwas_dir: Path | None = None,
    p_max: float | None = 5e-8,
    use_cache: bool = True,
) -> MatchResult:
    from . import genome_db

    path = _resolve_genome(store, genome)
    folder = _resolve_gwas(store, gwas_dir)
    g = genome_db.get(path, store=store, set_active_flag=False)
    return match_genome(folder, g, p_max=p_max, job=None, use_cache=use_cache)


def _tokens(query: str) -> list[str]:
    return [t for t in re.split(r"[\s,;|/]+", (query or "").strip().lower()) if t]


def _hay(a: Assoc) -> str:
    return " ".join([
        a.trait or "", a.mapped_trait or "", a.gene or "", a.rsid or "",
        a.study or "", a.pubmed or "",
    ]).lower()


def gwas_search(
    result: MatchResult,
    query: str,
    *,
    limit: int = 40,
) -> dict[str, Any]:
    """Rank traits on the matched chip by free-text query."""
    toks = _tokens(query)
    if not toks:
        raise RuntimeError("empty_query")

    # trait → (score, n_assocs, genes, rsids sample)
    scores: dict[str, float] = defaultdict(float)
    counts: Counter[str] = Counter()
    genes: dict[str, set[str]] = defaultdict(set)
    rs_sample: dict[str, set[str]] = defaultdict(set)

    for a in result.assocs:
        h = _hay(a)
        hit = 0.0
        for t in toks:
            if t in h:
                hit += 1.0
                if a.trait.lower() == t or t in (a.trait or "").lower():
                    hit += 0.5
        if hit <= 0:
            continue
        trait = a.trait
        scores[trait] += hit
        counts[trait] += 1
        if a.gene:
            for g in re.split(r"[,;]", a.gene):
                g = g.strip()
                if g:
                    genes[trait].add(g)
        rs_sample[trait].add(a.rsid)

    ranked = sorted(scores.keys(), key=lambda t: (-scores[t], -counts[t], t.lower()))
    rows = []
    for trait in ranked[: max(1, limit)]:
        rows.append({
            "trait": trait,
            "score": round(scores[trait], 2),
            "assocs": counts[trait],
            "genes": sorted(genes[trait])[:12],
            "rs_sample": sorted(rs_sample[trait])[:8],
        })

    next_queries = []
    if rows:
        next_queries.append(f'trait:{rows[0]["trait"]}')
        if rows[0]["rs_sample"]:
            next_queries.append(f'rs:{rows[0]["rs_sample"][0]}')
        if rows[0]["genes"]:
            next_queries.append(rows[0]["genes"][0])

    return {
        "query": query,
        "p_max": result.p_max,
        "genome": result.genome,
        "hits": len(rows),
        "traits": rows,
        "open_questions": [
            "Which trait is the real lead?",
            "Do you have a lab value for this marker?",
            "Any supplement already in use?",
        ],
        "next_queries": next_queries,
        "need_from_user": [],
        "hint": "Follow a trait with gwas.trait, then genome.rs on interesting rsIDs.",
    }


def gwas_trait(
    result: MatchResult,
    trait: str,
    *,
    limit: int = 80,
) -> dict[str, Any]:
    """Association rows for one trait name (exact, case-insensitive)."""
    want = (trait or "").strip().lower()
    if not want:
        raise RuntimeError("empty_query")

    rows = [a for a in result.assocs if (a.trait or "").lower() == want]
    if not rows:
        # soft: substring unique traits
        soft = sorted({
            a.trait for a in result.assocs if want in (a.trait or "").lower()
        }, key=str.lower)
        return {
            "trait": trait,
            "exact": False,
            "assocs": [],
            "candidates": soft[:30],
            "open_questions": ["Pick an exact trait name from candidates."],
            "next_queries": [f"trait:{c}" for c in soft[:5]],
            "need_from_user": [],
            "hint": "No exact trait match — try gwas.search or a candidate below.",
        }

    # Prefer smaller p then higher dose
    def sort_key(a: Assoc):
        d = effect_dose(a.you, a.risk)
        return (
            a.p is None,
            a.p if a.p is not None else 1.0,
            d is None,
            -(d if d is not None else -1),
            a.rsid,
        )

    ordered = sorted(rows, key=sort_key)
    out_rows = []
    for a in ordered[: max(1, limit)]:
        out_rows.append({
            "rsid": a.rsid,
            "you": a.you,
            "risk": a.risk,
            "dose": effect_dose(a.you, a.risk),
            "p": a.p,
            "or_beta": a.or_beta,
            "gene": a.gene,
            "chrom": a.chrom,
            "pos": a.pos,
            "study": a.study,
            "pubmed": a.pubmed,
            "mapped_trait": a.mapped_trait,
        })

    rsids = [r["rsid"] for r in out_rows[:15]]
    return {
        "trait": rows[0].trait,
        "exact": True,
        "assocs_total": len(rows),
        "assocs_returned": len(out_rows),
        "assocs": out_rows,
        "open_questions": [
            "Does dosage pattern look consistent across studies?",
            "Want a blood marker for this trait family?",
        ],
        "next_queries": [f"rs:{r}" for r in rsids[:5]],
        "need_from_user": [],
        "hint": "Pull genotypes with genome.rs on next_queries rsIDs.",
    }


def pgs_search(query: str, *, limit: int = 40, genome: Path | None = None) -> dict[str, Any]:
    """Search local PGS models by trait/EFO/id; note score status for genome."""
    from . import db
    from . import pgs as pgs_mod
    from . import genome_db

    toks = _tokens(query)
    if not toks:
        raise RuntimeError("empty_query")

    rows = db.fetchall(
        "SELECT pgs_id, theme, trait, efo, variants, usable, status "
        "FROM pgs.models"
    )
    scored: dict[str, Any] = {}
    path = genome or genome_db.active_path()
    if path is not None:
        scored = pgs_mod.load_results(Path(path))

    hits = []
    for pid, theme, trait, efo, variants, usable, status in rows:
        blob = f"{pid} {theme} {trait} {efo}".lower()
        score = sum(1.0 for t in toks if t in blob)
        if score <= 0:
            continue
        res = scored.get(pid)
        entry = {
            "pgs_id": pid,
            "theme": theme,
            "trait": trait,
            "efo": efo,
            "variants": variants,
            "usable": usable,
            "status": status,
            "score_match": score,
            "scored": False,
            "pct_eur": None,
            "sample_score": None,
        }
        if res is not None and not res.error:
            entry["scored"] = True
            entry["sample_score"] = res.score
            entry["pct_eur"] = res.eur.pct if res.eur else None
            entry["used"] = res.used
        hits.append(entry)

    hits.sort(key=lambda r: (-r["score_match"], 0 if r["status"] == "ok" else 1, r["pgs_id"]))
    hits = hits[: max(1, limit)]

    need = []
    if hits and not any(h["scored"] for h in hits):
        need.append("Run F4 scoring for promising PGS ids (or ask to score later).")

    return {
        "query": query,
        "hits": len(hits),
        "models": hits,
        "open_questions": [
            "Which PGS id is on-trait vs intake/proxy?",
            "Is status=ok and worth scoring?",
        ],
        "next_queries": [h["pgs_id"] for h in hits[:5]],
        "need_from_user": need,
        "hint": "Usable models need status=ok; scored=true means percentile is available.",
    }


def genome_rs(
    store: Store,
    rsids: Iterable[str],
    *,
    genome: Path | None = None,
) -> dict[str, Any]:
    """Look up genotypes for rsIDs on the active/given genome."""
    from . import db, genome_db

    path = _resolve_genome(store, genome)
    row = genome_db.file_row(path)
    if row is None:
        raise RuntimeError(f"no_genome:{path}")
    file_id = int(row[0])

    want: list[str] = []
    for raw in rsids:
        s = (raw or "").strip()
        if not s:
            continue
        if not s.lower().startswith("rs"):
            s = "rs" + s
        want.append(s)
    # dedupe preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for s in want:
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        ordered.append(s)
    if not ordered:
        raise RuntimeError("empty_query")

    found: dict[str, dict[str, Any]] = {}
    for i in range(0, len(ordered), 400):
        chunk = ordered[i:i + 400]
        placeholders = ",".join(["?"] * len(chunk))
        rows = db.fetchall(
            "SELECT rsid, chrom, pos, gt FROM genome.calls "
            f"WHERE file_id = ? AND lower(rsid) IN ({placeholders})",
            [file_id, *[c.lower() for c in chunk]],
        )
        for rsid, chrom, pos, gt in rows:
            found[str(rsid).lower()] = {
                "rsid": rsid, "chrom": chrom, "pos": int(pos), "gt": gt,
            }

    calls = []
    missing = []
    for s in ordered:
        hit = found.get(s.lower())
        if hit:
            calls.append(hit)
        else:
            missing.append(s)

    return {
        "genome": str(path),
        "requested": len(ordered),
        "found": len(calls),
        "missing": missing,
        "calls": calls,
        "open_questions": [
            "Combine these genotypes with gwas.trait risk alleles.",
        ],
        "next_queries": [],
        "need_from_user": [],
        "hint": "Missing rsIDs are absent from this kit (or not ingested).",
    }
