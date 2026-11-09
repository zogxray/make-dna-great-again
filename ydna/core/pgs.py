"""Полигенные оценки (PGS) генома из F1 против 1000 Genomes. Без UI.

Модели — строки catalog.tsv (сборка F2 / pgs_catalog) и их файлы весов из PGS Catalog.
Референс — «тонкая» панель: из полного PGEN 1000G один раз извлекаются биаллельные SNP, чьи rsID
есть в весах моделей. Образец считается здесь же, панель — plink2 --score на том же наборе вариантов.

Сопоставление по rsID и паре аллелей, без позиций: сборка сырого файла (GRCh37/38) не важна."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import genome_db
from .genome import Genome
from .jobs import Cancelled, Job, run_tool
from .store import Store, config_dir
from .ylink import cache_dir

THEMES = (("intelligence_emotions", "Intelligence & emotions"), ("sleep", "Sleep"),
          ("behavior", "Behavior"), ("brain", "Brain"), ("appearance", "Appearance"),
          ("body", "Body"), ("fitness", "Fitness"), ("diet", "Diet"))
# Подгруппы вкладки DISEASES (excluded_disease из pgs_catalog.EXCLUDE_RE).
DISEASE_GROUPS = (
    ("cancer", "Cancer"),
    ("sleep_clinical", "Sleep clinical"),
    ("psych", "Psych"),
    ("other", "Other"),
)
THEME_LABEL = {**dict(THEMES), **dict(DISEASE_GROUPS)}

_DISEASE_BUCKETS = (
    ("cancer", re.compile(
        r"cancer|carcinoma|neoplasm|tumou?r|leuk[a]?emia|lymphoma|melanoma|glioma", re.I)),
    ("sleep_clinical", re.compile(r"sleep apn|obstructive sleep|restless", re.I)),
    ("psych", re.compile(
        r"anxiety|depress|ptsd|schizophren|bipolar|autis|\badhd\b|attention deficit|"
        r"panic|anorexia|bulimia|eating disorder|delirium|dementia|amnestic|"
        r"post-traumatic|major depress", re.I)),
)


def disease_group(trait: str, efo: str = "") -> str:
    """Cancer / Sleep clinical / Psych / Other — по trait+EFO excluded_disease."""
    text = f"{trait} {efo}"
    for key, rx in _DISEASE_BUCKETS:
        if rx.search(text):
            return key
    return "other"


def sections_for(models: list["Model"]) -> list[tuple[str, str]]:
    """Порядок секций для overview/compare: DISEASE_GROUPS или THEMES по составу списка."""
    present = {m.theme for m in models}
    primary = DISEASE_GROUPS if present & set(dict(DISEASE_GROUPS)) else THEMES
    out = [(k, lab) for k, lab in primary if k in present]
    known = {k for k, _ in out}
    for k in sorted(present - known):
        out.append((k, THEME_LABEL.get(k, k)))
    return out

# Кураторское меню из GENETIC_IQ_TEST/run_pgs_v4.py TRAITS (уникальные ID, свой порядок и подписи).
# (pgs_id, theme, label)
CURATED: tuple[tuple[str, str, str], ...] = (
    ("PGS001232", "intelligence_emotions", "Fluid intelligence (10,055)"),
    ("PGS001021", "intelligence_emotions", "Anxiety and worry (9,747)"),
    ("PGS001022", "intelligence_emotions", "Worry after an embarrassing situation (7,714)"),
    ("PGS001016", "intelligence_emotions", "Sensitivity and proneness to offense (7,922)"),
    ("PGS001017", "intelligence_emotions", "Tendency to feel nervous (3,348)"),
    ("PGS001049", "intelligence_emotions", "Risk-taking tendency (4,299)"),
    ("PGS001091", "intelligence_emotions", "Loneliness and feelings of isolation (660)"),
    ("PGS001396", "intelligence_emotions", "Loss of interest and enthusiasm (2,431)"),
    ("PGS001080", "intelligence_emotions", "Fatigue and lethargy (5,594)"),
    ("PGS001398", "intelligence_emotions", "Satisfaction with friendships (659)"),
    ("PGS001090", "intelligence_emotions", "Feeling loved during childhood (7,555)"),
    ("PGS001018", "intelligence_emotions", "Leisure and social activities (model 1)"),
    ("PGS001019", "intelligence_emotions", "Participation in sports and social events"),
    ("PGS001020", "intelligence_emotions", "Leisure and social activities (model 3)"),
    ("PGS001055", "sleep", "Chronotype: morningness or eveningness (11,428)"),
    ("PGS001150", "sleep", "Sleep duration (4,106)"),
    ("PGS001000", "sleep", "Tendency to nap during the day (10,264)"),
    ("PGS001001", "sleep", "Ease of getting up in the morning (7,743)"),
    ("PGS000926", "sleep", "Daytime sleepiness (4,194)"),
    ("PGS000984", "behavior", "Video game playing frequency (5,774)"),
    ("PGS001052", "behavior", "Time spent outdoors in summer"),
    ("PGS001053", "behavior", "Time spent outdoors in winter"),
    ("PGS001007", "behavior", "Age at first sexual intercourse (9,997)"),
    ("PGS001087", "behavior", "Alcohol consumption frequency"),
    ("PGS001127", "behavior", "Never smoked"),
    ("PGS001391", "behavior", "Cannabis use"),
    ("PGS001540", "brain", "Brain volume: gray and white matter"),
    ("PGS001541", "brain", "Brain volume: adjusted for head size"),
    ("PGS001544", "brain", "Gray matter volume"),
    ("PGS001640", "brain", "White matter volume"),
    ("PGS001633", "brain", "Cortical volume: peripheral gray matter"),
    ("PGS001639", "brain", "Brain ventricular volume"),
    ("PGS001070", "brain", "Brain ventricular volume: normalized"),
    ("PGS001642", "brain", "Head size based on T1 scaling"),
    ("PGS001563", "brain", "Hippocampal volume (model 1)"),
    ("PGS001564", "brain", "Hippocampal volume (model 2)"),
    ("PGS001637", "brain", "Thalamic volume (model 1)"),
    ("PGS001638", "brain", "Thalamic volume (model 2)"),
    ("PGS001542", "brain", "Caudate nucleus volume (model 1)"),
    ("PGS001543", "brain", "Caudate nucleus volume (model 2)"),
    ("PGS001635", "brain", "Putamen volume (model 1)"),
    ("PGS001636", "brain", "Putamen volume (model 2)"),
    ("PGS001631", "brain", "Globus pallidus volume (model 1)"),
    ("PGS001632", "brain", "Globus pallidus volume (model 2)"),
    ("PGS001537", "brain", "Nucleus accumbens volume (model 1)"),
    ("PGS001538", "brain", "Nucleus accumbens volume (model 2)"),
    ("PGS001612", "brain", "Ventral striatum volume (model 1)"),
    ("PGS001613", "brain", "Ventral striatum volume (model 2)"),
    ("PGS001546", "brain", "Amygdala: left only (115 variants)"),
    ("PGS001547", "brain", "Brainstem"),
    ("PGS001507", "brain", "Iron in the basal ganglia (T2*, model 1)"),
    ("PGS001508", "brain", "Iron in the basal ganglia (T2*, model 2)"),
    ("PGS001509", "brain", "Iron in the basal ganglia (T2*, model 3)"),
    ("PGS001510", "brain", "Iron in the basal ganglia (T2*, model 4)"),
    ("PGS001511", "brain", "Iron in the basal ganglia (T2*, model 5)"),
    ("PGS001512", "brain", "Iron in the basal ganglia (T2*, model 6)"),
    ("PGS001513", "brain", "Iron in the basal ganglia (T2*, model 7)"),
    ("PGS001092", "appearance", "Black hair"),
    ("PGS001095", "appearance", "Dark brown hair"),
    ("PGS001094", "appearance", "Brown hair"),
    ("PGS001096", "appearance", "Light brown hair"),
    ("PGS001093", "appearance", "Blond hair"),
    ("PGS001098", "appearance", "Red hair"),
    ("PGS001097", "appearance", "Other hair color"),
    ("PGS001246", "appearance", "Tans easily"),
    ("PGS001245", "appearance", "Tans moderately"),
    ("PGS001244", "appearance", "Tans slightly"),
    ("PGS001247", "appearance", "Burns without tanning"),
    ("PGS001257", "appearance", "Childhood sunburns"),
    ("PGS001141", "appearance", "Face appears younger than actual age"),
    ("PGS001071", "appearance", "Face appears age-appropriate"),
    ("PGS001072", "appearance", "Face appears older than actual age (39 variants)"),
    ("PGS001229", "body", "Standing height (51,209)"),
    ("PGS000969", "body", "Sitting height"),
    ("PGS000998", "body", "Height relative to peers at age 10"),
    ("PGS001228", "body", "Body mass index (BMI)"),
    ("PGS001230", "body", "Weight"),
    ("PGS001101", "body", "Body fat percentage"),
    ("PGS001169", "body", "Fat-free mass"),
    ("PGS001227", "body", "Waist circumference"),
    ("PGS001162", "body", "Hip circumference"),
    ("PGS001379", "body", "Body surface area"),
    ("PGS001074", "fitness", "Other types of physical exercise"),
    ("PGS001075", "fitness", "Walking pace"),
    ("PGS001073", "fitness", "Duration of walks"),
    ("PGS001397", "fitness", "Walking for pleasure"),
    ("PGS001119", "fitness", "Left-hand grip strength"),
    ("PGS001120", "fitness", "Right-hand grip strength"),
    ("PGS001167", "fitness", "Leg muscle mass (model 1)"),
    ("PGS001166", "fitness", "Leg muscle mass (model 2)"),
    ("PGS001165", "fitness", "Arm muscle mass (model 1)"),
    ("PGS001146", "fitness", "Arm muscle mass (model 2)"),
    ("PGS001168", "fitness", "Trunk muscle mass"),
    ("PGS001108", "fitness", "Basal metabolic rate"),
    ("PGS001237", "fitness", "Lung function: FVC"),
    ("PGS001236", "fitness", "Lung function: FEV1"),
    ("PGS001009", "fitness", "Lung function: PEF"),
    ("PGS001233", "fitness", "Resting heart rate (model 1)"),
    ("PGS001523", "fitness", "Resting heart rate (model 2)"),
    ("PGS000978", "diet", "Bread consumption (3 483 variants)"),
    ("PGS000991", "diet", "Never eats sugar (2 816 variants)"),
    ("PGS001034", "diet", "Adding salt to food (6 651 variants)"),
    ("PGS001056", "diet", "Beef consumption (991 variants)"),
    ("PGS001057", "diet", "Breakfast cereal consumption (4 882 variants)"),
    ("PGS001058", "diet", "Cookies and breakfast cereal consumption (146 variants)"),
    ("PGS001059", "diet", "Consumption of other grain products (1 286 variants)"),
    ("PGS001060", "diet", "Cheese consumption (6 678 variants)"),
    ("PGS001061", "diet", "Cooked vegetable consumption (1 085 variants)"),
    ("PGS001062", "diet", "Fresh fruit consumption (4 257 variants)"),
    ("PGS001063", "diet", "Dietary changes due to illness over the past 5 years (730 variants)"),
    ("PGS001064", "diet", "Skimmed milk consumption (116 variants)"),
    ("PGS001065", "diet", "Never eats wheat (5 variants)"),
    ("PGS001066", "diet", "Poultry consumption (1 609 variants)"),
    ("PGS001067", "diet", "Processed meat consumption (2 346 variants)"),
    ("PGS001068", "diet", "Dietary variety (1 353 variants)"),
    ("PGS001069", "diet", "Water consumption (4 994 variants)"),
    ("PGS001389", "diet", "Dried fruit consumption (989 variants)"),
)
AUTOSOMES = frozenset(str(i) for i in range(1, 23))
COMPLEMENT = {"A": "T", "T": "A", "C": "G", "G": "C"}
PALINDROMIC = ({"A", "T"}, {"C", "G"})
MIN_VARIANTS = 100
MIN_ALLELE_FRAC = 0.99           # референсные образцы с меньшей долей учтённых аллелей не берутся
PANEL_VERSION = 1
SUPERPOPS = {"EUR": "European", "EAS": "East Asian", "SAS": "South Asian", "AFR": "African",
             "AMR": "Admixed American"}
POPS = {
    "CEU": "Utah, European ancestry", "FIN": "Finnish", "GBR": "British", "IBS": "Iberian, Spain",
    "TSI": "Toscani, Italy", "CDX": "Dai Chinese", "CHB": "Han Chinese, Beijing",
    "CHS": "Southern Han Chinese", "JPT": "Japanese, Tokyo", "KHV": "Kinh Vietnamese",
    "BEB": "Bengali, Bangladesh", "GIH": "Gujarati, Houston", "ITU": "Indian Telugu, UK",
    "PJL": "Punjabi, Lahore", "STU": "Sri Lankan Tamil, UK", "ACB": "African Caribbean, Barbados",
    "ASW": "African American, SW USA", "ESN": "Esan, Nigeria", "GWD": "Gambian (Mandinka)",
    "LWK": "Luhya, Kenya", "MSL": "Mende, Sierra Leone", "YRI": "Yoruba, Nigeria",
    "CLM": "Colombian, Medellín", "MXL": "Mexican, Los Angeles", "PEL": "Peruvian, Lima",
    "PUR": "Puerto Rican",
}

_lock = threading.Lock()
_panels: dict[str, "Panel"] = {}


# ───────────────────────── каталог и веса ─────────────────────────

@dataclass
class Model:
    pgs_id: str
    theme: str
    trait: str
    efo: str
    variants: int
    usable: int
    cov_w: float | None
    eur_pct: float | None
    method: str
    build: str
    publication: str
    pub_date: str
    status: str = "ok"          # catalog.tsv status; curated may be low_coverage / …


def _float(s: str | None) -> float | None:
    try:
        return float(s) if s not in (None, "") else None
    except ValueError:
        return None


def catalog_path(folder: Path) -> Path:
    return folder / "catalog" / "catalog.tsv"


def weights_path(folder: Path, pgs_id: str) -> Path:
    return folder / "data" / f"{pgs_id}.txt.gz"


def ensure_weights(folder: Path, pgs_id: str, job: Job | None = None) -> Path:
    """Путь к весам; если файла нет — скачать с FTP PGS Catalog."""
    dest = weights_path(folder, pgs_id)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    from .pgs_catalog import fetch_weights  # лениво: pgs_catalog импортирует pgs
    dest.parent.mkdir(parents=True, exist_ok=True)
    prev = job.stage if job else None
    if job:
        job.set_stage(f"downloading weights · {pgs_id}")
    try:
        ok = fetch_weights({"id": pgs_id, "ftp": ""}, dest)
    finally:
        if job and prev is not None:
            job.stage = prev
    if not ok or not dest.exists() or dest.stat().st_size <= 0:
        raise RuntimeError(f"could not download weight file for {pgs_id}")
    return dest


def _model_from_row(r: dict) -> Model | None:
    pid = r.get("pgs_id") or ""
    if not re.fullmatch(r"PGS\d{6}", pid):
        return None
    return Model(pid, r.get("theme") or "", (r.get("trait_reported") or pid).strip(),
                 r.get("efo") or "", int(_float(r.get("variants_number")) or 0),
                 int(_float(r.get("usable_variants")) or 0), _float(r.get("cov_w")),
                 _float(r.get("eur_pct")), r.get("method") or "", r.get("build") or "",
                 r.get("publication") or "", r.get("pub_date") or "",
                 status=r.get("status") or "")


def read_catalog_index(folder: Path) -> dict[str, Model]:
    """Все строки catalog.tsv с валидным PGS ID (любой status)."""
    path = catalog_path(folder)
    out: dict[str, Model] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            m = _model_from_row(r)
            if m:
                out[m.pgs_id] = m
    return out


def load_catalog(folder: Path, *, force_sync: bool = False) -> list[Model]:
    """Модели status=ok из DuckDB.

    Sync TSV→DB только если force_sync / пусто / catalog.tsv новее (ensure_catalog).
    F2 check веса смотрит на диск (weights_path.exists), force не нужен.
    """
    from . import pgs_db
    pgs_db.ensure_catalog(folder, force=force_sync)
    return pgs_db.load_catalog_ok()


def load_curated(folder: Path) -> list[Model]:
    """Кураторское меню из pgs.menu_items (без лишнего sync — F4/ensure уже в DB)."""
    from . import pgs_db
    pgs_db.seed_menus()
    return pgs_db.load_menu_models("curated")


Weight = tuple[str, str, str, str, float]       # rsID, хромосома, effect, other, вес


def read_weights(path: Path) -> list[Weight]:
    """Аутосомные SNP с rsID; повторы rsID отбрасываются."""
    rows: list[Weight] = []
    seen: set[str] = set()
    need = ("rsID", "chr_name", "effect_allele", "other_allele", "effect_weight")
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        head: list[str] = []
        for ln in fh:
            if ln.strip() and not ln.startswith("#"):
                head = ln.rstrip("\r\n").split("\t")
                break
        if not set(need) <= set(head):
            raise RuntimeError(f"{path.name}: unexpected columns {head}")
        ci, hi, ei, oi, wi = (head.index(c) for c in need)
        width = max(ci, hi, ei, oi, wi)
        for ln in fh:
            t = ln.rstrip("\r\n").split("\t")
            if len(t) <= width:
                continue
            rsid = t[ci].strip()
            chrom = t[hi].strip().upper().removeprefix("CHR")
            ea = t[ei].strip().upper()
            oa = t[oi].strip().upper()
            try:
                w = float(t[wi])
            except ValueError:
                continue
            if (not rsid or rsid in seen or chrom not in AUTOSOMES or ea not in COMPLEMENT
                    or oa not in COMPLEMENT or ea == oa or not math.isfinite(w)):
                continue
            seen.add(rsid)
            rows.append((rsid, chrom, ea, oa, w))
    if not rows:
        raise RuntimeError(f"{path.name}: no usable autosomal SNPs with rsIDs")
    return rows


# ───────────────────────── образец ─────────────────────────

Genotypes = dict[str, tuple[str, str, str]]     # rsID → (хромосома, аллель 1, аллель 2)


def genotypes(g: Genome) -> Genotypes:
    out: Genotypes = {}
    for name, c in g.chroms.items():
        if name not in AUTOSOMES:
            continue
        for rid, gt in zip(c.ids, c.gts):
            if len(gt) == 2 and gt[0] in COMPLEMENT and gt[1] in COMPLEMENT:
                out[rid] = (name, gt[0], gt[1])
    return out


def match(geno: Genotypes, weights: list[Weight]) -> dict[str, tuple[str, str, float, int]]:
    """rsID → (effect, other, вес, доза effect-аллеля). Обратная цепь допускается только для
    не-палиндромных SNP: у A/T и C/G её не отличить от другого аллеля."""
    out: dict[str, tuple[str, str, float, int]] = {}
    for rsid, chrom, ea, oa, w in weights:
        g = geno.get(rsid)
        if not g or g[0] != chrom:
            continue
        a, b = g[1], g[2]
        pair = {ea, oa}
        if a not in pair or b not in pair:
            if pair in PALINDROMIC:
                continue
            a, b = COMPLEMENT[a], COMPLEMENT[b]
            if a not in pair or b not in pair:
                continue
        out[rsid] = (ea, oa, w, (a == ea) + (b == ea))
    return out


# ───────────────────────── референсная панель ─────────────────────────

def panel_dir() -> Path:
    return cache_dir() / "pgs-panel"


@dataclass
class Panel:
    prefix: Path
    alleles: dict[str, tuple[str, str]]          # rsID → (REF, ALT)
    samples: dict[str, tuple[str, str]]          # IID → (суперпопуляция, популяция)


def _signature(ref: Path, models: list[Model]) -> str:
    st = (ref / "all_hg38.pgen").stat()
    ids = ",".join(sorted(m.pgs_id for m in models))
    return hashlib.sha1(f"{PANEL_VERSION}|{ref.resolve()}|{st.st_size}|{st.st_mtime_ns}|{ids}"
                        .encode()).hexdigest()[:16]


def panel_info() -> dict | None:
    try:
        return json.loads((panel_dir() / "panel.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def plink() -> str:
    exe = shutil.which("plink2")
    if not exe:
        raise RuntimeError("plink2 not found in PATH (https://www.cog-genomics.org/plink/2.0/)")
    return exe


def model_rsids(folder: Path, models: list[Model], job: Job) -> set[str]:
    """Все rsID из весов моделей; кешируется, потому что крупные модели — по миллиону строк."""
    ids_key = hashlib.sha1(",".join(sorted(m.pgs_id for m in models)).encode()).hexdigest()[:12]
    cache = panel_dir() / f"model-rsids-{ids_key}.txt"
    if cache.exists():
        job.set_stage("reading model rsIDs")
        return set(cache.read_text(encoding="utf-8").split())
    job.set_stage("collecting rsIDs from model weights", len(models))
    ids: set[str] = set()
    for i, m in enumerate(models):
        job.check()
        p = weights_path(folder, m.pgs_id)
        if p.exists():
            ids.update(w[0] for w in read_weights(p))
        job.progress(i + 1)
    cache.parent.mkdir(parents=True, exist_ok=True)
    for old in panel_dir().glob("model-rsids-*.txt"):
        old.unlink()
    cache.write_text("\n".join(sorted(ids)) + "\n", encoding="utf-8")
    return ids


def ensure_panel(ref: Path, folder: Path, models: list[Model], geno: Genotypes, job: Job) -> None:
    """Панель содержит SNP моделей, которые есть на чипе генома. Если у нового генома есть
    rsID моделей, которых в панели нет, она пересобирается с объединённым набором."""
    out = panel_dir()
    sig = _signature(ref, models)
    info = panel_info()
    extract = out / "extract.txt"
    have: set[str] = set()
    if info and info.get("signature") == sig and (out / "panel.pgen").exists() and extract.exists():
        have = set(extract.read_text(encoding="utf-8").split())
    needed = model_rsids(folder, models, job) & geno.keys()
    if needed <= have:
        return
    ids = needed | have
    out.mkdir(parents=True, exist_ok=True)
    tmp_ids = out / "extract.tmp"
    tmp_ids.write_text("\n".join(sorted(ids)) + "\n", encoding="utf-8")
    tmp = out / "build"
    job.unit = "B"
    run_tool([plink(), "--pfile", str(ref / "all_hg38"), "vzs", "--extract", str(tmp_ids),
              "--snps-only", "just-acgt", "--max-alleles", "2", "--rm-dup", "exclude-all",
              "--make-pgen", "--threads", "8", "--out", str(tmp)],
             job, tmp.with_suffix(".pgen"), f"building the reference panel from 1000 Genomes ({len(ids):,} rsIDs)")
    for ext in ("pgen", "pvar", "psam"):
        tmp.with_suffix(f".{ext}").replace(out / f"panel.{ext}")
    tmp_ids.replace(extract)
    variants = sum(1 for ln in (out / "panel.pvar").open(encoding="utf-8") if not ln.startswith("#"))
    (out / "panel.json").write_text(json.dumps({
        "signature": sig, "reference": str(ref), "models": len(models), "rsids": len(ids),
        "variants": variants, "built": datetime.now().isoformat(timespec="seconds"),
    }, indent=2), encoding="utf-8")
    with _lock:
        _panels.clear()


def load_panel(ref: Path) -> Panel:
    prefix = panel_dir() / "panel"
    key = f"{prefix}|{prefix.with_suffix('.pvar').stat().st_mtime_ns}"
    with _lock:
        if key in _panels:
            return _panels[key]
    alleles: dict[str, tuple[str, str]] = {}
    with prefix.with_suffix(".pvar").open(encoding="utf-8") as fh:
        cols: list[str] = []
        for ln in fh:
            if ln.startswith("##"):
                continue
            t = ln.rstrip("\n").split("\t")
            if ln.startswith("#"):
                cols = [c.lstrip("#") for c in t]
                continue
            row = dict(zip(cols, t))
            alleles[row["ID"]] = (row["REF"], row["ALT"])
    samples: dict[str, tuple[str, str]] = {}
    with (ref / "all_hg38.psam").open(encoding="utf-8") as fh:
        head = fh.readline().lstrip("#").rstrip("\n").split("\t")
        for ln in fh:
            row = dict(zip(head, ln.rstrip("\n").split("\t")))
            samples[row.get("IID", "")] = (row.get("SuperPop", ""), row.get("Population", ""))
    panel = Panel(prefix, alleles, samples)
    with _lock:
        _panels.clear()
        _panels[key] = panel
    return panel


# ───────────────────────── счёт ─────────────────────────

@dataclass
class GroupStat:
    n: int
    mean: float
    sd: float
    pct: float
    z: float | None


@dataclass
class Result:
    pgs_id: str
    trait: str
    theme: str
    weights: int = 0                   # аутосомных SNP в весах
    matched: int = 0                   # сопоставлено с генотипом
    used: int = 0                      # из них есть в референсе (общий набор)
    score: float | None = None         # средний вклад на аллель, как SCORE_AVG у plink
    groups: dict[str, GroupStat] = field(default_factory=dict)   # EUR, EUR/GBR, …, ALL
    hist: list[int] = field(default_factory=list)                # EUR, HIST_BINS корзин между min и max
    hist_at: int | None = None                                   # корзина образца
    error: str = ""
    computed: str = ""

    @property
    def eur(self) -> GroupStat | None:
        return self.groups.get("EUR")

    @classmethod
    def from_dict(cls, d: dict) -> "Result":
        d = dict(d)
        d["groups"] = {k: GroupStat(**v) for k, v in (d.get("groups") or {}).items()}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


HIST_BINS = 24


def percentile(values: list[float], x: float) -> float:
    return 100.0 * (sum(v < x for v in values) + 0.5 * sum(v == x for v in values)) / len(values)


def group_stat(values: list[float], x: float) -> GroupStat:
    m = sum(values) / len(values)
    sd = math.sqrt(sum((v - m) ** 2 for v in values) / len(values))
    return GroupStat(len(values), m, sd, percentile(values, x), (x - m) / sd if sd > 0 else None)


def _run(cmd: list[str], job: Job) -> None:
    """Короткий запуск plink: отмена задачи завершает процесс; прогресс задачи не трогает."""
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        while proc.poll() is None:
            try:
                job.check()
            except Cancelled:
                proc.terminate()
                proc.wait()
                raise
            time.sleep(0.05)
    finally:
        if proc.poll() is None:
            proc.kill()
    if proc.returncode:
        err = (proc.stderr.read().decode(errors="replace") if proc.stderr else "").strip()
        raise RuntimeError(f"plink2 failed ({proc.returncode}): {err[-300:]}")


def _reference_scores(panel: Panel, model: Model, common: dict[str, tuple[str, str, float, int]],
                      job: Job) -> dict[str, float]:
    digest = hashlib.sha1("".join(f"{r}{v[0]}" for r, v in sorted(common.items())).encode()).hexdigest()[:16]
    cache = cache_dir() / "pgs-ref" / f"{model.pgs_id}_{digest}.json"
    try:
        return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    expected = 2 * len(common)
    with tempfile.TemporaryDirectory(prefix="ydna-pgs-") as tmp:
        sf = Path(tmp) / "weights.txt"
        with sf.open("w", encoding="utf-8") as fh:
            fh.write("ID\tA1\tBETA\n")
            for rsid, (ea, _oa, w, _d) in common.items():
                fh.write(f"{rsid}\t{ea}\t{w:.12g}\n")
        out = Path(tmp) / "ref"
        _run([plink(), "--pfile", str(panel.prefix), "--score", str(sf), "1", "2", "3", "header-read",
              "no-mean-imputation", "--threads", "4", "--out", str(out)], job)
        with out.with_suffix(".sscore").open(encoding="utf-8") as fh:
            head = fh.readline().lstrip("#").split()
            col = next(c for c in head if c.endswith("_AVG"))
            vals: dict[str, float] = {}
            for ln in fh:
                row = dict(zip(head, ln.split()))
                v = float(row[col])
                if math.isfinite(v) and float(row.get("ALLELE_CT", expected)) >= MIN_ALLELE_FRAC * expected:
                    vals[row["IID"]] = v
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(vals), encoding="utf-8")
    return vals


def score_model(model: Model, folder: Path, geno: Genotypes, panel: Panel, job: Job) -> Result:
    res = Result(model.pgs_id, model.trait, model.theme, computed=datetime.now().isoformat(timespec="seconds"))
    weights = read_weights(ensure_weights(folder, model.pgs_id, job))
    res.weights = len(weights)
    matched = match(geno, weights)
    res.matched = len(matched)
    common = {r: v for r, v in matched.items()
              if (al := panel.alleles.get(r)) and {v[0], v[1]} == set(al)}
    res.used = len(common)
    if res.used < MIN_VARIANTS:
        raise RuntimeError(f"only {res.used} variants shared by the genome and the reference "
                           f"(matched {res.matched:,} of {res.weights:,})")
    res.score = sum(w * d for _ea, _oa, w, d in common.values()) / (2 * res.used)
    ref = _reference_scores(panel, model, common, job)
    by_group: dict[str, list[float]] = {"ALL": []}
    for iid, v in ref.items():
        sp, pop = panel.samples.get(iid, ("", ""))
        by_group["ALL"].append(v)
        if sp:
            by_group.setdefault(sp, []).append(v)
            by_group.setdefault(f"{sp}/{pop}", []).append(v)
    res.groups = {k: group_stat(v, res.score) for k, v in by_group.items() if len(v) >= 2}
    eur = by_group.get("EUR") or by_group["ALL"]
    lo, hi = min(eur + [res.score]), max(eur + [res.score])
    width = (hi - lo) / HIST_BINS or 1.0
    res.hist = [0] * HIST_BINS
    for v in eur:
        res.hist[min(HIST_BINS - 1, int((v - lo) / width))] += 1
    res.hist_at = min(HIST_BINS - 1, int((res.score - lo) / width))
    return res


# ───────────────────────── результаты ─────────────────────────

def results_path(genome: Path) -> Path:
    """Старый путь JSON (миграция / отладка). Живые результаты — DuckDB pgs.*."""
    tag = hashlib.sha1(str(genome.resolve()).encode()).hexdigest()[:8]
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", genome.stem)[:60]
    return config_dir() / "pgs" / f"{stem}_{tag}.json"


def results_stamp(genome: Path) -> int | None:
    """Метка изменения run (ns epoch) для polling в F4; None если run нет."""
    from . import pgs_db
    return pgs_db.results_stamp(genome)


def load_results(genome: Path) -> dict[str, Result]:
    from . import pgs_db
    return pgs_db.load_results(genome)


def save_result(genome: Path, res: Result) -> None:
    from . import pgs_db
    pgs_db.save_result(genome, res)


def scored_genomes() -> list[Path]:
    """Геномы с хотя бы одним run в DuckDB (файл генома на месте)."""
    from . import pgs_db
    return pgs_db.scored_genomes()


@dataclass
class Pair:
    """Одна модель, посчитанная для двух геномов: EUR-статистика A и B."""
    model: Model
    a: GroupStat
    b: GroupStat

    @property
    def delta(self) -> float:
        return abs(self.a.pct - self.b.pct)


def pair_up(models: list[Model], ra: dict[str, Result], rb: dict[str, Result]) -> tuple[list[Pair], int, int]:
    """Модели с EUR-перцентилем у обоих (в порядке каталога) и сколько посчитано только у A / только у B."""
    def ok(r: Result | None) -> GroupStat | None:
        return r.eur if r and not r.error else None

    pairs, only_a, only_b = [], 0, 0
    for m in models:
        a, b = ok(ra.get(m.pgs_id)), ok(rb.get(m.pgs_id))
        if a and b:
            pairs.append(Pair(m, a, b))
        else:
            only_a += bool(a)
            only_b += bool(b)
    return pairs, only_a, only_b


def profile_r(pairs: list[Pair]) -> float | None:
    """Корреляция Пирсона z-оценок A и B по общим моделям: ~0 у неродственных, ~1 у одного человека."""
    xs = [(p.a.z, p.b.z) for p in pairs if p.a.z is not None and p.b.z is not None]
    if len(xs) < 5:
        return None
    ma, mb = sum(a for a, _ in xs) / len(xs), sum(b for _, b in xs) / len(xs)
    cov = sum((a - ma) * (b - mb) for a, b in xs)
    va, vb = sum((a - ma) ** 2 for a, _ in xs), sum((b - mb) ** 2 for _, b in xs)
    return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else None


def run_models(store: Store, genome_path: Path, models: list[Model], all_models: list[Model],
               job: Job, genome: Genome | None = None) -> str:
    folder, ref = store.path("pgs_models"), store.path("1000g")
    if not folder:
        raise RuntimeError("set the PGS models folder in F2")
    if not ref or not (ref / "all_hg38.pgen").exists():
        raise RuntimeError("1000 Genomes is not ready (F2)")
    job.unit = "items"
    job.set_stage("reading the genome")
    geno = genotypes(genome_db.get(genome_path, genome))
    # Докачать веса скорируемых моделей до сборки панели — иначе rsID не попадут в extract.
    job.set_stage(f"ensuring weight files · {len(models)} model(s)", len(models))
    for i, m in enumerate(models):
        job.check()
        ensure_weights(folder, m.pgs_id, job)
        job.progress(i + 1)
    ensure_panel(ref, folder, all_models, geno, job)
    job.unit = "items"
    panel = load_panel(ref)
    job.set_stage(f"scoring {len(models)} model(s)", len(models))
    ok = failed = 0
    for i, m in enumerate(models):
        job.check()
        job.stage = f"scoring {i + 1}/{len(models)} · {m.pgs_id} · {m.trait}"
        try:
            res = score_model(m, folder, geno, panel, job)
            ok += 1
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            res = Result(m.pgs_id, m.trait, m.theme, error=str(exc),
                         computed=datetime.now().isoformat(timespec="seconds"))
            failed += 1
        save_result(genome_path, res)
        job.progress(i + 1)
    return f"{ok} scored" + (f", {failed} failed" if failed else "")
