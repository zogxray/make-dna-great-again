"""Оверлеи YFull + TheYtree поверх FTDNA-дерева: ages, SNP-алиасы, координаты, QC.

Не заменяют топологию F3. Индекс — в DuckDB tree.index_blobs; строится лениво при первом запросе.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .store import Store, data_dir

INDEX_VERSION = 2
_SNP_SPLIT = re.compile(r"[,;]+")
_ALIAS_SPLIT = re.compile(r"[/|]+")


@dataclass(slots=True)
class AgeOverlay:
    source: str                 # yfull | theytree
    formed_bp: int | None = None
    formed_lo: int | None = None
    formed_hi: int | None = None
    tmrca_bp: int | None = None
    tmrca_lo: int | None = None
    tmrca_hi: int | None = None
    note: str = ""              # e.g. theytree single ages field


@dataclass(slots=True)
class SnpOverlay:
    name: str
    aliases: tuple[str, ...] = ()
    hg19: tuple[int, str, str] | None = None   # pos, ref, alt
    hg38: tuple[int, str, str] | None = None
    t2t: tuple[int, str, str] | None = None
    bad_region: bool = False
    combbed: bool = False
    is_ancestor: bool = False
    haplogroups: tuple[str, ...] = ()          # до нескольких клад, где встретился


@dataclass
class OverlayIndex:
    yfull_ages: dict[str, AgeOverlay] = field(default_factory=dict)
    yfull_snps: dict[str, tuple[str, ...]] = field(default_factory=dict)
    yfull_children: dict[str, tuple[str, ...]] = field(default_factory=dict)
    theytree_ages: dict[str, AgeOverlay] = field(default_factory=dict)
    theytree_parent: dict[str, str] = field(default_factory=dict)
    theytree_children: dict[str, tuple[str, ...]] = field(default_factory=dict)
    theytree_t2t: set[str] = field(default_factory=set)
    snps: dict[str, SnpOverlay] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def age(self, name: str) -> list[AgeOverlay]:
        key = _hg_key(name)
        out: list[AgeOverlay] = []
        if key in self.yfull_ages:
            out.append(self.yfull_ages[key])
        if key in self.theytree_ages:
            out.append(self.theytree_ages[key])
        return out

    def crosswalk(self, name: str) -> dict[str, Any]:
        key = _hg_key(name)
        hit: dict[str, Any] = {"query": name}
        if key in self.yfull_ages:
            hit["yfull"] = key
            hit["yfull_snps"] = self.yfull_snps.get(key, ())
            hit["yfull_children"] = self.yfull_children.get(key, ())
        if key in self.theytree_ages:
            hit["theytree"] = key
            hit["theytree_parent"] = self.theytree_parent.get(key)
            hit["theytree_t2t"] = key in self.theytree_t2t
            hit["theytree_children"] = self.theytree_children.get(key, ())
        return hit

    def snp(self, name: str) -> SnpOverlay | None:
        return self.snps.get(_snp_key(name))

    def children_diff(self, name: str, ftdna_children: Iterable[str]) -> dict[str, Any] | None:
        """Сверка детей при точном совпадении имени. В дерево FTDNA ничего не пишет.

        Возвращает None, если узел не найден ни в YFull, ни в TheYtree.
        """
        key = _hg_key(name)
        yf = self.yfull_children.get(key)
        tt = self.theytree_children.get(key)
        if yf is None and tt is None and key not in self.yfull_ages and key not in self.theytree_ages:
            return None
        ftdna = {_hg_key(c) for c in ftdna_children if _hg_key(c)}
        yf_set = set(yf or ())
        tt_set = set(tt or ())
        return {
            "name": key,
            "ftdna": tuple(sorted(ftdna)),
            "yfull": yf or (),
            "theytree": tt or (),
            "shared_yfull": tuple(sorted(ftdna & yf_set)),
            "shared_theytree": tuple(sorted(ftdna & tt_set)),
            "extra_yfull": tuple(sorted(yf_set - ftdna)),
            "extra_theytree": tuple(sorted(tt_set - ftdna)),
            "only_ftdna_vs_yfull": tuple(sorted(ftdna - yf_set)) if yf is not None else (),
            "only_ftdna_vs_theytree": tuple(sorted(ftdna - tt_set)) if tt is not None else (),
            "has_yfull": key in self.yfull_ages,
            "has_theytree": key in self.theytree_ages,
        }


_cache: OverlayIndex | None = None
_cache_sig: tuple | None = None


def _hg_key(name: str) -> str:
    return str(name or "").strip()


def _snp_key(name: str) -> str:
    return str(name or "").strip().upper()


def _as_int(value: Any) -> int | None:
    try:
        if value in (None, "", "-", "—"):
            return None
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _coord(pos: Any, ref: Any, alt: Any) -> tuple[int, str, str] | None:
    p = _as_int(pos)
    r = str(ref or "").strip().upper()
    a = str(alt or "").strip().upper()
    if p is None or not r or not a:
        return None
    return p, r, a


def _bp_label(years: int | None) -> str:
    if years is None:
        return "—"
    return f"{years:,} ybp"


def format_age_line(age: AgeOverlay) -> str:
    """Одна строка для UI: источник и цифры в ybp (не CE/BCE Discover)."""
    if age.source == "yfull":
        bits = [f"formed {_bp_label(age.formed_bp)}"]
        if age.formed_lo is not None and age.formed_hi is not None:
            bits[-1] += f" [{age.formed_lo:,}–{age.formed_hi:,}]"
        bits.append(f"TMRCA {_bp_label(age.tmrca_bp)}")
        if age.tmrca_lo is not None and age.tmrca_hi is not None:
            bits[-1] += f" [{age.tmrca_lo:,}–{age.tmrca_hi:,}]"
        return "YFull · " + " · ".join(bits)
    if age.source == "theytree":
        return f"TheYtree · age {_bp_label(age.tmrca_bp)}" + (f" · {age.note}" if age.note else "")
    return age.source


def _default_dirs() -> dict[str, Path]:
    return {
        "yfull_ytree": data_dir() / "yfull" / "ytree",
        "theytree": data_dir() / "theytree",
    }


def resolve_overlay_dir(store: Store, key: str) -> Path | None:
    """Папка источника: из Store, иначе дефолт data/, если там есть файлы."""
    path = store.path(key)
    candidates = [Path(path)] if path else []
    candidates.append(_default_dirs()[key])
    for cand in candidates:
        if not cand.is_dir():
            continue
        if key == "yfull_ytree" and any(cand.glob("tree_*.json")):
            return cand
        if key == "theytree" and (cand / "tree").is_dir():
            return cand
    return None


def _source_sig(store: Store) -> tuple:
    rows: list[tuple] = [("v", INDEX_VERSION)]
    for key in ("yfull_ytree", "theytree"):
        path = resolve_overlay_dir(store, key)
        if not path:
            rows.append((key, None))
            continue
        if key == "yfull_ytree":
            files = sorted(path.glob("tree_*.json"))
            tip = files[-1] if files else None
            if tip and tip.is_file():
                st = tip.stat()
                rows.append((key, tip.name, st.st_mtime_ns, st.st_size))
            else:
                rows.append((key, None))
            continue
        stats = path / "metadata" / "data_statistics.json"
        tree = path / "tree"
        rel = next(iter(sorted(tree.glob("*snp_relations*.csv"))), None) if tree.is_dir() else None
        struct = next(iter(sorted(tree.glob("*structure*.csv"))), None) if tree.is_dir() else None
        found = False
        for p in (stats, struct, rel):
            if p and p.is_file():
                st = p.stat()
                rows.append((key, p.name, st.st_mtime_ns, st.st_size))
                found = True
        if not found:
            rows.append((key, None))
    return tuple(rows)


def _sig_digest(sig: tuple) -> str:
    return hashlib.sha256(repr(sig).encode("utf-8")).hexdigest()[:16]


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig") as fh:
        kept: list[str] = []
        for line in fh:
            s = line.lstrip()
            if not s or s.startswith("#") or s.startswith('"#'):
                continue
            kept.append(line)
    return list(csv.DictReader(io.StringIO("".join(kept))))


def _add_snp(index: OverlayIndex, name: str, *,
             aliases: Iterable[str] = (),
             hg19=None, hg38=None, t2t=None,
             bad: bool = False, combbed: bool = False, ancestor: bool = False,
             haplogroup: str | None = None) -> None:
    key = _snp_key(name)
    if not key:
        return
    prev = index.snps.get(key)
    alias_set = {a for a in (_snp_key(x) for x in aliases) if a and a != key}
    hgs: set[str] = set()
    if haplogroup:
        hgs.add(haplogroup)
    if prev:
        alias_set.update(prev.aliases)
        hgs.update(prev.haplogroups)
        hg19 = hg19 or prev.hg19
        hg38 = hg38 or prev.hg38
        t2t = t2t or prev.t2t
        bad = bad or prev.bad_region
        combbed = combbed or prev.combbed
        ancestor = ancestor or prev.is_ancestor
        name = prev.name or name
    # Ограничиваем списки, чтобы индекс не раздувался.
    alias_t = tuple(sorted(alias_set)[:24])
    hg_t = tuple(sorted(hgs)[:8])
    index.snps[key] = SnpOverlay(
        name=name.strip() or key,
        aliases=alias_t,
        hg19=hg19, hg38=hg38, t2t=t2t,
        bad_region=bad, combbed=combbed, is_ancestor=ancestor,
        haplogroups=hg_t,
    )


def _load_yfull(index: OverlayIndex, folder: Path) -> str | None:
    files = sorted(folder.glob("tree_*.json"))
    if not files:
        return None
    path = files[-1]
    root = json.loads(path.read_text(encoding="utf-8"))
    ver = path.stem.replace("tree_", "")

    def walk(node: dict) -> None:
        hid = _hg_key(node.get("id") or "")
        kids_raw = [c for c in (node.get("children") or []) if isinstance(c, dict)]
        if hid:
            index.yfull_ages[hid] = AgeOverlay(
                source="yfull",
                formed_bp=_as_int(node.get("formed")),
                formed_lo=_as_int(node.get("formedlowage")),
                formed_hi=_as_int(node.get("formedhighage")),
                tmrca_bp=_as_int(node.get("tmrca")),
                tmrca_lo=_as_int(node.get("tmrcalowage")),
                tmrca_hi=_as_int(node.get("tmrcahighage")),
            )
            child_ids = tuple(
                cid for c in kids_raw
                if (cid := _hg_key(c.get("id") or ""))
            )
            if child_ids:
                index.yfull_children[hid] = child_ids
            raw = str(node.get("snps") or "").strip()
            tokens: list[str] = []
            if raw:
                for part in _SNP_SPLIT.split(raw):
                    part = part.strip()
                    if not part:
                        continue
                    aliases = [a.strip() for a in _ALIAS_SPLIT.split(part) if a.strip()]
                    if not aliases:
                        continue
                    tokens.extend(aliases)
                    for a in aliases:
                        _add_snp(index, a, aliases=aliases, haplogroup=hid)
            if tokens:
                # Уникальные, порядок первого появления.
                seen: set[str] = set()
                ordered: list[str] = []
                for t in tokens:
                    k = _snp_key(t)
                    if k not in seen:
                        seen.add(k)
                        ordered.append(t)
                index.yfull_snps[hid] = tuple(ordered[:80])
        for child in kids_raw:
            walk(child)

    walk(root)
    index.meta["yfull"] = {"file": path.name, "version": ver, "nodes": len(index.yfull_ages)}
    return ver


def _load_theytree(index: OverlayIndex, folder: Path) -> str | None:
    struct = next(iter(sorted((folder / "tree").glob("*structure*.csv"))), None)
    rel = next(iter(sorted((folder / "tree").glob("*snp_relations*.csv"))), None)
    stats_path = folder / "metadata" / "data_statistics.json"
    ver = None
    if stats_path.is_file():
        try:
            ver = str(json.loads(stats_path.read_text(encoding="utf-8")).get("version") or "")
        except (ValueError, OSError):
            ver = None

    kids_acc: dict[str, list[str]] = {}
    if struct and struct.is_file():
        for row in _read_csv_rows(struct):
            name = _hg_key(row.get("name") or "")
            if not name or name.upper() == "ROOT":
                continue
            age = _as_int(row.get("ages"))
            index.theytree_ages[name] = AgeOverlay(
                source="theytree",
                tmrca_bp=age,
                note="structure.ages",
            )
            parent = _hg_key(row.get("parentname") or "")
            if parent:
                index.theytree_parent[name] = parent
                kids_acc.setdefault(parent, []).append(name)
            if str(row.get("is_t2t") or "") == "1":
                index.theytree_t2t.add(name)
        for parent, kids in kids_acc.items():
            # Стабильный порядок, без дублей.
            seen: set[str] = set()
            ordered: list[str] = []
            for k in kids:
                if k not in seen:
                    seen.add(k)
                    ordered.append(k)
            index.theytree_children[parent] = tuple(ordered)

    # Сначала собираем алиасы по локусу hg38, потом пишем SnpOverlay.
    locus_names: dict[tuple[int, str, str], set[str]] = {}
    pending: list[tuple] = []
    if rel and rel.is_file():
        for row in _read_csv_rows(rel):
            snp = str(row.get("snpname") or "").strip()
            if not snp:
                continue
            hg38 = _coord(row.get("location_hg38"), row.get("ref_hg38"), row.get("alt_hg38"))
            hg19 = _coord(row.get("location_hg19"), row.get("ref_hg19"), row.get("alt_hg19"))
            t2t = _coord(row.get("location_t2t2"), row.get("ref_t2t2"), row.get("alt_t2t2"))
            bad = str(row.get("is_bad_region") or "") == "1"
            combbed = str(row.get("is_combbed_area") or "") == "1"
            ancestor = str(row.get("is_ancestor") or "") == "1"
            hg = _hg_key(row.get("haplogroup") or "") or None
            if hg38:
                locus_names.setdefault(hg38, set()).add(snp)
            pending.append((snp, hg19, hg38, t2t, bad, combbed, ancestor, hg))

        for snp, hg19, hg38, t2t, bad, combbed, ancestor, hg in pending:
            aliases = locus_names.get(hg38, set()) if hg38 else set()
            _add_snp(index, snp, aliases=aliases, hg19=hg19, hg38=hg38, t2t=t2t,
                     bad=bad, combbed=combbed, ancestor=ancestor, haplogroup=hg)

    index.meta["theytree"] = {
        "version": ver,
        "nodes": len(index.theytree_ages),
        "snps": len(pending),
        "t2t_branches": len(index.theytree_t2t),
    }
    return ver


def build_overlays(store: Store | None = None) -> OverlayIndex:
    store = store or Store()
    index = OverlayIndex()
    yf = resolve_overlay_dir(store, "yfull_ytree")
    tt = resolve_overlay_dir(store, "theytree")
    if yf:
        _load_yfull(index, yf)
    if tt:
        _load_theytree(index, tt)
    index.meta["snp_index"] = len(index.snps)
    return index


def load_overlays(store: Store | None = None, *, force: bool = False) -> OverlayIndex | None:
    """Вернуть индекс или None, если оба источника не настроены / пусты."""
    from . import tree_db

    global _cache, _cache_sig
    store = store or Store()
    sig = _source_sig(store)
    if not any(row[0] in ("yfull_ytree", "theytree") and row[1] is not None for row in sig):
        return None
    if not force and _cache is not None and _cache_sig == sig:
        return _cache

    digest = _sig_digest(sig)
    blob_key = (digest,)
    if not force:
        try:
            obj = tree_db.load_index_blob(tree_db.KIND_OVERLAYS, blob_key)
            if isinstance(obj, OverlayIndex) and obj.meta.get("version") == INDEX_VERSION:
                _cache, _cache_sig = obj, sig
                return obj
        except Exception:  # noqa: BLE001
            pass

    index = build_overlays(store)
    index.meta["version"] = INDEX_VERSION
    if not index.yfull_ages and not index.theytree_ages and not index.snps:
        return None
    try:
        tree_db.save_index_blob(tree_db.KIND_OVERLAYS, blob_key, "overlays", index)
    except Exception:  # noqa: BLE001
        pass
    _cache, _cache_sig = index, sig
    return index
