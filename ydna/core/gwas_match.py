"""Match an F1 genome against the local GWAS Catalog associations table (F2).

Streams the associations TSV once, keeps only rows whose rsIDs appear in the
genome. Optional on-disk cache keyed by genome path + catalog mtime + p-cut.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .genome import Genome
from .jobs import Job
from .sources import GwasCatalog
from .store import cache_dir

_RS = re.compile(r"(rs\d+)", re.I)
_P_CUTS = (None, 5e-8, 1e-5, 1e-3)  # None = no cut


@dataclass
class Assoc:
    rsid: str
    you: str
    risk: str
    p: float | None
    or_beta: str
    trait: str
    mapped_trait: str
    gene: str
    chrom: str
    pos: str
    study: str
    pubmed: str
    author: str
    date: str


@dataclass
class MatchResult:
    genome: str
    catalog: str
    catalog_date: str
    genome_rs: int
    catalog_rs: int
    matched_rs: int
    traits_hit: int
    p_max: float | None
    assocs: list[Assoc] = field(default_factory=list)
    # trait → count of matched association rows
    trait_counts: dict[str, int] = field(default_factory=dict)

    @property
    def chip_pct(self) -> float | None:
        if not self.genome_rs:
            return None
        return 100.0 * self.matched_rs / self.genome_rs


def genome_rs_map(g: Genome) -> dict[str, str]:
    """rsID → genotype string (first seen wins)."""
    out: dict[str, str] = {}
    for c in g.chroms.values():
        if not c.calls_loaded:
            continue
        for rid, gt in zip(c.ids, c.gts):
            if rid.startswith("rs") and rid not in out:
                out[rid] = gt
    return out


def _parse_p(raw: str) -> float | None:
    s = (raw or "").strip()
    if not s or s.upper() in ("NA", "NR", "NS"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _risk_allele(strongest: str, rsid: str) -> str:
    """'rs128494-C' → 'C'; bare allele or empty if unknown."""
    s = (strongest or "").strip()
    if not s:
        return ""
    if "-" in s:
        left, _, right = s.rpartition("-")
        if right and (not left or rsid.lower() in left.lower() or left.lower().startswith("rs")):
            return right.strip()
    if len(s) <= 3 and s.upper() == s:
        return s
    return ""


def _norm_rs(rsid: str) -> str:
    s = rsid.strip()
    if s.lower().startswith("rs"):
        return "rs" + s[2:]
    return s


def _rsids_from_cell(cell: str) -> list[str]:
    return [_norm_rs(m.group(1)) for m in _RS.finditer(cell or "")]


def _cache_file(genome: Path, assoc: Path, p_max: float | None) -> Path:
    key = f"{genome.resolve()}|{assoc.resolve()}|{assoc.stat().st_mtime_ns}|{p_max}"
    digest = hashlib.sha1(key.encode()).hexdigest()[:20]
    d = cache_dir() / "gwas_match"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{digest}.json.gz"


def catalog_date(folder: Path) -> str:
    man = folder / "gwas-download-manifest.json"
    if not man.is_file():
        return ""
    try:
        return str(json.loads(man.read_text(encoding="utf-8")).get("catalog_date") or "")
    except (OSError, ValueError, TypeError):
        return ""


# Back-compat for early imports.
_catalog_date = catalog_date


def load_cached(path: Path) -> MatchResult | None:
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            raw = json.load(fh)
        assocs = [Assoc(**a) for a in raw.get("assocs") or []]
        return MatchResult(
            genome=raw.get("genome") or "",
            catalog=raw.get("catalog") or "",
            catalog_date=raw.get("catalog_date") or "",
            genome_rs=int(raw.get("genome_rs") or 0),
            catalog_rs=int(raw.get("catalog_rs") or 0),
            matched_rs=int(raw.get("matched_rs") or 0),
            traits_hit=int(raw.get("traits_hit") or 0),
            p_max=raw.get("p_max"),
            assocs=assocs,
            trait_counts=dict(raw.get("trait_counts") or {}),
        )
    except (OSError, ValueError, TypeError, KeyError):
        return None


def save_cached(path: Path, result: MatchResult) -> None:
    payload = {
        "genome": result.genome,
        "catalog": result.catalog,
        "catalog_date": result.catalog_date,
        "genome_rs": result.genome_rs,
        "catalog_rs": result.catalog_rs,
        "matched_rs": result.matched_rs,
        "traits_hit": result.traits_hit,
        "p_max": result.p_max,
        "trait_counts": result.trait_counts,
        "assocs": [asdict(a) for a in result.assocs],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    tmp.replace(path)


def match_genome(
    folder: Path,
    genome: Genome,
    *,
    p_max: float | None = 5e-8,
    job: Job | None = None,
    use_cache: bool = True,
) -> MatchResult:
    """Match genome rsIDs to GWAS Catalog associations under optional p-value cut."""
    src = GwasCatalog()
    assoc_path = src.associations_tsv(folder)
    if assoc_path is None:
        raise RuntimeError("GWAS Catalog associations table not found — download in F2")

    cache = _cache_file(genome.path, assoc_path, p_max)
    if use_cache:
        hit = load_cached(cache)
        if hit is not None:
            return hit

    rs_map = genome_rs_map(genome)
    if not rs_map:
        raise RuntimeError("genome has no rsIDs loaded")
    # lower-case key for matching; keep original genotype via rs_map lookup by normalized
    rs_lookup = {_norm_rs(k).lower(): (k, v) for k, v in rs_map.items()}

    if job:
        job.set_stage("scanning GWAS associations", max(1, assoc_path.stat().st_size))

    catalog_rs: set[str] = set()
    matched_rs: set[str] = set()
    trait_counts: Counter[str] = Counter()
    assocs: list[Assoc] = []
    bytes_read = 0

    with assoc_path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        header = fh.readline()
        bytes_read += len(header.encode("utf-8", errors="replace"))
        cols = [c.strip() for c in header.rstrip("\n\r").split("\t")]
        idx = {c: i for i, c in enumerate(cols)}

        def col(*names: str) -> int | None:
            for n in names:
                if n in idx:
                    return idx[n]
            return None

        i_snps = col("SNPS", "SNP", "STRONGEST SNP-RISK ALLELE")
        i_strong = col("STRONGEST SNP-RISK ALLELE")
        i_trait = col("DISEASE/TRAIT", "MAPPED_TRAIT")
        i_mapped = col("MAPPED_TRAIT", "DISEASE/TRAIT")
        i_gene = col("MAPPED_GENE", "REPORTED GENE(S)")
        i_p = col("P-VALUE", "PVALUE")
        i_or = col("OR or BETA", "OR", "BETA")
        i_chr = col("CHR_ID", "CHR")
        i_pos = col("CHR_POS", "BP")
        i_study = col("STUDY ACCESSION", "STUDY")
        i_pubmed = col("PUBMEDID")
        i_author = col("FIRST AUTHOR")
        i_date = col("DATE")
        if i_snps is None or i_trait is None:
            raise RuntimeError(f"{assoc_path.name}: missing SNPS / DISEASE/TRAIT columns")

        n_lines = 0
        for line in fh:
            n_lines += 1
            bytes_read += len(line.encode("utf-8", errors="replace"))
            if job and n_lines % 50_000 == 0:
                job.check()
                job.progress(min(bytes_read, assoc_path.stat().st_size))
            parts = line.rstrip("\n\r").split("\t")
            if len(parts) <= i_snps:
                continue
            snps_cell = parts[i_snps]
            rsids = _rsids_from_cell(snps_cell)
            if not rsids and i_strong is not None and i_strong < len(parts):
                rsids = _rsids_from_cell(parts[i_strong])
            if not rsids:
                continue
            for raw_rs in rsids:
                catalog_rs.add(raw_rs.lower())
            pval = _parse_p(parts[i_p]) if i_p is not None and i_p < len(parts) else None
            if p_max is not None and (pval is None or pval > p_max):
                continue
            trait = (parts[i_trait] if i_trait < len(parts) else "").strip() or "unknown"
            mapped = (parts[i_mapped] if i_mapped is not None and i_mapped < len(parts) else "").strip()
            gene = (parts[i_gene] if i_gene is not None and i_gene < len(parts) else "").strip()
            strongest = (parts[i_strong] if i_strong is not None and i_strong < len(parts) else "")
            chrom = (parts[i_chr] if i_chr is not None and i_chr < len(parts) else "").strip()
            pos = (parts[i_pos] if i_pos is not None and i_pos < len(parts) else "").strip()
            study = (parts[i_study] if i_study is not None and i_study < len(parts) else "").strip()
            pubmed = (parts[i_pubmed] if i_pubmed is not None and i_pubmed < len(parts) else "").strip()
            author = (parts[i_author] if i_author is not None and i_author < len(parts) else "").strip()
            date = (parts[i_date] if i_date is not None and i_date < len(parts) else "").strip()
            or_beta = (parts[i_or] if i_or is not None and i_or < len(parts) else "").strip()

            for raw_rs in rsids:
                key = _norm_rs(raw_rs).lower()
                hit = rs_lookup.get(key)
                if not hit:
                    continue
                canon, you = hit
                matched_rs.add(key)
                trait_counts[trait] += 1
                assocs.append(Assoc(
                    rsid=canon,
                    you=you,
                    risk=_risk_allele(strongest, canon),
                    p=pval,
                    or_beta=or_beta,
                    trait=trait,
                    mapped_trait=mapped,
                    gene=gene,
                    chrom=chrom,
                    pos=pos,
                    study=study,
                    pubmed=pubmed,
                    author=author,
                    date=date,
                ))

    if job:
        job.progress(assoc_path.stat().st_size)

    # Prefer smaller p first within a trait when listing.
    assocs.sort(key=lambda a: (a.trait.lower(), a.p is None, a.p if a.p is not None else 1.0, a.rsid))

    result = MatchResult(
        genome=str(genome.path),
        catalog=str(assoc_path),
        catalog_date=catalog_date(folder),
        genome_rs=len(rs_map),
        catalog_rs=len(catalog_rs),
        matched_rs=len(matched_rs),
        traits_hit=len(trait_counts),
        p_max=p_max,
        assocs=assocs,
        trait_counts=dict(trait_counts),
    )
    if use_cache:
        try:
            save_cached(cache, result)
        except OSError:
            pass
    return result


def traits_sorted(result: MatchResult) -> list[tuple[str, int]]:
    return sorted(result.trait_counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))


def assocs_for_trait(result: MatchResult, trait: str) -> list[Assoc]:
    return [a for a in result.assocs if a.trait == trait]


def effect_dose(you: str, risk: str) -> int | None:
    """Copies of Catalog effect/risk allele in the investigated genotype.

    Returns 0 / 1 / 2, or None if alleles are missing / non-ACGT / ambiguous.
    No strand flip — compares letters as stored.
    """
    r = (risk or "").strip().upper()
    g = (you or "").strip().upper().replace("/", "").replace(" ", "")
    if not r or not g or r in {"—", "-", "?", ".", "NA", "NR"}:
        return None
    if r not in {"A", "C", "G", "T"}:
        return None
    if len(g) == 2 and all(c in "ACGT" for c in g):
        return (g[0] == r) + (g[1] == r)
    if len(g) == 1 and g in "ACGT":
        return int(g == r)
    return None


def dose_label(dose: int | None) -> str:
    if dose is None:
        return "?"
    return str(dose)


def sort_assocs_by_dose(rows: list[Assoc]) -> list[Assoc]:
    """Prefer higher effect-allele dosage, then smaller p, then rsID."""
    def key(a: Assoc):
        d = effect_dose(a.you, a.risk)
        return (
            d is None,
            -(d if d is not None else -1),
            a.p is None,
            a.p if a.p is not None else 1.0,
            a.rsid,
        )
    return sorted(rows, key=key)


P_CUTS = _P_CUTS

__all__ = [
    "Assoc", "MatchResult", "P_CUTS", "assocs_for_trait", "catalog_date",
    "dose_label", "effect_dose", "genome_rs_map", "match_genome",
    "sort_assocs_by_dose", "traits_sorted",
]
