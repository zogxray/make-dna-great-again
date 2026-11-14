"""Cliopatria (Seshat) polity polygons for the F6 map — local GeoJSON → map cache.

Source: https://github.com/Seshat-Global-History-Databank/cliopatria (CC BY 4.0).
Raw GeoJSON is large; we keep a simplified ring cache for braille rendering.
"""

from __future__ import annotations

import pickle
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .jobs import Job
from .seshat import epoch_year_ce
from .sources import CLIOPATRIA_CACHE, CLIOPATRIA_GEOJSON, Cliopatria
from .store import Store, data_dir

_CACHE_VER = 1
_RDP_EPS = 0.45          # degrees — braille world/region scale
_MAX_RING_KEEP = 80      # hard cap per exterior after simplify
_INDEX: "CliopatriaIndex | None" = None
_INDEX_PATH: Path | None = None


@dataclass(frozen=True)
class PolityGeom:
    name: str
    from_year: int
    to_year: int
    area: float
    seshat_id: str
    rings: tuple[tuple[tuple[float, float], ...], ...]  # exterior rings only

    def active(self, year: int) -> bool:
        return self.from_year <= year <= self.to_year

    def bbox(self) -> tuple[float, float, float, float] | None:
        xs: list[float] = []
        ys: list[float] = []
        for ring in self.rings:
            for x, y in ring:
                xs.append(x)
                ys.append(y)
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    def centroid(self) -> tuple[float, float] | None:
        # Area-weighted-ish: mean of all ring vertices (good enough for radar hub).
        n = 0
        sx = sy = 0.0
        for ring in self.rings:
            for x, y in ring:
                sx += x
                sy += y
                n += 1
        if not n:
            return None
        return sx / n, sy / n


@dataclass
class CliopatriaIndex:
    path: Path
    geojson: Path
    polities: tuple[PolityGeom, ...]

    def __post_init__(self) -> None:
        self._year_cache: dict[tuple, list[PolityGeom]] = {}

    def for_year(self, year: int, *, limit: int = 40,
                 bbox: tuple[float, float, float, float] | None = None
                 ) -> list[PolityGeom]:
        """POLITY rows active in ``year``, largest first, optional lon/lat box."""
        key = (year, limit, bbox)
        hit = self._year_cache.get(key)
        if hit is not None:
            return hit
        hits = [p for p in self.polities if p.active(year)]
        if bbox is not None:
            west, south, east, north = bbox
            hits = [p for p in hits if _bbox_overlap(p.bbox(), west, south, east, north)]
        hits.sort(key=lambda p: -p.area)
        out = hits[: max(1, limit)]
        self._year_cache[key] = out
        if len(self._year_cache) > 32:
            self._year_cache.pop(next(iter(self._year_cache)))
        return out


def geojson_path(store: Store | None = None, folder: Path | None = None
                 ) -> Path | None:
    root = folder or _folder(store)
    if root is None:
        return None
    p = root / CLIOPATRIA_GEOJSON
    return p if p.is_file() and p.stat().st_size > 0 else None


def cache_path(store: Store | None = None, folder: Path | None = None
               ) -> Path | None:
    root = folder or _folder(store)
    return (root / CLIOPATRIA_CACHE) if root else None


def load_index(store: Store | None = None, job: Job | None = None,
               folder: Path | None = None) -> CliopatriaIndex | None:
    """Load (or build) the simplified polity cache. None if not downloaded."""
    global _INDEX, _INDEX_PATH
    gj = geojson_path(store, folder=folder)
    if gj is None:
        return None
    cache = gj.with_name(CLIOPATRIA_CACHE)
    if (_INDEX is not None and _INDEX_PATH == cache
            and cache.is_file() and _INDEX.geojson == gj):
        return _INDEX
    if not cache.is_file() or cache.stat().st_mtime < gj.stat().st_mtime:
        build_map_cache(gj, cache, job=job)
    idx = _read_cache(gj, cache)
    _INDEX, _INDEX_PATH = idx, cache
    return idx


def build_map_cache(geojson: Path, dest: Path | None = None,
                    job: Job | None = None) -> Path:
    """Parse GeoJSON → simplified pickle next to the file (or ``dest``)."""
    import json

    dest = dest or geojson.with_name(CLIOPATRIA_CACHE)
    if job:
        job.set_stage("parsing Cliopatria GeoJSON")
    t0 = time.time()
    data = json.loads(geojson.read_text(encoding="utf-8"))
    feats = data.get("features") or []
    if job:
        job.set_stage("simplifying polity rings", total=len(feats), done=0)
    out: list[PolityGeom] = []
    for i, feat in enumerate(feats):
        if job and i and i % 400 == 0:
            job.progress(i)
            job.check()
        props = feat.get("properties") or {}
        if str(props.get("Type") or "") != "POLITY":
            continue
        try:
            y0 = int(props["FromYear"])
            y1 = int(props["ToYear"])
            area = float(props.get("Area") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        name = str(props.get("Name") or "").strip()
        if not name:
            continue
        rings = _simplify_geom(feat.get("geometry") or {})
        if not rings:
            continue
        out.append(PolityGeom(
            name=name, from_year=y0, to_year=y1, area=area,
            seshat_id=str(props.get("SeshatID") or "").strip(),
            rings=rings,
        ))
    if job:
        job.progress(len(feats))
        job.set_stage("writing Cliopatria map cache")
    payload = {
        "ver": _CACHE_VER,
        "geojson": str(geojson.resolve()),
        "mtime_ns": geojson.stat().st_mtime_ns,
        "n": len(out),
        "built_s": round(time.time() - t0, 2),
        "polities": out,
    }
    tmp = dest.with_suffix(dest.suffix + ".partial")
    tmp.write_bytes(pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL))
    tmp.replace(dest)
    return dest


def map_polities(
    idx: CliopatriaIndex | None,
    *,
    epoch: str | None = None,
    year: int | None = None,
    limit: int = 36,
    bbox: tuple[float, float, float, float] | None = None,
) -> list[dict[str, Any]]:
    """Braille-map payload: outline rings + legend metadata for one epoch year."""
    if idx is None:
        return []
    yr = year if year is not None else epoch_year_ce(epoch)
    if yr is None:
        return []
    rows = idx.for_year(yr, limit=limit, bbox=bbox)
    out: list[dict[str, Any]] = []
    for i, p in enumerate(rows):
        cxy = p.centroid()
        out.append({
            "id": p.name,
            "label": _short(p.name),
            "name": p.name,
            "seshat_id": p.seshat_id,
            "area": p.area,
            "rank": float(max(0, 100 - i * (100 / max(1, len(rows))))),
            "color": _polity_color(p.name, i),
            "rings": p.rings,
            "lon": cxy[0] if cxy else None,
            "lat": cxy[1] if cxy else None,
            "kind": "cliopatria",
        })
    return out


def _folder(store: Store | None) -> Path | None:
    src = Cliopatria()
    if store is not None:
        p = store.path(src.key)
        if p and p.is_dir():
            return p
    cand = data_dir() / src.key
    return cand if cand.is_dir() else None


def _read_cache(geojson: Path, cache: Path) -> CliopatriaIndex:
    raw = pickle.loads(cache.read_bytes())
    if not isinstance(raw, dict) or raw.get("ver") != _CACHE_VER:
        raise RuntimeError("Cliopatria cache version mismatch — re-run F2 verify")
    pols = raw.get("polities") or ()
    if isinstance(pols, list):
        pols = tuple(pols)
    return CliopatriaIndex(path=cache, geojson=geojson, polities=pols)


def _simplify_geom(geom: dict) -> tuple[tuple[tuple[float, float], ...], ...]:
    kind = geom.get("type")
    coords = geom.get("coordinates")
    if not kind or not coords:
        return ()
    exteriors: list[list[tuple[float, float]]] = []
    if kind == "Polygon":
        if coords and coords[0]:
            exteriors.append(_as_xy(coords[0]))
    elif kind == "MultiPolygon":
        for poly in coords:
            if poly and poly[0]:
                exteriors.append(_as_xy(poly[0]))
    else:
        return ()
    out: list[tuple[tuple[float, float], ...]] = []
    for ring in exteriors:
        if len(ring) < 3:
            continue
        # Stride first (cheap), then RDP.
        step = max(1, len(ring) // 400)
        if step > 1:
            slim = ring[::step]
            if slim[-1] != ring[-1]:
                slim.append(ring[-1])
            ring = slim
        simp = _rdp(ring, _RDP_EPS)
        if len(simp) > _MAX_RING_KEEP:
            stride = max(1, len(simp) // _MAX_RING_KEEP)
            head = simp[::stride]
            if head[-1] != simp[-1]:
                head.append(simp[-1])
            simp = head
        if len(simp) >= 3:
            out.append(tuple((round(x, 2), round(y, 2)) for x, y in simp))
    return tuple(out)


def _as_xy(ring: Iterable) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for pt in ring:
        try:
            x, y = float(pt[0]), float(pt[1])
        except (TypeError, ValueError, IndexError):
            continue
        pts.append((x, y))
    return pts


def _rdp(pts: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    if len(pts) < 3:
        return list(pts)
    ax, ay = pts[0]
    bx, by = pts[-1]
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy or 1e-12
    best_i, best_d = 0, 0.0
    for i in range(1, len(pts) - 1):
        px, py = pts[i]
        t = ((px - ax) * dx + (py - ay) * dy) / denom
        t = 0.0 if t < 0 else (1.0 if t > 1 else t)
        qx, qy = ax + t * dx, ay + t * dy
        d = (px - qx) ** 2 + (py - qy) ** 2
        if d > best_d:
            best_d, best_i = d, i
    if best_d <= eps * eps:
        return [pts[0], pts[-1]]
    left = _rdp(pts[: best_i + 1], eps)
    right = _rdp(pts[best_i:], eps)
    return left[:-1] + right


def _bbox_overlap(bb: tuple[float, float, float, float] | None,
                  west: float, south: float, east: float, north: float) -> bool:
    if bb is None:
        return False
    x0, y0, x1, y1 = bb
    return not (x1 < west or x0 > east or y1 < south or y0 > north)


def _short(name: str, width: int = 16) -> str:
    s = (name or "").strip()
    if s.lower().startswith("the "):
        s = s[4:]
    if len(s) <= width:
        return s
    return s[: width - 1] + "…"


# Stable soft hues for polity outlines (avoid MAP purple cluster / neon).
_POLITY_PALETTE = (
    "#6B8F71", "#8B7355", "#5A7A8C", "#9A7B4F", "#6A7B6E",
    "#7A6B5A", "#4F7A6E", "#8A6A5A", "#5A6B7A", "#7A7A55",
)


def _polity_color(name: str, rank_i: int) -> str:
    h = 0
    for ch in name:
        h = (h * 33 + ord(ch)) & 0xFFFFFFFF
    return _POLITY_PALETTE[(h + rank_i) % len(_POLITY_PALETTE)]
