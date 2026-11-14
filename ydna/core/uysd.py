"""UYSD (Universal Y-SNP Database) — modern Y haplogroup frequencies.

F2 downloads the region GeoJSON. Per-haplogroup frequencies are fetched from the
public HTML page (embedded JSON) and cached under the UYSD folder. YFull-style
names (R-M269) match FTDNA tips for major nodes.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .jobs import USER_AGENT
from .store import Store, data_dir

UYSD_PAGE = "https://ysnp.erasmusmc.nl"
UYSD_GEOJSON = "regions_and_countries.geojson"
UYSD_GEOJSON_URL = f"{UYSD_PAGE}/static/ysnp/data/{UYSD_GEOJSON}"

_FREQ_RE = re.compile(
    r"var\s+frequencies\s*=\s*JSON\.parse\(\"((?:\\.|[^\"\\])*)\"\)",
    re.S,
)

_INDEX: "UysdIndex | None" = None
_INDEX_PATH: Path | None = None


@dataclass(frozen=True)
class UysdFreq:
    region_id: int
    country: str
    region: str
    pct: float
    n_derived: int
    n_ancestral: int
    interpolated: bool

    @property
    def label(self) -> str:
        if self.region:
            return f"{self.country} · {self.region}"
        return self.country or f"#{self.region_id}"

    @property
    def n(self) -> int:
        return self.n_derived + self.n_ancestral


@dataclass
class UysdIndex:
    path: Path
    geojson: Path
    regions: dict[int, tuple[str, str]]  # id → (country, region)
    centroids: dict[int, tuple[float, float]] = field(default_factory=dict)

    def cache_dir(self) -> Path:
        d = self.path / "cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def map_points(self, haplogroup: str, *, limit: int = 36,
                   fetch: bool = True, min_pct: float = 5.0) -> list[dict]:
        """Frequency discs for the braille map (lon/lat from region centroids)."""
        rows = self.frequencies(haplogroup, limit=max(limit, 80), fetch=fetch)
        out: list[dict] = []
        for r in rows:
            if r.pct < min_pct and len(out) >= 8:
                continue
            xy = self.centroids.get(r.region_id)
            if not xy:
                continue
            lon, lat = xy
            out.append({
                "id": f"uysd:{r.region_id}",
                "label": r.label,
                "lon": lon,
                "lat": lat,
                "pct": r.pct,
                "n_derived": r.n_derived,
                "n": r.n,
                "kind": "uysd",
            })
            if len(out) >= limit:
                break
        return out

    def cached(self, haplogroup: str) -> list[UysdFreq] | None:
        p = self.cache_dir() / f"{_safe_name(haplogroup)}.json"
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(data, list):
            return None
        return [_row_from_dict(x) for x in data if isinstance(x, dict)]

    def store_cache(self, haplogroup: str, rows: list[UysdFreq]) -> None:
        p = self.cache_dir() / f"{_safe_name(haplogroup)}.json"
        payload = [
            {
                "region_id": r.region_id,
                "country": r.country,
                "region": r.region,
                "pct": r.pct,
                "n_derived": r.n_derived,
                "n_ancestral": r.n_ancestral,
                "interpolated": r.interpolated,
            }
            for r in rows
        ]
        p.write_text(json.dumps(payload), encoding="utf-8")

    def frequencies(self, haplogroup: str, *, limit: int = 8,
                    fetch: bool = True) -> list[UysdFreq]:
        hg = (haplogroup or "").strip()
        if not hg:
            return []
        hit = self.cached(hg)
        if hit is not None:
            return hit[:limit]
        if not fetch:
            return []
        rows = fetch_haplogroup_frequencies(hg, regions=self.regions)
        if rows is not None:
            self.store_cache(hg, rows)
            return rows[:limit]
        return []


def _safe_name(hg: str) -> str:
    return re.sub(r"[^A-Za-z0-9._+-]+", "_", hg.strip())[:120] or "hg"


def _row_from_dict(d: dict) -> UysdFreq:
    return UysdFreq(
        region_id=int(d.get("region_id") or 0),
        country=str(d.get("country") or ""),
        region=str(d.get("region") or ""),
        pct=float(d.get("pct") or 0),
        n_derived=int(d.get("n_derived") or 0),
        n_ancestral=int(d.get("n_ancestral") or 0),
        interpolated=bool(d.get("interpolated")),
    )


def uysd_dir(store: Store | None = None) -> Path | None:
    if store is not None:
        p = store.path("uysd")
        if p and p.is_dir():
            return p
    d = data_dir() / "uysd"
    return d if d.is_dir() else None


def load_index(store: Store | None = None,
               path: Path | None = None) -> UysdIndex | None:
    global _INDEX, _INDEX_PATH
    root = path or uysd_dir(store)
    if root is None:
        return None
    geo = root / UYSD_GEOJSON
    if not geo.is_file():
        return None
    if _INDEX is not None and _INDEX_PATH == geo:
        return _INDEX
    try:
        data = json.loads(geo.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    regions: dict[int, tuple[str, str]] = {}
    centroids: dict[int, tuple[float, float]] = {}
    for feat in data.get("features") or []:
        props = feat.get("properties") or {}
        try:
            rid = int(props["id"])
        except (KeyError, TypeError, ValueError):
            continue
        regions[rid] = (str(props.get("country") or ""),
                        str(props.get("region") or ""))
        c = _geom_centroid(feat.get("geometry"))
        if c is not None:
            centroids[rid] = c
    if not regions:
        return None
    _INDEX = UysdIndex(path=root, geojson=geo, regions=regions, centroids=centroids)
    _INDEX_PATH = geo
    return _INDEX


def _geom_centroid(geom: dict | None) -> tuple[float, float] | None:
    if not isinstance(geom, dict):
        return None
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    pts: list[tuple[float, float]] = []

    def walk(node) -> None:
        if not isinstance(node, (list, tuple)) or not node:
            return
        if isinstance(node[0], (int, float)) and len(node) >= 2:
            try:
                pts.append((float(node[0]), float(node[1])))
            except (TypeError, ValueError):
                return
            return
        for child in node:
            walk(child)

    if gtype == "Point" and isinstance(coords, (list, tuple)) and len(coords) >= 2:
        return float(coords[0]), float(coords[1])
    walk(coords)
    if not pts:
        return None
    # Subsample huge rings.
    step = max(1, len(pts) // 400)
    use = pts[::step]
    return sum(p[0] for p in use) / len(use), sum(p[1] for p in use) / len(use)


def fetch_haplogroup_frequencies(
    haplogroup: str,
    *,
    regions: dict[int, tuple[str, str]] | None = None,
    timeout: float = 12.0,
) -> list[UysdFreq] | None:
    """Parse the public UYSD haplogroup page. Returns None on network/parse failure."""
    hg = (haplogroup or "").strip()
    if not hg:
        return []
    url = f"{UYSD_PAGE}/haplogroup/{urllib.parse.quote(hg, safe='')}"
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            page = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    m = _FREQ_RE.search(page)
    if not m:
        return None
    blob = m.group(1)
    items = None
    for candidate in (
        blob.encode("utf-8").decode("unicode_escape"),
        html_lib.unescape(blob),
        blob.replace(r"\u0022", '"').replace(r"\u0027", "'"),
    ):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                items = parsed
                break
            if isinstance(parsed, str):
                parsed2 = json.loads(parsed)
                if isinstance(parsed2, list):
                    items = parsed2
                    break
        except (ValueError, TypeError, UnicodeDecodeError):
            continue
    if items is None:
        return None
    reg = regions or {}
    rows: list[UysdFreq] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            rid = int(it.get("key"))
            pct = float(it.get("val") or 0)
        except (TypeError, ValueError):
            continue
        country, region = reg.get(rid, ("", ""))
        rows.append(UysdFreq(
            region_id=rid,
            country=country,
            region=region,
            pct=pct,
            n_derived=int(it.get("nr_derived") or 0),
            n_ancestral=int(it.get("nr_ancestral") or 0),
            interpolated=str(it.get("inter") or "").lower() == "true",
        ))
    rows.sort(key=lambda r: (-r.pct, -r.n_derived, r.label))
    return rows
