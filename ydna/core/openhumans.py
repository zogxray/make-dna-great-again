"""OpenHumans public catalog (direct-sharing-354): load, classify, filter, download.

Catalog metadata lives under data/samples/openhumans_ds354/ (from scripts/catalog_openhumans.py
or F2 refresh). Downloaded files go to the F2 source folder (default data/samples/openhumans/).

Update policy: keep existing on-disk samples; refresh catalog from the API for *new*
ids; download only missing files, then DuckDB / genome ingest.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from .jobs import Job, USER_AGENT
from .store import data_dir

CATALOG_DIRNAME = "openhumans_ds354"
SAMPLES_DIRNAME = "openhumans"
CATALOG_JSONL = "catalog.jsonl"
CATALOG_SUMMARY = "summary.json"

OH_API = "https://www.openhumans.org/api/public-data/"
OH_SOURCE = "direct-sharing-354"
_Y_HINT = re.compile(
    r"(?:\bY[\s_-]?DNA\b|\bychromosome\b|\bchrY\b|"
    r"23andme|ancestry|ftdna|family\s*tree|myheritage|livingdna|"
    r"genotyp|\.vcf|genome|haplogroup|yfull|theytree)",
    re.I,
)

COMPAT_READY = "ready"
COMPAT_NEED = "need"
COMPAT_PEEK = "peek"
COMPAT_SKIP = "skip"

COMPAT_GLYPH = {
    COMPAT_READY: "●",
    COMPAT_NEED: "○",
    COMPAT_PEEK: "△",
    COMPAT_SKIP: "✖",
}
COMPAT_LABEL = {
    COMPAT_READY: "READY",
    COMPAT_NEED: "NEED CODE",
    COMPAT_PEEK: "PEEK",
    COMPAT_SKIP: "SKIP",
}
COMPAT_STYLE = {
    COMPAT_READY: "#53A94B",
    COMPAT_NEED: "#D4AD72",
    COMPAT_PEEK: "#8BAB4A",
    COMPAT_SKIP: "#71697A",
}

KIND_CHIP = "chip"
KIND_VCF = "vcf"
KIND_ZIP = "zip"
KIND_WEARABLE = "wearable"
KIND_OTHER = "other"

VENDOR_ANY = "any"
VENDORS = ("23andme", "ancestry", "ftdna", "bigy", "vcf", "pgp", "other", "noise")

_NOISE_TAGS = frozenset({
    "gps", "location", "fitbit", "american gut", "survey",
    "applicationinfo", "deviceinfo", "displayinfo", "uploadinfo",
    "preferences", "temporarybasals", "bgreadings", "glucosevalues",
    "apsdata", "treatments", "metadata",
})
_GENO_RE = re.compile(
    r"23andme|ancestry|genotyp|genome|ftdna|family\s*tree|myheritage|livingdna|"
    r"\bvcf\b|big[\s_-]?y|haplogroup|dna-data|pgpc|complete\s*genomics|gencove|"
    r"argen|veritas|promethease",
    re.I,
)


def catalog_dir() -> Path:
    return data_dir() / "samples" / CATALOG_DIRNAME


def default_samples_dir() -> Path:
    return data_dir() / "samples" / SAMPLES_DIRNAME


def catalog_jsonl(path: Path | None = None) -> Path:
    return (path or catalog_dir()) / CATALOG_JSONL


def _oh_ext(name: str) -> str:
    n = name.lower()
    for suf in (".tar.gz", ".tar.bz2", ".vcf.bz2", ".vcf.gz", ".txt.gz", ".csv.bz2",
                ".json.gz", ".fastq.gz"):
        if n.endswith(suf):
            return suf
    return Path(n).suffix if Path(n).suffix else ""


def _oh_api_get(url: str, retries: int = 5) -> dict:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.load(resp)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                json.JSONDecodeError) as exc:
            last = exc
            time.sleep(min(60, 2 ** attempt))
    raise RuntimeError(f"Open Humans API failed: {url}") from last


def _oh_api_row(item: dict) -> dict:
    md = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    tags = md.get("tags") if isinstance(md.get("tags"), list) else []
    tags = [str(t) for t in tags]
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    basename = str(item.get("basename") or "")
    desc = str(md.get("description") or "")
    return {
        "id": item.get("id"),
        "basename": basename,
        "created": item.get("created"),
        "download_url": item.get("download_url"),
        "source": item.get("source"),
        "user_id": user.get("id"),
        "user_name": user.get("name"),
        "username": user.get("username"),
        "tags": tags,
        "tags_join": "|".join(tags),
        "description": desc,
        "creation_date": md.get("creation_date"),
        "sourceURL": md.get("sourceURL"),
        "metadata_json": json.dumps(md, ensure_ascii=False, sort_keys=True),
        "ext": _oh_ext(basename),
        "y_hint": bool(
            _Y_HINT.search(basename) or _Y_HINT.search(desc)
            or any(_Y_HINT.search(t) for t in tags)
        ),
    }


def refresh_catalog_api(
    job: Job | None = None,
    *,
    source: str = OH_SOURCE,
    page_size: int = 100,
    stop_known_pages: int = 5,
    sleep_s: float = 0.12,
) -> tuple[int, "OhCatalog"]:
    """Fetch new public-data rows into catalog.jsonl (append-only for unknown ids).

    Walks offset pages from 0. Stops after `stop_known_pages` consecutive pages
    with no new ids (OH lists tend to be newest-first). Existing rows stay;
    returns (n_new, reloaded catalog).
    """
    root = catalog_dir()
    root.mkdir(parents=True, exist_ok=True)
    jsonl = root / CATALOG_JSONL
    seen: set[int] = set()
    if jsonl.is_file():
        with jsonl.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = row.get("id")
                if isinstance(rid, int):
                    seen.add(rid)
                elif rid is not None:
                    try:
                        seen.add(int(rid))
                    except (TypeError, ValueError):
                        pass

    new_rows: list[dict] = []
    known_streak = 0
    page = 0
    total = 0
    while True:
        if job:
            job.check()
            job.set_stage(
                f"catalog API · off {page * page_size} · +{len(new_rows)} new",
                None, len(new_rows),
            )
        params = {"source": source, "limit": str(page_size), "offset": str(page * page_size)}
        data = _oh_api_get(OH_API + "?" + urllib.parse.urlencode(params))
        total = int(data.get("count") or total or 0)
        batch = data.get("results") or []
        page_new = 0
        for item in batch:
            if not isinstance(item, dict):
                continue
            rid = item.get("id")
            try:
                rid_i = int(rid)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if rid_i in seen:
                continue
            row = _oh_api_row(item)
            row["id"] = rid_i
            seen.add(rid_i)
            new_rows.append(row)
            page_new += 1
        if page_new == 0 and batch:
            known_streak += 1
        else:
            known_streak = 0
        if known_streak >= stop_known_pages:
            break
        if not batch:
            break
        if total and (page + 1) * page_size >= total and page_new == 0:
            break
        page += 1
        if sleep_s > 0:
            time.sleep(sleep_s)

    if new_rows:
        with jsonl.open("a", encoding="utf-8") as fh:
            for row in new_rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        summary = root / CATALOG_SUMMARY
        try:
            prev = json.loads(summary.read_text(encoding="utf-8")) if summary.is_file() else {}
        except (ValueError, OSError):
            prev = {}
        prev.update({
            "catalogued_at_utc": datetime.now(timezone.utc).isoformat(),
            "api_source_filter": source,
            "n_files": len(seen),
            "last_refresh_new": len(new_rows),
            "api_claimed_count": total or prev.get("api_claimed_count"),
        })
        summary.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8")

    global _cache, _cache_mtime
    _cache, _cache_mtime = None, None
    cat = load_catalog(force=True)
    if job:
        job.set_stage(f"catalog +{len(new_rows)} new · {cat.n:,} total", cat.n, cat.n)
    return len(new_rows), cat


@dataclass(frozen=True)
class OhFile:
    id: int
    basename: str
    download_url: str
    source: str
    user_id: str
    username: str
    user_name: str
    tags: tuple[str, ...]
    description: str
    ext: str
    y_hint: bool
    vendor: str
    compat: str
    kind: str

    @property
    def tags_join(self) -> str:
        return "|".join(self.tags)

    def local_name(self) -> str:
        safe = self.basename.replace("/", "_").replace("\\", "_")
        return f"{self.id}_{safe}"

    def local_path(self, folder: Path) -> Path:
        return folder / self.local_name()

    def is_local(self, folder: Path | None) -> bool:
        if folder is None or not folder.is_dir():
            return False
        p = self.local_path(folder)
        return p.is_file() and p.stat().st_size > 0


@dataclass
class OhFilters:
    kind: str = KIND_CHIP          # chip · vcf · zip · all_geno · noise · all
    vendor: str = VENDOR_ANY       # any · 23andme · …
    have: str = "missing"          # any · local · missing
    compat: str = "any"            # any · ready · need · peek
    y: str = "any"                 # any · y · bigy
    hide_noise: bool = True
    query: str = ""

    def label(self) -> str:
        bits = [
            f"kind={self.kind}",
            f"vendor={self.vendor}",
            f"have={self.have}",
            f"compat={self.compat}",
            f"y={self.y}",
        ]
        if self.hide_noise:
            bits.append("noise=hide")
        if self.query.strip():
            bits.append(f"q={self.query.strip()!r}")
        return " · ".join(bits)


@dataclass
class OhCatalog:
    files: list[OhFile] = field(default_factory=list)
    path: Path | None = None
    claimed: int | None = None
    catalogued_at: str = ""

    @property
    def n(self) -> int:
        return len(self.files)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.files:
            out[f"compat:{f.compat}"] = out.get(f"compat:{f.compat}", 0) + 1
            out[f"kind:{f.kind}"] = out.get(f"kind:{f.kind}", 0) + 1
            out[f"vendor:{f.vendor}"] = out.get(f"vendor:{f.vendor}", 0) + 1
        return out


_cache: OhCatalog | None = None
_cache_mtime: int | None = None


def classify(basename: str, tags: list[str], description: str, ext: str,
             y_hint: bool = False) -> tuple[str, str, str]:
    """→ (vendor, compat, kind). Heuristic from metadata only (no file peek)."""
    tags_l = [t.lower() for t in tags]
    tagset = set(tags_l)
    name = (basename or "").lower()
    desc = (description or "").lower()
    blob = f"{name} {desc} {' '.join(tags_l)}"
    ext_l = (ext or "").lower()
    if not ext_l and name:
        for suf in (".vcf.bz2", ".vcf.gz", ".txt.gz", ".csv.bz2", ".tar.gz"):
            if name.endswith(suf):
                ext_l = suf
                break
        else:
            ext_l = Path(name).suffix

    genoish = bool(_GENO_RE.search(blob)) or bool(y_hint)
    noise_hit = bool(tagset & _NOISE_TAGS) or any(
        x in name for x in ("fitbit", "gps-data", "american-gut", "bgreadings")
    )
    if noise_hit and not genoish:
        return "noise", COMPAT_SKIP, KIND_WEARABLE

    is_vcf = (
        "vcf" in tagset
        or ".vcf" in name
        or ext_l in (".vcf", ".vcf.gz", ".vcf.bz2")
    )
    is_bigy = (
        "bigy" in name or "big_y" in name or "big y" in blob or "bigy_data" in name
        or "bigy" in tagset
    )
    if is_bigy:
        return "bigy", COMPAT_NEED, (KIND_VCF if is_vcf else KIND_CHIP)

    if is_vcf:
        if "23andme" in blob:
            return "23andme", COMPAT_NEED, KIND_VCF
        if "ancestry" in blob:
            return "ancestry", COMPAT_NEED, KIND_VCF
        return "vcf", COMPAT_NEED, KIND_VCF

    if "pgpc" in blob or "complete genomics" in blob or "gencove" in blob:
        return "pgp", COMPAT_PEEK, KIND_OTHER

    def _chip_ext() -> bool:
        return ext_l in (".txt", ".txt.gz", ".csv", ".tsv") or name.endswith(
            (".txt", ".csv", ".tsv")
        )

    def _zip_ext() -> bool:
        return ext_l == ".zip" or name.endswith(".zip")

    if "23andme" in tagset or "23andme" in name or "23andme" in desc:
        if _chip_ext() and "unprocessed" not in tagset:
            return "23andme", COMPAT_READY, KIND_CHIP
        if _zip_ext() or "unprocessed" in tagset:
            return "23andme", COMPAT_NEED, KIND_ZIP
        if genoish:
            return "23andme", COMPAT_PEEK, KIND_OTHER

    if ("ancestry" in tagset or "ancestrydna" in tagset or "ancestry" in name
            or "ancestry" in desc):
        if _chip_ext() and "unprocessed" not in tagset:
            return "ancestry", COMPAT_READY, KIND_CHIP
        if _zip_ext() or "unprocessed" in tagset:
            return "ancestry", COMPAT_NEED, KIND_ZIP
        if genoish:
            return "ancestry", COMPAT_PEEK, KIND_OTHER

    if ("ftdna" in tagset or "familytreedna" in blob or "family tree dna" in blob
            or "ftdna" in name):
        return "ftdna", COMPAT_NEED, KIND_CHIP

    if _zip_ext() and genoish:
        return "other", COMPAT_NEED, KIND_ZIP

    if genoish:
        if _chip_ext():
            return "other", COMPAT_PEEK, KIND_CHIP
        return "other", COMPAT_PEEK, KIND_OTHER

    if noise_hit:
        return "noise", COMPAT_SKIP, KIND_WEARABLE
    return "other", COMPAT_SKIP, KIND_OTHER


def _row_to_file(row: dict) -> OhFile | None:
    rid = row.get("id")
    if not isinstance(rid, int):
        try:
            rid = int(rid)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
    tags = row.get("tags")
    if not isinstance(tags, list):
        join = str(row.get("tags_join") or "")
        tags = [t for t in join.split("|") if t]
    tags = [str(t) for t in tags]
    basename = str(row.get("basename") or "")
    desc = str(row.get("description") or "")
    ext = str(row.get("ext") or "")
    y_hint = bool(row.get("y_hint"))
    vendor, compat, kind = classify(basename, tags, desc, ext, y_hint)
    return OhFile(
        id=rid,
        basename=basename,
        download_url=str(row.get("download_url") or ""),
        source=str(row.get("source") or ""),
        user_id=str(row.get("user_id") or ""),
        username=str(row.get("username") or ""),
        user_name=str(row.get("user_name") or ""),
        tags=tuple(tags),
        description=desc,
        ext=ext,
        y_hint=y_hint,
        vendor=vendor,
        compat=compat,
        kind=kind,
    )


def load_catalog(path: Path | None = None, *, force: bool = False) -> OhCatalog:
    """Load Open Humans catalog.

    Prefer in-memory mtime cache, then DuckDB (``data.openhumans_files``) when
    synced to the current ``catalog.jsonl`` mtime. Only parse jsonl (+ classify)
    when the DB is missing/stale or ``force=True``. After a jsonl parse, sync
    into DuckDB so the next open is a single query.
    """
    global _cache, _cache_mtime
    root = path or catalog_dir()
    jsonl = root / CATALOG_JSONL
    if not jsonl.is_file():
        return OhCatalog(path=root)
    mtime = jsonl.stat().st_mtime_ns
    if not force and _cache is not None and _cache_mtime == mtime and _cache.path == root:
        return _cache

    if not force:
        try:
            from . import openhumans_db as ohdb
            cat = ohdb.try_load_catalog(root, mtime)
        except Exception:  # noqa: BLE001 — fall back to jsonl
            cat = None
        if cat is not None and cat.n > 0:
            _cache, _cache_mtime = cat, mtime
            return cat

    files: list[OhFile] = []
    with jsonl.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            item = _row_to_file(row)
            if item:
                files.append(item)
    claimed = None
    catalogued_at = ""
    summary = root / CATALOG_SUMMARY
    if summary.is_file():
        try:
            data = json.loads(summary.read_text(encoding="utf-8"))
            catalogued_at = str(data.get("catalogued_at_utc") or "")
            n = data.get("n_files")
            if isinstance(n, int):
                claimed = n
        except (ValueError, TypeError, OSError):
            pass
    cat = OhCatalog(files=files, path=root, claimed=claimed, catalogued_at=catalogued_at)
    _cache, _cache_mtime = cat, mtime
    # Keep DuckDB in sync so F8/F2 can skip jsonl next time.
    try:
        from . import openhumans_db as ohdb
        ohdb.sync_catalog(cat, force=force)
    except Exception:  # noqa: BLE001
        pass
    return cat


def local_ids(folder: Path | None) -> set[int]:
    if folder is None or not folder.is_dir():
        return set()
    out: set[int] = set()
    for p in folder.iterdir():
        if not p.is_file() or p.name.endswith(".part"):
            continue
        head = p.name.split("_", 1)[0]
        if head.isdigit():
            out.add(int(head))
    return out


def is_bigy_file(f: OhFile) -> bool:
    if f.vendor == "bigy":
        return True
    blob = f"{f.basename} {f.description} {f.tags_join}".lower()
    return "bigy" in blob.replace(" ", "").replace("_", "").replace("-", "") or "big y" in blob


def is_y_dna_file(f: OhFile) -> bool:
    """Y-ish: catalog y_hint, Big Y, or explicit YDNA/YDATA/haplogroup tags."""
    if f.y_hint or is_bigy_file(f):
        return True
    tags = {t.lower() for t in f.tags}
    if tags & {"ydna", "ydata", "y-dna", "bigy", "haplogroup", "ychromosome"}:
        return True
    blob = f"{f.basename} {f.description}".lower()
    return bool(re.search(r"\b(y[\s_-]?dna|chr\s*y|haplogroup|big[\s_-]?y)\b", blob))


def _query_match(hay: str, q: str) -> bool:
    """Substring match; also 'big y' ↔ BigY / big_y."""
    q = q.strip().lower()
    if not q:
        return True
    if q in hay:
        return True
    compact_q = re.sub(r"[\s_-]+", "", q)
    compact_h = re.sub(r"[\s_-]+", "", hay)
    return bool(compact_q) and compact_q in compact_h


def apply_filters(files: list[OhFile], flt: OhFilters,
                  folder: Path | None = None) -> list[OhFile]:
    have_ids = local_ids(folder) if flt.have != "any" else set()
    q = flt.query.strip().lower()
    out: list[OhFile] = []
    for f in files:
        if flt.hide_noise and f.compat == COMPAT_SKIP and f.kind == KIND_WEARABLE:
            continue
        if flt.kind == KIND_CHIP and f.kind != KIND_CHIP:
            continue
        if flt.kind == KIND_VCF and f.kind != KIND_VCF:
            continue
        if flt.kind == KIND_ZIP and f.kind != KIND_ZIP:
            continue
        if flt.kind == "noise" and f.kind != KIND_WEARABLE:
            continue
        if flt.kind == "all_geno" and f.compat == COMPAT_SKIP:
            continue
        # kind == all → no kind filter
        if flt.vendor != VENDOR_ANY and f.vendor != flt.vendor:
            continue
        if flt.compat != "any" and f.compat != flt.compat:
            continue
        if flt.y == "y" and not is_y_dna_file(f):
            continue
        if flt.y == "bigy" and not is_bigy_file(f):
            continue
        if flt.have == "local" and f.id not in have_ids:
            continue
        if flt.have == "missing" and f.id in have_ids:
            continue
        if q:
            hay = f"{f.basename} {f.description} {f.tags_join} {f.username} {f.user_name}".lower()
            if not _query_match(hay, q):
                continue
        out.append(f)
    return out


def cycle_kind(flt: OhFilters) -> OhFilters:
    order = (KIND_CHIP, KIND_VCF, KIND_ZIP, "all_geno", "noise", "all")
    i = order.index(flt.kind) if flt.kind in order else 0
    return replace(flt, kind=order[(i + 1) % len(order)])


def cycle_vendor(flt: OhFilters) -> OhFilters:
    order = (VENDOR_ANY,) + VENDORS
    i = order.index(flt.vendor) if flt.vendor in order else 0
    return replace(flt, vendor=order[(i + 1) % len(order)])


def cycle_have(flt: OhFilters) -> OhFilters:
    order = ("missing", "local", "any")
    i = order.index(flt.have) if flt.have in order else 0
    return replace(flt, have=order[(i + 1) % len(order)])


def cycle_compat(flt: OhFilters) -> OhFilters:
    order = ("any", COMPAT_READY, COMPAT_NEED, COMPAT_PEEK)
    i = order.index(flt.compat) if flt.compat in order else 0
    return replace(flt, compat=order[(i + 1) % len(order)])


def cycle_y(flt: OhFilters) -> OhFilters:
    """any → y (Y-ish) → bigy. Widens kind off chip so BigY VCF/CSV are visible."""
    order = ("any", "y", "bigy")
    i = order.index(flt.y) if flt.y in order else 0
    y = order[(i + 1) % len(order)]
    kind = flt.kind
    if y != "any" and kind == KIND_CHIP:
        kind = "all_geno"
    return replace(flt, y=y, kind=kind)


def preset_starter(files: list[OhFile], folder: Path | None,
                   *, n_ready_23: int = 10, n_ready_anc: int = 5,
                   n_both: int = 5, n_ftdna: int = 3, n_bigy: int = 1) -> list[OhFile]:
    """Curated first-download set (prefer not-yet-local)."""
    have = local_ids(folder)
    picked: list[OhFile] = []
    seen: set[int] = set()

    def take_n(pool: list[OhFile], n: int) -> None:
        got = 0
        for f in pool:
            if got >= n:
                break
            if f.id in seen or f.id in have:
                continue
            picked.append(f)
            seen.add(f.id)
            got += 1

    ready_23 = [f for f in files if f.compat == COMPAT_READY and f.vendor == "23andme"]
    ready_anc = [f for f in files if f.compat == COMPAT_READY and f.vendor == "ancestry"]
    ftdna = [f for f in files if f.vendor == "ftdna"]
    bigy = [f for f in files if f.vendor == "bigy"]

    by_user: dict[str, set[str]] = {}
    for f in files:
        if f.compat == COMPAT_READY and f.vendor in ("23andme", "ancestry") and f.user_id:
            by_user.setdefault(f.user_id, set()).add(f.vendor)
    dual_users = {u for u, vs in by_user.items() if len(vs) >= 2}
    dual = [f for f in files
            if f.user_id in dual_users and f.compat == COMPAT_READY]

    take_n(ready_23, n_ready_23)
    take_n(ready_anc, n_ready_anc)
    take_n(dual, n_both)
    take_n(ftdna, n_ftdna)
    take_n(bigy, n_bigy)
    return picked


def download_files(folder: Path, files: list[OhFile], job: Job,
                   store=None) -> str:
    """Download missing → DuckDB; READY also ingest into genome.*.

    On-disk samples are not re-fetched; fresh genome rows are kept.
    """
    from . import openhumans_db
    return openhumans_db.ensure_files(folder, files, job, store=store)


def vendor_short(vendor: str) -> str:
    return {
        "23andme": "23",
        "ancestry": "ANC",
        "ftdna": "FT",
        "bigy": "Y",
        "vcf": "VCF",
        "pgp": "PGP",
        "noise": "—",
        "other": "?",
    }.get(vendor, vendor[:3].upper())
