"""Seshat Polaris workbook: polities + religion / complexity codes for LORE.

Читает OOXML без openpyxl (Polaris использует inlineStr). Индекс кэшируется
в памяти на путь файла.
"""

from __future__ import annotations

import pickle
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .sources import SESHAT_POLARIS, Seshat
from .store import Store, data_dir

_CACHE_NAME = "polaris_lore_index_v1.pkl"

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_CELL_T = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"

# Переменные, которые тянем в LORE-якорь (остальное игнорируем).
_GENERAL_VARS = {
    "polity_religion",
    "polity_religion_family",
    "polity_religion_genus",
    "polity_religious_tradition",
}
_SC_VARS = {
    "professional_priesthood",
    "written_record",
    "religious_literature",
    "burial_site",
    "religious_level",
}
_RELIGION_VARS = {
    "human_sacrifice",
    "religious_fragmentation",
    "sync_rel_pra_ind_beli",
    "moralizing_supernatural_punishment_and_reward",
    "moralizing_religion_adopted_by_elites",
    "moralizing_religion_adopted_by_commoners",
}

# Страна / культура → Seshat region (точные имена листов Polities).
_GEO_HINTS: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("pontic", "caspian", "yamna", "yamnaya", "scyth", "sarmat", "ukraine",
      "steppe"),
     ("Pontic-Caspian", "Turkestan")),
    (("russia", "belarus", "moscow", "novgorod"),
     ("Eastern Europe", "Pontic-Caspian")),
    (("poland", "czech", "slovakia", "hungary", "austria", "germany",
      "corded", "beaker", "baltic", "lithuania", "latvia", "estonia"),
     ("Western Europe", "Eastern Europe")),
    (("britain", "england", "scotland", "wales", "ireland", "united kingdom"),
     ("Western Europe",)),
    (("france", "spain", "portugal", "belgium", "netherlands", "switzerland",
      "celt", "la tene", "hallstatt"),
     ("Western Europe",)),
    (("italy", "rome", "roman", "etruscan"),
     ("Southern Europe",)),
    (("greece", "crete", "mycenae", "hellen"),
     ("Southeastern Europe", "Southern Europe")),
    (("balkan", "serbia", "croatia", "romania", "bulgaria", "albania",
      "macedonia"),
     ("Southeastern Europe",)),
    (("anatolia", "turkey", "caucasus", "armenia", "georgia", "azerbaijan"),
     ("Anatolia-Caucasus",)),
    (("iran", "persia", "elam"), ("Iran",)),
    (("mesopotamia", "iraq", "sumer", "babylon", "assyria"), ("Mesopotamia",)),
    (("egypt", "nile", "nubia"), ("Northeast Africa",)),
    (("mongolia", "xiongnu", "altai"), ("Mongolia", "Northeast Asia")),
    (("china", "yellow river", "zhou"), ("North China",)),
    (("india", "vedic", "indus"), ("North India", "Southern India")),
    (("scandinavia", "sweden", "norway", "denmark", "viking", "norse",
      "faroe"),
     ("Western Europe",)),
]

_STOP = frozenset({
    "age", "iron", "bronze", "early", "late", "middle", "culture", "period",
    "ancient", "sample", "the", "and", "for", "from", "with", "group",
    "horizon", "people", "ware", "dna", "haplogroup", "neolithic", "mesolithic",
    "paleolithic", "chalcolithic", "eneolithic", "medieval", "empire", "kingdom",
    "republic", "complex", "scandinavian", "european", "federation", "province",
    "district", "county", "island", "grave", "cave", "copper", "funnel", "battle",
    "axe", "plain", "latium",
})


@dataclass
class Polity:
    number: int
    polity_id: str
    name: str
    region: str
    nga: str
    start_year: int | None
    end_year: int | None
    codes: dict[str, str] = field(default_factory=dict)

    def year_overlap(self, year: int | None) -> bool:
        if year is None or self.start_year is None or self.end_year is None:
            return True
        lo, hi = sorted((self.start_year, self.end_year))
        return lo <= year <= hi

    def religion_line(self) -> str:
        bits = []
        for key in ("polity_religion", "polity_religion_family",
                    "polity_religious_tradition"):
            v = self.codes.get(key)
            if v and v.lower() not in ("unknown", "uncoded", "none", ""):
                bits.append(v)
        return " · ".join(dict.fromkeys(bits)) if bits else "—"

    def cult_flags(self) -> list[str]:
        """Короткие флаги для LORE: temples/priests/sacrifice/writing."""
        out: list[str] = []
        mapping = [
            ("professional_priesthood", "priesthood"),
            ("written_record", "writing"),
            ("religious_literature", "rel.literature"),
            ("burial_site", "burial sites"),
            ("human_sacrifice", "human sacrifice"),
            ("moralizing_supernatural_punishment_and_reward", "moralizing religion"),
            ("religious_fragmentation", "rel.fragmentation"),
        ]
        for key, label in mapping:
            v = (self.codes.get(key) or "").lower()
            if v in ("present", "p", "yes", "true", "1"):
                out.append(f"{label}: yes")
            elif v in ("absent", "a", "no", "false", "0"):
                out.append(f"{label}: no")
        return out


@dataclass
class SeshatIndex:
    path: Path
    polities: list[Polity]
    by_id: dict[str, Polity]

    def match(self, hints: Iterable[str], year: int | None = None,
              limit: int = 5) -> list[tuple[Polity, int]]:
        """Ранжирует политии: имя/регион из hints + пересечение года.

        Без имени или целевого региона не поднимаем «случайные» политии
        только по году (иначе Crete/Cambodia забивают степь).
        """
        tokens = _hint_tokens(hints)
        hint_blob = " ".join(str(h).lower() for h in hints if h)
        if not tokens and not hint_blob:
            return []
        want_regions: set[str] = set()
        for keys, regions in _GEO_HINTS:
            if any(k in hint_blob for k in keys):
                want_regions.update(regions)
        want_l = {r.lower() for r in want_regions}
        scored: list[tuple[Polity, int]] = []
        for p in self.polities:
            region_l = p.region.lower()
            name_l = p.name.lower()
            pid_l = p.polity_id.lower()
            name_hit = 0
            pid_words = pid_l.replace("_", " ")
            for tok in tokens:
                if len(tok) < 4:
                    continue
                if _whole(tok, name_l) or _whole(tok, pid_words):
                    name_hit += 6
                elif _whole(tok, region_l):
                    name_hit += 2
            # только точное имя региона (иначе "eastern europe" ⊂ "southeastern europe")
            in_region = bool(want_l) and region_l in want_l
            year_ok = year is None or p.year_overlap(year)
            year_near = False
            if year is not None and not year_ok and p.start_year is not None and p.end_year is not None:
                lo, hi = sorted((p.start_year, p.end_year))
                dist = min(abs(year - lo), abs(year - hi))
                year_near = dist <= 400
            # жёсткий фильтр: имя ИЛИ (регион ∧ год/рядом)
            if name_hit == 0 and not (in_region and (year_ok or year_near)):
                continue
            if year is not None and not year_ok and not year_near:
                continue
            score = name_hit
            if in_region:
                score += 5
            if year_ok:
                score += 4
            elif year_near:
                score += 1
            if score >= 5:
                scored.append((p, score))
        scored.sort(key=lambda x: (-x[1], x[0].name))
        return scored[:limit]


_cache: dict[str, SeshatIndex] = {}


def workbook_path(store: Store | None = None) -> Path | None:
    """Путь к Polaris2026.xlsx из Store или data/seshat/."""
    src = Seshat()
    folder: Path | None = None
    if store is not None:
        folder = store.path(src.key)
    if folder is None:
        folder = data_dir() / "seshat"
    return src.workbook(folder)


def load_index(path: Path | None = None, store: Store | None = None) -> SeshatIndex | None:
    book = path or workbook_path(store)
    if book is None or not book.is_file():
        return None
    key = str(book.resolve())
    hit = _cache.get(key)
    if hit is not None:
        return hit
    disk = book.parent / _CACHE_NAME
    try:
        if disk.is_file() and disk.stat().st_mtime_ns >= book.stat().st_mtime_ns:
            with disk.open("rb") as fh:
                idx = pickle.load(fh)
            if isinstance(idx, SeshatIndex) and idx.path.resolve() == book.resolve():
                _cache[key] = idx
                return idx
    except Exception:  # noqa: BLE001
        pass
    idx = _build_index(book)
    _cache[key] = idx
    try:
        with disk.open("wb") as fh:
            pickle.dump(idx, fh, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:  # noqa: BLE001
        pass
    return idx


def drop_cache() -> None:
    _cache.clear()


# Approximate map anchors for Seshat regions (no polity polygons in Polaris).
REGION_CENTROID: dict[str, tuple[float, float]] = {
    "Western Europe": (5.0, 48.0),
    "Eastern Europe": (30.0, 52.0),
    "Southeastern Europe": (22.0, 42.0),
    "Southern Europe": (12.0, 42.0),
    "Pontic-Caspian": (44.0, 48.0),
    "Turkestan": (65.0, 42.0),
    "Anatolia-Caucasus": (40.0, 39.0),
    "Iran": (53.0, 32.0),
    "Mesopotamia": (45.0, 33.0),
    "Northeast Africa": (32.0, 27.0),
    "East Africa": (38.0, 5.0),
    "West Africa": (-2.0, 10.0),
    "North China": (112.0, 35.0),
    "Northeast Asia": (125.0, 45.0),
    "Mongolia": (105.0, 47.0),
    "North India": (78.0, 28.0),
    "Southern India": (78.0, 15.0),
    "Mexico": (-99.0, 20.0),
}

# AADR epoch key → rough mid calendar year (CE; BCE negative) for Seshat overlap.
_EPOCH_YEAR_CE: dict[str, int] = {
    "modern": 1950,
    "present": 1850,
    "early_modern": 1650,
    "medieval": 1000,
    "late_antiquity": 350,
    "antiquity": -500,
    "iron": -1000,
    "bronze": -2000,
    "copper": -4500,
    "neolithic": -7000,
    "paleolithic": -15000,
}


def epoch_year_ce(epoch: str | None) -> int | None:
    if not epoch or epoch == "all":
        return None
    return _EPOCH_YEAR_CE.get(epoch)


def short_polity_label(name: str, *, width: int = 16) -> str:
    """Compact Seshat name for map: drop leading 'The', trim."""
    s = (name or "").strip()
    if s.lower().startswith("the "):
        s = s[4:]
    if len(s) <= width:
        return s
    return s[: width - 1] + "…"


def map_anchors(
    idx: SeshatIndex | None,
    *,
    hints: list[str],
    epoch: str | None = None,
    year: int | None = None,
    aadr_xy: list[tuple[float, float, str]] | None = None,
    limit: int = 8,
) -> list[dict]:
    """Seshat layer for the epoch map: anchors near AADR hits or region centroids.

    Each dict: lon, lat, label, color, glyph, kind='seshat'.
    Not filled polygons — point labels only.
    """
    if idx is None:
        return []
    yr = year if year is not None else epoch_year_ce(epoch)
    hits = idx.match(hints, year=yr, limit=max(limit * 2, 12))
    if not hits:
        return []
    # hint blob → which region centroids are "active" from AADR side
    aadr_xy = aadr_xy or []
    out: list[dict] = []
    used_cells: set[tuple[int, int]] = set()
    for polity, score in hits:
        lon = lat = None
        # Prefer mean of AADR points whose text hints at this polity/region.
        near: list[tuple[float, float]] = []
        keys = (
            polity.name.lower(),
            polity.region.lower(),
            polity.nga.lower() if polity.nga else "",
            polity.polity_id.replace("_", " ").lower(),
        )
        for x, y, blob in aadr_xy:
            b = (blob or "").lower()
            if not b:
                continue
            if any(k and len(k) >= 4 and k in b for k in keys):
                near.append((x, y))
            else:
                # region via geo hint table
                for toks, regions in _GEO_HINTS:
                    if polity.region in regions and any(t in b for t in toks):
                        near.append((x, y))
                        break
        if near:
            lon = sum(p[0] for p in near) / len(near)
            lat = sum(p[1] for p in near) / len(near)
        else:
            xy = REGION_CENTROID.get(polity.region)
            if not xy:
                continue
            lon, lat = xy
        # crude de-dupe so two polities don't stack on the same cell
        cell = (int(lon // 8), int(lat // 6))
        if cell in used_cells:
            continue
        used_cells.add(cell)
        out.append({
            "lon": lon,
            "lat": lat,
            # glyph carries §; label is the polity name only (avoids "§ § Name")
            "label": short_polity_label(polity.name),
            "color": "#AEA5B8",
            "glyph": "§",
            "kind": "seshat",
            "rank": float(score),
            "id": polity.polity_id,
        })
        if len(out) >= limit:
            break
    return out


# ───────────────────────── OOXML ─────────────────────────

def _col_row(ref: str) -> tuple[int, int]:
    m = re.match(r"([A-Z]+)(\d+)", ref)
    if not m:
        return 0, 0
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - 64)
    return col - 1, int(m.group(2)) - 1


def _cell_val(c: ET.Element) -> str | None:
    if c.attrib.get("t") == "inlineStr":
        is_ = c.find("m:is", _NS)
        if is_ is None:
            return None
        return "".join((t.text or "") for t in is_.iter(_CELL_T))
    v = c.find("m:v", _NS)
    return v.text if v is not None and v.text is not None else None


def _sheet_map(zf: zipfile.ZipFile) -> dict[str, str]:
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rid = {r.attrib["Id"]: r.attrib["Target"] for r in rels}
    out: dict[str, str] = {}
    for sh in wb.findall("m:sheets/m:sheet", _NS):
        r = sh.attrib[_REL]
        target = rid[r]
        if target.startswith("/"):
            path = target.lstrip("/")
        elif target.startswith("xl/"):
            path = target
        else:
            path = "xl/" + target
        out[sh.attrib["name"]] = path
    return out


def _iter_rows(zf: zipfile.ZipFile, sheet_path: str):
    root = ET.fromstring(zf.read(sheet_path))
    for row_el in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
        cells: dict[int, str | None] = {}
        for c in row_el.findall("m:c", _NS):
            ref = c.attrib.get("r")
            if not ref:
                continue
            ci, _ = _col_row(ref)
            cells[ci] = _cell_val(c)
        if cells:
            yield cells


def _as_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _norm_code(v: str | None) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _build_index(book: Path) -> SeshatIndex:
    with zipfile.ZipFile(book) as zf:
        sheets = _sheet_map(zf)
        polities: list[Polity] = []
        by_id: dict[str, Polity] = {}
        for cells in _iter_rows(zf, sheets["Polities"]):
            if cells.get(0) == "polity_number":
                continue
            pid = (cells.get(1) or "").strip()
            if not pid:
                continue
            p = Polity(
                number=_as_int(cells.get(0)) or 0,
                polity_id=pid,
                name=(cells.get(2) or pid).strip(),
                region=(cells.get(3) or "").strip(),
                nga=(cells.get(4) or "").strip(),
                start_year=_as_int(cells.get(5)),
                end_year=_as_int(cells.get(6)),
            )
            polities.append(p)
            by_id[pid] = p

        def _attach(sheet_name: str, wanted: set[str],
                    id_col: int = 2, var_col: int = 4, val_col: int = 5) -> None:
            path = sheets.get(sheet_name)
            if not path:
                return
            for cells in _iter_rows(zf, path):
                if cells.get(0) == "seshat_region" or cells.get(var_col) == "variable_name":
                    continue
                var = cells.get(var_col)
                if not var or var not in wanted:
                    continue
                pid = (cells.get(id_col) or "").strip()
                p = by_id.get(pid)
                if p is None:
                    continue
                val = _norm_code(cells.get(val_col))
                if val is not None and var not in p.codes:
                    p.codes[var] = val

        _attach("General", _GENERAL_VARS)
        _attach("Religion", _RELIGION_VARS)
        _attach("Social complexity", _SC_VARS)

    return SeshatIndex(path=book, polities=polities, by_id=by_id)


def _hint_tokens(hints: Iterable[str]) -> list[str]:
    raw = " ".join(str(h) for h in hints if h).lower()
    raw = re.sub(r"[^\w\s\-]", " ", raw)
    toks = [t for t in raw.split() if len(t) >= 4 and t not in _STOP]
    return list(dict.fromkeys(toks))


def _whole(tok: str, text: str) -> bool:
    """Целое слово: 'italy' ≠ 'italian', 'rome' ∈ 'late roman republic'."""
    return re.search(rf"(?<![a-z0-9]){re.escape(tok)}(?![a-z0-9])", text) is not None
