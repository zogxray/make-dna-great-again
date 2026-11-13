"""Lineage query helpers without Discover/AADR fixtures."""

from __future__ import annotations

from ydna.core.lineage_query import affinity_top, pick_milestones
from ydna.core.aadr import F3


def test_affinity_top_sorts_by_z():
    rows = [
        F3(name="B", est=0.1, se=0.01, z=2.0, n=10, snps=100),
        F3(name="A", est=0.2, se=0.01, z=5.0, n=10, snps=100),
    ]
    out = affinity_top(rows, limit=5, source="test")
    assert out["top"][0]["name"] == "A"
    assert out["source"] == "test"


def test_pick_milestones_short_path():
    assert pick_milestones(["A", "B", "C"], limit=12) == ["A", "B", "C"]
