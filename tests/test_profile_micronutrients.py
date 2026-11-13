"""Micronutrient pack filters GWAS traits without false friends."""

from __future__ import annotations

from ydna.core.gwas_match import Assoc, MatchResult
from ydna.core.profile_micronutrients import analyze_match, nutrient_for_trait


def test_nutrient_for_trait_filters_drugs():
    assert nutrient_for_trait("Vitamin D levels") == "vitamin_d"
    assert nutrient_for_trait("Serum 25-Hydroxyvitamin D levels") == "vitamin_d"
    assert nutrient_for_trait("Medication use (calcium channel blockers)") is None
    assert nutrient_for_trait("Calcium levels") == "calcium"
    assert nutrient_for_trait("Omega-3 fatty acid levels") == "omega3"


def test_analyze_match_builds_cards():
    assocs = [
        Assoc("rs1", "AA", "A", 1e-10, "0.1", "Vitamin D levels", "vitamin D", "GC", "1", "1", "GCST1", "1", "A", "2020"),
        Assoc("rs2", "AG", "A", 1e-9, "0.1", "Vitamin D levels", "vitamin D", "GC", "1", "2", "GCST1", "1", "A", "2020"),
        Assoc("rs3", "GG", "G", 1e-12, "0.2", "Omega-3 fatty acid levels", "omega-3", "F", "11", "1", "GCST2", "2", "B", "2021"),
    ]
    result = MatchResult(
        genome="/tmp/x.txt", catalog="/tmp/c.tsv", catalog_date="",
        genome_rs=100, catalog_rs=50, matched_rs=3, traits_hit=2, p_max=5e-8,
        assocs=assocs, trait_counts={"Vitamin D levels": 2, "Omega-3 fatty acid levels": 1},
    )
    out = analyze_match(result)
    by_id = {n["id"]: n for n in out["nutrients"]}
    assert by_id["vitamin_d"]["gwas_assocs"] == 2
    assert by_id["omega3"]["gwas_assocs"] == 1
    assert by_id["zinc"]["gwas_assocs"] == 0
    assert "Oracle" in by_id["vitamin_d"]["disclaimer"]
    assert "say" in by_id["vitamin_d"] and by_id["vitamin_d"]["say"]
    assert "Vitamin D" in by_id["vitamin_d"]["say"]
    assert out.get("oracle_footnote")
