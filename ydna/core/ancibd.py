"""ancIBD pairwise IBD among published AADR Eurasians (Ringbauer et al. 2023).

F2 downloads the Zenodo segment table and builds a compact neighbor index
(sum cM ≥ threshold). Matching uses AADR Genetic IDs with optional ``.SG`` /
``.AG`` suffix stripping.
"""

from __future__ import annotations

import csv
import pickle
from dataclasses import dataclass
from pathlib import Path

from .store import Store, data_dir

_CACHE_VER = 1
_MIN_CM = 8.0
_INDEX: "AncIbdIndex | None" = None
_INDEX_PATH: Path | None = None

ANCIBD_TSV = "ibd220.ibd.v54.1.pub.tsv"
ANCIBD_INDEX = "ancibd-neighbors.pkl"


@dataclass(frozen=True)
class KinEdge:
    other: str
    sum_cm: float
    n_seg: int


@dataclass
class AncIbdIndex:
    path: Path
    neighbors: dict[str, tuple[KinEdge, ...]]

    def for_id(self, gid: str, *, limit: int = 12) -> list[KinEdge]:
        keys = _id_keys(gid)
        best: dict[str, KinEdge] = {}
        for k in keys:
            for e in self.neighbors.get(k, ()):
                prev = best.get(e.other)
                if prev is None or e.sum_cm > prev.sum_cm:
                    best[e.other] = e
        rows = sorted(best.values(), key=lambda e: (-e.sum_cm, -e.n_seg, e.other))
        return rows[:limit]


def _id_keys(gid: str) -> list[str]:
    g = (gid or "").strip()
    if not g:
        return []
    out = [g]
    base = g.rsplit(".", 1)[0] if "." in g else g
    if base not in out:
        out.append(base)
    return out


def ancibd_dir(store: Store | None = None) -> Path | None:
    if store is not None:
        p = store.path("ancibd")
        if p and p.is_dir():
            return p
    d = data_dir() / "ancibd"
    return d if d.is_dir() else None


def load_index(store: Store | None = None,
               path: Path | None = None) -> AncIbdIndex | None:
    global _INDEX, _INDEX_PATH
    root = path or ancibd_dir(store)
    if root is None:
        return None
    pkl = root / ANCIBD_INDEX
    if not pkl.is_file():
        return None
    if _INDEX is not None and _INDEX_PATH == pkl:
        return _INDEX
    try:
        data = pickle.loads(pkl.read_bytes())
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict) or data.get("ver") != _CACHE_VER:
        return None
    raw = data.get("neighbors") or {}
    neighbors: dict[str, tuple[KinEdge, ...]] = {}
    for k, rows in raw.items():
        edges = tuple(
            KinEdge(str(o), float(cm), int(n))
            for o, cm, n in rows
        )
        neighbors[str(k)] = edges
    _INDEX = AncIbdIndex(path=root, neighbors=neighbors)
    _INDEX_PATH = pkl
    return _INDEX


def build_index(tsv: Path, dest: Path, *, min_cm: float = _MIN_CM,
                job=None) -> dict:
    """Aggregate segment TSV → neighbor pickle. Returns stats for the manifest."""
    # Detect columns once.
    with tsv.open(encoding="utf-8", newline="") as f:
        head = f.readline()
    cols = [c.strip().strip('"') for c in head.strip().split("\t")]
    # ancIBD public table: iid1 iid2 … lengthM (Morgan) or length (cM)
    def _find(*names: str) -> int | None:
        lower = {c.lower(): i for i, c in enumerate(cols)}
        for n in names:
            if n.lower() in lower:
                return lower[n.lower()]
        return None

    i1 = _find("iid1", "id1", "sample1")
    i2 = _find("iid2", "id2", "sample2")
    i_cm = _find("lengthM", "length_m", "lenm")
    i_cm_direct = _find("length", "lengthCM", "length_cm", "cm")
    if i1 is None or i2 is None:
        raise RuntimeError(f"{tsv.name}: need iid1/iid2 columns, got {cols[:12]}")

    pair: dict[tuple[str, str], list[float]] = {}
    n_seg = 0
    with tsv.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        next(reader, None)
        for row in reader:
            if len(row) <= max(i1, i2):
                continue
            a, b = row[i1].strip(), row[i2].strip()
            if not a or not b or a == b:
                continue
            cm = 0.0
            try:
                if i_cm is not None and i_cm < len(row) and row[i_cm]:
                    cm = float(row[i_cm]) * 100.0  # Morgan → cM
                elif i_cm_direct is not None and i_cm_direct < len(row) and row[i_cm_direct]:
                    cm = float(row[i_cm_direct])
            except ValueError:
                continue
            if cm < min_cm:
                continue
            key = (a, b) if a < b else (b, a)
            pair.setdefault(key, []).append(cm)
            n_seg += 1
            if job and n_seg % 200_000 == 0:
                try:
                    job.set_stage(f"ancIBD · {n_seg:,} segments ≥{min_cm:g} cM")
                except Exception:  # noqa: BLE001
                    pass

    neighbors: dict[str, list[tuple[str, float, int]]] = {}
    for (a, b), segs in pair.items():
        s = float(sum(segs))
        n = len(segs)
        neighbors.setdefault(a, []).append((b, s, n))
        neighbors.setdefault(b, []).append((a, s, n))
    for k, rows in neighbors.items():
        rows.sort(key=lambda t: (-t[1], -t[2], t[0]))
        neighbors[k] = rows[:40]  # cap per individual

    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ver": _CACHE_VER, "min_cm": min_cm, "neighbors": neighbors,
               "n_pairs": len(pair), "n_seg": n_seg, "n_ids": len(neighbors)}
    dest.write_bytes(pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL))
    global _INDEX, _INDEX_PATH
    _INDEX = None
    _INDEX_PATH = None
    return {"n_pairs": len(pair), "n_seg": n_seg, "n_ids": len(neighbors),
            "min_cm": min_cm}
