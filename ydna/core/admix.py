"""Слои 1/4/5: smartpca, qpAdm (admixtools2), Beagle, статус ChromoPainter. Без UI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import zipfile
from itertools import combinations
from pathlib import Path

import numpy as np

from . import aadr, fstats, genome_db, kg
from .genome import Genome
from .jobs import JOBS, Job, fetch, run_tool
from .pgs import AUTOSOMES, PALINDROMIC, POPS, plink
from .store import Store
from .ylink import cache_dir

RIGHT_WANT = ("Mbuti", "Yoruba", "Han", "Papuan", "Karitiana", "Khomani",
              "Ami", "Mixe", "Ulchi", "Dai", "Surui")
BEAGLE_URL = "https://faculty.washington.edu/browning/beagle/beagle.22Jul22.46e.jar"
BEAGLE_JAR = "beagle.22Jul22.46e.jar"
MAPS_URL = "https://bochet.gcc.biostat.washington.edu/beagle/genetic_maps/plink.GRCh38.map.zip"
MAX_SNPS = 30_000
MAX_PER_GROUP = 8
MIN_LEFT_SNPS = 200_000
CALL_FRAC = 0.70
SNP_FRAC = 0.85

_QPADM_R = r"""
library(admixtools)
args <- commandArgs(trailingOnly=TRUE)
pref <- args[[1]]; jobs_f <- args[[2]]; out_f <- args[[3]]
jobs <- read.table(jobs_f, sep="\t", header=TRUE, stringsAsFactors=FALSE, quote="")
f2 <- f2_from_geno(pref, maxmiss=1, adjust_pseudohaploid=FALSE)
lines <- character()
for (i in seq_len(nrow(jobs))) {
  left <- strsplit(jobs$left[i], "+", fixed=TRUE)[[1]]
  right <- strsplit(jobs$right[i], ",", fixed=TRUE)[[1]]
  res <- tryCatch(qpadm(f2, left, right, target="YOU", verbose=FALSE), error=function(e) e)
  if (inherits(res, "error")) {
    lines <- c(lines, sprintf(
      '{"epoch":"%s","rotate":"%s","left":[%s],"p":null,"error":"%s"}',
      jobs$epoch[i], jobs$rotate[i],
      paste0('"', left, '"', collapse=","), gsub('["\\]', "", conditionMessage(res))))
    next
  }
  w <- res$weights
  rd <- res$rankdrop
  p <- if (!is.null(rd) && nrow(rd) >= 1) rd$p[1] else NA
  wjson <- paste0(sprintf('{"pop":"%s","w":%s,"se":%s}', w$left, w$weight, w$se), collapse=",")
  feasible <- all(w$weight >= -0.05 & w$weight <= 1.05) && is.finite(p) && p > 0.01
  lines <- c(lines, sprintf(
    '{"epoch":"%s","rotate":"%s","left":[%s],"right":[%s],"p":%s,"feasible":%s,"weights":[%s]}',
    jobs$epoch[i], jobs$rotate[i],
    paste0('"', left, '"', collapse=","),
    paste0('"', right, '"', collapse=","),
    if (is.finite(p)) p else "null",
    if (isTRUE(feasible)) "true" else "false", wjson))
}
writeLines(paste0("[", paste(lines, collapse=","), "]"), out_f)
"""

_F3_R = r"""
library(admixtools)
args <- commandArgs(trailingOnly=TRUE)
f2 <- f2_from_geno(args[[1]], maxmiss=1, adjust_pseudohaploid=FALSE)
x <- f3(f2, args[[2]], args[[3]], args[[4]])
est <- x$est[1]; se <- x$se[1]
cat(sprintf("%.8f\t%.8f\n", est, se))
"""


def tools_dir() -> Path:
    p = cache_dir() / "tools"
    p.mkdir(parents=True, exist_ok=True)
    return p


def which_smartpca() -> str | None:
    return shutil.which("smartpca")


def which_rscript() -> str | None:
    return shutil.which("Rscript")


def which_java() -> str | None:
    return shutil.which("java")


def chromopainter_bin() -> Path:
    return tools_dir() / "ChromoPainterv2"


def which_chromopainter() -> str | None:
    p = chromopainter_bin()
    if p.exists() and os.access(p, os.X_OK):
        return str(p)
    return shutil.which("chromopainter") or shutil.which("ChromoPainter")


def beagle_jar() -> Path:
    return tools_dir() / BEAGLE_JAR


def maps_dir() -> Path:
    return tools_dir() / "maps_hg38"


def _run(cmd: list[str], job: Job, sentinel: Path, label: str, log: Path) -> None:
    job.set_stage(label)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT)
        try:
            while proc.poll() is None:
                job.check()
                if sentinel.exists():
                    job.progress(sentinel.stat().st_size)
                time.sleep(0.5)
        finally:
            if proc.poll() is None:
                proc.kill()
    if proc.returncode:
        err = log.read_text(encoding="utf-8", errors="replace")[-400:]
        raise RuntimeError(f"{label} failed ({proc.returncode}): {err}")


def _find_group(cat: aadr.Catalog, want: str) -> str | None:
    if want in cat.groups:
        return want
    hits = [n for n in cat.groups if n == want or n.startswith((want + ".", want + "_"))]
    if not hits:
        return None
    hits.sort(key=lambda n: (-cat.groups[n].n, n))
    return hits[0]


def _thin(n: int, cap: int = MAX_SNPS) -> np.ndarray:
    if n <= cap:
        return np.arange(n)
    return np.arange(0, n, max(1, n // cap))[:cap]


def _write_eigenstrat(prefix: Path, snps: list[tuple],
                      inds: list[tuple[str, str, np.ndarray]]) -> None:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    with prefix.with_suffix(".snp").open("w", encoding="utf-8") as fh:
        for rsid, chrom, gm, pos, a1, a2 in snps:
            fh.write(f"{rsid}\t{chrom}\t{gm}\t{pos}\t{a1}\t{a2}\n")
    with prefix.with_suffix(".ind").open("w", encoding="utf-8") as fh:
        for iid, group, _g in inds:
            fh.write(f"{iid}\tU\t{group}\n")
    G = np.stack([g for _iid, _grp, g in inds])
    G = np.where((G >= 0) & (G <= 2), G, 9).astype(np.int8)
    with prefix.with_suffix(".geno").open("w", encoding="utf-8") as fh:
        for j in range(G.shape[1]):
            fh.write("".join(str(int(v)) for v in G[:, j]) + "\n")


def _user_geno(dose: np.ndarray, n: np.ndarray, thin: np.ndarray) -> np.ndarray:
    g = np.full(len(thin), 9, np.int8)
    for j, i in enumerate(thin):
        if n[i] > 0:
            g[j] = round(float(dose[i]))
    return g


def _read_samples(geno: Path, indices: list[int],
                  snp_index: np.ndarray) -> dict[int, np.ndarray]:
    _nind, nsnp = fstats._header(geno)
    rlen = fstats.row_bytes(nsnp)
    out: dict[int, np.ndarray] = {}
    with geno.open("rb") as fh:
        buf = memoryview(bytearray(rlen))
        for i in indices:
            fh.seek(fstats.HEADER + i * rlen)
            if fh.readinto(buf) != rlen:
                raise RuntimeError(f"TGENO truncated at individual {i}")
            out[i] = fstats.dosages(buf, snp_index)
    return out


def _pick_right(cat: aadr.Catalog) -> list[str]:
    found = []
    for want in RIGHT_WANT:
        name = _find_group(cat, want)
        if name and name not in found:
            found.append(name)
        if len(found) >= 7:
            break
    return found


def _pick_members(cat: aadr.Catalog, group: str, want: np.ndarray) -> list[int]:
    g = cat.groups.get(group)
    if not g:
        return []
    cands = [cat.samples[i] for i in g.samples if want[cat.samples[i].i]]
    cands.sort(key=lambda s: (not s.diploid, -s.snps))
    return [s.i for s in cands[:MAX_PER_GROUP]]


def _qpadm_jobs(cat: aadr.Catalog, ranked: list, right: list[str]) -> list[tuple[str, str, list[str], list[str]]]:
    """(epoch, rotate_label, left, right) — пары и тройки, полные right и ротации."""
    by_ep: dict[str, list] = {}
    for r in ranked:
        g = cat.groups.get(r.name)
        if g and r.name not in right:
            by_ep.setdefault(g.epoch, []).append(r)
    rotations = [("full", right)]
    if len(right) >= 6:
        for i in (0, len(right) // 2, len(right) - 1):
            rot = [p for j, p in enumerate(right) if j != i]
            rotations.append((f"drop:{right[i]}", rot))
    jobs = []
    for ep, rows in by_ep.items():
        lefts = [r.name for r in rows[:4]]
        if len(lefts) < 2:
            continue
        models = [list(p) for p in combinations(lefts, 2)]
        if len(lefts) >= 3:
            models.extend(list(p) for p in combinations(lefts, 3))
        for rot_lab, rot in rotations:
            for left in models:
                jobs.append((ep, rot_lab, left, rot))
    return jobs


def run_qpadm(store: Store, genome_path: Path, job: Job, genome: Genome | None = None,
              questionable: bool = False) -> str:
    rscript = which_rscript()
    if not rscript:
        raise RuntimeError("Rscript not found (need R + admixtools2)")
    folder = store.path("aadr")
    if not folder:
        raise RuntimeError("set the AADR folder in F2")
    cat = aadr.load_catalog(folder, job)
    geno, snp_path = cat.files.get("geno"), cat.files.get("snp")
    if geno is None or snp_path is None:
        raise RuntimeError("AADR .geno/.snp missing")
    bundle = aadr.load_bundle(genome_path, questionable)
    ranked = sorted((r for r in bundle.groups.values()
                     if r.ok and r.snps >= MIN_LEFT_SNPS), key=lambda r: -r.est)
    if len(ranked) < 2:
        raise RuntimeError("scan f3 first (need two groups with ≥200k SNPs)")
    right = _pick_right(cat)
    if len(right) < 5:
        raise RuntimeError(f"need ≥5 right pops, found {len(right)}")
    qjobs = _qpadm_jobs(cat, ranked, right)
    if not qjobs:
        raise RuntimeError("not enough left-source candidates by epoch")
    used = sorted({n for _ep, _r, left, rot in qjobs for n in (*left, *rot)})

    job.set_stage("matching chip SNPs")
    g = genome_db.get(genome_path, genome)
    snps = fstats.read_snp(snp_path)
    panel, dose_t, n_t = fstats.match_chip(snps, g)
    want = fstats._wanted(cat, questionable)
    indices = []
    for name in used:
        indices.extend(_pick_members(cat, name, want))
    if len(indices) < 10:
        raise RuntimeError("too few individuals for qpAdm extract")

    job.set_stage("reading genotypes", len(indices))
    got = _read_samples(geno, indices, panel.index)
    raw: list[tuple[str, str, np.ndarray]] = [
        ("YOU", "YOU", np.where(n_t > 0, np.clip(np.rint(dose_t), 0, 2), 9).astype(np.int8)),
    ]
    for i in indices:
        s = cat.samples[i]
        geno_i = got[i]
        if not s.diploid:
            geno_i = np.where(geno_i == 1, 2, geno_i)
            geno_i = np.where((geno_i == 0) | (geno_i == 2), geno_i, 9)
        else:
            geno_i = np.where((geno_i >= 0) & (geno_i <= 2), geno_i, 9)
        raw.append((s.gid.replace(" ", "_"), s.group, geno_i.astype(np.int8)))
    min_full = int(CALL_FRAC * 0.5 * len(panel.index))
    raw = [row for row in raw
           if row[0] == "YOU" or int(np.sum((row[2] >= 0) & (row[2] <= 2))) >= min_full]
    stacked = np.stack([row[2] for row in raw])
    rate = np.mean((stacked >= 0) & (stacked <= 2), axis=0)
    keep = np.where(rate >= SNP_FRAC)[0]
    if len(keep) < 3000:
        raise RuntimeError(f"only {len(keep)} SNPs with ≥{SNP_FRAC:.0%} call rate")
    thin = keep[_thin(len(keep))]
    inds = [(iid, grp, g[thin]) for iid, grp, g in raw]
    min_call = int(CALL_FRAC * len(thin))
    inds = [row for row in inds
            if row[0] == "YOU" or int(np.sum((row[2] >= 0) & (row[2] <= 2))) >= min_call]
    remain = {grp for _iid, grp, _g in inds}
    qjobs = [j for j in qjobs if all(p in remain for p in (*j[2], *j[3]))]
    if not qjobs:
        raise RuntimeError("no qpAdm models left after dropping low-coverage samples")
    need = {"YOU"} | {p for _e, _r, left, rot in qjobs for p in (*left, *rot)}
    inds = [row for row in inds if row[1] in need]
    snp_rows = [snps[int(panel.index[i])] for i in thin]

    work = cache_dir() / "qpadm" / aadr.results_path(genome_path, questionable).stem
    work.mkdir(parents=True, exist_ok=True)
    pref = work / "data"
    job.set_stage("writing eigenstrat")
    _write_eigenstrat(pref, snp_rows, inds)
    jobs_f = work / "jobs.tsv"
    with jobs_f.open("w", encoding="utf-8") as fh:
        fh.write("epoch\trotate\tleft\tright\n")
        for ep, rot_lab, left, rot in qjobs:
            fh.write(f"{ep}\t{rot_lab}\t{'+'.join(left)}\t{','.join(rot)}\n")
    (work / "qpadm.R").write_text(_QPADM_R, encoding="utf-8")
    out_json = work / "qpadm.json"
    _run([rscript, str(work / "qpadm.R"), str(pref), str(jobs_f), str(out_json)],
         job, out_json, f"qpAdm · {len(qjobs)} models (epochs + rotations)", work / "qpadm.log")
    raw = json.loads(out_json.read_text(encoding="utf-8"))
    for row in raw:
        row["target"] = "YOU"
    raw.sort(key=lambda r: (not r.get("feasible"), -(r.get("p") or 0)))
    aadr.save_results(genome_path, bundle.outgroup, bundle.snps, bundle.groups,
                      people=bundle.people, check=bundle.check,
                      questionable=questionable, partial=bundle.partial,
                      pca=bundle.pca, qpadm=raw)
    best = next((r for r in raw if r.get("feasible")), raw[0] if raw else {})
    left = "+".join(best.get("left") or [])
    p = best.get("p")
    return (f"{len(raw)} models · {len({r.get('epoch') for r in raw})} epochs · best {left}"
            + (f" p={p:.3g}" if p is not None else ""))


def run_smartpca(store: Store, genome_path: Path, job: Job, genome: Genome | None = None,
                 questionable: bool = False) -> str:
    exe = which_smartpca()
    if not exe:
        raise RuntimeError("smartpca not in PATH (group SVD PCA is already in the f3 scan)")
    folder = store.path("aadr")
    if not folder:
        raise RuntimeError("set the AADR folder in F2")
    cat = aadr.load_catalog(folder, job)
    geno, snp_path = cat.files.get("geno"), cat.files.get("snp")
    if geno is None or snp_path is None:
        raise RuntimeError("AADR .geno/.snp missing")
    bundle = aadr.load_bundle(genome_path, questionable)
    ranked = sorted((r for r in bundle.groups.values() if r.ok), key=lambda r: -r.est)[:30]
    job.set_stage("matching chip SNPs")
    g = genome_db.get(genome_path, genome)
    snps = fstats.read_snp(snp_path)
    panel, dose_t, n_t = fstats.match_chip(snps, g)
    thin = _thin(len(panel.index))
    snp_rows = [snps[int(panel.index[i])] for i in thin]
    want = fstats._wanted(cat, questionable)
    indices: list[int] = []
    for r in ranked:
        indices.extend(_pick_members(cat, r.name, want))
    indices = indices[:250]
    job.set_stage("reading genotypes", len(indices))
    got = _read_samples(geno, indices, panel.index[thin])
    inds = [("YOU", "YOU", _user_geno(dose_t, n_t, thin))]
    pops = set()
    for i in indices:
        s = cat.samples[i]
        pops.add(s.group)
        inds.append((s.gid.replace(" ", "_"), s.group, got[i]))
    work = cache_dir() / "smartpca" / aadr.results_path(genome_path, questionable).stem
    work.mkdir(parents=True, exist_ok=True)
    pref = work / "data"
    _write_eigenstrat(pref, snp_rows, inds)
    (work / "poplist").write_text("\n".join(sorted(pops)) + "\n", encoding="utf-8")
    par = work / "smartpca.par"
    par.write_text(
        f"genotypename: {pref}.geno\n"
        f"snpname: {pref}.snp\n"
        f"indivname: {pref}.ind\n"
        f"evecoutname: {work / 'data.evec'}\n"
        f"evaloutname: {work / 'data.eval'}\n"
        f"lsqproject: YES\n"
        f"poplistname: {work / 'poplist'}\n"
        "numoutevec: 4\nnumoutlieriter: 0\n",
        encoding="utf-8",
    )
    evec = work / "data.evec"
    _run([exe, "-p", str(par)], job, evec, "smartpca lsqproject", work / "smartpca.log")
    labels, xy = [], []
    with evec.open(encoding="utf-8") as fh:
        for ln in fh:
            p = ln.split()
            if len(p) < 4 or p[0].startswith("#"):
                continue
            labels.append(p[0])
            xy.append([float(p[1]), float(p[2])])
    pca = bundle.pca or {}
    pca = {**pca, "smartpca": {"labels": labels, "xy": xy, "method": "lsqproject"}}
    aadr.save_results(genome_path, bundle.outgroup, bundle.snps, bundle.groups,
                      people=bundle.people, check=bundle.check,
                      questionable=questionable, partial=bundle.partial,
                      pca=pca, qpadm=bundle.qpadm)
    return f"smartpca {len(labels)} samples · lsqproject YOU"


def ensure_beagle(job: Job) -> Path:
    jar = beagle_jar()
    if not jar.exists() or jar.stat().st_size < 1_000_000:
        fetch(BEAGLE_URL, jar, job, "Beagle 5.4")
    maps = maps_dir()
    marker = maps / "plink.chr1.GRCh38.map"
    if not marker.exists():
        zpath = tools_dir() / "plink.GRCh38.map.zip"
        fetch(MAPS_URL, zpath, job, "hg38 genetic maps")
        maps.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(maps)
    return jar


def _concat_map(dest: Path) -> Path:
    maps = sorted(maps_dir().glob("plink.chr*.GRCh38.map"))
    if not maps:
        return dest
    with dest.open("w", encoding="utf-8") as out:
        for p in maps:
            out.write(p.read_text(encoding="utf-8"))
    return dest


def aadr_rsids(snp_path: Path) -> list[str]:
    ids = []
    for rsid, chrom, _gm, _pos, a1, a2 in fstats.read_snp(snp_path):
        if chrom not in AUTOSOMES or not rsid.startswith("rs"):
            continue
        if {a1, a2} in PALINDROMIC:
            continue
        ids.append(rsid)
    return ids


def _norm_chr(chrom: str) -> str:
    return chrom.upper().removeprefix("CHR")


def _write_user_vcf(path: Path, pvar: list[tuple[str, str, int, str, str]],
                    by_id: dict[str, tuple[str, str, str]]) -> int:
    """Чип → GT, сайты только в 1000G — ./.  Возвращает число вызванных SNP."""
    path.parent.mkdir(parents=True, exist_ok=True)
    called = 0
    with path.open("w", encoding="utf-8") as fh:
        fh.write("##fileformat=VCFv4.2\n")
        fh.write('##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n')
        fh.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tYOU\n")
        for rsid, chrom, pos, ref, alt in pvar:
            hit = by_id.get(rsid)
            gt = "./."
            if hit:
                d = fstats._align(hit[1], hit[2], ref, alt)
                if d is not None:
                    gt = {0: "0/0", 1: "0/1", 2: "1/1"}.get(d, "./.")
                    if gt != "./.":
                        called += 1
            fh.write(f"{chrom}\t{pos}\t{rsid}\t{ref}\t{alt}\t.\t.\t.\tGT\t{gt}\n")
    return called


def _parse_imputed_records(vcf: Path) -> dict[str, tuple[float, str, str]]:
    import gzip
    out: dict[str, tuple[float, str, str]] = {}
    opener = gzip.open if str(vcf).endswith(".gz") else open
    with opener(vcf, "rt", encoding="utf-8") as fh:
        for ln in fh:
            if ln.startswith("#"):
                continue
            t = ln.rstrip("\n").split("\t")
            if len(t) < 10:
                continue
            rsid, ref, alt = t[2], t[3].upper(), t[4].upper()
            rec = dict(zip(t[8].split(":"), t[9].split(":")))
            p = None
            if "DS" in rec:
                try:
                    p = float(rec["DS"]) / 2.0
                except ValueError:
                    p = None
            if p is None:
                gt = rec.get("GT", "./.")
                if "." in gt:
                    continue
                alleles = gt.replace("|", "/").split("/")
                p = sum(int(a) for a in alleles if a in "01") / max(1, len(alleles))
            out[rsid] = (p, ref, alt)
    return out


def _map_for_chr(chrom: str) -> Path | None:
    n = _norm_chr(chrom)
    p = maps_dir() / f"plink.chr{n}.GRCh38.map"
    return p if p.exists() else None


def run_impute(store: Store, genome_path: Path, job: Job, genome: Genome | None = None,
               questionable: bool = False) -> str:
    java = which_java()
    if not java:
        raise RuntimeError("java not found (need a JRE for Beagle)")
    jar = ensure_beagle(job)
    g = genome_db.get(genome_path, genome)
    ref = kg.ref_path(store)
    folder = store.path("aadr")
    snp_path = None
    if folder:
        cat = aadr.load_catalog(folder, job)
        snp_path = cat.files.get("snp")
    if snp_path:
        job.set_stage("extracting AADR SNPs from 1000 Genomes")
        ids = aadr_rsids(snp_path)
        prefix = kg.ensure_chip_pgen(ref, ids, job)
    else:
        prefix, _rs, _rf, _al, _p = kg.panel_alleles(store, genome_path, g, job)
    pvar = [r for r in kg.read_pvar(prefix) if {r[3], r[4]} not in PALINDROMIC]
    by_id, _ = fstats.chip_alleles(g)
    work = cache_dir() / "impute" / kg.results_path(genome_path).stem
    work.mkdir(parents=True, exist_ok=True)
    phase = _check_phase(prefix, job)
    (work / "phase.json").write_text(json.dumps(phase), encoding="utf-8")

    by_chr: dict[str, list] = {}
    for row in pvar:
        by_chr.setdefault(_norm_chr(row[1]), []).append(row)

    imputed: dict[str, tuple[float, str, str]] = {}
    job.unit = "items"
    chroms = [c for c in (str(i) for i in range(1, 23)) if c in by_chr]
    job.set_stage("Beagle per chromosome", len(chroms))
    for i, ch in enumerate(chroms):
        job.check()
        rows = by_chr[ch]
        user_vcf = work / f"user.chr{ch}.vcf"
        _write_user_vcf(user_vcf, rows, by_id)
        ref_pref = work / f"ref.chr{ch}"
        ref_vcf = work / f"ref.chr{ch}.vcf.gz"
        if not ref_vcf.exists():
            ids_f = work / f"chr{ch}.ids"
            ids_f.write_text("\n".join(r[0] for r in rows) + "\n", encoding="utf-8")
            run_tool(
                [plink(), "--pfile", str(prefix), "--extract", str(ids_f),
                 "--export", "vcf", "bgz", "--out", str(ref_pref)],
                job, ref_vcf, f"1000G VCF chr{ch}",
            )
        out_pref = work / f"imp.chr{ch}"
        out_vcf = Path(str(out_pref) + ".vcf.gz")
        if not out_vcf.exists():
            cmd = [java, "-Xmx6g", "-jar", str(jar), f"gt={user_vcf}", f"ref={ref_vcf}",
                   f"out={out_pref}", "nthreads=4"]
            mp = _map_for_chr(ch)
            if mp:
                cmd.append(f"map={mp}")
            _run(cmd, job, out_vcf, f"Beagle chr{ch}", work / f"beagle.chr{ch}.log")
        if out_vcf.exists():
            imputed.update(_parse_imputed_records(out_vcf))
        job.progress(i + 1)

    bits = [f"Beagle {len(imputed):,} SNPs"]
    if snp_path and imputed:
        job.set_stage("AADR f3/f4 on imputed")
        panel, dose, n_t = fstats.match_imputed(fstats.read_snp(snp_path), imputed)
        p_t = np.divide(dose, n_t, out=np.zeros_like(dose), where=n_t > 0)
        msg = fstats.scan_f3(store, genome_path, job, g, questionable, kind="imp",
                             p_t=p_t, n_t=n_t, panel=panel)
        bits.append(msg)

    job.set_stage("1000G f3 on imputed")
    p_by = {r: v[0] for r, v in imputed.items()}
    bits.append(kg.scan_f3(store, genome_path, job, g, kind="imp", p_by_id=p_by))
    b_imp = kg.load_bundle(genome_path, kind="imp")
    kg.save_results(genome_path, b_imp.groups, snps=b_imp.snps, pca=b_imp.pca,
                    people=b_imp.people, kind="imp", paint=b_imp.paint, phase=phase)
    b0 = kg.load_bundle(genome_path)
    if b0.groups:
        kg.save_results(genome_path, b0.groups, snps=b0.snps, pca=b0.pca,
                        people=b0.people, paint=b0.paint, phase=phase)

    if which_chromopainter():
        job.set_stage("ChromoPainter")
        try:
            bits.append(run_paint(store, genome_path, job, g, work))
        except (OSError, RuntimeError) as exc:
            bits.append(f"paint skipped: {exc}")
    return " · ".join(bits)


def _check_phase(prefix: Path, job: Job) -> dict:
    """Экспорт крошечного VCF: есть ли ‘|’ в GT (фаза 1000G)."""
    ids, n = [], 0
    with prefix.with_suffix(".pvar").open(encoding="utf-8") as fh:
        for ln in fh:
            if ln.startswith("#"):
                continue
            t = ln.rstrip("\n").split("\t")
            ids.append(t[2] if len(t) > 2 else t[0])
            n += 1
            if n >= 40:
                break
    work = prefix.parent / f"{prefix.name}_phase"
    idf = work.with_suffix(".ids")
    idf.write_text("\n".join(ids) + "\n", encoding="utf-8")
    run_tool([plink(), "--pfile", str(prefix), "--extract", str(idf),
              "--export", "vcf", "--out", str(work)],
             job, work.with_suffix(".vcf"), "checking 1000G phase")
    vcf = work.with_suffix(".vcf")
    bars = slashes = 0
    if vcf.exists():
        for ln in vcf.read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.startswith("#"):
                continue
            t = ln.split("\t")
            if len(t) < 10:
                continue
            gt = t[9].split(":")[0]
            bars += gt.count("|")
            slashes += gt.count("/")
    phased = bars > 0 and bars >= slashes
    return {"phased": phased, "bar": bars, "slash": slashes}


def _pick_donors(psam: Path, per_pop: int = 8) -> list[tuple[str, str]]:
    buckets: dict[str, list[str]] = {}
    with psam.open(encoding="utf-8") as fh:
        head = fh.readline().lstrip("#").rstrip("\n").split("\t")
        for ln in fh:
            row = dict(zip(head, ln.rstrip("\n").split("\t")))
            pop = row.get("Population") or ""
            iid = row.get("IID") or row.get("ID") or ""
            if pop in POPS and iid:
                buckets.setdefault(pop, []).append(iid)
    out = []
    for pop, iids in buckets.items():
        out.extend((i, pop) for i in iids[:per_pop])
    return out


def _vcf_haps(vcf: Path, want: set[str]) -> tuple[list[int], dict[str, tuple[str, str]]]:
    import gzip
    opener = gzip.open if str(vcf).endswith(".gz") else open
    pos: list[int] = []
    haps: dict[str, list[list[str]]] = {}
    iids: list[str] = []
    with opener(vcf, "rt", encoding="utf-8") as fh:
        for ln in fh:
            if ln.startswith("##"):
                continue
            t = ln.rstrip("\n").split("\t")
            if ln.startswith("#"):
                iids = t[9:]
                for iid in iids:
                    if iid in want or iid == "YOU":
                        haps[iid] = [[], []]
                continue
            if len(t) < 10:
                continue
            try:
                pos.append(int(t[1]))
            except ValueError:
                continue
            fmt = t[8].split(":")
            gi = fmt.index("GT") if "GT" in fmt else 0
            for iid, sample in zip(iids, t[9:]):
                if iid not in haps:
                    continue
                gt = sample.split(":")[gi]
                a = gt.replace("/", "|").split("|")
                if len(a) < 2 or a[0] not in "01" or a[1] not in "01":
                    a = ["0", "0"]
                haps[iid][0].append(a[0])
                haps[iid][1].append(a[1])
    out = {iid: ("".join(h[0]), "".join(h[1])) for iid, h in haps.items() if h[0]}
    return pos, out


def run_paint(store: Store, genome_path: Path, job: Job, genome: Genome | None,
              work: Path) -> str:
    exe = which_chromopainter()
    if not exe:
        raise RuntimeError("ChromoPainterv2 is not compiled")
    g = genome_db.get(genome_path, genome)
    prefix, _rs, _rf, _al, _p = kg.panel_alleles(store, genome_path, g, job)
    donors = _pick_donors(prefix.with_suffix(".psam"))
    if len(donors) < 20:
        raise RuntimeError("not enough 1000G donors for painting")
    tot: dict[str, float] = {}
    nearest: dict[str, float] = {}
    n_chr = 0
    for ch in (str(i) for i in range(1, 23)):
        job.check()
        ref_vcf = work / f"ref.chr{ch}.vcf.gz"
        you_vcf = Path(str(work / f"imp.chr{ch}") + ".vcf.gz")
        if not ref_vcf.exists() or not you_vcf.exists():
            continue
        pos, haps = _vcf_haps(ref_vcf, {iid for iid, _ in donors})
        _pos2, you_h = _vcf_haps(you_vcf, {"YOU"})
        if "YOU" not in you_h or len(pos) < 100:
            continue
        # trim to shared SNP count
        n = min(len(pos), len(you_h["YOU"][0]),
                min((len(h[0]) for h in haps.values()), default=0))
        if n < 100:
            continue
        pos = pos[:n]
        iids = [iid for iid, _pop in donors if iid in haps]
        hap_path = work / f"paint.chr{ch}.hap"
        rec_path = work / f"paint.chr{ch}.rec"
        with hap_path.open("w", encoding="utf-8") as fh:
            fh.write(f"{2 * (len(iids) + 1)}\n{n}\nP " + " ".join(str(p) for p in pos) + "\n")
            for iid in iids:
                h0, h1 = haps[iid]
                fh.write(h0[:n] + "\n" + h1[:n] + "\n")
            yh0, yh1 = you_h["YOU"]
            fh.write(yh0[:n] + "\n" + yh1[:n] + "\n")
        with rec_path.open("w", encoding="utf-8") as fh:
            fh.write("start.pos recom.rate.perbp\n")
            for j, p in enumerate(pos):
                rate = 1e-8 if j + 1 < len(pos) else 0
                fh.write(f"{p} {rate}\n")
        id_path = work / f"paint.chr{ch}.ids"
        pop_path = work / f"paint.chr{ch}.pops"
        with id_path.open("w", encoding="utf-8") as fh:
            for iid, pop in donors:
                if iid in haps:
                    fh.write(f"{iid} {pop} 1\n")
            fh.write("YOU YOU 1\n")
        pops_d = sorted({pop for iid, pop in donors if iid in haps})
        with pop_path.open("w", encoding="utf-8") as fh:
            for pop in pops_d:
                fh.write(f"{pop} D\n")
            fh.write("YOU R\n")
        out_pref = work / f"paint.chr{ch}"
        _run([exe, "-g", str(hap_path), "-r", str(rec_path), "-t", str(id_path),
              "-f", str(pop_path), "0", "0", "-o", str(out_pref), "-s", "5"],
             job, Path(str(out_pref) + ".chunkcounts.out"),
             f"ChromoPainter chr{ch}", work / f"paint.chr{ch}.log")
        counts = Path(str(out_pref) + ".chunkcounts.out")
        if not counts.exists():
            continue
        n_chr += 1
        lines = counts.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) < 2:
            continue
        for ln in lines[1:]:
            p = ln.replace(",", " ").split()
            if not p or p[0] not in ("YOU", "1"):
                continue
            # find numeric fields aligned to donor names in header
            nums = []
            for tok in p[1:]:
                try:
                    nums.append(float(tok))
                except ValueError:
                    continue
            names = [iid for iid in iids]
            for iid, val in zip(names, nums):
                nearest[iid] = nearest.get(iid, 0) + val
                pop = next((pp for ii, pp in donors if ii == iid), "")
                if pop:
                    tot[pop] = tot.get(pop, 0) + val
            break
    s = sum(tot.values()) or 1.0
    props = {k: v / s for k, v in sorted(tot.items(), key=lambda kv: -kv[1])}
    near = sorted(nearest.items(), key=lambda kv: -kv[1])[:8]
    pop_of = dict(donors)
    paint = {
        "pops": props,
        "nearest": [{"iid": i, "pop": pop_of.get(i, ""), "chunks": c} for i, c in near],
        "chromosomes": n_chr,
    }
    b = kg.load_bundle(genome_path)
    kg.save_results(genome_path, b.groups, snps=b.snps, pca=b.pca, people=b.people,
                    paint=paint)
    top = ", ".join(f"{k} {v:.0%}" for k, v in list(props.items())[:3]) or "none"
    return f"ChromoPainter {n_chr} chr · {top}"


def run_paint_job(store: Store, genome_path: Path, job: Job,
                  genome: Genome | None = None) -> str:
    if not which_chromopainter():
        src = tools_dir() / "ChromoPainterv2.c"
        if not src.exists():
            fetch("https://raw.githubusercontent.com/sahwa/ChromoPainterV2/main/ChromoPainterv2.c",
                  src, job, "ChromoPainterv2.c")
        job.set_stage("compiling ChromoPainterv2")
        subprocess.check_call(["gcc", "-O2", "-o", str(chromopainter_bin()), str(src), "-lm", "-lz"])
    work = cache_dir() / "impute" / kg.results_path(genome_path).stem
    if not any(work.glob("imp.chr*.vcf.gz")):
        raise RuntimeError("run Beagle imputation first (LAYERS · Imputation)")
    g = genome_db.get(genome_path, genome)
    return run_paint(store, genome_path, job, g, work)


def verify_f3(store: Store, genome_path: Path, job: Job, genome: Genome | None,
              cat: aadr.Catalog, panel: fstats.Panel, dose_t: np.ndarray, n_t: np.ndarray,
              groups: dict, questionable: bool) -> dict:
    rscript = which_rscript()
    if not rscript:
        return {"admixtools": "Rscript missing"}
    geno, snp_path = cat.files.get("geno"), cat.files.get("snp")
    if geno is None or snp_path is None:
        return {"admixtools": "no geno"}
    ranked = sorted((r for r in groups.values() if r.ok), key=lambda r: -r.est)
    if not ranked:
        return {"admixtools": "no groups"}
    target = ranked[0].name
    right = cat.outgroup or "Mbuti"
    want = fstats._wanted(cat, questionable)
    thin = _thin(len(panel.index), 8_000)
    snps = fstats.read_snp(snp_path)
    snp_rows = [snps[int(panel.index[i])] for i in thin]
    idxs = _pick_members(cat, target, want)[:6]
    og = _pick_members(cat, right, want)[:6]
    if not idxs or not og:
        return {"admixtools": "no members"}
    got = _read_samples(geno, idxs + og, panel.index[thin])
    inds = [("YOU", "YOU", _user_geno(dose_t, n_t, thin))]
    for i in idxs + og:
        s = cat.samples[i]
        geno_i = got[i]
        if not s.diploid:
            geno_i = np.where((geno_i == 0) | (geno_i == 2), geno_i, 9)
        grp = "YOU_SRC" if i in idxs else "OUT"
        inds.append((s.gid.replace(" ", "_"), grp, geno_i))
    work = cache_dir() / "f3check" / aadr.results_path(genome_path).stem
    work.mkdir(parents=True, exist_ok=True)
    pref = work / "data"
    _write_eigenstrat(pref, snp_rows, inds)
    (work / "f3.R").write_text(_F3_R, encoding="utf-8")
    out = work / "f3.txt"
    _run([rscript, str(work / "f3.R"), str(pref), "OUT", "YOU", "YOU_SRC"],
         job, out, "admixtools2 f3 check", work / "f3.log")
    text = out.read_text(encoding="utf-8").strip().split()
    r_est, r_se = float(text[0]), float(text[1])
    ours = ranked[0]
    ok = abs(r_est - ours.est) < 4 * max(r_se, ours.se, 1e-4)
    return {
        "admixtools": {
            "group": target, "r_est": r_est, "r_se": r_se,
            "numpy_est": ours.est, "numpy_se": ours.se, "ok": ok,
        }
    }


def layer_state(_store: Store, genome: Path | None, source: str,
                aadr_b: aadr.Bundle | None, kg_b: kg.Bundle | None,
                aadr_imp: aadr.Bundle | None, kg_imp: kg.Bundle | None) -> list[dict]:
    def job_of(*keys: str) -> dict | None:
        for k in keys:
            j = JOBS.get(k)
            if j and j.running:
                return {"status": "running", "detail": j.stage, "action": None}
        return None

    chip = "ready" if genome else "missing"
    rows = [{
        "id": "0", "title": "Chip genotypes",
        "status": chip, "detail": genome.name if genome else "load a file in F1",
        "action": None,
    }]
    pca = (aadr_b.pca if source == "aadr" and aadr_b else None) or (
        kg_b.pca if kg_b else None)
    run = job_of("aadr-pca")
    if run:
        rows.append({"id": "1", "title": "PCA", **run})
    elif pca:
        var = pca.get("var") or []
        extra = (f"PC1 {var[0]*100:.0f}%  PC2 {var[1]*100:.0f}%" if len(var) >= 2
                 else pca.get("method", "svd"))
        if pca.get("smartpca"):
            extra += " · smartpca"
        action = "screen.layer('pca')" if source == "aadr" and not pca.get("smartpca") else None
        rows.append({"id": "1", "title": "PCA (group SVD)", "status": "done",
                     "detail": extra, "action": action})
    else:
        rows.append({"id": "1", "title": "PCA", "status": "missing",
                     "detail": "produced at the end of an f3 scan",
                     "action": "screen.score"})
    b = aadr_b if source == "aadr" else kg_b
    n = sum(1 for r in (b.groups if b else {}).values() if r.ok) if b else 0
    run = job_of("aadr-f3" if source == "aadr" else "kg-f3")
    og = "Mbuti" if source == "aadr" else "YRI"
    if run:
        rows.append({"id": "2", "title": f"outgroup-f3 ({og})", **run})
    else:
        rows.append({
            "id": "2", "title": f"outgroup-f3 ({og})",
            "status": "done" if n else "missing",
            "detail": f"{n} scored" if n else "press F",
            "action": None if n else "screen.score",
        })
    ep = 0
    if source == "aadr" and aadr_b:
        ep = sum(1 for r in aadr_b.groups.values() if r.ok and r.epoch_cluster == 0)
    rows.append({
        "id": "3", "title": "f4 clusters by epoch" if source == "aadr" else "f4 vs #1 (YRI)",
        "status": "done" if n else "missing",
        "detail": (f"{ep} epoch-tied groups" if source == "aadr" and n
                   else ("Z vs best pop" if n else "after f3")),
        "action": None,
    })
    run = job_of("aadr-impute")
    n_imp_a = sum(1 for r in (aadr_imp.groups if aadr_imp else {}).values() if r.ok)
    n_imp_k = sum(1 for r in (kg_imp.groups if kg_imp else {}).values() if r.ok)
    n_imp = n_imp_a or n_imp_k
    if run:
        rows.append({"id": "4", "title": "Imputation (Beagle)", **run})
    else:
        detail = "click: AADR∩1000G → Beagle → f3/f4 on imputed"
        if n_imp_a:
            detail = f"AADR imputed {n_imp_a} groups"
            if n_imp_k:
                detail += f" · 1000G {n_imp_k} pops"
            detail += " · chip vs imputed below"
        elif n_imp_k:
            detail = f"1000G imputed {n_imp_k} pops"
        rows.append({
            "id": "4", "title": "Imputation (Beagle)",
            "status": "done" if n_imp else "missing",
            "detail": detail,
            "action": None if n_imp else "screen.layer('impute')",
        })
    run = job_of("aadr-qpadm")
    q = aadr_b.qpadm if source == "aadr" and aadr_b else None
    if run:
        rows.append({"id": "5", "title": "qpAdm", **run})
    elif source != "aadr":
        rows.append({"id": "5", "title": "qpAdm", "status": "n/a",
                     "detail": "AADR only", "action": None})
    else:
        best = q[0] if q else None
        detail = "scan f3, then click"
        if best:
            left = " + ".join(best.get("left") or [])
            p = best.get("p")
            detail = f"YOU ← {left}" + (f"  p={p:.3g}" if p is not None else "")
        rows.append({
            "id": "5", "title": "qpAdm + merger tree",
            "status": "done" if q else "missing",
            "detail": detail,
            "action": None if q else "screen.layer('qpadm')",
        })
    run = job_of("kg-paint")
    paint = (kg_b.paint if kg_b else None) or (kg_imp.paint if kg_imp else None)
    cp = which_chromopainter()
    if run:
        rows.append({"id": "cp", "title": "ChromoPainter", **run})
    elif paint and paint.get("pops"):
        top = ", ".join(f"{k} {v:.0%}" for k, v in list(paint["pops"].items())[:3])
        rows.append({"id": "cp", "title": "ChromoPainter", "status": "done",
                     "detail": top, "action": None})
    else:
        rows.append({
            "id": "cp", "title": "ChromoPainter",
            "status": "missing",
            "detail": ("run after Beagle" if cp else "binary missing — click to compile"),
            "action": "screen.layer('paint')",
        })
    rows.append({
        "id": "tools", "title": "Tools",
        "status": "ready",
        "detail": " · ".join(x for x in (
            f"plink2 {shutil.which('plink2') or 'no'}",
            f"smartpca {which_smartpca() or 'no'}",
            f"R {which_rscript() or 'no'}",
            f"java {which_java() or 'no'}",
            f"Beagle {'yes' if beagle_jar().exists() else 'no'}",
            f"ChromoPainter {'yes' if cp else 'no'}",
            ("1000G phased" if (kg_b and kg_b.phase and kg_b.phase.get("phased"))
             else ("1000G unphased" if kg_b and kg_b.phase else "")),
        ) if x),
        "action": None,
    })
    return rows
