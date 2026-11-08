"""AADR: таблица .anno, группы, эпохи и сопоставление Y с Haplotree. Без UI.

Генотипы и f3 — в fstats.py. Здесь только то, что читается из текста (.anno/.ind/.snp)."""

from __future__ import annotations

import csv
import hashlib
import math
import re
import threading
from dataclasses import dataclass, field, fields
from pathlib import Path

from .jobs import Job
from .sources import Aadr as AadrSource
from .store import config_dir
from .ylink import Haplotree, YBranch, YLink

CACHE_VERSION = 3
MIN_SNPS = 10_000
F3_MIN_SNPS = 10_000                # ниже — серая строка, в рейтинг не идёт
PASS = {"Pass", "PROVISIONAL_PASS", "MERGE_PASS"}
QUESTIONABLE = {"Questionable"}
DIPLOID = {"DG", "SG"}
OUTGROUP_NAMES = ("Mbuti.DG", "Mbuti", "Mbuti.SG")

# (ключ, подпись, max_bp включительно). bp == 0 — современные, отдельная эпоха.
EPOCHS = (
    ("modern", "Modern (0 BP)", 0),
    ("present", "Contemporary (<200 BP)", 200),
    ("early_modern", "Early modern (200–500 BP)", 500),
    ("medieval", "Medieval (500–1.5k BP)", 1500),
    ("late_antiquity", "Late antiquity (1.5–2k BP)", 2000),
    ("antiquity", "Antiquity (2–3k BP)", 3000),
    ("iron", "Iron Age (3–4k BP)", 4000),
    ("bronze", "Bronze Age (4–6k BP)", 6000),
    ("copper", "Copper Age (6–7k BP)", 7000),
    ("neolithic", "Neolithic (7–11.7k BP)", 11700),
    ("paleolithic", "Meso / Paleolithic (>11.7k BP)", 1e9),
)
EPOCH_LABEL = {k: lab for k, lab, _ in EPOCHS}

_lock = threading.Lock()
_catalogs: dict[tuple, "Catalog"] = {}

# заголовки .anno — по уникальному префиксу, не по номеру колонки (релизы двигают поля)
_ANNO = (
    ("gid", "Genetic ID"),
    ("pid", "Persistent Genetic ID"),
    ("iid", "Individual ID"),
    ("skel_code", "Skeletal code"),
    ("skel", "Skeletal element"),
    ("pub_first", "First publication"),
    ("pub", "Publication abbreviation"),
    ("doi", "doi for publication"),
    ("repo", "Link to the most permanent repository"),
    ("date_method", "Method for Determining Date"),
    ("date", "Date mean in BP"),
    ("date_sd", "Date standard deviation in BP"),
    ("date_full", "Full Date"),
    ("age", "Age at death"),
    ("group", "Group ID"),
    ("locality", "Locality"),
    ("country", "Political Entity"),
    ("lat", "Latitude"),
    ("lon", "Longitude"),
    ("pulldown", "Pulldown Strategy"),
    ("suffix", "Suffices"),
    ("data_type", "Data type"),
    ("n_lib", "No. Libraries"),
    ("coverage", "Mean coverage on 1.15M"),
    ("cov_off", "Mean coverage on non-targeted"),
    ("snps_2m", "SNPs hit on autosomal targets (Computed using easystats on enhance 2M"),
    ("snps", "SNPs hit on autosomal targets (Computed using easystats on 1240k"),
    ("snps_ho", "SNPs hit on autosomal targets (Computed using easystats on HO"),
    ("snps_comp_ho", "SNPs hit on autosomal targets (Computed using easystats on Compatibility_HO"),
    ("snps_comp", "SNPs hit on autosomal targets (Computed using easystats on Compatibility"),
    ("sex", "Molecular Sex"),
    ("family", "Family relations"),
    ("roh", "Sum total of ROH"),
    ("y_term", "Y haplogroup in terminal mutation"),
    ("y_isogg", "Y haplogroup  in ISOGG"),
    ("y_manual", "Y haplogroup manually"),
    ("mt_cov", "mtDNA coverage"),
    ("mt", "mtDNA haplogroup if"),
    ("mt_match", "mtDNA match to consensus"),
    ("damage", "Damage rate"),
    ("sex_ratio", "Sex ratio"),
    ("angsd", "ANGSD MOM"),
    ("hapconx", "hapConX"),
    ("lib_type", "Library type"),
    ("libraries", "Libraries"),
    ("endogenous", "endogenous by library"),
)


def epoch_of(bp: float | None) -> str:
    if bp is None:
        return "unknown"
    if bp <= 0:
        return "modern"
    for key, _lab, hi in EPOCHS:
        if key == "modern":
            continue
        if bp <= hi:
            return key
    return "paleolithic"


def bp_label(bp: float | None) -> str:
    if bp is None:
        return "?"
    if bp <= 0:
        return "present"
    year = 1950 - bp
    if year >= 0:
        return f"{year:.0f} CE"
    return f"{-year:.0f} BCE"


def _num(s: str) -> float | None:
    try:
        v = float(s.replace(",", ""))
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _clean(s: str) -> str:
    s = (s or "").strip().strip('"')
    return "" if s in (".", "..", "n/a", "N/A", "NA", "na") else s


def _blank(s: str) -> str:
    s = _clean(s)
    low = s.lower()
    if not s or low in (".", "..") or low.startswith("n/a"):
        return ""
    return s


def _date_span(raw: str) -> str:
    s = _blank(raw)
    return s.split("(")[0].strip() if s else ""


_SPAN = re.compile(r"(\d+)\s*[-–]\s*(\d+)\s*(calBCE|calCE|BCE|CE)?", re.I)


def date_range(samples: list["Sample"], bp: float | None = None) -> str:
    """Объединяет Full Date образцов, иначе средняя дата группы."""
    bce: list[tuple[int, int]] = []
    other: list[str] = []
    for s in samples:
        if not s.date_span:
            continue
        m = _SPAN.search(s.date_span.replace(",", ""))
        if not m:
            other.append(s.date_span)
            continue
        a, b = int(m.group(1)), int(m.group(2))
        unit = (m.group(3) or "").lower()
        if "bce" in unit:
            bce.append((max(a, b), min(a, b)))
        else:
            other.append(s.date_span)
    if bce:
        return f"{max(x[0] for x in bce)}–{min(x[1] for x in bce)} calBCE"
    if len(other) == 1:
        return other[0]
    return bp_label(bp)


def hapconx_label(raw: str) -> str:
    nums = re.findall(r"[0-9.]+", raw or "")
    if len(nums) < 2:
        return ""
    return f"{float(nums[0]) * 100:.1f}–{float(nums[1]) * 100:.1f}%"


def place_of(locality: str, country: str) -> str:
    loc = (locality or "").split("(")[0].strip()
    country = (country or "").strip()
    if loc and country and country.lower() not in loc.lower():
        return f"{loc} · {country}"
    return loc or country


def _suffix(gid: str) -> str:
    return gid.rsplit(".", 1)[-1].upper() if "." in gid else ""


def _prefer(suffix: str) -> int:
    return {"DG": 3, "SG": 2, "AG": 1, "TW": 1}.get(suffix, 0)


def trio(folder: Path) -> tuple[str, dict[str, Path | None]] | None:
    return AadrSource().trio(folder)


# ───────────────────────── записи ─────────────────────────

@dataclass
class Sample:
    i: int                          # строка .ind = ряд TGENO
    gid: str
    group: str
    keep: bool
    questionable: bool
    diploid: bool
    snps: int
    bp: float | None
    lat: float | None
    lon: float | None
    locality: str
    country: str
    sex: str
    y: str
    mt: str
    pub: str
    person: str
    date_span: str = ""
    coverage: float | None = None
    hapconx: str = ""
    y_isogg: str = ""
    mt_cov: float | None = None
    anno: dict = field(default_factory=dict)


@dataclass
class Group:
    name: str
    samples: list[int]              # индексы Sample в Catalog.samples
    n: int
    bp: float | None
    lat: float | None
    lon: float | None
    epoch: str
    country: str
    locality: str
    y: list[str]
    mt: list[str]


@dataclass
class Catalog:
    folder: Path
    prefix: str
    files: dict[str, Path | None]
    nind: int
    nsnp: int
    samples: list[Sample]
    groups: dict[str, Group]
    by_gid: dict[str, int]
    outgroup: str | None
    n_pass: int
    n_quest: int
    n_drop: int

    def group_list(self, questionable: bool = False) -> list[Group]:
        return [g for g in self.groups.values()
                if any(self.samples[i].keep or (questionable and self.samples[i].questionable)
                       for i in g.samples)]


@dataclass
class Hit:
    """Сопоставление Y-гаплогруппы образца с Haplotree и линией пользователя."""
    node: str
    relation: str                   # ancestor · same · downstream · other
    y: str


ON_BRANCH = frozenset({"same", "ancestor", "downstream"})


# ───────────────────────── разбор .anno ─────────────────────────

def _anno_map(header: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    assess = warn = None
    for i, h in enumerate(header):
        h = h.strip().strip('"')
        hu = h.upper()
        if hu == "ASSESSMENT" or (hu.startswith("ASSESSMENT") and "WARN" not in hu):
            assess = i
        if hu.startswith("ASSESSMENT") and "WARN" in hu:
            warn = i
        best = None
        for key, prefix in _ANNO:
            if key not in out and h.startswith(prefix):
                if best is None or len(prefix) > len(best[1]):
                    best = (key, prefix)
        if best:
            out[best[0]] = i
    if assess is not None:
        out["assess"] = assess
    if warn is not None:
        out["assess_warn"] = warn
    return out


def _person(gid: str, pid: str, iid: str) -> str:
    """Один человек = префикс Genetic ID до суффикса .AG/.DG/.SG (Loschbour.AG и .DG)."""
    return gid.rsplit(".", 1)[0] if "." in gid else gid


def _pick(cands: list[Sample]) -> Sample:
    return max(cands, key=lambda s: (s.snps, _prefer(_suffix(s.gid)), not s.questionable, s.keep))


def as_group(s: Sample) -> Group:
    """Строка списка для одного человека (режим People)."""
    return Group(
        s.gid, [s.i], 1, s.bp, s.lat, s.lon, epoch_of(s.bp),
        s.country, s.locality, [s.y] if s.y else [], [s.mt] if s.mt else [],
    )


def _group(name: str, samples: list[Sample], idxs: list[int]) -> Group:
    kept = [samples[i] for i in idxs if samples[i].keep]
    use = kept or [samples[i] for i in idxs]
    bps = [s.bp for s in use if s.bp is not None]
    lats = [s.lat for s in use if s.lat is not None and s.lon is not None]
    lons = [s.lon for s in use if s.lat is not None and s.lon is not None]
    bp = sorted(bps)[len(bps) // 2] if bps else None
    countries = [s.country for s in use if s.country]
    locs = [s.locality for s in use if s.locality]
    ys = [s.y for s in use if s.y]
    mts = [s.mt for s in use if s.mt]
    return Group(
        name, idxs, len(kept), bp,
        sum(lats) / len(lats) if lats else None,
        sum(lons) / len(lons) if lons else None,
        epoch_of(bp),
        max(set(countries), key=countries.count) if countries else "",
        max(set(locs), key=locs.count) if locs else "",
        sorted(set(ys)), sorted(set(mts)),
    )


def _read_ind(path: Path) -> list[tuple[str, str, str]]:
    rows = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            p = ln.split()
            if len(p) >= 3:
                rows.append((p[0], p[1], p[2]))
    return rows


def _read_anno(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8", errors="replace", newline="") as fh:
        r = csv.reader(fh, delimiter="\t")
        header = next(r)
        cols = _anno_map(header)
        need = ("gid", "group", "assess")
        if not all(k in cols for k in need):
            raise RuntimeError(f"{path.name}: missing columns {need}, got {list(cols)}")
        out: dict[str, dict] = {}
        for row in r:
            def g(k: str) -> str:
                i = cols.get(k)
                return _clean(row[i]) if i is not None and i < len(row) else ""
            gid = g("gid")
            if gid:
                out[gid] = {k: g(k) for k, _ in _ANNO}
                out[gid]["assess"] = g("assess").split()[0] if g("assess") else ""
                out[gid]["assess_warn"] = g("assess_warn")
    return out


def catalog_cache_key(folder: Path) -> tuple | None:
    """Ключ кэша каталога (folder, ind_mtime, anno_mtime, version) или None."""
    found = trio(folder)
    if not found:
        return None
    _prefix, files = found
    if not files.get("ind") or not files["ind"].exists():
        return None
    return (
        str(Path(folder).resolve()),
        files["ind"].stat().st_mtime_ns,
        (files["anno"].stat().st_mtime_ns if files.get("anno") and files["anno"].exists() else 0),
        CACHE_VERSION,
    )


def load_catalog(folder: Path, job: Job | None = None) -> Catalog:
    from . import ref_db

    key = catalog_cache_key(folder)
    if key is None:
        found = trio(folder)
        if not found:
            raise RuntimeError("no AADR .geno in the folder (F2)")
        raise RuntimeError("AADR .ind is missing")
    found = trio(folder)
    assert found is not None
    prefix, files = found
    with _lock:
        if key in _catalogs:
            return _catalogs[key]
    payload: Catalog | None = None
    try:
        payload = ref_db.load_catalog_cache(key)
    except Exception:
        payload = None
    if payload is None:
        if job:
            job.set_stage("reading AADR .anno")
        payload = _build_catalog(folder, prefix, files)
        try:
            ref_db.save_catalog_cache(key, payload)
        except Exception:
            pass
    with _lock:
        _catalogs.clear()
        _catalogs[key] = payload
    return payload


def _build_catalog(folder: Path, prefix: str, files: dict[str, Path | None]) -> Catalog:
    from .sources import _geno_header
    ind = _read_ind(files["ind"])
    anno = _read_anno(files["anno"]) if files.get("anno") and files["anno"].exists() else {}
    hdr = _geno_header(files["geno"]) if files.get("geno") else None
    nind, nsnp = hdr or (len(ind), 0)
    people: dict[str, list[Sample]] = {}
    samples: list[Sample] = []
    for i, (gid, _sex, grp) in enumerate(ind):
        a = anno.get(gid, {})
        assess = a.get("assess") or "Pass"
        snps = int(_num(a.get("snps") or "") or 0)
        quest = assess in QUESTIONABLE
        critical = assess not in PASS and not quest
        keep = (assess in PASS) and (snps >= MIN_SNPS or snps == 0)
        group = a.get("group") or grp
        y = (_blank(a.get("y_manual") or "") or _blank(a.get("y_term") or "")
             or _blank(a.get("y_isogg") or ""))
        s = Sample(i, gid, group, keep and not critical, quest, _suffix(gid) in DIPLOID, snps,
                   _num(a.get("date") or ""), _num(a.get("lat") or ""), _num(a.get("lon") or ""),
                   a.get("locality") or "", a.get("country") or "", a.get("sex") or _sex,
                   y, _blank(a.get("mt") or ""), a.get("pub") or "",
                   _person(gid, a.get("pid") or "", a.get("iid") or ""),
                   date_span=_date_span(a.get("date_full") or ""),
                   coverage=_num(a.get("coverage") or ""),
                   hapconx=_blank(a.get("hapconx") or ""),
                   y_isogg=_blank(a.get("y_isogg") or ""),
                   mt_cov=_num(a.get("mt_cov") or ""),
                   anno=a)
        samples.append(s)
        people.setdefault(s.person, []).append(s)
    winners = {_pick(v).i for v in people.values()}
    n_quest = n_drop = 0
    for s in samples:
        if s.i not in winners:
            s.keep, s.questionable = False, False
            n_drop += 1
        elif s.questionable:
            s.keep = False
            n_quest += 1
        elif not s.keep:
            n_drop += 1
    by_gid = {s.gid: s.i for s in samples}
    buckets: dict[str, list[int]] = {}
    for s in samples:
        buckets.setdefault(s.group, []).append(s.i)
    groups = {name: _group(name, samples, idxs) for name, idxs in buckets.items()}
    outgroup = next((n for n in OUTGROUP_NAMES if n in groups and groups[n].n >= 2), None)
    if outgroup is None:
        outgroup = next((n for n, g in groups.items() if "Mbuti" in n and g.n >= 2), None)
    n_pass = sum(1 for s in samples if s.keep)
    return Catalog(folder, prefix, files, nind, nsnp, samples, groups, by_gid, outgroup,
                   n_pass, n_quest, n_drop)


# ───────────────────────── Y ─────────────────────────

def resolve_y(label: str, tree: Haplotree) -> int | None:
    if not label:
        return None
    if label in tree.index:
        return tree.index[label]
    mut = label.split("-")[-1].split("/")[-1].strip()
    if mut and mut in tree.variants:
        return tree.variants[mut][0]
    if label.startswith("R1b"):
        for cand in ("R-M269", "R-M343", "R-M473"):
            if cand in tree.index:
                return tree.index[cand]
    return None


def sample_hit(s: Sample, tree: Haplotree, derived: set[int], lineage: set[int],
               tip: int | None = None) -> Hit | None:
    n = resolve_y(s.y, tree)
    if n is None:
        return None
    if s.bp is None or s.bp < 200:
        return None
    if tip is not None:
        if n == tip:
            rel = "same"
        elif n in lineage:
            rel = "ancestor"
        elif tip in tree.lineage({n}):
            rel = "downstream"
        else:
            rel = "other"
    elif n in derived:
        rel = "same"
    elif n in lineage:
        rel = "ancestor"
    elif derived and derived <= tree.lineage({n}):
        rel = "downstream"
    else:
        rel = "other"
    return Hit(tree.names[n], rel, s.y)


def y_hits(catalog: Catalog, tree: Haplotree | None, link: YLink | None,
           branch: YBranch | None = None) -> tuple[dict[str, Hit], dict[str, Hit]]:
    """group → Hit и gid → Hit. Древние (bp ≥ 200). Ветка — our_branch, остальное Y — other."""
    if not tree:
        return {}, {}
    derived: set[int] = set()
    lineage: set[int] = set()
    tip: int | None = None
    if branch and branch.tip in tree.index:
        tip = tree.index[branch.tip]
        lineage = {tree.index[n] for n in branch.path if n in tree.index}
        derived = {tip}
    elif link and link.derived:
        derived = {tree.index[m.node] for m in (link.marks[i] for i in link.derived)
                   if m.node in tree.index}
        lineage = tree.lineage(derived)
    rank = {"same": 0, "ancestor": 1, "downstream": 2, "other": 3}
    groups: dict[str, Hit] = {}
    people: dict[str, Hit] = {}
    for g in catalog.groups.values():
        best: Hit | None = None
        for s in (catalog.samples[i] for i in g.samples):
            if not s.keep and not s.questionable:
                continue
            hit = sample_hit(s, tree, derived, lineage, tip)
            if not hit:
                continue
            people[s.gid] = hit
            if best is None or rank[hit.relation] < rank[best.relation]:
                best = hit
        if best:
            groups[g.name] = best
    return groups, people


# ───────────────────────── результаты f3 ─────────────────────────

@dataclass
class F3:
    name: str
    est: float
    se: float
    z: float
    n: int
    snps: int
    cluster: int = 0                # 0 = неотличим от лучшего
    f4_z: float = 0.0               # Z f4(Mbuti, ты; X, лучший); 0 у #1
    epoch_cluster: int = 1          # 0 = неотличим от лучшего в своей эпохе

    @property
    def ok(self) -> bool:
        return self.se > 0 and math.isfinite(self.est) and self.snps >= F3_MIN_SNPS


@dataclass
class Bundle:
    groups: dict[str, F3] = field(default_factory=dict)
    people: dict[str, F3] = field(default_factory=dict)
    check: dict | None = None
    questionable: bool = False
    partial: bool = False
    snps: int = 0
    outgroup: str = ""
    pca: dict | None = None
    qpadm: list | None = None


KEEP = object()                     # save_results: не трогать уже записанный pca/qpadm


def results_path(genome: Path, questionable: bool = False, kind: str = "") -> Path:
    """Старый путь JSON (миграция). Живые результаты — DuckDB ref.*."""
    tag = hashlib.sha1(str(genome.resolve()).encode()).hexdigest()[:8]
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", genome.stem)[:60]
    q = "_q" if questionable else ""
    extra = f"_{kind}" if kind else ""
    return config_dir() / "aadr" / f"{stem}_{tag}{q}{extra}.json"


def _f3_from(raw: dict) -> F3 | None:
    if "est" not in raw:
        return None
    keys = {f.name for f in fields(F3)}
    return F3(**{k: raw[k] for k in keys if k in raw})


def load_bundle(genome: Path, questionable: bool = False, kind: str = "") -> Bundle:
    from . import ref_db
    b = ref_db.load_bundle(genome, panel="aadr", kind=kind or "",
                           questionable=questionable)
    blobs = b.blobs or {}
    return Bundle(
        groups=b.groups, people=b.people, check=blobs.get("check"),
        questionable=bool(b.questionable if b.groups or b.people else questionable),
        partial=b.partial, snps=b.snps, outgroup=b.outgroup,
        pca=blobs.get("pca"), qpadm=blobs.get("qpadm"),
    )


def load_results(genome: Path, questionable: bool = False) -> dict[str, F3]:
    return load_bundle(genome, questionable).groups


def save_results(genome: Path, outgroup: str, snps: int, groups: dict[str, F3], *,
                 people: dict[str, F3] | None = None, check: dict | None = None,
                 questionable: bool = False, partial: bool = False, kind: str = "",
                 pca=KEEP, qpadm=KEEP) -> None:
    from . import ref_db
    blobs: dict = {}
    if pca is not KEEP:
        blobs["pca"] = pca
    if qpadm is not KEEP:
        blobs["qpadm"] = qpadm
    if check is not None:
        blobs["check"] = check
    ref_db.save_bundle(
        genome, panel="aadr", kind=kind or "", questionable=questionable,
        partial=partial, outgroup=outgroup, snps=snps, groups=groups,
        people=people if people is not None else {},
        blobs=blobs,
    )


def mark_clusters(rows: list[F3], z_cut: float = 3.0) -> None:
    """Черновой кластер по f3 ± SE, пока нет f4. cluster=0 у ничьей с лучшим."""
    ok = [r for r in rows if r.ok]
    if not ok:
        return
    best = max(ok, key=lambda r: r.est)
    for r in ok:
        se = math.sqrt(best.se ** 2 + r.se ** 2)
        r.cluster = 0 if (best.est - r.est) < z_cut * se else 1
