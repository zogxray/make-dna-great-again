"""DuckDB index matcher must match the Python TSV scanner bit-for-bit."""

from __future__ import annotations

from pathlib import Path

import pytest

from ydna.core import gwas_match as gm


# Column names match the GWAS Catalog FTP associations dump.
HEADER = (
    "DATE ADDED TO CATALOG\tPUBMEDID\tFIRST AUTHOR\tDATE\tJOURNAL\tLINK\tSTUDY\t"
    "DISEASE/TRAIT\tINITIAL SAMPLE SIZE\tREPLICATION SAMPLE SIZE\tREGION\t"
    "CHR_ID\tCHR_POS\tREPORTED GENE(S)\tMAPPED_GENE\tUPSTREAM_GENE_ID\t"
    "DOWNSTREAM_GENE_ID\tSNP_GENE_IDS\tUPSTREAM_GENE_DISTANCE\t"
    "DOWNSTREAM_GENE_DISTANCE\tSTRONGEST SNP-RISK ALLELE\tSNPS\tMERGED\t"
    "SNP_ID_CURRENT\tCONTEXT\tINTERGENIC\tRISK ALLELE FREQUENCY\tP-VALUE\t"
    "PVALUE_MLOG\tP-VALUE (TEXT)\tOR or BETA\t95% CI (TEXT)\t"
    "PLATFORM [SNPS PASSING QC]\tCNV\tMAPPED_TRAIT\tMAPPED_TRAIT_URI\t"
    "STUDY ACCESSION\tGENOTYPING TECHNOLOGY\n"
)


def _row(
    *,
    trait: str,
    snps: str,
    strong: str,
    p: str,
    or_beta: str = "1.2",
    gene: str = "GENE1",
    chrom: str = "1",
    pos: str = "100",
    study: str = "GCST1",
    pubmed: str = "1",
    author: str = "Doe",
    date: str = "2020-01-01",
    mapped: str = "mapped",
) -> str:
    cols = [""] * 38
    cols[1] = pubmed
    cols[2] = author
    cols[3] = date
    cols[7] = trait
    cols[11] = chrom
    cols[12] = pos
    cols[14] = gene
    cols[20] = strong
    cols[21] = snps
    cols[27] = p
    cols[30] = or_beta
    cols[34] = mapped
    cols[36] = study
    return "\t".join(cols) + "\n"


class _Chrom:
    calls_loaded = True

    def __init__(self, ids: list[str], gts: list[str]) -> None:
        self.ids = ids
        self.gts = gts


class _Genome:
    def __init__(self, path: Path, rs_map: dict[str, str]) -> None:
        self.path = path
        ids = list(rs_map)
        self.chroms = {"1": _Chrom(ids, [rs_map[k] for k in ids])}


def _fingerprint(r: gm.MatchResult):
    return (
        r.catalog_rs,
        r.matched_rs,
        r.traits_hit,
        len(r.assocs),
        sorted(r.trait_counts.items()),
        [
            (a.rsid, a.you, a.risk, a.p, a.or_beta, a.trait, a.mapped_trait,
             a.gene, a.chrom, a.pos, a.study, a.pubmed, a.author, a.date)
            for a in r.assocs
        ],
    )


def test_duckdb_matches_python_on_synthetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    folder = tmp_path / "gwas"
    folder.mkdir()
    assoc = folder / "gwas-catalog-download-associations-alt-full.tsv"
    assoc.write_text(
        HEADER
        + _row(trait="Trait A", snps="rs1, rs2", strong="rs1-A", p="1e-9", or_beta="3.57733E-4")
        + _row(trait="Trait A", snps="rs1", strong="rs1-C", p="5e-8", or_beta="1.5")
        + _row(trait="Trait B", snps="", strong="rs9-T", p="1e-10")  # fallback to strongest
        + _row(trait="Trait C", snps="rs3", strong="rs3-G", p="1e-3")  # fails p cut
        + _row(trait="Trait D", snps="rs99", strong="rs99-A", p="1e-20")  # not in genome
        + _row(trait="Trait E", snps="rs2;rs2", strong="rs2-G", p="2e-9", or_beta="0.8"),
        encoding="utf-8",
    )

    rs_map = {"rs1": "AC", "rs2": "GG", "rs9": "TT"}
    genome = _Genome(tmp_path / "kit.txt", rs_map)
    rs_lookup = {gm._norm_rs(k).lower(): (k, v) for k, v in rs_map.items()}

    duck = gm._match_duckdb(folder, assoc, genome, rs_map, rs_lookup, p_max=5e-8, job=None)
    py = gm._match_python(folder, assoc, genome, rs_map, rs_lookup, p_max=5e-8, job=None)

    # catalog_rs counts rs before p-cut: rs1, rs2, rs3, rs9, rs99
    assert duck.catalog_rs == py.catalog_rs == 5
    assert _fingerprint(duck) == _fingerprint(py)
    assert duck.matched_rs == 3
    assert any(a.rsid == "rs9" and a.risk == "T" for a in duck.assocs)
