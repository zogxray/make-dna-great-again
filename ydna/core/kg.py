"""1000 Genomes (3202, hg38, phased hardcalls): outgroup-f3/f4 с YRI и PCA по частотам.

Сопоставление с чипом — rsID и аллели, координаты из PVAR (hg38). Частоты — plink2 --freq
по Population. YRI — аутгруппа вместо Mbuti."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, fields
from pathlib import Path

import numpy as np

from . import genome_db
from .aadr import F3, Group
from .fstats import Panel, _align, _mark_f4, _record, chip_alleles, f3_blocks
from .genome import Genome
from .jobs import Job, run_tool
from .pgs import AUTOSOMES, COMPLEMENT, PALINDROMIC, POPS, plink
from .store import Store, config_dir
from .ylink import cache_dir

OUTGROUP = "YRI"
MIN_SNPS = 1000
POP_SUPER = {
    "CEU": "EUR", "FIN": "EUR", "GBR": "EUR", "IBS": "EUR", "TSI": "EUR",
    "CDX": "EAS", "CHB": "EAS", "CHS": "EAS", "JPT": "EAS", "KHV": "EAS",
    "BEB": "SAS", "GIH": "SAS", "ITU": "SAS", "PJL": "SAS", "STU": "SAS",
    "ACB": "AFR", "ASW": "AFR", "ESN": "AFR", "GWD": "AFR", "LWK": "AFR",
    "MSL": "AFR", "YRI": "AFR",
    "CLM": "AMR", "MXL": "AMR", "PEL": "AMR", "PUR": "AMR",
}
COORDS = {
    "GBR": (51.51, -0.13), "FIN": (60.17, 24.94), "CEU": (40.76, -111.89),
    "IBS": (40.42, -3.70), "TSI": (43.77, 11.25),
    "CHB": (39.90, 116.41), "CHS": (23.13, 113.26), "CDX": (22.0, 100.8),
    "JPT": (35.68, 139.69), "KHV": (21.03, 105.85),
    "BEB": (23.81, 90.41), "GIH": (29.76, -95.37), "ITU": (51.51, -0.13),
    "PJL": (31.58, 74.33), "STU": (51.51, -0.13),
    "YRI": (7.38, 3.89), "LWK": (0.51, 35.27), "GWD": (13.45, -16.58),
    "MSL": (8.48, -13.23), "ESN": (6.34, 5.63), "ACB": (13.1, -59.6),
    "ASW": (32.3, -90.2),
    "CLM": (6.25, -75.56), "MXL": (34.05, -118.24), "PEL": (-12.05, -77.04),
    "PUR": (18.47, -66.12),
}
COUNTRIES = {
    "GBR": "United Kingdom", "FIN": "Finland", "CEU": "USA", "IBS": "Spain", "TSI": "Italy",
    "CHB": "China", "CHS": "China", "CDX": "China", "JPT": "Japan", "KHV": "Vietnam",
    "BEB": "Bangladesh", "GIH": "USA", "ITU": "United Kingdom", "PJL": "Pakistan",
    "STU": "United Kingdom", "YRI": "Nigeria", "LWK": "Kenya", "GWD": "Gambia",
    "MSL": "Sierra Leone", "ESN": "Nigeria", "ACB": "Barbados", "ASW": "USA",
    "CLM": "Colombia", "MXL": "USA", "PEL": "Peru", "PUR": "Puerto Rico",
}


def kg_dir() -> Path:
    p = cache_dir() / "kg"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ref_path(store: Store) -> Path:
    ref = store.path("1000g")
    if not ref or not (ref / "all_hg38.pgen").exists():
        raise RuntimeError("set the 1000 Genomes folder in F2 (all_hg38.pgen)")
    return ref


def results_path(genome: Path, kind: str = "") -> Path:
    """Старый путь JSON (миграция). Живые результаты — DuckDB ref.*."""
    tag = hashlib.sha1(str(genome.resolve()).encode()).hexdigest()[:8]
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", genome.stem)[:60]
    extra = f"_{kind}" if kind else ""
    return config_dir() / "kg" / f"{stem}_{tag}{extra}.json"


@dataclass
class Bundle:
    groups: dict[str, F3] = field(default_factory=dict)
    people: dict[str, F3] = field(default_factory=dict)
    pca: dict | None = None
    paint: dict | None = None
    snps: int = 0
    outgroup: str = OUTGROUP
    partial: bool = False
    phase: dict | None = None


def _f3_from(raw: dict) -> F3 | None:
    if "est" not in raw:
        return None
    keys = {f.name for f in fields(F3)}
    return F3(**{k: raw[k] for k in keys if k in raw})


def load_bundle(genome: Path, kind: str = "") -> Bundle:
    from . import ref_db
    b = ref_db.load_bundle(genome, panel="kg", kind=kind or "", questionable=False)
    blobs = b.blobs or {}
    return Bundle(
        groups=b.groups, people=b.people, pca=blobs.get("pca"),
        paint=blobs.get("paint"), snps=b.snps,
        outgroup=b.outgroup or OUTGROUP, partial=b.partial,
        phase=blobs.get("phase"),
    )


def save_results(genome: Path, groups: dict[str, F3], *, snps: int, pca=None,
                 kind: str = "", partial: bool = False,
                 people: dict[str, F3] | None = None, paint=None, phase=None) -> None:
    from . import ref_db
    blobs: dict = {}
    # None = KEEP (старое поведение JSON merge)
    if pca is not None:
        blobs["pca"] = pca
    if paint is not None:
        blobs["paint"] = paint
    if phase is not None:
        blobs["phase"] = phase
    ref_db.save_bundle(
        genome, panel="kg", kind=kind or "", questionable=False,
        partial=partial, outgroup=OUTGROUP, snps=snps, groups=groups,
        people=people if people is not None else ref_db.KEEP,
        blobs=blobs,
    )


def pop_counts(ref: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    psam = ref / "all_hg38.psam"
    if not psam.exists():
        return counts
    with psam.open(encoding="utf-8") as fh:
        head = fh.readline().lstrip("#").rstrip("\n").split("\t")
        for ln in fh:
            row = dict(zip(head, ln.rstrip("\n").split("\t")))
            pop = row.get("Population", "")
            if pop:
                counts[pop] = counts.get(pop, 0) + 1
    return counts


def person_groups(store: Store | None = None) -> list[Group]:
    rows = []
    counts_ref: Path | None = None
    if store:
        try:
            counts_ref = ref_path(store)
        except RuntimeError:
            counts_ref = None
    if not counts_ref:
        return rows
    psam = counts_ref / "all_hg38.psam"
    if not psam.exists():
        return rows
    with psam.open(encoding="utf-8") as fh:
        head = fh.readline().lstrip("#").rstrip("\n").split("\t")
        for ln in fh:
            row = dict(zip(head, ln.rstrip("\n").split("\t")))
            iid = row.get("IID", "")
            pop = row.get("Population", "")
            superp = row.get("SuperPop") or POP_SUPER.get(pop, "")
            if not iid:
                continue
            lat, lon = COORDS.get(pop, (None, None))  # type: ignore[misc]
            rows.append(Group(
                iid, [], 1, 0.0, lat, lon, superp,
                COUNTRIES.get(pop, ""), pop, [], [],
            ))
    return rows


def groups(store: Store | None = None) -> list[Group]:
    counts: dict[str, int] = {}
    if store:
        try:
            counts = pop_counts(ref_path(store))
        except RuntimeError:
            counts = {}
    rows = []
    for code, label in POPS.items():
        lat, lon = COORDS.get(code, (None, None))  # type: ignore[misc]
        rows.append(Group(
            code, [], counts.get(code, 0), 0.0, lat, lon, POP_SUPER.get(code, ""),
            COUNTRIES.get(code, ""), label, [], [],
        ))
    return rows


def chip_rsids(g: Genome) -> list[str]:
    ids = []
    for chrom, c in g.chroms.items():
        ch = chrom.upper().removeprefix("CHR")
        if ch not in AUTOSOMES:
            continue
        for rsid in c.ids:
            if rsid and rsid.startswith("rs"):
                ids.append(rsid)
    return ids


def ensure_chip_pgen(ref: Path, rsids: list[str], job: Job) -> Path:
    sig = hashlib.sha1("\n".join(sorted(set(rsids))).encode()).hexdigest()[:12]
    prefix = kg_dir() / f"chip_{sig}"
    if (prefix.with_suffix(".pgen")).exists() and (prefix.with_suffix(".pvar")).exists():
        return prefix
    extract = kg_dir() / f"extract_{sig}.txt"
    extract.write_text("\n".join(sorted(set(rsids))) + "\n", encoding="utf-8")
    tmp = kg_dir() / f"build_{sig}"
    job.unit = "B"
    run_tool(
        [plink(), "--pfile", str(ref / "all_hg38"), "vzs", "--extract", str(extract),
         "--snps-only", "just-acgt", "--max-alleles", "2", "--rm-dup", "exclude-all",
         "--make-pgen", "--threads", "8", "--out", str(tmp)],
        job, tmp.with_suffix(".pgen"),
        f"extracting chip SNPs from 1000 Genomes ({len(set(rsids)):,} rsIDs)",
    )
    for ext in ("pgen", "pvar", "psam"):
        tmp.with_suffix(f".{ext}").replace(prefix.with_suffix(f".{ext}"))
    return prefix


def read_pvar(prefix: Path) -> list[tuple[str, str, int, str, str]]:
    rows = []
    with prefix.with_suffix(".pvar").open(encoding="utf-8") as fh:
        cols: list[str] = []
        for ln in fh:
            if ln.startswith("##"):
                continue
            t = ln.rstrip("\n").split("\t")
            if ln.startswith("#"):
                cols = [c.lstrip("#") for c in t]
                continue
            row = dict(zip(cols, t)) if cols else {}
            chrom_raw = row.get("CHROM") or t[0]
            chrom = chrom_raw.upper().removeprefix("CHR")
            if chrom not in AUTOSOMES:
                continue
            try:
                pos = int(row.get("POS") or t[1])
            except ValueError:
                continue
            rsid = row.get("ID") or t[2]
            ref, alt = (row.get("REF") or t[3]).upper(), (row.get("ALT") or t[4]).upper()
            if "," in alt or ref not in COMPLEMENT or alt not in COMPLEMENT or ref == alt:
                continue
            rows.append((rsid, chrom_raw, pos, ref, alt))
    return rows


def _parse_afreq(path: Path) -> dict[str, tuple[str, str, float, float]]:
    out: dict[str, tuple[str, str, float, float]] = {}
    with path.open(encoding="utf-8") as fh:
        head = fh.readline().lstrip("#").rstrip("\n").split("\t")
        for ln in fh:
            row = dict(zip(head, ln.rstrip("\n").split("\t")))
            try:
                freq = float(row["ALT_FREQS"].split(",")[0])
                n = float(row["OBS_CT"])
            except (KeyError, ValueError):
                continue
            rid = row.get("ID", "")
            if rid:
                out[rid] = (row.get("REF", "").upper(), row.get("ALT", "").upper(), freq, n)
    return out


def _ensure_freq(prefix: Path, pop: str, job: Job) -> Path:
    dest = prefix.parent / f"{prefix.name}_{pop}"
    afreq = dest.with_suffix(".afreq")
    if afreq.exists() and afreq.stat().st_size > 0:
        return afreq
    run_tool(
        [plink(), "--pfile", str(prefix), "--keep-if", f"Population=={pop}",
         "--freq", "--out", str(dest)],
        job, afreq, f"allele frequencies · {pop}",
    )
    return afreq


def _pca(labels: list[str], freq: np.ndarray, you: np.ndarray, n: np.ndarray) -> dict | None:
    if freq.shape[0] < 3 or freq.shape[1] < 50:
        return None
    rows = []
    for i in range(len(labels)):
        row = freq[i].astype(np.float64)
        row[n[i] <= 0] = np.nan
        rows.append(row)
    X = np.vstack([*rows, you.astype(np.float64)])
    mu = np.nanmean(X, axis=0)
    miss = np.isnan(X)
    X[miss] = np.take(mu, np.where(miss)[1])
    X -= mu
    keep = X.std(axis=0) > 1e-8
    if int(keep.sum()) < 10:
        return None
    X = X[:, keep]
    u, s, _vt = np.linalg.svd(X, full_matrices=False)
    k = min(2, u.shape[1])
    var = (s[:k] ** 2 / (s ** 2).sum()).tolist() if float(s.sum()) else [0.0] * k
    return {"method": "svd", "labels": [*labels, "YOU"],
            "xy": (u[:, :k] * s[:k]).tolist(), "var": var}


def scan_f3(store: Store, genome_path: Path, job: Job, genome: Genome | None = None,
            kind: str = "", p_t: np.ndarray | None = None,
            p_by_id: dict[str, float] | None = None) -> str:
    ref = ref_path(store)
    g = genome_db.get(genome_path, genome)
    by_id, _by_pos = chip_alleles(g)
    rsids = chip_rsids(g)
    if len(rsids) < MIN_SNPS:
        raise RuntimeError(f"only {len(rsids)} autosomal rsIDs on the chip")

    job.set_stage("extracting chip SNPs from 1000 Genomes")
    prefix = ensure_chip_pgen(ref, rsids, job)
    pvar = read_pvar(prefix)
    job.set_stage("aligning chip to 1000 Genomes", len(pvar))

    keep_id, dose, blocks = [], [], []
    block_key: dict[tuple[int, int], int] = {}
    for rsid, chrom, bp, ref_a, alt_a in pvar:
        hit = by_id.get(rsid)
        if not hit:
            continue
        if {ref_a, alt_a} in PALINDROMIC or {hit[1], hit[2]} in PALINDROMIC:
            continue
        d = _align(hit[1], hit[2], ref_a, alt_a)
        if d is None:
            continue
        ch = chrom.upper().removeprefix("CHR")
        key = (int(ch), bp // 5_000_000)
        if key not in block_key:
            block_key[key] = len(block_key)
        keep_id.append(rsid)
        dose.append(d)
        blocks.append(block_key[key])
    if len(keep_id) < MIN_SNPS:
        raise RuntimeError(f"only {len(keep_id)} SNPs overlap the chip and 1000 Genomes")
    n_snps = len(keep_id)
    block = np.array(blocks, np.int32)
    n_blocks = len(block_key)
    n_user = np.full(n_snps, 2.0)
    chip_p = np.array(dose, np.float64) / 2.0
    if p_by_id:
        p_t = np.array([p_by_id[r] if r in p_by_id else chip_p[i]
                        for i, r in enumerate(keep_id)], np.float64)
    elif p_t is None:
        p_t = chip_p
    elif len(p_t) != n_snps:
        raise RuntimeError("imputed dosages do not match the 1000G chip panel")

    names = [p for p in POPS if p != OUTGROUP]
    gi = {n: k for k, n in enumerate(names)}
    alt = np.zeros((len(names), n_snps), np.float64)
    nct = np.zeros((len(names), n_snps), np.float64)
    alt_o = np.zeros(n_snps, np.float64)
    n_o = np.zeros(n_snps, np.float64)
    n_by_pop = pop_counts(ref)

    pops_all = [OUTGROUP, *names]
    job.unit = "items"
    job.set_stage("population frequencies", len(pops_all))
    for i, pop in enumerate(pops_all):
        job.check()
        freq = _parse_afreq(_ensure_freq(prefix, pop, job))
        target_alt, target_n = (alt_o, n_o) if pop == OUTGROUP else (alt[gi[pop]], nct[gi[pop]])
        for j, rsid in enumerate(keep_id):
            hit = freq.get(rsid)
            if not hit:
                continue
            _ref, _alt, f, n = hit
            if n <= 0:
                continue
            target_alt[j] = f * n
            target_n[j] = n
        job.progress(i + 1)

    ok_o = n_o >= 2
    p_o = np.divide(alt_o, n_o, out=np.zeros_like(alt_o), where=ok_o)
    n_o = np.where(ok_o, n_o, 0)
    if int(ok_o.sum()) < MIN_SNPS:
        raise RuntimeError("YRI has too few SNPs after overlap")

    groups_out: dict[str, F3] = {}
    job.set_stage("outgroup-f3 (YRI)", len(names))
    for k, name in enumerate(names):
        job.check()
        est, se, used = f3_blocks(p_o, n_o, p_t, n_user, alt[k], nct[k], block, n_blocks)
        n_ind = n_by_pop.get(name, 0)
        if used > 0:
            groups_out[name] = _record(name, est, se, n_ind, used)
        job.progress(k + 1)

    panel = Panel(np.zeros(n_snps, np.int32), block, n_blocks)
    _mark_f4(groups_out, gi, p_o, n_o, p_t, n_user, alt, nct, panel)

    people_out = _people_f3(prefix, keep_id, p_t, n_user, p_o, n_o, block, n_blocks, job)
    thin = np.arange(0, n_snps, max(1, n_snps // 30_000))[:30_000]
    pca = _pca(names, alt[:, thin] / np.maximum(nct[:, thin], 1e-9), p_t[thin], nct[:, thin])
    pca = _plink_pca(prefix, p_t, keep_id, job, pca)
    save_results(genome_path, groups_out, snps=n_snps, pca=pca, kind=kind, people=people_out)
    ranked = sorted((r for r in groups_out.values() if r.ok), key=lambda r: -r.est)
    top = ", ".join(r.name for r in ranked[:3]) if ranked else "none"
    extra = f" · {len(people_out)} people" if people_out else ""
    return f"{len(groups_out)} pops{extra} · {n_snps:,} SNPs · YRI outgroup · top: {top}"


def _people_f3(prefix: Path, keep_id: list[str], p_t: np.ndarray, n_user: np.ndarray,
               p_o: np.ndarray, n_o: np.ndarray, block: np.ndarray, n_blocks: int,
               job: Job) -> dict[str, F3]:
    thin = np.arange(0, len(keep_id), max(1, len(keep_id) // 20_000))[:20_000]
    ids = [keep_id[i] for i in thin]
    dest = prefix.parent / f"{prefix.name}_people"
    raw = dest.with_suffix(".raw")
    if not raw.exists():
        idf = dest.with_suffix(".ids")
        idf.write_text("\n".join(ids) + "\n", encoding="utf-8")
        run_tool([plink(), "--pfile", str(prefix), "--extract", str(idf),
                  "--export", "A", "--out", str(dest)],
                 job, raw, "exporting 1000G people dosages")
    if not raw.exists():
        return {}
    job.set_stage("people f3 (YRI)")
    p_ot, n_ot = p_o[thin], n_o[thin]
    p_tt, n_tt = p_t[thin], n_user[thin]
    blk, remap = block[thin], {}
    nblk = 0
    blk_i = np.zeros(len(thin), np.int32)
    for j, b in enumerate(blk):
        if int(b) not in remap:
            remap[int(b)] = nblk
            nblk += 1
        blk_i[j] = remap[int(b)]
    people: dict[str, F3] = {}
    with raw.open(encoding="utf-8") as fh:
        head = fh.readline().rstrip("\n").split()
        # IID at col 1; dosages from col 6
        iid_i = head.index("IID") if "IID" in head else 1
        dose_start = 6 if len(head) > 6 else 5
        rows = list(fh)
    job.set_stage("people f3 (YRI)", len(rows))
    psam_pop = {}
    psam = prefix.with_suffix(".psam")
    if psam.exists():
        with psam.open(encoding="utf-8") as fh:
            h = fh.readline().lstrip("#").rstrip("\n").split("\t")
            for ln in fh:
                row = dict(zip(h, ln.rstrip("\n").split("\t")))
                psam_pop[row.get("IID", "")] = row.get("Population", "")
    for i, ln in enumerate(rows):
        job.check()
        t = ln.split()
        iid = t[iid_i]
        if psam_pop.get(iid) == OUTGROUP:
            continue
        vals = np.zeros(len(thin), np.float64)
        nct = np.zeros(len(thin), np.float64)
        for j, tok in enumerate(t[dose_start:dose_start + len(thin)]):
            if tok in ("NA", "nan", "."):
                continue
            try:
                vals[j] = float(tok)
            except ValueError:
                continue
            nct[j] = 2.0
        est, se, used = f3_blocks(p_ot, n_ot, p_tt, n_tt, vals, nct, blk_i, nblk)
        if used > 0:
            people[iid] = _record(iid, est, se, 1, used)
        if i % 50 == 0:
            job.progress(i + 1)
    job.progress(len(rows))
    return people


def _plink_pca(prefix: Path, p_t: np.ndarray, keep_id: list[str], job: Job,
               fallback: dict | None) -> dict | None:
    dest = prefix.parent / f"{prefix.name}_pca"
    wts = Path(str(dest) + ".eigenvec.allele")
    evec = Path(str(dest) + ".eigenvec")
    try:
        job.set_stage("plink2 PCA")
        run_tool([plink(), "--pfile", str(prefix), "--pca", "4", "allele-wts",
                  "--memory", "6000", "--out", str(dest)],
                 job, evec, "plink2 PCA")
    except RuntimeError:
        return fallback
    by_id = {r: i for i, r in enumerate(keep_id)}
    you = np.zeros(4)
    var_w = np.zeros(4)
    if wts.exists():
        with wts.open(encoding="utf-8") as fh:
            head = fh.readline().lstrip("#").rstrip("\n").split("\t")
            for ln in fh:
                row = dict(zip(head, ln.rstrip("\n").split("\t")))
                rid = row.get("ID") or row.get("SNP") or ""
                j = by_id.get(rid)
                if j is None:
                    continue
                for k in range(4):
                    key = f"PC{k + 1}"
                    if key in row:
                        try:
                            w = float(row[key])
                        except ValueError:
                            continue
                        you[k] += (p_t[j] - 0.5) * w
                        var_w[k] += w * w
    labels, xy = ["YOU"], [[float(you[0]), float(you[1])]]
    if evec.exists():
        with evec.open(encoding="utf-8") as fh:
            hdr = fh.readline().lstrip("#").split()
            iid_i = hdr.index("IID") if "IID" in hdr else 1
            pc1 = hdr.index("PC1") if "PC1" in hdr else iid_i + 1
            for ln in fh:
                t = ln.split()
                if len(t) <= pc1 + 1:
                    continue
                labels.append(t[iid_i])
                xy.append([float(t[pc1]), float(t[pc1 + 1])])
    out = {"method": "plink2", "labels": labels, "xy": xy, "you": you.tolist()}
    if fallback:
        out["svd"] = fallback
    return out


def panel_alleles(store: Store, genome_path: Path, genome: Genome | None,
                  job: Job) -> tuple[Path, list[str], list[str], list[str], np.ndarray]:
    """chip pgen prefix, rsids, REF, ALT, user p (copies of ALT / 2). Для Beagle/impute."""
    ref = ref_path(store)
    g = genome_db.get(genome_path, genome)
    by_id, _ = chip_alleles(g)
    prefix = ensure_chip_pgen(ref, chip_rsids(g), job)
    rsids, refs, alts, dose = [], [], [], []
    for rsid, _chrom, _bp, ref_a, alt_a in read_pvar(prefix):
        hit = by_id.get(rsid)
        if not hit:
            continue
        if {ref_a, alt_a} in PALINDROMIC or {hit[1], hit[2]} in PALINDROMIC:
            continue
        d = _align(hit[1], hit[2], ref_a, alt_a)
        if d is None:
            continue
        rsids.append(rsid)
        refs.append(ref_a)
        alts.append(alt_a)
        dose.append(d)
    return prefix, rsids, refs, alts, np.array(dose, np.float64) / 2.0
