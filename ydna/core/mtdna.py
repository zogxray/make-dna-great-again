"""mtDNA reference helpers: PhyloTree labels + AmtDB ancient samples.

PhyloTree (HaploGrep trees) and AmtDB are optional F2 sources. Matching is
prefix-based on haplogroup strings (H67 ⊂ H6 ⊂ H). Optional kit tip: score
defining PhyloTree mutations against F1 MT calls (lightweight HaploGrep-ish).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from .store import Store, data_dir

_TREE_INDEX: "PhyloTreeIndex | None" = None
_TREE_PATH: Path | None = None
_AMT_INDEX: "AmtDbIndex | None" = None
_AMT_PATH: Path | None = None

_HG_NAME = re.compile(r'haplogroup\s+name="([^"]+)"', re.I)
_POLY_RE = re.compile(r"<poly>([^<]+)</poly>", re.I)
_OPEN_HG = re.compile(r'<haplogroup\s+name="([^"]+)">', re.I)
_CLOSE_HG = re.compile(r"</haplogroup>", re.I)
_POLY_PARSE = re.compile(r"^(\d+)([ACGT])$", re.I)


def _norm_hg(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip())


def hg_related(a: str, b: str) -> bool:
    """True if one haplogroup string is a prefix of the other (clade relation)."""
    aa, bb = _norm_hg(a), _norm_hg(b)
    if not aa or not bb:
        return False
    return aa.startswith(bb) or bb.startswith(aa)


@dataclass(frozen=True)
class HgNode:
    name: str
    parent: str | None
    polys: tuple[tuple[int, str], ...]  # (pos, allele) defining mutations


@dataclass(frozen=True)
class PhyloTreeIndex:
    path: Path
    version: str
    names: frozenset[str]
    nodes: tuple[HgNode, ...] = ()

    def has(self, hg: str) -> bool:
        return _norm_hg(hg) in self.names

    def tip_from_alleles(self, alleles: dict[int, str]) -> tuple[str, float] | None:
        """Best haplogroup for kit MT alleles (rCRS positions). Returns (name, score).

        Scores each node against *path-cumulative* defining mutations (node +
        ancestors), so deeper tips are preferred when inherited markers match.
        """
        if not self.nodes or not alleles:
            return None
        by_name = {n.name: n for n in self.nodes}
        cum: dict[str, tuple[tuple[int, str], ...]] = {}

        def accumulate(name: str | None) -> tuple[tuple[int, str], ...]:
            if not name:
                return ()
            if name in cum:
                return cum[name]
            node = by_name.get(name)
            if node is None:
                cum[name] = ()
                return ()
            merged = accumulate(node.parent) + node.polys
            cum[name] = merged
            return merged

        best: tuple[str, float, int] | None = None  # name, score, hit count
        for node in self.nodes:
            polys = accumulate(node.name)
            if not polys:
                continue
            hit = miss = 0
            for pos, allele in polys:
                got = alleles.get(pos)
                if not got:
                    continue
                if allele.upper() in got.upper():
                    hit += 1
                else:
                    miss += 1
            if hit == 0:
                continue
            total = hit + miss
            score = hit / total if total else 0.0
            # Prefer perfect / high score, then more hits (deeper path).
            key = (score, hit)
            if best is None or key > (best[1], best[2]):
                best = (node.name, score, hit)
        if best is None or best[1] < 0.5:
            return None
        return best[0], best[1]


@dataclass(frozen=True)
class AmtSample:
    identifier: str
    mt_hg: str
    y_hg: str
    lat: float | None
    lon: float | None
    country: str
    culture: str
    epoch: str
    year_from: int | None
    year_to: int | None
    site: str


@dataclass
class AmtDbIndex:
    path: Path
    csv_path: Path
    samples: tuple[AmtSample, ...]

    def __post_init__(self) -> None:
        self._by_hg: dict[str, list[AmtSample]] | None = None

    def _index(self) -> dict[str, list[AmtSample]]:
        if self._by_hg is None:
            by: dict[str, list[AmtSample]] = {}
            for s in self.samples:
                if s.mt_hg:
                    by.setdefault(_norm_hg(s.mt_hg), []).append(s)
            self._by_hg = by
        return self._by_hg

    def match(self, hg: str, *, limit: int = 12) -> list[AmtSample]:
        """Samples whose mt haplogroup shares a prefix with ``hg``."""
        key = _norm_hg(hg)
        if not key:
            return []
        out: list[AmtSample] = []
        seen: set[str] = set()
        # Exact first, then longer (downstream), then shorter (upstream).
        for name, rows in sorted(self._index().items(),
                                 key=lambda kv: (0 if kv[0] == key
                                                 else 1 if kv[0].startswith(key)
                                                 else 2 if key.startswith(kv[0])
                                                 else 9,
                                                 -len(kv[0]))):
            if not (name == key or name.startswith(key) or key.startswith(name)):
                continue
            for s in rows:
                if s.identifier in seen:
                    continue
                seen.add(s.identifier)
                out.append(s)
                if len(out) >= limit:
                    return out
        return out


def _parse_int(x: str) -> int | None:
    x = (x or "").strip()
    if not x or x.upper() == "NA":
        return None
    try:
        return int(float(x))
    except ValueError:
        return None


def _parse_float(x: str) -> float | None:
    x = (x or "").strip()
    if not x or x.upper() == "NA":
        return None
    try:
        return float(x)
    except ValueError:
        return None


def phylotree_dir(store: Store | None = None) -> Path | None:
    if store is not None:
        p = store.path("phylotree_mt")
        if p and p.is_dir():
            return p
    d = data_dir() / "phylotree_mt"
    return d if d.is_dir() else None


def amtdb_dir(store: Store | None = None) -> Path | None:
    if store is not None:
        p = store.path("amtdb")
        if p and p.is_dir():
            return p
    d = data_dir() / "amtdb"
    return d if d.is_dir() else None


def load_phylotree(store: Store | None = None,
                   path: Path | None = None) -> PhyloTreeIndex | None:
    global _TREE_INDEX, _TREE_PATH
    root = path or phylotree_dir(store)
    if root is None:
        return None
    xml = root / "tree.xml"
    if not xml.is_file():
        # Unpacked zip may nest one folder.
        nested = list(root.glob("*/tree.xml"))
        xml = nested[0] if nested else xml
    if not xml.is_file():
        return None
    if _TREE_INDEX is not None and _TREE_PATH == xml:
        return _TREE_INDEX
    names: set[str] = set()
    nodes: list[HgNode] = []
    version = ""
    meta = xml.parent / "tree.yaml"
    if meta.is_file():
        try:
            text = meta.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^version:\s*(\S+)", text, re.M)
            if m:
                version = m.group(1)
        except OSError:
            pass
    try:
        text = xml.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    stack: list[tuple[str, list[tuple[int, str]]]] = []
    pos = 0
    while pos < len(text):
        m_open = _OPEN_HG.search(text, pos)
        m_close = _CLOSE_HG.search(text, pos)
        m_poly = _POLY_RE.search(text, pos)
        cands = [(m_open.start(), "open", m_open) for m_open in ([m_open] if m_open else [])]
        if m_close:
            cands.append((m_close.start(), "close", m_close))
        if m_poly:
            cands.append((m_poly.start(), "poly", m_poly))
        if not cands:
            break
        cands.sort(key=lambda t: t[0])
        _, kind, m = cands[0]
        if kind == "open":
            stack.append((m.group(1), []))
            pos = m.end()
        elif kind == "poly":
            pos = m.end()
            if not stack:
                continue
            pm = _POLY_PARSE.match(m.group(1).strip())
            if pm:
                stack[-1][1].append((int(pm.group(1)), pm.group(2).upper()))
        else:  # close
            pos = m.end()
            if not stack:
                continue
            name, polys = stack.pop()
            parent = stack[-1][0] if stack else None
            names.add(_norm_hg(name))
            nodes.append(HgNode(name=name, parent=parent, polys=tuple(polys)))
    if not names:
        return None
    _TREE_INDEX = PhyloTreeIndex(path=xml.parent, version=version or "?",
                                 names=frozenset(names), nodes=tuple(nodes))
    _TREE_PATH = xml
    return _TREE_INDEX


def mt_alleles_from_genome(genome) -> dict[int, str]:
    """pos → genotype string for MT chromosome (empty if missing)."""
    try:
        from . import genome_db
        if "MT" not in getattr(genome, "chroms", {}):
            return {}
        chrom = genome_db.ensure_chrom(genome, "MT")
    except Exception:  # noqa: BLE001
        return {}
    out: dict[int, str] = {}
    for p, gt in zip(chrom.pos, chrom.gts):
        g = (gt or "").strip().upper()
        if not g or g in (".", "--", "NN", "00", "NOCALL"):
            continue
        out[int(p)] = g
    return out


def tip_from_genome(genome, store: Store | None = None
                    ) -> tuple[str, float] | None:
    """Our mt tip from F1 MT calls + PhyloTree (None if tree/kit missing)."""
    tree = load_phylotree(store)
    if tree is None:
        return None
    alleles = mt_alleles_from_genome(genome)
    if len(alleles) < 5:
        return None
    return tree.tip_from_alleles(alleles)


def load_amtdb(store: Store | None = None,
               path: Path | None = None) -> AmtDbIndex | None:
    global _AMT_INDEX, _AMT_PATH
    root = path or amtdb_dir(store)
    if root is None:
        return None
    csv_path = root / "amtdb_metadata.csv"
    if not csv_path.is_file():
        alts = list(root.glob("*.csv"))
        csv_path = alts[0] if alts else csv_path
    if not csv_path.is_file():
        return None
    if _AMT_INDEX is not None and _AMT_PATH == csv_path:
        return _AMT_INDEX
    rows: list[AmtSample] = []
    try:
        with csv_path.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                lat = _parse_float(r.get("latitude") or "")
                lon = _parse_float(r.get("longitude") or "")
                rows.append(AmtSample(
                    identifier=(r.get("identifier") or "").strip(),
                    mt_hg=(r.get("mt_hg") or "").strip(),
                    y_hg=(r.get("ychr_hg") or "").strip(),
                    lat=lat, lon=lon,
                    country=(r.get("country") or "").strip(),
                    culture=(r.get("culture") or "").strip(),
                    epoch=(r.get("epoch") or "").strip(),
                    year_from=_parse_int(r.get("year_from") or ""),
                    year_to=_parse_int(r.get("year_to") or ""),
                    site=(r.get("site") or "").strip(),
                ))
    except OSError:
        return None
    _AMT_INDEX = AmtDbIndex(path=root, csv_path=csv_path, samples=tuple(rows))
    _AMT_PATH = csv_path
    return _AMT_INDEX


def map_anchors_for_hg(hg: str, *, store: Store | None = None,
                       limit: int = 8) -> list[dict]:
    """AmtDB points for ocean legend / map focus (``m:Identifier``)."""
    idx = load_amtdb(store)
    if idx is None or not hg:
        return []
    out: list[dict] = []
    for s in idx.match(hg, limit=limit):
        if s.lat is None or s.lon is None:
            continue
        label = s.identifier
        if s.mt_hg:
            label = f"{s.identifier} · {s.mt_hg}"
        out.append({
            "id": s.identifier,
            "label": label,
            "lon": s.lon,
            "lat": s.lat,
            "kind": "amtdb",
            "mt": s.mt_hg,
            "culture": s.culture,
            "epoch": s.epoch,
        })
    return out
