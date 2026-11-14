"""Unit tests for mtDNA tip scoring, UYSD centroids, ancIBD index build."""

from __future__ import annotations

from pathlib import Path

from ydna.core import ancibd as ancibd_mod
from ydna.core import mtdna as mtdna_mod
from ydna.core import uysd as uysd_mod
from ydna.core.aadr import Group
from ydna.workspaces.reference.view import summary_prompt


def test_hg_related_prefix():
    assert mtdna_mod.hg_related("H1a1", "H1")
    assert mtdna_mod.hg_related("H1", "H1a1")
    assert mtdna_mod.hg_related("H1a1", "H1a1")
    assert not mtdna_mod.hg_related("H1", "U5")
    assert not mtdna_mod.hg_related("", "H")


def test_tip_from_alleles_prefers_path_match(tmp_path: Path):
    # Tiny synthetic tree: root R0 → H (73G) → H1 (3010A)
    xml = tmp_path / "tree.xml"
    xml.write_text(
        """
        <phylotree>
          <haplogroup name="R0">
            <poly>73A</poly>
            <haplogroup name="H">
              <poly>73G</poly>
              <haplogroup name="H1">
                <poly>3010A</poly>
              </haplogroup>
            </haplogroup>
          </haplogroup>
        </phylotree>
        """.strip(),
        encoding="utf-8",
    )
    # Reset module caches so load picks up tmp tree.
    mtdna_mod._TREE_INDEX = None
    mtdna_mod._TREE_PATH = None
    tree = mtdna_mod.load_phylotree(path=tmp_path)
    assert tree is not None
    tip = tree.tip_from_alleles({73: "G", 3010: "A"})
    assert tip is not None
    assert tip[0] == "H1"
    assert tip[1] >= 0.5
    # Mismatch on child marker → stop at H if only 73G matches well enough.
    tip_h = tree.tip_from_alleles({73: "G", 3010: "G"})
    assert tip_h is not None
    assert tip_h[0] in ("H", "H1")


def test_amtdb_match(tmp_path: Path):
    csv_path = tmp_path / "amtdb_metadata.csv"
    csv_path.write_text(
        "identifier,mt_hg,ychr_hg,latitude,longitude,country,culture,epoch,"
        "year_from,year_to,site\n"
        "A1,H1a,,48.0,16.0,Austria,LBK,Neolithic,-5000,-4500,SiteA\n"
        "B1,U5a,,50.0,10.0,Germany,WHG,Paleolithic,-8000,-7000,SiteB\n"
        "C1,H1,,47.0,15.0,Austria,BellBeaker,Bronze,-2200,-1800,SiteC\n",
        encoding="utf-8",
    )
    mtdna_mod._AMT_INDEX = None
    mtdna_mod._AMT_PATH = None
    idx = mtdna_mod.load_amtdb(path=tmp_path)
    assert idx is not None
    hits = idx.match("H1", limit=5)
    ids = [s.identifier for s in hits]
    assert "A1" in ids and "C1" in ids
    assert "B1" not in ids


def test_uysd_geom_centroid_polygon():
    geom = {
        "type": "Polygon",
        "coordinates": [[
            [0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0],
        ]],
    }
    c = uysd_mod._geom_centroid(geom)
    assert c is not None
    # Mean of ring vertices (incl. closing point) — inside the bbox.
    assert 0.0 < c[0] < 2.0 and 0.0 < c[1] < 2.0
    pt = uysd_mod._geom_centroid({"type": "Point", "coordinates": [10.5, -3.25]})
    assert pt == (10.5, -3.25)


def test_ancibd_build_index(tmp_path: Path):
    tsv = tmp_path / "tiny.tsv"
    tsv.write_text(
        "iid1\tiid2\tlengthM\n"
        "S1\tS2\t0.12\n"   # 12 cM
        "S1\tS2\t0.05\n"   # 5 cM — below default 8
        "S1\tS3\t0.20\n"   # 20 cM
        "S2\tS3\t0.09\n"   # 9 cM
        , encoding="utf-8",
    )
    dest = tmp_path / ancibd_mod.ANCIBD_INDEX
    stats = ancibd_mod.build_index(tsv, dest, min_cm=8.0)
    assert dest.is_file()
    assert stats["n_seg"] == 3
    assert stats["n_ids"] == 3
    ancibd_mod._INDEX = None
    ancibd_mod._INDEX_PATH = None
    idx = ancibd_mod.load_index(path=tmp_path)
    assert idx is not None
    edges = idx.for_id("S1", limit=5)
    others = {e.other: e for e in edges}
    assert "S3" in others and others["S3"].sum_cm >= 19.0
    assert "S2" in others and abs(others["S2"].sum_cm - 12.0) < 0.01


def test_summary_prompt_includes_local_extras():
    g = Group(
        name="Test_Group", samples=[], n=0, bp=5000.0,
        lat=48.0, lon=16.0, epoch="neolithic",
        country="Austria", locality="X", y=["R1b"], mt=["H1"],
    )
    text = summary_prompt(
        g, [],
        our_mt="H1a1",
        our_mt_score=0.9,
        our_y="R-M269",
        uysd_top=[{"haplogroup": "R-M269", "label": "Italy", "pct": 42.0, "n": 100}],
        amtdb_hits=[{"id": "A1", "mt": "H1", "culture": "LBK", "country": "AT"}],
        kin_edges=[{"seed": "S1", "other": "S2", "sum_cm": 22.5}],
    )
    assert "Our mt tip: H1a1" in text
    assert "UYSD modern freq" in text
    assert "AmtDB mt parallels" in text
    assert "ancIBD kinship" in text
