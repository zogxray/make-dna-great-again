"""Связь именованных Y-маркеров генома с FTDNA Haplotree и скачанными ресурсами Discover. Без UI.

Haplotree (~130 МБ JSON) сводится к компактному индексу: узлы, родители, вариант → (узел, позиция,
аллели). Индекс — в DuckDB tree.index_blobs (+ память); первое построение ~10 с."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from array import array
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .genome import NO_CALL, Genome, gt_class
from .jobs import USER_AGENT, Job, ensure_space
from .store import Store, cache_dir, config_dir

INDEX_VERSION = 1
DISCOVER_INDEX_VERSION = 1
SEARCH_URL = "https://discover.familytreedna.com/HaplogroupSearch/{marker}"
RESOURCE_URL = "https://discover.familytreedna.com/resources/y-dna/{name}.json"
DELAY = (2.0, 5.0)
RETRIES = 4
AVG_RESOURCE = 200 << 10
RESOURCE_FIELDS = ["haplogroup", "status", "resource_file", "updated_utc", "error"]
# Старый not_found без Search — можно повторить; после Search пишем другой error / status=alias.
_LEGACY_NOT_FOUND = "no such resource (HTTP 403/404)"

DER, ANC, MISMATCH, NOCALL = "der", "anc", "mismatch", "nocall"
# Near-tie: тот же #DER-узлов, сумма DER не хуже лучшей больше чем на Δ (не posterior).
NEAR_DER_SUM_DELTA = 2

_lock = threading.Lock()
_trees: dict[tuple, "Haplotree"] = {}
_discover: dict[tuple, "DiscoverIndex"] = {}
_markers: dict[tuple, tuple[str, list[str]]] = {}


def safe_stem(value: str) -> str:
    """Имя файла ресурса, как в ftdna_downloader.py."""
    readable = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")[:100] or "item"
    return f"{readable}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:10]}"


def stem_name(stem: str) -> str | None:
    """Обратное к safe_stem, когда имя не искажено санитизацией (хеш совпадает)."""
    readable, _, digest = stem.rpartition("_")
    if readable and hashlib.sha256(readable.encode("utf-8")).hexdigest()[:10] == digest:
        return readable
    return None


def eta_text(requests: int) -> str:
    sec = requests * sum(DELAY) / 2
    if sec < 90:
        return f"~{max(1, round(sec / 60))} min" if sec >= 30 else f"~{int(sec)} s"
    if sec < 2 * 3600:
        return f"~{round(sec / 60)} min"
    if sec < 2 * 86400:
        return f"~{sec / 3600:.0f} h"
    return f"~{sec / 86400:.1f} days"


def fetch_cost(requests: int) -> str:
    size = requests * AVG_RESOURCE
    vol = f"{size / (1 << 30):.1f} GB" if size >= 1 << 30 else f"{max(1, size >> 20)} MB"
    return f"{eta_text(requests)}, ~{vol}"


# ───────────────────────── Haplotree ─────────────────────────

class Haplotree:
    def __init__(self, names: list[str], parent: array, variants: dict[str, tuple[int, int, str, str]],
                 published: str) -> None:
        self.names = names
        self.parent = parent
        self.variants = variants
        self.published = published
        self.index = {n: i for i, n in enumerate(names)}

    def __len__(self) -> int:
        return len(self.names)

    def lineage(self, nodes: Iterable[int]) -> set[int]:
        """Узлы вместе со всеми предками."""
        out: set[int] = set()
        for n in nodes:
            while n >= 0 and n not in out:
                out.add(n)
                n = self.parent[n]
        return out

    def depth(self, n: int) -> int:
        d = 0
        while (n := self.parent[n]) >= 0:
            d += 1
        return d

    def child_lists(self) -> list[list[int]]:
        kids: list[list[int]] = [[] for _ in range(len(self))]
        for i, p in enumerate(self.parent):
            if p >= 0:
                kids[p].append(i)
        return kids

    def descendants_of(self, tip: int) -> set[int]:
        """Все потомки tip (без самого tip)."""
        kids = self.child_lists()
        out: set[int] = set()
        stack = list(kids[tip])
        while stack:
            i = stack.pop()
            if i in out:
                continue
            out.add(i)
            stack.extend(kids[i])
        return out

    def positions(self) -> dict[int, list[tuple[int, str, str]]]:
        """позиция hg38 → [(узел, ancestral, derived), …]."""
        cached = getattr(self, "_positions", None)
        if cached is None:
            idx: dict[int, list[tuple[int, str, str]]] = {}
            for _name, (node, pos, anc, der) in self.variants.items():
                if pos:
                    idx.setdefault(pos, []).append((node, anc, der))
            self._positions = idx
            cached = idx
        return cached

    def snp_by_pos(self) -> dict[int, str]:
        """позиция hg38 → первичное имя SNP (первое в индексе вариантов)."""
        cached = getattr(self, "_snp_by_pos", None)
        if cached is None:
            idx: dict[int, str] = {}
            for name, (_node, pos, _anc, _der) in self.variants.items():
                if pos and pos not in idx:
                    idx[pos] = name
            self._snp_by_pos = idx
            cached = idx
        return cached

    def path_of(self, n: int) -> list[str]:
        chain: list[str] = []
        seen: set[int] = set()
        while n >= 0 and n not in seen:
            seen.add(n)
            chain.append(self.names[n])
            n = self.parent[n]
        chain.reverse()
        return chain


def _build_index(path: Path) -> tuple:
    with path.open("rb") as fh:
        data = json.load(fh)
    nodes = data["allNodes"]
    ids = list(nodes)
    pos_of = {k: i for i, k in enumerate(ids)}
    names = [nodes[k]["name"] for k in ids]
    parent = array("i", (pos_of.get(str(nodes[k].get("parentId")), -1) for k in ids))
    variants: dict[str, tuple[int, int, str, str]] = {}
    for i, k in enumerate(ids):
        for v in nodes[k].get("variants") or ():
            if v.get("variant"):
                variants.setdefault(v["variant"], (i, v.get("position") or 0,
                                                   v.get("ancestral") or "", v.get("derived") or ""))
    return names, parent, variants, str(data.get("publishedDate") or "")


def cached_haplotree_size(path: Path) -> int | None:
    """Node count if this haplotree is already in the process cache (no I/O rebuild)."""
    try:
        st = path.stat()
        key = (str(path.resolve()), st.st_size, st.st_mtime_ns, INDEX_VERSION)
    except OSError:
        return None
    with _lock:
        tree = _trees.get(key)
        return len(tree) if tree is not None else None


def load_haplotree(path: Path) -> Haplotree:
    from . import tree_db

    st = path.stat()
    key = (str(path.resolve()), st.st_size, st.st_mtime_ns, INDEX_VERSION)
    with _lock:
        if key in _trees:
            return _trees[key]
        payload = None
        try:
            payload = tree_db.load_index_blob(tree_db.KIND_HAPLOTREE, key)
        except Exception:
            payload = None
        if payload is None:
            payload = _build_index(path)
            try:
                tree_db.save_index_blob(tree_db.KIND_HAPLOTREE, key, path, payload)
            except Exception:
                pass
        _trees.clear()
        _trees[key] = tree = Haplotree(*payload)
        return tree


# ───────────────────────── Discover ─────────────────────────

@dataclass
class Discover:
    folder: Path
    names: set[str]                 # скачанные гаплогруппы
    not_found: set[str]             # ответили 404 (resources.csv)
    unnamed: int = 0                # файлы, имя которых не восстановить

    @property
    def log(self) -> Path:
        return self.folder.parent.parent / "resources.csv"


def discover_state(folder: Path) -> Discover:
    by_file: dict[str, str] = {}
    not_found: set[str] = set()
    log = folder.parent.parent / "resources.csv"
    if log.exists():
        with log.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                name = (row.get("haplogroup") or "").strip()
                if not name:
                    continue
                status = row.get("status") or ""
                error = (row.get("error") or "").strip()
                if status == "alias":
                    # Haplotree-имя отсутствует в Discover; скачали подсказку Search.
                    not_found.add(name)
                elif status == "not_found":
                    # Повторяем только старые записи без запроса Search.
                    if error != _LEGACY_NOT_FOUND:
                        not_found.add(name)
                elif row.get("resource_file"):
                    by_file[Path(row["resource_file"]).name] = name
    names: set[str] = set()
    unnamed = 0
    with os.scandir(folder) as it:
        for e in it:
            if not e.name.endswith(".json"):
                continue
            name = stem_name(e.name[:-5]) or by_file.get(e.name)
            if name:
                names.add(name)
            else:
                unnamed += 1
    return Discover(folder, names, not_found - names, unnamed)


# variant: (hg38 pos or 0, lower-case names, ancestral, derived)
Variant = tuple[int, tuple[str, ...], str, str]


@dataclass
class DiscoverIndex:
    """Варианты скачанных Discover JSON — как в check_haplo/reverse_haplo."""
    folder: Path
    nodes: dict[str, list[Variant]]     # haplogroup → variants on the page


def _discover_cache_key(folder: Path) -> tuple:
    n = mtime = 0
    with os.scandir(folder) as it:
        for e in it:
            if e.name.endswith(".json"):
                n += 1
                try:
                    mtime = max(mtime, e.stat().st_mtime_ns)
                except OSError:
                    pass
    return (str(folder.resolve()), n, mtime, DISCOVER_INDEX_VERSION)


def _build_discover_index(folder: Path, progress=None) -> dict[str, list[Variant]]:
    by_file: dict[str, str] = {}
    log = folder.parent.parent / "resources.csv"
    if log.exists():
        with log.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("resource_file") and row.get("haplogroup"):
                    by_file[Path(row["resource_file"]).name] = row["haplogroup"].strip()
    nodes: dict[str, list[Variant]] = {}
    with os.scandir(folder) as it:
        files = [e.path for e in it if e.name.endswith(".json")]
    total = len(files)
    step = max(1, total // 100) if total else 1
    for i, path in enumerate(files, 1):
        if progress and (i == 1 or i % step == 0 or i == total):
            progress("Indexing Discover variants", i, total)
        p = Path(path)
        try:
            with p.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        h = data.get("haplogroup") or {}
        name = (h.get("name") if isinstance(h, dict) else None) or stem_name(p.stem) or by_file.get(p.name)
        if not name:
            continue
        variants: list[Variant] = []
        for v in data.get("variants") or ():
            if not isinstance(v, dict):
                continue
            pos = v.get("position")
            pos_i = int(pos) if isinstance(pos, int) or (isinstance(pos, str) and pos.isdigit()) else 0
            names = [str(v.get("name") or "").lower()]
            for al in v.get("aliases") or ():
                if isinstance(al, str) and al:
                    names.append(al.lower())
            variants.append((pos_i, tuple(dict.fromkeys(n for n in names if n)),
                             str(v.get("anc") or ""), str(v.get("der") or "")))
        nodes[name] = variants
    return nodes


def load_discover_index(folder: Path, progress=None) -> DiscoverIndex:
    from . import tree_db

    key = _discover_cache_key(folder)
    with _lock:
        if key in _discover:
            return _discover[key]
        payload = None
        try:
            payload = tree_db.load_index_blob(tree_db.KIND_DISCOVER, key)
        except Exception:
            payload = None
        if payload is None:
            if progress:
                progress("Building Discover SNP index", 0, 1)
            payload = _build_discover_index(folder, progress)
            try:
                tree_db.save_index_blob(tree_db.KIND_DISCOVER, key, folder, payload)
            except Exception:
                pass
        elif progress:
            progress("Discover SNP index (cached)", 1, 1)
        _discover.clear()
        _discover[key] = idx = DiscoverIndex(folder, payload)
        return idx


def progress_markers(path: Path) -> dict[str, list[str]]:
    """marker → гаплогруппы из Search (progress.csv). Старые версии ftdna_downloader.py оставляли
    колонку marker пустой — тогда маркер восстанавливается из имени файла Search."""
    out: dict[str, list[str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            marker = (row.get("marker") or "").strip()
            if not marker and row.get("search_file"):
                marker = stem_name(Path(row["search_file"]).stem) or ""
            if not marker:
                continue
            try:
                names = json.loads(row.get("result_names") or "[]")
            except ValueError:
                names = []
            out[marker] = [n for n in names if isinstance(n, str)] if isinstance(names, list) else []
    return out


# ───────────────────────── геном ─────────────────────────

def is_named(rid: str) -> bool:
    """Именованный Y-маркер (BY/FT/M/…), не rs* и не VCF-заглушка chrom:pos."""
    if not rid or rid == "." or rid.startswith("rs"):
        return False
    if ":" in rid:
        return False
    return True


def _is_y_placeholder(rid: str, pos: int) -> bool:
    """ID без имени: '.', пусто, Y:… / chrY:… или chrom:pos от VCF-парсера."""
    if not rid or rid == ".":
        return True
    left, sep, right = rid.rpartition(":")
    if not sep:
        return False
    head = left[3:] if left.upper().startswith("CHR") else left
    if head.upper() in ("Y", "24"):
        return True  # Y:2658271 / Y:000000 — синтетика чипа, не имя SNP
    if right.isdigit() and int(right) == pos:
        return True
    return False


def resolve_genome_y_positions(
    genome: Genome,
    tree: Haplotree | None = None,
    overlays=None,
) -> int:
    """Для именованных Y-SNP с pos=0 (Big Y CSV Known SNP) — позиция из дерева.

    Строки, у которых позицию так и не нашли, отбрасываются. → число заполненных.
    """
    y = genome.chroms.get("Y")
    if not y or not y.n or (tree is None and overlays is None):
        return 0
    filled = 0
    for i in range(y.n):
        if int(y.pos[i]) > 0:
            continue
        rid = y.ids[i]
        if not is_named(rid):
            continue
        pos = 0
        if tree is not None:
            v = tree.variants.get(rid)
            if v and v[1]:
                pos = int(v[1])
        if not pos and overlays is not None:
            snp = overlays.snp(rid)
            if snp is not None:
                build = (genome.build_detected or genome.build or "").strip()
                coord = snp.hg19 if build == "37" else snp.hg38
                if not coord:
                    coord = snp.hg38 or snp.hg19
                if coord and coord[0]:
                    pos = int(coord[0])
        if pos > 0:
            y.pos[i] = pos
            filled += 1
    # без позиции в индексе не храним (иначе ломает сортировку / link)
    keep = [i for i in range(y.n) if int(y.pos[i]) > 0]
    if len(keep) != y.n:
        y.ids = [y.ids[i] for i in keep]
        y.gts = [y.gts[i] for i in keep]
        y.pos = array("l", (int(y.pos[i]) for i in keep))
        y.sorted_input = False
        y.finalize()
    elif filled:
        y.sorted_input = False
        y.finalize()
    if filled and "Y positions from tree" not in (genome.layout or ""):
        genome.layout = (genome.layout + " · Y positions from tree").strip(" ·")
    return filled


def annotate_genome_y(
    genome: Genome,
    tree: Haplotree | None = None,
    overlays=None,
) -> int:
    """Подставить имена SNP в placeholder-ID на Y.

    hg38 → FTDNA haplotree (и/или TheYtree hg38); hg37 → TheYtree hg19.
    rs* не трогаем. → число замен.
    """
    y = genome.chroms.get("Y")
    if not y or not y.n:
        return 0
    map38: dict[int, str] = tree.snp_by_pos() if tree is not None else {}
    map37: dict[int, str] = {}
    if overlays is not None:
        if not map38:
            map38 = overlays.snp_by_pos("hg38")
        map37 = overlays.snp_by_pos("hg19")
    if not map37 and not map38:
        return 0
    build = (genome.build_detected or genome.build or "").strip()
    # Сборка из header / majority: 37 → сначала hg19-карта.
    primary, secondary = (map37, map38) if build == "37" else (map38, map37)
    n = 0
    for i in range(y.n):
        pos = int(y.pos[i])
        if not _is_y_placeholder(y.ids[i], pos):
            continue
        name = primary.get(pos) or secondary.get(pos)
        if not name:
            continue
        y.ids[i] = name
        n += 1
    if n:
        y.finalize()
        if "Y SNP names" not in (genome.layout or ""):
            src = "tree/YFull catalogs"
            genome.layout = (genome.layout + f" · Y SNP names from {src}").strip(" ·")
    return n


def allele_state(gt: str, anc: str, der: str) -> str:
    if not gt or all(c in NO_CALL for c in gt):
        return NOCALL
    if der and gt in (der, der * 2):
        return DER
    if anc and gt in (anc, anc * 2):
        return ANC
    return MISMATCH


@dataclass
class Mark:
    node: str
    anc: str
    der: str
    state: str
    downloaded: bool
    pos_ok: bool


@dataclass
class YLink:
    """Именованные Y-маркеры генома, сопоставленные с Haplotree и Discover."""
    marks: dict[int, Mark]          # индекс SNP в хромосоме Y → отметка
    named: int
    pos_ok: int
    states: Counter
    nodes: set[str]
    nodes_downloaded: int
    discover: bool
    published: str
    derived: list[int] = field(default_factory=list)

    @property
    def found(self) -> int:
        return len(self.marks)


@dataclass
class YBranch:
    """Ветка активного генома на haplotree: путь от корня к глубочайшему DER.

    candidates — лучший путь и near-tie / точные ничьи (включая path).
    UI красит развилки и циклит O. path / tip — активный (по умолчанию 0).
    near_tie — среди candidates есть путь слабее точного best (не только равенство).

    uncertain — узлы с het/mismatch (в tip-счёт НЕ входят, только UI).
    pruned — дети Our-пути с anc>der (отсечённые развилки), только UI.
    uncertain_het / uncertain_other — счётчики для статус-строки.
    """
    tip: str
    path: list[str]
    der: dict[str, int]
    anc: dict[str, int]
    derived_marks: int
    candidates: list[list[str]] = field(default_factory=list)
    uncertain: dict[str, int] = field(default_factory=dict)
    pruned: list[str] = field(default_factory=list)
    uncertain_het: int = 0
    uncertain_other: int = 0
    near_tie: bool = False

    def __post_init__(self) -> None:
        if not self.candidates and self.path:
            self.candidates = [list(self.path)]
        elif self.candidates and not self.path:
            self.path = list(self.candidates[0])
            self.tip = self.path[-1] if self.path else self.tip
        if self.pruned and not isinstance(self.pruned, list):
            self.pruned = list(self.pruned)

    @property
    def n_candidates(self) -> int:
        return len(self.candidates) if self.candidates else (1 if self.path else 0)

    @property
    def tie_label(self) -> str:
        """Для статуса/notify: '' | 'tied' | 'near-tie'."""
        if self.n_candidates <= 1:
            return ""
        return "near-tie" if self.near_tie else "tied"

    @property
    def uncertain_marks(self) -> int:
        return int(self.uncertain_het) + int(self.uncertain_other)

    def select(self, index: int) -> None:
        """Сделать candidates[index] активным path/tip (для цикла O)."""
        if not self.candidates:
            return
        i = index % len(self.candidates)
        self.path = list(self.candidates[i])
        self.tip = self.path[-1] if self.path else self.tip
        self.derived_marks = sum(self.der.get(n, 0) for n in self.path)


def link_y(g: Genome, tree: Haplotree, disc: Discover | None) -> YLink | None:
    y = g.chroms.get("Y")
    if not y:
        return None
    have = disc.names if disc else set()
    marks: dict[int, Mark] = {}
    named = pos_ok = 0
    for i, rid in enumerate(y.ids):
        if not is_named(rid):
            continue
        named += 1
        v = tree.variants.get(rid)
        if not v:
            continue
        node, pos, anc, der = v
        name = tree.names[node]
        ok = pos == y.pos[i]
        pos_ok += ok
        marks[i] = Mark(name, anc, der, allele_state(y.gts[i], anc, der), name in have, ok)
    nodes = {m.node for m in marks.values()}
    return YLink(marks, named, pos_ok, Counter(m.state for m in marks.values()), nodes,
                 len(nodes & have), disc is not None, tree.published,
                 [i for i, m in marks.items() if m.state == DER])


def _sample_y_maps(genome: Genome) -> tuple[dict[int, str], dict[str, tuple[int, str]]]:
    """Как load_sample в check_haplo: одна аллель на позицию, индекс по имени маркера."""
    y = genome.chroms.get("Y")
    by_pos: dict[int, str] = {}
    by_name: dict[str, tuple[int, str]] = {}
    if not y:
        return by_pos, by_name
    for i in range(y.n):
        pos = int(y.pos[i])
        gt = y.gts[i]
        by_pos[pos] = gt
        by_name[y.ids[i].lower()] = (pos, gt)
    return by_pos, by_name


def _discover_counts(genome: Genome, index: DiscoverIndex) -> tuple[Counter, Counter]:
    """DER/ANC по страницам Discover (как check_haplo --match any).

    Не используется в our_branch: оставлен для отладки / будущего optional merge.
    """
    by_pos, by_name = _sample_y_maps(genome)
    der: Counter = Counter()
    anc: Counter = Counter()
    for node, variants in index.nodes.items():
        d = a = 0
        for pos, names, anc_a, der_a in variants:
            gt = None
            if pos and pos in by_pos:
                gt = by_pos[pos]
            else:
                for nm in names:
                    hit = by_name.get(nm)
                    if not hit:
                        continue
                    spos, allele = hit
                    if pos and spos != pos:
                        continue          # имя совпало, позиция другая — как check_haplo
                    gt = allele
                    break
            if gt is None:
                continue
            st = allele_state(gt, anc_a, der_a)
            if st == DER:
                d += 1
            elif st == ANC:
                a += 1
        if d:
            der[node] = d
        if a:
            anc[node] = a
    return der, anc


def _node_counts(link: YLink, tree: Haplotree, genome: Genome | None
                 ) -> tuple[Counter, Counter, Counter, int, int]:
    """DER/ANC (+ mismatch для UI) по haplotree: имя и позиция, без двойного счёта.

    mismatch/het в tip-счёт не входят — только подсветка «шум».
    Возвращает der, anc, uncertain[node], het_n, other_mismatch_n.
    """
    der: Counter = Counter()
    anc: Counter = Counter()
    uncertain: Counter = Counter()
    het_n = other_n = 0
    y = genome.chroms.get("Y") if genome else None

    def _note_mismatch(name: str, gt: str) -> None:
        nonlocal het_n, other_n
        uncertain[name] += 1
        if gt_class(gt) == "het":
            het_n += 1
        else:
            other_n += 1

    for i, m in link.marks.items():
        if m.state == DER:
            der[m.node] += 1
        elif m.state == ANC:
            anc[m.node] += 1
        elif m.state == MISMATCH:
            gt = y.gts[i] if y and 0 <= i < y.n else ""
            _note_mismatch(m.node, gt)
    if not y:
        return der, anc, uncertain, het_n, other_n
    by_pos = tree.positions()
    seen_pos: set[int] = set()
    for i in range(y.n):
        if i in link.marks:
            continue
        pos = int(y.pos[i])
        if pos in seen_pos:
            continue
        seen_pos.add(pos)
        hits = by_pos.get(pos)
        if not hits:
            continue
        gt = y.gts[i]
        for node, a, d in hits:
            name = tree.names[node]
            st = allele_state(gt, a, d)
            if st == DER:
                der[name] += 1
            elif st == ANC:
                anc[name] += 1
            elif st == MISMATCH:
                _note_mismatch(name, gt)
    return der, anc, uncertain, het_n, other_n


def _pruned_near_path(tree: Haplotree, paths: list[list[str]],
                      der: Counter, anc: Counter) -> list[str]:
    """Дети узлов Our-пути с anc>der — развилки, которые walk отсёк."""
    kids = tree.child_lists()
    on: set[str] = set()
    for path in paths:
        on.update(path)
    out: set[str] = set()
    for name in on:
        i = tree.index.get(name)
        if i is None:
            continue
        for c in kids[i]:
            cn = tree.names[c]
            if anc[cn] > der[cn]:
                out.add(cn)
    return sorted(out)


def _score_near(got: tuple[int, int], best: tuple[int, int]) -> bool:
    """Тот же #DER-узлов и сумма DER в пределах NEAR_DER_SUM_DELTA от лучшей."""
    n, s = got[0], got[1]
    bn, bs = best[0], best[1]
    if n != bn:
        return False
    return s >= bs - NEAR_DER_SUM_DELTA


def our_branch(link: YLink, tree: Haplotree, genome: Genome | None = None,
               discover: Path | None = None, progress=None) -> YBranch | None:
    """Путь образца по топологии haplotree.

    Счёт DER/ANC — только по вариантам haplotree (имя из YLink + позиция hg38).
    Discover-карточки в tip не участвуют: частичная папка и alias имён узлов
    иначе ломали путь. Аргумент discover оставлен для совместимости вызовов.
    progress(stage, done, total) — для статус-строки, без блокировки UI.
    """
    _ = discover  # tip не зависит от карточек Discover
    if progress:
        progress("Scoring Y SNPs against haplotree", 0, 1)
    der, anc, uncertain, het_n, other_n = _node_counts(link, tree, genome)
    if progress:
        progress("Scoring Y SNPs against haplotree", 1, 1)
    if not der:
        return None
    if progress:
        progress("Finding Y path", 0, 1)
    kids = tree.child_lists()
    # Детские пути с лучшим счётом и near-tie (не только точное равенство).
    memo: dict[int, list[tuple[int, int, list[str]]]] = {}

    def walk(i: int) -> list[tuple[int, int, list[str]]]:
        if i in memo:
            return memo[i]
        name = tree.names[i]
        d, a = der[name], anc[name]
        if a > d:
            memo[i] = []
            return []
        own = 1 if d else 0
        child_paths: list[tuple[int, int, list[str]]] = []
        for c in kids[i]:
            child_paths.extend(walk(c))
        if not child_paths:
            res = [(own, d, [name])]
        else:
            key = max((p[0], p[1]) for p in child_paths)
            keep = [p for p in child_paths if _score_near((p[0], p[1]), key)]
            res = [(own + p[0], d + p[1], [name] + p[2]) for p in keep]
        memo[i] = res
        return res

    roots = [i for i, p in enumerate(tree.parent) if p < 0]
    cands: list[tuple[int, int, list[str]]] = []
    for r in roots:
        for got in walk(r):
            if got[0]:
                cands.append(got)
    if not cands:
        return None
    cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best = (cands[0][0], cands[0][1])
    tied = [c for c in cands if _score_near((c[0], c[1]), best)]
    near_tie = any((c[0], c[1]) != best for c in tied)

    def _trim(raw: list[str]) -> list[str]:
        path = list(raw)
        while path and der[path[-1]] == 0:
            path.pop()
        return path

    # (score, path) — primary: точный best, затем имя tip.
    scored: list[tuple[tuple[int, int], list[str]]] = []
    seen_tips: set[str] = set()
    for own, dsum, raw in tied:
        path = _trim(raw)
        if not path:
            continue
        tip = path[-1]
        if tip in seen_tips:
            continue
        seen_tips.add(tip)
        scored.append(((own, dsum), path))
    scored.sort(key=lambda sp: (0 if sp[0] == best else 1, sp[1][-1]))
    paths = [p for _sc, p in scored]
    if not paths:
        return None
    if progress:
        progress("Finding Y path", 1, 1)
    primary = paths[0]
    pruned = _pruned_near_path(tree, paths, der, anc)
    return YBranch(
        primary[-1], primary, dict(der), dict(anc),
        sum(der[n] for n in primary), candidates=paths,
        uncertain=dict(uncertain), pruned=pruned,
        uncertain_het=het_n, uncertain_other=other_n,
        near_tie=near_tie and len(paths) > 1,
    )


# Версия сериализации YLink в genome.y_links (смена → полный пересчёт).
YLINK_CACHE_ALG = "ylink:v1"


def ylink_fps(genome: Path, store: Store) -> tuple[str, str, str]:
    """(genome_mtime_ns, haplotree_fp, discover_fp) for genome.y_links freshness."""
    genome = Path(genome).expanduser().resolve()
    gmt = str(genome.stat().st_mtime_ns) if genome.is_file() else ""
    tree_path = store.path("ftdna_haplotree")
    if tree_path and Path(tree_path).is_file():
        st = Path(tree_path).stat()
        tfp = f"{st.st_mtime_ns}:{st.st_size}"
    else:
        tfp = ""
    folder = store.path("ftdna_discover")
    if folder and Path(folder).is_dir():
        folder = Path(folder).resolve()
        log = folder.parent.parent / "resources.csv"
        log_m = log.stat().st_mtime_ns if log.is_file() else 0
        dfp = f"{YLINK_CACHE_ALG}:{folder}:{log_m}"
    else:
        dfp = YLINK_CACHE_ALG
    return gmt, tfp, dfp


def _pack_ylink(link: YLink) -> dict:
    return {
        "alg": YLINK_CACHE_ALG,
        "marks": {
            str(i): {
                "node": m.node, "anc": m.anc, "der": m.der,
                "state": m.state, "downloaded": bool(m.downloaded),
                "pos_ok": bool(m.pos_ok),
            }
            for i, m in link.marks.items()
        },
        "named": int(link.named),
        "pos_ok": int(link.pos_ok),
        "states": {str(k): int(v) for k, v in link.states.items()},
        "nodes": sorted(link.nodes),
        "nodes_downloaded": int(link.nodes_downloaded),
        "discover": bool(link.discover),
        "published": str(link.published or ""),
        "derived": [int(i) for i in link.derived],
    }


def _unpack_ylink(raw: dict) -> YLink | None:
    if not isinstance(raw, dict) or raw.get("alg") != YLINK_CACHE_ALG:
        return None
    marks: dict[int, Mark] = {}
    for k, v in (raw.get("marks") or {}).items():
        if not isinstance(v, dict):
            continue
        try:
            marks[int(k)] = Mark(
                str(v.get("node") or ""),
                str(v.get("anc") or ""),
                str(v.get("der") or ""),
                str(v.get("state") or NOCALL),
                bool(v.get("downloaded")),
                bool(v.get("pos_ok")),
            )
        except (TypeError, ValueError):
            continue
    states = Counter({str(k): int(v) for k, v in (raw.get("states") or {}).items()})
    nodes = {str(n) for n in (raw.get("nodes") or [])}
    derived = [int(i) for i in (raw.get("derived") or [])]
    return YLink(
        marks,
        int(raw.get("named") or 0),
        int(raw.get("pos_ok") or 0),
        states,
        nodes,
        int(raw.get("nodes_downloaded") or 0),
        bool(raw.get("discover")),
        str(raw.get("published") or ""),
        derived,
    )


def load_cached_ylink(genome: Path, store: Store) -> YLink | None:
    """Fresh YLink from DuckDB, or None on miss / stale fingerprints."""
    from . import db

    genome = Path(genome).expanduser().resolve()
    gmt, tfp, dfp = ylink_fps(genome, store)
    if not tfp:
        return None
    row = db.fetchone(
        "SELECT payload, genome_mtime_ns, tree_fp, discover_fp "
        "FROM genome.y_links WHERE genome_path = ?",
        [str(genome)],
    )
    if not row:
        return None
    if row[1] != gmt or row[2] != tfp or row[3] != dfp:
        return None
    raw = row[0]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    return _unpack_ylink(raw) if isinstance(raw, dict) else None


def save_cached_ylink(genome: Path, link: YLink, store: Store) -> None:
    from . import db

    genome = Path(genome).expanduser().resolve()
    gmt, tfp, dfp = ylink_fps(genome, store)
    payload = json.dumps(_pack_ylink(link), ensure_ascii=False)
    with db.locked():
        con = db.connect()
        con.execute("""
            INSERT INTO genome.y_links (
                genome_path, payload, genome_mtime_ns, tree_fp, discover_fp, updated_at
            ) VALUES (?, ?::JSON, ?, ?, ?, ?)
            ON CONFLICT (genome_path) DO UPDATE SET
                payload = excluded.payload,
                genome_mtime_ns = excluded.genome_mtime_ns,
                tree_fp = excluded.tree_fp,
                discover_fp = excluded.discover_fp,
                updated_at = excluded.updated_at
        """, [
            str(genome), payload, gmt, tfp, dfp, datetime.now(timezone.utc),
        ])


def link_genome(g: Genome, store: Store) -> YLink | str:
    """YLink или пояснение, почему связи нет. Hits genome.y_links when fresh."""
    if "Y" not in g.chroms:
        return "no Y chromosome in this file"
    tree_path = store.path("ftdna_haplotree")
    if not tree_path or not tree_path.is_file():
        return "set the FTDNA Y-DNA Haplotree path in F2 to link the named Y markers"
    cached = load_cached_ylink(g.path, store)
    if cached is not None:
        return cached
    tree = load_haplotree(tree_path)
    folder = store.path("ftdna_discover")
    disc = discover_state(folder) if folder and folder.is_dir() else None
    link = link_y(g, tree, disc)
    if link is None:
        return "no Y chromosome in this file"
    try:
        save_cached_ylink(g.path, link, store)
    except Exception:  # noqa: BLE001
        pass
    return link


def active_genome() -> Path | None:
    """Активный геном F1 из genome.files (DuckDB)."""
    from . import genome_db
    return genome_db.active_path()


def genome_files() -> list[Path]:
    """Список файлов F1 из genome.files (существующие на диске)."""
    from . import genome_db
    return [p for p in genome_db.listed_paths() if p.is_file()]


def genome_markers(path: Path) -> list[str]:
    """Именованные Y-маркеры: Y-only SELECT из DuckDB, без полной сборки Genome."""
    from . import genome_db

    path = Path(path).expanduser().resolve()
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    with _lock:
        if key in _markers:
            return _markers[key][1]
    ids = genome_db.y_rsids(path)
    if ids is None:
        # ещё не ingested — один раз через ensure
        g = genome_db.get(path)
        y = g.chroms.get("Y")
        ids = list(y.ids) if y else []
    names = [r for r in ids if is_named(r)]
    with _lock:
        _markers.clear()
        _markers[key] = (path.name, names)
    return names


# ───────────────────────── покрытие (F2) ─────────────────────────

@dataclass
class Coverage:
    tree_nodes: int
    downloaded: int                 # скачано из узлов Haplotree
    outside: int                    # скачано, но в текущем Haplotree такого узла нет
    not_found: int                  # 404 у Discover
    n_missing_all: int              # сколько нет во всём дереве (без 404); имена — missing_all_names()
    marker_source: str              # откуда взяты маркеры пользователя
    named: int
    found: int
    linked: int                     # узлы, которые определяют маркеры
    linked_have: int
    lineage: int                    # те же узлы вместе с предками
    lineage_have: int
    missing_mine: list[str]         # чего не хватает для маркеров (без 404), от корня к листьям

    @property
    def percent(self) -> float:
        return 100 * self.downloaded / self.tree_nodes if self.tree_nodes else 0.0


def user_markers(folder: Path | None) -> tuple[str, list[str]]:
    g = active_genome()
    if g:
        try:
            return g.name, genome_markers(g)
        except (OSError, ValueError):
            pass
    csv_path = folder.parent.parent / "progress.csv" if folder else None
    if csv_path and csv_path.exists():
        return "progress.csv", list(progress_markers(csv_path))
    return "", []


_cov_lock = threading.Lock()
_cov_cache: tuple[tuple, Coverage | str] | None = None


def _coverage_fp(store: Store) -> tuple:
    """Лёгкий ключ без scandir Discover (на F2 tick хватает path + mtime tree/genome/csv)."""
    tree_path = store.path("ftdna_haplotree")
    folder = store.path("ftdna_discover")
    g = active_genome()
    parts: list = []
    if tree_path and Path(tree_path).is_file():
        st = Path(tree_path).stat()
        parts.append(("t", str(Path(tree_path).resolve()), st.st_mtime_ns, st.st_size))
    else:
        parts.append(("t", None))
    if folder and Path(folder).is_dir():
        folder = Path(folder).resolve()
        log = folder.parent.parent / "resources.csv"
        log_m = log.stat().st_mtime_ns if log.is_file() else 0
        parts.append(("d", str(folder), log_m))
    else:
        parts.append(("d", None))
    if g and g.is_file():
        st = g.stat()
        parts.append(("g", str(g.resolve()), st.st_mtime_ns, st.st_size))
    else:
        parts.append(("g", None))
    return tuple(parts)


def coverage(store: Store) -> Coverage | str:
    """Покрытие Discover↔Haplotree. Кэш на fp источников (один расчёт на F2 tick)."""
    global _cov_cache
    fp = _coverage_fp(store)
    with _cov_lock:
        if _cov_cache is not None and _cov_cache[0] == fp:
            return _cov_cache[1]
        result = _coverage_compute(store)
        _cov_cache = (fp, result)
        return result


def _coverage_compute(store: Store) -> Coverage | str:
    tree_path = store.path("ftdna_haplotree")
    folder = store.path("ftdna_discover")
    if not tree_path or not tree_path.is_file():
        return "set the FTDNA Y-DNA Haplotree path to compare"
    if not folder or not folder.is_dir():
        return "set the Discover folder to compare"
    tree = load_haplotree(tree_path)
    disc = discover_state(folder)
    have = {tree.index[n] for n in disc.names if n in tree.index}
    skip = {tree.index[n] for n in disc.not_found if n in tree.index}
    source, markers = user_markers(folder)
    linked = {tree.variants[m][0] for m in markers if m in tree.variants}
    found = sum(1 for m in markers if m in tree.variants)
    lineage = tree.lineage(linked)
    mine = sorted(lineage - have - skip, key=lambda n: (tree.depth(n), tree.names[n]))
    n_rest = sum(1 for n in range(len(tree)) if n not in have and n not in skip)
    return Coverage(
        len(tree), len(have), len(disc.names) - len(have), len(disc.not_found), n_rest,
        source, len(markers), found, len(linked), len(linked & have),
        len(lineage), len(lineage & have), [tree.names[n] for n in mine],
    )


def missing_all_names(store: Store) -> list[str]:
    """Имена для fetch-all (лениво; не держим ~100k строк в Coverage)."""
    tree_path = store.path("ftdna_haplotree")
    folder = store.path("ftdna_discover")
    if not tree_path or not Path(tree_path).is_file() or not folder or not Path(folder).is_dir():
        return []
    tree = load_haplotree(tree_path)
    disc = discover_state(folder)
    have = {tree.index[n] for n in disc.names if n in tree.index}
    skip = {tree.index[n] for n in disc.not_found if n in tree.index}
    return [tree.names[n] for n in range(len(tree)) if n not in have and n not in skip]


# ───────────────────────── этапы Discover fetch (F2) ─────────────────────────

FETCH_STAGES = (
    ("our", "1 · Our path"),
    ("children", "2 · Children below Our"),
    ("genome", "3 · This genome"),
    ("all_genomes", "4 · All genomes"),
    ("tree", "5 · Whole tree"),
)


@dataclass
class FetchPlan:
    """Пять этапов докачки Discover. Списки — только ещё не скачанные (и не not_found).

    UI-счётчики exclusive: 3 не включает 1–2, 4 не включает 1–3.
    При старте job этап пересчитывается «как есть» (уже лежащее на диске пропускается).
    """
    tip: str | None
    our: list[str]
    children: list[str]
    genome: list[str]          # exclusive of our∪children
    all_genomes: list[str]     # exclusive of our∪children∪genome
    n_tree: int
    genome_label: str
    n_genomes: int

    def names(self, stage: str) -> list[str]:
        if stage == "our":
            return list(self.our)
        if stage == "children":
            return list(self.children)
        if stage == "genome":
            return list(self.genome)
        if stage == "all_genomes":
            return list(self.all_genomes)
        if stage == "tree":
            return []  # имена — missing_all_names() (лениво)
        return []

    def count(self, stage: str) -> int:
        if stage == "tree":
            return self.n_tree
        return len(self.names(stage))


def _have_skip(tree: Haplotree, disc: Discover) -> tuple[set[int], set[int]]:
    have = {tree.index[n] for n in disc.names if n in tree.index}
    skip = {tree.index[n] for n in disc.not_found if n in tree.index}
    return have, skip


def _missing_names(tree: Haplotree, nodes: set[int], have: set[int], skip: set[int]) -> list[str]:
    ordered = sorted(
        (n for n in nodes if n not in have and n not in skip),
        key=lambda n: (tree.depth(n), tree.names[n]),
    )
    return [tree.names[n] for n in ordered]


def _linked_nodes(tree: Haplotree, markers: list[str]) -> set[int]:
    return {tree.variants[m][0] for m in markers if m in tree.variants}


def _tip_path_indices(store: Store, tree: Haplotree) -> tuple[str | None, list[int]]:
    """Our tip + path indices (корень→tip). Discover index не нужен — счёт по haplotree."""
    from . import genome_db

    path = active_genome()
    if not path:
        return None, []
    try:
        if genome_db.is_fresh(path):
            g = genome_db.genome_from_db(path, only={"Y"})
        else:
            g = genome_db.get(path, store=store)
    except Exception:  # noqa: BLE001
        return None, []
    link = link_y(g, tree, None)
    if not link:
        return None, []
    branch = our_branch(link, tree, g, None)
    if not branch:
        return None, []
    idxs = [tree.index[n] for n in branch.path if n in tree.index]
    return branch.tip, idxs


_plan_lock = threading.Lock()
_plan_cache: tuple[tuple, FetchPlan | str] | None = None


def _plan_fp(store: Store) -> tuple:
    parts = list(_coverage_fp(store))
    # все F1-файлы влияют на этап 4
    for p in genome_files():
        try:
            st = p.stat()
            parts.append(("gf", str(p.resolve()), st.st_mtime_ns, st.st_size))
        except OSError:
            parts.append(("gf", str(p), None))
    return tuple(parts)


def fetch_plan(store: Store) -> FetchPlan | str:
    """План этапов 1–5. Кэш по fp tree/discover/genomes."""
    global _plan_cache
    fp = _plan_fp(store)
    with _plan_lock:
        if _plan_cache is not None and _plan_cache[0] == fp:
            return _plan_cache[1]
        result = _fetch_plan_compute(store)
        _plan_cache = (fp, result)
        return result


def _fetch_plan_compute(store: Store) -> FetchPlan | str:
    tree_path = store.path("ftdna_haplotree")
    folder = store.path("ftdna_discover")
    if not tree_path or not Path(tree_path).is_file():
        return "set the FTDNA Y-DNA Haplotree path"
    if not folder or not Path(folder).is_dir():
        return "set the Discover folder"
    tree = load_haplotree(Path(tree_path))
    disc = discover_state(Path(folder))
    have, skip = _have_skip(tree, disc)

    tip, path_idxs = _tip_path_indices(store, tree)
    path_set = set(path_idxs)
    our = _missing_names(tree, path_set, have, skip)

    child_set: set[int] = set()
    if tip and tip in tree.index:
        child_set = tree.descendants_of(tree.index[tip])
    children = _missing_names(tree, child_set, have, skip)

    active = active_genome()
    g_label = active.name if active else "—"
    this_linked: set[int] = set()
    if active:
        this_linked = _linked_nodes(tree, genome_markers(active))
    prior = path_set | child_set
    genome_excl = _missing_names(tree, this_linked - prior, have, skip)

    all_linked: set[int] = set(this_linked)
    files = genome_files()
    for p in files:
        if active and p.resolve() == active.resolve():
            continue
        try:
            all_linked |= _linked_nodes(tree, genome_markers(p))
        except Exception:  # noqa: BLE001
            continue
    all_excl = _missing_names(tree, all_linked - prior - this_linked, have, skip)

    n_tree = sum(1 for n in range(len(tree)) if n not in have and n not in skip)
    return FetchPlan(
        tip=tip,
        our=our,
        children=children,
        genome=genome_excl,
        all_genomes=all_excl,
        n_tree=n_tree,
        genome_label=g_label,
        n_genomes=len(files),
    )


def stage_fetch_names(store: Store, stage: str) -> list[str]:
    """Имена для job этапа. tree — полный missing; остальные — из плана (уже exclusive)."""
    if stage == "tree":
        return missing_all_names(store)
    plan = fetch_plan(store)
    if isinstance(plan, str):
        return []
    return plan.names(stage)


# ───────────────────────── докачка из Discover ─────────────────────────

def _pause(job: Job, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        job.check()
        time.sleep(0.1)


def _get(url: str, job: Job, label: str) -> bytes | None:
    """Тело ответа или None, если ресурса нет. Discover отдаёт файлы из S3: на отсутствующий
    ключ приходит 403 AccessDenied, а не 404."""
    for attempt in range(1, RETRIES + 1):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                return None
            if exc.code < 500 and exc.code != 429 or attempt == RETRIES:
                raise RuntimeError(f"{label}: HTTP {exc.code}") from exc
            err = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            if attempt == RETRIES:
                raise RuntimeError(f"{label}: {exc}") from exc
            err = str(exc)
        job.stage = f"retry {attempt}/{RETRIES - 1} in {15 * attempt}s · {label}: {err}"
        _pause(job, 15 * attempt)
    return None


def _log(log: Path, name: str, status: str, path: Path, error: str = "") -> None:
    new = not log.exists()
    try:
        rel = str(path.relative_to(log.parent.parent))
    except ValueError:
        rel = str(path)
    with log.open("a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=RESOURCE_FIELDS)
        if new:
            w.writeheader()
        w.writerow({"haplogroup": name, "status": status, "resource_file": rel,
                    "updated_utc": datetime.now(timezone.utc).isoformat(), "error": error})


def _search_hints(name: str, job: Job) -> list[str]:
    """Имена гаплогрупп из HaplogroupSearch; [] если 403/404 или пустой ответ."""
    body = _get(SEARCH_URL.format(marker=urllib.parse.quote(name, safe="")), job, f"search {name}")
    if body is None:
        return []
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise RuntimeError(f"search {name}: invalid JSON") from exc
    if not isinstance(data, list):
        return []
    hints: list[str] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        hint = item.get("name")
        if isinstance(hint, str) and hint and hint not in seen:
            seen.add(hint)
            hints.append(hint)
    return hints


def _save_resource(folder: Path, log: Path, name: str, body: bytes) -> Path:
    data = json.loads(body)
    if not isinstance(data, dict) or not isinstance(data.get("haplogroup"), dict):
        raise RuntimeError(f"{name}: unexpected resource JSON")
    dest = folder / f"{safe_stem(name)}.json"
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(body)
    tmp.replace(dest)
    _log(log, name, "downloaded", dest)
    return dest


def fetch_resources(folder: Path, names: list[str], job: Job) -> str:
    """Докачка Discover: сначала прямой JSON по имени, Search — только при 403/404.

    Типичный haplotree-узел лежит под тем же ключом → один запрос вместо Search+JSON.
    Если ресурса нет — HaplogroupSearch (alias) и JSON по подсказкам; уже пробованное
    имя повторно не качаем. Уже лежащие *.json пропускаются без HTTP.
    """
    folder.mkdir(parents=True, exist_ok=True)
    ensure_space(folder, len(names) * AVG_RESOURCE + (256 << 20))
    log = folder.parent.parent / "resources.csv"
    got = missing = aliased = 0
    direct_hits = 0
    job.unit = "items"
    job.set_stage(f"fetching {len(names):,} haplogroups from Discover", len(names))
    for i, name in enumerate(names):
        dest = folder / f"{safe_stem(name)}.json"
        if dest.exists():
            job.progress(i + 1)
            continue

        # 1) Прямой resource — без Search.
        job.stage = f"fetching {name} · {got:,} saved · {missing:,} missing"
        _pause(job, random.uniform(*DELAY))
        body = _get(RESOURCE_URL.format(name=urllib.parse.quote(name, safe="")), job, name)
        if body is not None:
            _save_resource(folder, log, name, body)
            got += 1
            direct_hits += 1
            job.progress(i + 1)
            continue

        # 2) 403/404 → Search за alias, затем JSON по подсказкам (имя уже пробовали).
        job.stage = f"search {name} · {got:,} saved · {missing:,} missing"
        _pause(job, random.uniform(*DELAY))
        hints = _search_hints(name, job)
        saved = 0
        for target in hints:
            if target == name:
                continue
            tdest = folder / f"{safe_stem(target)}.json"
            if tdest.exists():
                saved += 1
                continue
            job.stage = (f"fetching {target} (for {name})"
                         f" · {got:,} saved · {missing:,} missing")
            _pause(job, random.uniform(*DELAY))
            body = _get(RESOURCE_URL.format(name=urllib.parse.quote(target, safe="")),
                        job, target)
            if body is None:
                continue
            _save_resource(folder, log, target, body)
            got += 1
            saved += 1

        if not dest.exists():
            if saved and hints:
                hint_path = next(
                    (folder / f"{safe_stem(h)}.json" for h in hints
                     if (folder / f"{safe_stem(h)}.json").is_file()),
                    folder / f"{safe_stem(hints[0])}.json",
                )
                _log(log, name, "alias", hint_path,
                     f"direct miss → Search → {', '.join(hints)}")
                aliased += 1
            elif hints:
                missing += 1
                _log(log, name, "not_found", dest,
                     f"direct 403/404; search hints {', '.join(hints)} but no resource JSON")
            else:
                missing += 1
                _log(log, name, "not_found", dest,
                     f"direct 403/404; search: no hint; {_LEGACY_NOT_FOUND}")

        job.progress(i + 1)
    bits = [f"{got:,} downloaded"]
    if direct_hits:
        bits.append(f"{direct_hits:,} direct (no Search)")
    if aliased:
        bits.append(f"{aliased:,} via Search alias")
    if missing:
        bits.append(f"{missing:,} not available in Discover")
    return ", ".join(bits)
