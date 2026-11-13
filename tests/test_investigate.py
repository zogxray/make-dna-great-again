"""Agent point queries: gwas.search / gwas.trait ranking."""

from __future__ import annotations

from ydna.core.gwas_match import Assoc, MatchResult
from ydna.core import investigate as inv


def _result(*assocs: Assoc) -> MatchResult:
    counts: dict[str, int] = {}
    for a in assocs:
        counts[a.trait] = counts.get(a.trait, 0) + 1
    return MatchResult(
        genome="/tmp/x.txt", catalog="/tmp/c.tsv", catalog_date="",
        genome_rs=100, catalog_rs=50, matched_rs=len(assocs),
        traits_hit=len(counts), p_max=5e-8,
        assocs=list(assocs), trait_counts=counts,
    )


def test_gwas_search_ranks_vitamin_d():
    result = _result(
        Assoc("rs1", "AA", "A", 1e-10, "0.1", "Vitamin D levels", "vitamin D", "GC", "1", "1", "GCST1", "1", "A", "2020"),
        Assoc("rs2", "AG", "A", 1e-9, "0.1", "Sleep duration", "sleep", "MEIS1", "2", "2", "GCST2", "2", "B", "2021"),
        Assoc("rs3", "GG", "G", 1e-12, "0.2", "Calcium levels", "calcium", "CASR", "3", "3", "GCST3", "3", "C", "2022"),
    )
    out = inv.gwas_search(result, "vitamin D")
    assert out["hits"] >= 1
    assert out["traits"][0]["trait"] == "Vitamin D levels"
    assert out["next_queries"]
    assert out["next_queries"][0].startswith("trait:")


def test_gwas_trait_exact_and_soft():
    result = _result(
        Assoc("rs1", "AA", "A", 1e-10, "0.1", "Vitamin D levels", "vitamin D", "GC", "1", "1", "GCST1", "1", "A", "2020"),
        Assoc("rs9", "AG", "A", 1e-8, "0.2", "Vitamin D levels", "vitamin D", "GC", "1", "9", "GCST1", "1", "A", "2020"),
    )
    exact = inv.gwas_trait(result, "Vitamin D levels")
    assert exact["exact"] is True
    assert exact["assocs_total"] == 2
    assert exact["assocs"][0]["rsid"] in {"rs1", "rs9"}
    assert any(q.startswith("rs:") for q in exact["next_queries"])

    soft = inv.gwas_trait(result, "vitamin")
    assert soft["exact"] is False
    assert "Vitamin D levels" in soft["candidates"]


def test_gwas_search_empty_raises():
    result = _result()
    try:
        inv.gwas_search(result, "  ")
        assert False, "expected empty_query"
    except RuntimeError as exc:
        assert str(exc) == "empty_query"
