"""Сборка локального каталога PGS-моделей (бывший GENETIC_IQ_TEST/build_catalog.py).

Пайплайн (дешёвое сначала; PLINK / 1000G не нужны):
  1. Метаданные всех scores из PGS Catalog REST API (кэш catalog/scores_all.json).
  2. Тема по trait+EFO; явные болезни → status excluded_disease (веса всё равно качаем).
  3. Фильтры: число вариантов, доля EUR в GWAS/dev.
  4. Веса → data/PGS*.txt.gz; покрытие по активному геному F1 (ydna.core.pgs.match).
  5. catalog/catalog.tsv + catalog/pilot_ids.txt; sync в DuckDB (pgs.models).

F2 · PGS models · D вызывает build(). CLI: python -m ydna.core.pgs_catalog RAW…
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import genome_db, pgs, pgs_db
from .jobs import USER_AGENT, Cancelled, Job
from .pgs import Genotypes
from .ylink import active_genome, genome_files

API_ALL = "https://www.pgscatalog.org/rest/score/all"
FTP_SCORE = ("https://ftp.ebi.ac.uk/pub/databases/spot/pgs/scores/"
             "{pid}/ScoringFiles/{pid}.txt.gz")
PAGE = 100

# Первое совпадение выигрывает (порядок важен).
THEME_PATTERNS: list[tuple[str, str]] = [
    ("brain", r"\bbrain\b|hippocamp|thalam|caudate|putamen|pallid|accumbens|striat|amygdal|"
              r"brainstem|\bcortical\b|\bcortex\b|gr[ae]y matter|white matter|"
              r"brain ventric|lateral ventric|cerebral ventric|basal ganglia|\bt2\*"),
    ("sleep", r"\bsleep|chronotype|morningness|eveningness|insomnia|\bnapping|\bnap\b|"
              r"getting up in the morning|\bsnor|daytime dozing|diurnal"),
    ("appearance", r"\bhair\b|hair colo|\bskin\b|tanning|\btan(s|ned)?\b|sunburn|freckl|"
                   r"facial|\bface\b|eye colo|balding|baldness|pigment"),
    ("fitness", r"grip|walking|physical activity|exercise|heart rate|pulse rate|lung function|"
                r"\bfev1|\bfvc|forced (expiratory|vital)|peak expiratory|basal metabolic|"
                r"\bvo2|\bsport|\bgym\b|strenuous|moderate activity"),
    ("body", r"\bheight\b|body mass index|\bbmi\b|\bweight\b|body fat|fat[- ]free|waist|"
             r"hip circumference|body surface|lean (body )?mass|muscle mass|\bobesity|adipos|"
             r"body size|\btrunk\b|\bleg\b|\barm\b|whole body"),
    ("behavior", r"video game|smok|alcohol|cannabis|coffee|caffeine|\btea (intake|consumption)|"
                 r"outdoor|sexual intercourse|drinker|drinking|tobacco|cigarette|television|"
                 r"computer use|\bleisure|social activit|sedentary"),
    ("diet", r"\bintake|consumption|\bdiet|\bfood|bread|cheese|\bbeef|\bfruit|vegetable|cereal|"
             r"\bmilk|\bsalt\b|\bmeat|poultry|\bfish\b|\bbutter|\bwheat|never eat|oily fish"),
    ("intelligence_emotions",
     r"intelligen|cognitive|cognition|\bfluid\b|reasoning|educational attainment|"
     r"years of education|schooling|anxiety|\bworry|nervous|neurotic|\bmood|lonel|happiness|"
     r"wellbeing|well-being|satisfaction|irritab|\brisk[- ]tak|loss of interest|enthusias|"
     r"fatigue|lethargy|offen[cs]e|embarrass|guilt|miserab|\btense\b|depressive symptoms|"
     r"\bfeeling|\bemotion|\bloved|friendship|\bmemory|reaction time|\bmath"),
]
THEME_RE = [(name, re.compile(pat, re.I)) for name, pat in THEME_PATTERNS]

EXCLUDE_RE = re.compile(
    r"cancer|carcinoma|neoplasm|tumou?r|leuk[a]?emia|lymphoma|melanoma|diabet|infarction|stroke|"
    r"coronary|heart (failure|disease|attack)|alzheimer|parkinson|schizophren|bipolar|autis|"
    r"dementia|epilep|asthma|\bcopd\b|chronic obstructive|arthritis|osteopor|fracture|"
    r"sleep apn[o]?ea|major depress|depressive disorder|anxiety disorder|panic disorder|"
    r"\bptsd\b|post-traumatic|anorexia|bulimia|eating disorder|\badhd\b|attention deficit|"
    r"psoria|eczema|dermatitis|acne|vitiligo|alopecia|restless|birth weight|low birth",
    re.I,
)

COLS = [
    "pgs_id", "theme", "status", "trait_reported", "efo", "variants_number", "usable_variants",
    "usable_rate", "matched", "cov_n", "cov_w", "eur_pct", "method", "weight_type",
    "build", "publication", "pub_date",
]


@dataclass
class BuildOpts:
    eur_min: float = 80.0
    allow_unknown_ancestry: bool = False
    min_variants: int = 20
    max_variants: int = 1_000_000
    min_cov: float = 0.30
    pilot_per_theme: int = 6
    workers: int = 4
    max_download: int | None = None
    no_coverage: bool = False
    refresh: bool = False
    themes: set[str] | None = None


def classify(rec: dict) -> tuple[str | None, str | None]:
    text = " ".join([rec["trait_reported"], *rec["efo"]]).lower()
    theme = next((name for name, rx in THEME_RE if rx.search(text)), None)
    if theme is None:
        return None, None
    if EXCLUDE_RE.search(text):
        return theme, "excluded_disease"
    return theme, None


def _http_json(url: str, retries: int = 4) -> dict:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}")


def eur_share(ancestry_distribution: Any) -> float | None:
    ad = ancestry_distribution or {}
    for key in ("gwas", "dev"):
        dist = (ad.get(key) or {}).get("dist")
        if dist:
            try:
                return float(dist.get("EUR", 0.0))
            except (TypeError, ValueError):
                return None
    return None


def trim(rec: dict) -> dict:
    pub = rec.get("publication") or {}
    return {
        "id": rec.get("id"),
        "name": rec.get("name") or "",
        "trait_reported": rec.get("trait_reported") or "",
        "efo": [t.get("label", "") for t in (rec.get("trait_efo") or []) if t],
        "variants_number": rec.get("variants_number"),
        "method": rec.get("method_name") or "",
        "weight_type": rec.get("weight_type") or "",
        "build": rec.get("variants_genomebuild") or "",
        "eur": eur_share(rec.get("ancestry_distribution")),
        "ftp": rec.get("ftp_scoring_file") or "",
        "pub": pub.get("id") or "",
        "pub_date": pub.get("date_publication") or "",
        "pub_author": pub.get("firstauthor") or "",
    }


def fetch_all(cache_path: Path, job: Job | None = None, *, refresh: bool = False,
              log: Callable[[str], None] | None = None) -> list[dict]:
    cache_path = Path(cache_path)
    say = log or (lambda _m: None)
    if cache_path.exists() and not refresh:
        say(f"Using cached catalog metadata: {cache_path}")
        return json.loads(cache_path.read_text(encoding="utf-8"))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    url, records, total = f"{API_ALL}?limit={PAGE}&offset=0", [], None
    say("Downloading PGS Catalog score metadata…")
    if job:
        job.set_stage("PGS Catalog API metadata")
    while url:
        if job:
            job.check()
        body = _http_json(url)
        total = body.get("count", total)
        records.extend(trim(r) for r in body.get("results", []))
        if job:
            if total and job.total != total:
                job.set_stage("PGS Catalog API metadata", int(total), len(records))
            else:
                job.progress(len(records))
        url = body.get("next")
    if total is not None and len(records) != total:
        say(f"WARNING: API reported {total:,} scores but {len(records):,} were received.")
    cache_path.write_text(json.dumps(records), encoding="utf-8")
    return records


def fetch_weights(rec: dict, dest: Path) -> bool:
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        return True
    url = rec["ftp"] or FTP_SCORE.format(pid=rec["id"])
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f)
            tmp.replace(dest)
            return True
        except (urllib.error.URLError, TimeoutError, OSError):
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            time.sleep(2 * (attempt + 1))
    return False


def coverage(path: Path, genos: list[Genotypes]) -> dict:
    """usable/matched/cov_* или {'status': reason}. Правила — как F4 (pgs.match)."""
    try:
        weights = pgs.read_weights(path)
    except RuntimeError as exc:
        msg = str(exc).lower()
        return {"status": "no_rsid_column" if "unexpected columns" in msg
                else "no_usable_variants"}
    except Exception:  # noqa: BLE001 — битый gz и т.п.
        return {"status": "read_error"}
    if not weights:
        return {"status": "no_usable_variants"}
    matched_sets = [set(pgs.match(g, weights)) for g in genos]
    matched = set.intersection(*matched_sets) if matched_sets else set()
    total_w = sum(abs(w[4]) for w in weights) or 1.0
    got_w = sum(abs(w[4]) for w in weights if w[0] in matched)
    return {
        "usable": len(weights),
        "matched": len(matched),
        "cov_n": len(matched) / len(weights),
        "cov_w": got_w / total_w,
    }


def fmt(x: Any, nd: int = 3) -> str:
    return "" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def pick_pilot(rows: list[dict], per_theme: int) -> list[dict]:
    by_theme: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["status"] == "ok":
            by_theme[r["theme"]].append(r)
    chosen: list[dict] = []
    for _theme, items in sorted(by_theme.items()):
        items.sort(key=lambda r: -(r["cov_w"] or 0))
        picked: list[dict] = []
        seen_traits: set[str] = set()
        for r in items:
            key = r["trait_reported"].strip().lower()
            if key not in seen_traits and len(picked) < per_theme:
                picked.append(r)
                seen_traits.add(key)
        for r in items:
            if r not in picked and len(picked) < per_theme:
                picked.append(r)
        chosen.extend(picked)
    return chosen


def coverage_genomes() -> list[Path]:
    """Активный F1 + опционально второй файл (как RAW_1 [RAW_2] у старого скрипта)."""
    active = active_genome()
    if not active or not active.is_file():
        raise RuntimeError("No active genome in F1 — open a genotype file first")
    paths = [active]
    for p in genome_files():
        if p.resolve() != active.resolve() and p.is_file():
            paths.append(p)
            break
    return paths


def build(workdir: Path, job: Job | None = None, opts: BuildOpts | None = None,
          raws: list[Path] | None = None) -> str:
    """Собрать каталог в workdir. Возвращает краткое сообщение для Job."""
    opts = opts or BuildOpts()
    work = Path(workdir).expanduser().resolve()
    cat_dir, data_dir = work / "catalog", work / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cat_dir.mkdir(parents=True, exist_ok=True)

    def say(msg: str) -> None:
        if job:
            job.message = msg

    if opts.themes and not opts.themes <= {n for n, _ in THEME_PATTERNS}:
        raise RuntimeError(f"unknown theme(s): {sorted(opts.themes - {n for n, _ in THEME_PATTERNS})}")

    records = fetch_all(cat_dir / "scores_all.json", job, refresh=opts.refresh, log=say)
    rows: list[dict] = []
    pending: list[tuple[dict, dict]] = []
    stage: Counter[str] = Counter()

    if job:
        job.set_stage("classifying scores", len(records))
    for i, rec in enumerate(records):
        if job and i % 500 == 0:
            job.check()
            job.progress(i)
        theme, reject = classify(rec)
        if theme is None or (opts.themes and theme not in opts.themes):
            stage["no_theme"] += 1
            continue
        row = {
            "pgs_id": rec["id"], "theme": theme, "status": "",
            "trait_reported": rec["trait_reported"],
            "efo": "; ".join(rec["efo"]), "variants_number": rec["variants_number"],
            "usable_variants": None, "usable_rate": None, "matched": None,
            "cov_n": None, "cov_w": None, "eur_pct": rec["eur"], "method": rec["method"],
            "weight_type": rec["weight_type"], "build": rec["build"],
            "publication": rec["pub"], "pub_date": rec["pub_date"],
        }
        n = rec["variants_number"]
        # Технические фильтры — для всех, в т.ч. клинических (иначе DISEASES
        # засоряется моделями без rsID / с 6 SNP вроде PGS000399).
        if not isinstance(n, int) or not (opts.min_variants <= n <= opts.max_variants):
            row["status"] = "variants_out_of_range"
        elif rec["eur"] is None and not opts.allow_unknown_ancestry:
            row["status"] = "ancestry_unknown"
        elif rec["eur"] is not None and rec["eur"] < opts.eur_min:
            row["status"] = "eur_below_threshold"
        elif reject == "excluded_disease":
            row["status"] = "excluded_disease"
            pending.append((rec, row))
        elif reject:
            row["status"] = reject
        else:
            row["status"] = "pending_coverage"
            pending.append((rec, row))
        stage[row["status"]] += 1
        rows.append(row)
    if job:
        job.progress(len(records))

    say(f"{len(records):,} scores · {len(rows):,} themed · {len(pending):,} to cover")

    if pending and not opts.no_coverage:
        if opts.max_download:
            pending = pending[:opts.max_download]
        genome_paths = raws or coverage_genomes()
        if job:
            job.set_stage("loading genome(s) for coverage", len(genome_paths))
        genos = [pgs.genotypes(genome_db.get(p)) for p in genome_paths]
        if job:
            job.set_stage("weights + coverage", len(pending))
        say(f"Downloading weights + coverage for {len(pending):,} candidates…")
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, opts.workers)) as pool:
            futures = {
                pool.submit(fetch_weights, rec, data_dir / f"{rec['id']}.txt.gz"): (rec, row)
                for rec, row in pending
            }
            for fut in as_completed(futures):
                if job:
                    job.check()
                rec, row = futures[fut]
                done += 1
                if job:
                    job.progress(done)
                if not fut.result():
                    row["status"] = "download_failed"
                    continue
                res = coverage(data_dir / f"{rec['id']}.txt.gz", genos)
                if "status" in res:
                    row["status"] = res["status"]
                    continue
                row.update(
                    usable_variants=res["usable"], matched=res["matched"],
                    cov_n=res["cov_n"], cov_w=res["cov_w"],
                    usable_rate=res["usable"] / rec["variants_number"]
                    if rec["variants_number"] else None,
                )
                # Клинические модели остаются excluded_disease, даже при хорошем coverage.
                if row["status"] != "excluded_disease":
                    row["status"] = (
                        "too_few_matched" if res["matched"] < 100
                        else "low_coverage" if res["cov_w"] < opts.min_cov
                        else "ok"
                    )
    elif opts.no_coverage:
        say("Coverage step skipped (--no-coverage)")

    order = {"ok": 0, "low_coverage": 1, "excluded_disease": 2}
    rows.sort(key=lambda r: (r["theme"], order.get(r["status"], 9), -(r["cov_w"] or 0)))
    out = cat_dir / "catalog.tsv"
    if job:
        job.set_stage("writing catalog.tsv")
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(COLS)
        for r in rows:
            w.writerow([
                fmt(r[c]) if c in {"usable_rate", "cov_n", "cov_w"} else
                fmt(r[c], 1) if c == "eur_pct" else fmt(r[c])
                for c in COLS
            ])

    pilot = pick_pilot(rows, opts.pilot_per_theme)
    if pilot:
        (cat_dir / "pilot_ids.txt").write_text(
            "".join(r["pgs_id"] + "\n" for r in pilot), encoding="utf-8")

    if job:
        job.set_stage("syncing DuckDB")
        job.check()
    pgs_db.ensure_catalog(work, force=True)

    final = Counter(r["status"] for r in rows)
    summary = ", ".join(f"{k}={v}" for k, v in final.most_common(6))
    return f"{out.name}: {len(rows):,} rows ({summary})"


def build_for_store(workdir: Path, job: Job) -> str:
    """Точка входа F2 download."""
    try:
        return build(workdir, job)
    except Cancelled:
        raise


def _cli(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Build a local PGS catalog (F2 · PGS models)")
    ap.add_argument("raw", nargs="*", help="genotype files (default: active F1 genome)")
    ap.add_argument("--workdir", default=None, help="output folder (default: data/pgs)")
    ap.add_argument("--themes", help="comma-separated subset of themes")
    ap.add_argument("--eur-min", type=float, default=80.0)
    ap.add_argument("--allow-unknown-ancestry", action="store_true")
    ap.add_argument("--min-variants", type=int, default=20)
    ap.add_argument("--max-variants", type=int, default=1_000_000)
    ap.add_argument("--min-cov", type=float, default=0.30)
    ap.add_argument("--pilot-per-theme", type=int, default=6)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-download", type=int)
    ap.add_argument("--no-coverage", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)

    from .store import data_dir
    work = Path(args.workdir).expanduser().resolve() if args.workdir else data_dir() / "pgs"
    opts = BuildOpts(
        eur_min=args.eur_min,
        allow_unknown_ancestry=args.allow_unknown_ancestry,
        min_variants=args.min_variants,
        max_variants=args.max_variants,
        min_cov=args.min_cov,
        pilot_per_theme=args.pilot_per_theme,
        workers=args.workers,
        max_download=args.max_download,
        no_coverage=args.no_coverage,
        refresh=args.refresh,
        themes=set(args.themes.split(",")) if args.themes else None,
    )
    raws = [Path(p).expanduser().resolve() for p in args.raw] or None
    if raws and len(raws) > 2:
        ap.error("at most two raw files are supported")
    try:
        msg = build(work, opts=opts, raws=raws)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
