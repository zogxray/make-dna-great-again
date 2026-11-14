"""f-статистики по packed TGENO AADR. Слой 2: outgroup-f3(Mbuti; я, группа).

Сопоставление SNP с чипом — rsID и аллели (как в PGS); при сборке GRCh37 ещё и chrom:pos.
Псевдогаплоиды (суффикс не DG/SG): одна аллель, генотип 0/2. Блочный jackknife 5 сМ."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import aadr, genome_db
from .aadr import KEEP, F3, mark_clusters, save_results
from .genome import Genome
from .jobs import Cancelled, Job
from .pgs import AUTOSOMES, COMPLEMENT, PALINDROMIC
from .store import Store

HEADER = 48
BLOCK_M = 0.05                      # размер блока jackknife, морганы
MIN_SNPS = 1000
MIN_OUT = 2
# Person-level f3 is O(n_samples); HO keeps ~24k moderns and turns a scan into hours
# (plus DuckDB rewrites every flush). Cap: groups still scored; People mode needs a
# smaller panel (1240K ancients) or a later subsampled pass.
PEOPLE_F3_CAP = 8_000
# TGENO хранит копии allele1; 0↔2 даёт копии allele2. 3 = missing.
_FLIP = np.array([2, 1, 0, 3], dtype=np.uint8)


def _header(path: Path) -> tuple[int, int]:
    parts = path.open("rb").read(HEADER).split(b"\x00")[0].split()
    if len(parts) < 3 or parts[0] not in (b"GENO", b"TGENO"):
        raise RuntimeError(f"{path.name}: not packed EIGENSTRAT")
    return int(parts[1]), int(parts[2])


def row_bytes(nsnp: int) -> int:
    return (nsnp + 3) // 4


def dosages(row: bytes | memoryview, snp_index: np.ndarray) -> np.ndarray:
    """Копии allele2 (.snp col. 6): 0/1/2, 3 = missing.

    Биты как rbuff в ADMIXTOOLS; 0↔2: в TGENO v66 хранится копия allele1
    (HG00096 против GBR: corr −0.7 до переворота, +0.7 после)."""
    buf = np.frombuffer(row, dtype=np.uint8)
    byte_i = snp_index >> 2
    shift = (3 - (snp_index & 3)) << 1
    raw = (buf[byte_i] >> shift) & 3
    return _FLIP[raw]


@dataclass
class Panel:
    index: np.ndarray               # int32, позиция SNP в TGENO
    block: np.ndarray               # int32
    n_blocks: int
    rsid: list[str] = field(default_factory=list)
    a2: list[str] = field(default_factory=list)


def read_snp(path: Path) -> list[tuple[str, str, float, int, str, str]]:
    """rsid, chrom, genetic_pos (M), physical_pos, allele1, allele2."""
    rows = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            p = ln.split()
            if len(p) < 6:
                continue
            chrom = p[1].upper().removeprefix("CHR")
            try:
                gm, pos = float(p[2]), int(p[3])
            except ValueError:
                continue
            rows.append((p[0], chrom, gm, pos, p[4].upper(), p[5].upper()))
    return rows


def chip_alleles(g: Genome) -> tuple[dict[str, tuple[str, str, str]], dict[tuple[str, int], tuple[str, str]]]:
    by_id: dict[str, tuple[str, str, str]] = {}
    by_pos: dict[tuple[str, int], tuple[str, str]] = {}
    use_pos = (g.build or "37") == "37"
    for name, c in g.chroms.items():
        if name not in AUTOSOMES:
            continue
        for rid, pos, gt in zip(c.ids, c.pos, c.gts):
            if len(gt) != 2 or gt[0] not in COMPLEMENT or gt[1] not in COMPLEMENT:
                continue
            by_id[rid] = (name, gt[0], gt[1])
            if use_pos:
                by_pos[name, int(pos)] = (gt[0], gt[1])
    return by_id, by_pos


def _freq_as_a2(p_alt: float, ref: str, alt: str, a1: str, a2: str) -> float | None:
    """Частота AADR allele2 из частоты 1000G ALT."""
    pair = {a1, a2}
    if alt in pair and ref in pair:
        return p_alt if alt == a2 else 1.0 - p_alt
    if pair in PALINDROMIC or {ref, alt} in PALINDROMIC:
        return None
    refc, altc = COMPLEMENT.get(ref), COMPLEMENT.get(alt)
    if not refc or not altc:
        return None
    if altc in pair and refc in pair:
        return p_alt if altc == a2 else 1.0 - p_alt
    return None


def _align(a: str, b: str, a1: str, a2: str) -> int | None:
    pair = {a1, a2}
    if a in pair and b in pair:
        return (a == a2) + (b == a2)
    if pair in PALINDROMIC:
        return None
    a, b = COMPLEMENT[a], COMPLEMENT[b]
    if a in pair and b in pair:
        return (a == a2) + (b == a2)
    return None


def match_chip(snps: list[tuple[str, str, float, int, str, str]],
               g: Genome) -> tuple[Panel, np.ndarray, np.ndarray]:
    by_id, by_pos = chip_alleles(g)
    idx, blocks, dose, n_al, rsids, a2s = [], [], [], [], [], []
    block_key: dict[tuple[int, int], int] = {}
    for i, (rsid, chrom, gm, pos, a1, a2) in enumerate(snps):
        if chrom not in AUTOSOMES or a1 not in COMPLEMENT or a2 not in COMPLEMENT or a1 == a2:
            continue
        if {a1, a2} in PALINDROMIC:
            continue
        hit = by_id.get(rsid)
        if hit:
            ch, a, b = hit
            if ch != chrom:
                continue
        else:
            ab = by_pos.get((chrom, pos))
            if not ab:
                continue
            a, b = ab
        if {a, b} in PALINDROMIC:
            continue
        d = _align(a, b, a1, a2)
        if d is None:
            continue
        ch_i = int(chrom)
        key = (ch_i, int(gm / BLOCK_M)) if gm > 0 else (ch_i, pos // 5_000_000)
        if key not in block_key:
            block_key[key] = len(block_key)
        idx.append(i)
        blocks.append(block_key[key])
        dose.append(d)
        n_al.append(2)
        rsids.append(rsid)
        a2s.append(a2)
    if len(idx) < MIN_SNPS:
        raise RuntimeError(f"only {len(idx)} SNPs overlap the chip and the reference panel "
                           f"(need {MIN_SNPS})")
    panel = Panel(np.array(idx, np.int32), np.array(blocks, np.int32), len(block_key),
                  rsids, a2s)
    return panel, np.array(dose, np.float64), np.array(n_al, np.float64)


def match_imputed(snps: list[tuple[str, str, float, int, str, str]],
                  imputed: dict[str, tuple[float, str, str]]
                  ) -> tuple[Panel, np.ndarray, np.ndarray]:
    """Панель = AADR ∩ imputed (rsID). p — частота 1000G ALT; выравнивается к allele2."""
    idx, blocks, dose, n_al, rsids, a2s = [], [], [], [], [], []
    block_key: dict[tuple[int, int], int] = {}
    for i, (rsid, chrom, gm, pos, a1, a2) in enumerate(snps):
        if chrom not in AUTOSOMES or a1 not in COMPLEMENT or a2 not in COMPLEMENT or a1 == a2:
            continue
        if {a1, a2} in PALINDROMIC:
            continue
        hit = imputed.get(rsid)
        if not hit:
            continue
        p_alt, ref, alt = hit
        if not math.isfinite(p_alt):
            continue
        p_a2 = _freq_as_a2(p_alt, ref, alt, a1, a2)
        if p_a2 is None:
            continue
        copies = 2.0 * p_a2
        ch_i = int(chrom)
        key = (ch_i, int(gm / BLOCK_M)) if gm > 0 else (ch_i, pos // 5_000_000)
        if key not in block_key:
            block_key[key] = len(block_key)
        idx.append(i)
        blocks.append(block_key[key])
        dose.append(copies)
        n_al.append(2)
        rsids.append(rsid)
        a2s.append(a2)
    if len(idx) < MIN_SNPS:
        raise RuntimeError(f"only {len(idx)} SNPs overlap imputed and AADR (need {MIN_SNPS})")
    panel = Panel(np.array(idx, np.int32), np.array(blocks, np.int32), len(block_key),
                  rsids, a2s)
    return panel, np.array(dose, np.float64), np.array(n_al, np.float64)


def _add(g: np.ndarray, diploid: bool, alt: np.ndarray, n: np.ndarray) -> None:
    # NumPy 2: uint16[mask] += int32 → _UFuncOutputCastingError; where= обходит каст.
    if diploid:
        ok = g < 3
        np.add(alt, g, out=alt, where=ok, casting="unsafe")
        np.add(n, 2, out=n, where=ok, casting="unsafe")
    else:
        ok = (g == 0) | (g == 2)
        np.add(alt, g >> 1, out=alt, where=ok, casting="unsafe")
        np.add(n, 1, out=n, where=ok, casting="unsafe")


def jackknife(num: np.ndarray, den: np.ndarray) -> tuple[float, float]:
    tot_n, tot_d = float(num.sum()), float(den.sum())
    if tot_d <= 0:
        return math.nan, math.nan
    est = tot_n / tot_d
    leave = [(tot_n - float(num[i])) / (tot_d - float(den[i]))
             for i in range(len(num)) if tot_d - float(den[i]) > 0]
    k = len(leave)
    if k < 3:
        return est, math.nan
    m = sum(leave) / k
    var = ((k - 1) / k) * sum((x - m) ** 2 for x in leave)
    return est, math.sqrt(max(0.0, var))


def f3_blocks(p_o: np.ndarray, n_o: np.ndarray, p_t: np.ndarray, n_t: np.ndarray,
              alt_g: np.ndarray, n_g: np.ndarray, block: np.ndarray, n_blocks: int) -> tuple[float, float, int]:
    ok = (n_o > 0) & (n_t > 0) & (n_g > 0)
    used = int(ok.sum())
    if used == 0:
        return math.nan, math.nan, 0
    p_g = np.divide(alt_g, n_g, out=np.zeros_like(p_o), where=ok)
    val = (p_o - p_t) * (p_o - p_g)
    co = ok & (n_o > 1)
    val[co] -= p_o[co] * (1.0 - p_o[co]) / (n_o[co] - 1.0)
    num = np.zeros(n_blocks, np.float64)
    den = np.zeros(n_blocks, np.float64)
    np.add.at(num, block[ok], val[ok])
    np.add.at(den, block[ok], 1.0)
    est, se = jackknife(num, den)
    return est, se, used


def f4_blocks(p_o: np.ndarray, n_o: np.ndarray, p_t: np.ndarray, n_t: np.ndarray,
              p_a: np.ndarray, n_a: np.ndarray, p_b: np.ndarray, n_b: np.ndarray,
              block: np.ndarray, n_blocks: int) -> tuple[float, float, int]:
    """f4(O, T; A, B) = (p_o − p_t)(p_a − p_b). Jackknife по тем же блокам, что f3."""
    ok = (n_o > 0) & (n_t > 0) & (n_a > 0) & (n_b > 0)
    used = int(ok.sum())
    if used == 0:
        return math.nan, math.nan, 0
    val = (p_o - p_t) * (p_a - p_b)
    num = np.zeros(n_blocks, np.float64)
    den = np.zeros(n_blocks, np.float64)
    np.add.at(num, block[ok], val[ok])
    np.add.at(den, block[ok], 1.0)
    est, se = jackknife(num, den)
    return est, se, used


def _wanted(cat: aadr.Catalog, questionable: bool) -> np.ndarray:
    flag = np.zeros(cat.nind, np.bool_)
    for s in cat.samples:
        if s.keep or (questionable and s.questionable):
            flag[s.i] = True
    return flag


def _record(name: str, est: float, se: float, n: int, used: int) -> F3:
    z = est / se if se and math.isfinite(se) and se > 0 and math.isfinite(est) else 0.0
    return F3(name, float(est) if math.isfinite(est) else 0.0,
              float(se) if math.isfinite(se) else 0.0, float(z), int(n), int(used),
              cluster=1)


def _from_gtypes(g: np.ndarray, diploid: bool, p_o: np.ndarray, n_o: np.ndarray,
                 p_t: np.ndarray, n_t: np.ndarray, panel: Panel
                 ) -> tuple[tuple[float, float, int], np.ndarray, np.ndarray]:
    if diploid:
        ok = g < 3
        alt = np.where(ok, g, 0).astype(np.float64)
        n = np.where(ok, 2.0, 0.0)
    else:
        ok = (g == 0) | (g == 2)
        alt = np.where(ok, g >> 1, 0).astype(np.float64)
        n = np.where(ok, 1.0, 0.0)
    return f3_blocks(p_o, n_o, p_t, n_t, alt, n, panel.block, panel.n_blocks), alt, n


def _mark_f4(results: dict[str, F3], gi: dict[str, int], p_o: np.ndarray, n_o: np.ndarray,
             p_t: np.ndarray, n_t: np.ndarray, alt: np.ndarray, nct: np.ndarray,
             panel: Panel, z_cut: float = 3.0, field: str = "cluster") -> None:
    scored = [r for r in results.values() if r.ok and r.name in gi]
    if not scored:
        return
    best = max(scored, key=lambda r: r.est)
    kb = gi[best.name]
    n_best = nct[kb].astype(np.float64)
    p_best = np.divide(alt[kb], n_best, out=np.zeros_like(p_o), where=n_best > 0)
    setattr(best, field, 0)
    if field == "cluster":
        best.f4_z = 0.0
    for r in scored:
        if r.name == best.name:
            continue
        k = gi[r.name]
        n_x = nct[k].astype(np.float64)
        p_x = np.divide(alt[k], n_x, out=np.zeros_like(p_o), where=n_x > 0)
        est, se, _used = f4_blocks(p_o, n_o, p_t, n_t, p_x, n_x, p_best, n_best,
                                   panel.block, panel.n_blocks)
        z = est / se if se and math.isfinite(se) and se > 0 else 0.0
        setattr(r, field, 0 if z < z_cut else 1)
        if field == "cluster":
            r.f4_z = float(z)


def _mark_epoch_f4(results: dict[str, F3], cat: aadr.Catalog, gi: dict[str, int],
                   p_o: np.ndarray, n_o: np.ndarray, p_t: np.ndarray, n_t: np.ndarray,
                   alt: np.ndarray, nct: np.ndarray, panel: Panel, top_n: int = 20) -> None:
    buckets: dict[str, list[F3]] = {}
    for r in results.values():
        g = cat.groups.get(r.name)
        if g and r.ok:
            buckets.setdefault(g.epoch, []).append(r)
    for rows in buckets.values():
        rows.sort(key=lambda r: -r.est)
        for r in rows[top_n:]:
            r.epoch_cluster = 1
        sub = {r.name: r for r in rows[:top_n]}
        if len(sub) == 1:
            next(iter(sub.values())).epoch_cluster = 0
            continue
        _mark_f4(sub, gi, p_o, n_o, p_t, n_t, alt, nct, panel, field="epoch_cluster")


def _group_pca(names: list[str], gi: dict[str, int], alt: np.ndarray, nct: np.ndarray,
               p_t: np.ndarray, n_t: np.ndarray, n_o: np.ndarray,
               max_snps: int = 30_000, min_frac: float = 0.4) -> dict | None:
    ok = n_o > 0
    idx = np.where(ok)[0]
    if len(idx) > max_snps:
        idx = idx[:: max(1, len(idx) // max_snps)][:max_snps]
    if len(idx) < 20:
        return None
    rows, labels = [], []
    for name in names:
        k = gi[name]
        n = nct[k][idx].astype(np.float64)
        if (n > 0).mean() < min_frac:
            continue
        p = np.divide(alt[k][idx], n, out=np.full(len(idx), np.nan), where=n > 0)
        rows.append(p)
        labels.append(name)
    if len(rows) < 3:
        return None
    you = np.where(n_t[idx] > 0, p_t[idx], np.nan)
    X = np.vstack([*rows, you])
    labels.append("YOU")
    mu = np.nanmean(X, axis=0)
    nan = np.isnan(X)
    X[nan] = np.take(mu, np.where(nan)[1])
    X -= mu
    sd = X.std(axis=0)
    keep = sd > 1e-8
    if int(keep.sum()) < 10:
        return None
    X = X[:, keep]
    u, s, _vt = np.linalg.svd(X, full_matrices=False)
    k = min(2, u.shape[1])
    xy = (u[:, :k] * s[:k]).tolist()
    var = (s[:k] ** 2 / (s ** 2).sum()).tolist() if float(s.sum()) else [0.0] * k
    return {"method": "svd", "labels": labels, "xy": xy, "var": var}


def _pick_probe(cat: aadr.Catalog, want: np.ndarray) -> aadr.Sample | None:
    for s in cat.samples:
        if want[s.i] and s.gid.upper().startswith("HG00096"):
            return s
    best: aadr.Sample | None = None
    for s in cat.samples:
        if not want[s.i] or not s.diploid or s.group == cat.outgroup:
            continue
        g = cat.groups.get(s.group)
        if not g or g.n < 10:
            continue
        if best is None or s.snps > best.snps:
            best = s
    return best


def _leave_one_out(probe: aadr.Sample, alt_h: np.ndarray, n_h: np.ndarray,
                   names: list[str], gi: dict[str, int], alt: np.ndarray, nct: np.ndarray,
                   p_o: np.ndarray, n_o: np.ndarray, panel: Panel) -> dict:
    p_t = np.divide(alt_h, n_h, out=np.zeros_like(p_o), where=n_h > 0)
    natal = probe.group
    ranked: list[tuple[float, str, float, int]] = []
    for name in names:
        k = gi[name]
        alt_g = alt[k].astype(np.float64)
        n_g = nct[k].astype(np.float64)
        if name == natal:
            alt_g = alt_g - alt_h
            n_g = np.maximum(n_g - n_h, 0)
        est, se, used = f3_blocks(p_o, n_o, p_t, n_h, alt_g, n_g, panel.block, panel.n_blocks)
        if math.isfinite(est) and used >= MIN_SNPS:
            ranked.append((est, name, se, used))
    ranked.sort(reverse=True)
    rank = next((i + 1 for i, row in enumerate(ranked) if row[1] == natal), None)
    natal_row = next((row for row in ranked if row[1] == natal), None)
    return {
        "gid": probe.gid, "group": natal, "rank": rank, "n_scored": len(ranked),
        "best": ranked[0][1] if ranked else "",
        "est": natal_row[0] if natal_row else None,
        "se": natal_row[2] if natal_row else None,
        "ok": rank == 1,
    }


def _read_outgroup(geno: Path, indices: list[int], panel: Panel,
                   cat: aadr.Catalog, alt_o: np.ndarray, n_o: np.ndarray) -> None:
    _nind, nsnp = _header(geno)
    rlen = row_bytes(nsnp)
    with geno.open("rb") as fh:
        buf = memoryview(bytearray(rlen))
        for i in indices:
            fh.seek(HEADER + i * rlen)
            if fh.readinto(buf) != rlen:
                raise RuntimeError(f"TGENO truncated at individual {i}")
            _add(dosages(buf, panel.index), cat.samples[i].diploid, alt_o, n_o)


def scan_f3(store: Store, genome_path: Path, job: Job, genome: Genome | None = None,
            questionable: bool = False, kind: str = "",
            p_t: np.ndarray | None = None, n_t: np.ndarray | None = None,
            panel: Panel | None = None, panel_key: str = "aadr") -> str:
    """Outgroup-f3 vs AADR 1240K (``panel_key='aadr'``) or Human Origins (``'aadr_ho'``)."""
    folder = store.path(panel_key)
    label = "Human Origins" if panel_key == "aadr_ho" else "AADR"
    if not folder:
        raise RuntimeError(f"set the {label} folder in F2")
    snp_panel = aadr.snp_panel_for(panel_key)
    cat = aadr.load_catalog(folder, job, panel=snp_panel)
    geno, snp_path = cat.files.get("geno"), cat.files.get("snp")
    if geno is None or snp_path is None:
        raise RuntimeError(f"{label} .geno/.snp missing")
    if not cat.outgroup:
        raise RuntimeError("no Mbuti outgroup with ≥2 samples after QC")

    job.set_stage(f"matching SNPs to {label}")
    g = genome_db.get(genome_path, genome)
    if panel is None:
        panel, dose_t, n_chip = match_chip(read_snp(snp_path), g)
        if p_t is None or n_t is None:
            n_t = n_chip
            p_t = np.divide(dose_t, n_t, out=np.zeros_like(dose_t), where=n_t > 0)
    elif p_t is None or n_t is None:
        raise RuntimeError("imputed scan needs dosages for the panel")
    if len(p_t) != len(panel.index) or len(n_t) != len(panel.index):
        raise RuntimeError(f"dosages do not match the {label} panel")

    want = _wanted(cat, questionable)
    names = [n for n, gr in cat.groups.items() if n != cat.outgroup
             and any(want[cat.samples[i].i] for i in gr.samples)]
    gi = {n: k for k, n in enumerate(names)}
    n_snps = len(panel.index)
    try:
        alt = np.zeros((len(names), n_snps), np.uint16)
        nct = np.zeros((len(names), n_snps), np.uint16)
    except MemoryError as exc:
        raise RuntimeError(f"not enough RAM for {len(names)} groups × {n_snps} SNPs") from exc
    alt_o = np.zeros(n_snps, np.float64)
    n_o = np.zeros(n_snps, np.float64)
    n_ind = np.zeros(len(names), np.int32)
    left = np.zeros(len(names), np.int32)

    row_of: list[list[tuple[int, bool]]] = [[] for _ in range(cat.nind)]
    og: list[int] = []
    n_keep = 0
    for s in cat.samples:
        if not want[s.i]:
            continue
        n_keep += 1
        if s.group == cat.outgroup:
            og.append(s.i)
        elif s.group in gi:
            k = gi[s.group]
            row_of[s.i].append((k, s.diploid))
            left[k] += 1
    # HO Affy moderns almost all Pass → person-f3 dominates runtime; keep groups only.
    score_people = n_keep <= PEOPLE_F3_CAP

    job.set_stage("reading outgroup", len(og))
    _read_outgroup(geno, og, panel, cat, alt_o, n_o)
    ok_o = n_o >= MIN_OUT
    p_o = np.divide(alt_o, n_o, out=np.zeros_like(alt_o), where=ok_o)
    n_o = np.where(ok_o, n_o, 0)
    if int(ok_o.sum()) < MIN_SNPS:
        raise RuntimeError("outgroup has too few SNPs after QC")

    nind, nsnp = _header(geno)
    if nind != cat.nind:
        raise RuntimeError(f"TGENO nind {nind} ≠ .ind {cat.nind}")
    rlen = row_bytes(nsnp)
    og_set = set(og)
    probe = _pick_probe(cat, want)
    groups: dict[str, F3] = {}
    people: dict[str, F3] = {}
    alt_h = n_h = None
    check: dict | None = None
    last_save = 0.0

    pca: dict | None = None

    def flush(partial: bool) -> None:
        nonlocal last_save, pca
        if partial:
            mark_clusters(list(groups.values()))
        else:
            _mark_f4(groups, gi, p_o, n_o, p_t, n_t, alt, nct, panel)
            _mark_epoch_f4(groups, cat, gi, p_o, n_o, p_t, n_t, alt, nct, panel)
            job.set_stage("group PCA")
            pca = _group_pca(names, gi, alt, nct, p_t, n_t, n_o)
        if people:
            mark_clusters(list(people.values()))
        # Partial: rewrite groups only — rewriting tens of thousands of person rows
        # every 1.5s is what made HO scans look hung / die mid-flight.
        save_results(genome_path, cat.outgroup or "", n_snps, groups,
                     people=KEEP if partial else people,
                     check=None if partial else check, questionable=questionable,
                     partial=partial, kind=kind, pca=None if partial else pca,
                     panel=panel_key)
        last_save = time.monotonic()

    job.unit = "items"
    people_note = "" if score_people else f" · groups only ({n_keep:,} samples > {PEOPLE_F3_CAP:,})"
    job.set_stage(f"scanning {label}{people_note}", nind)
    try:
        with geno.open("rb") as fh:
            fh.seek(HEADER)
            buf = memoryview(bytearray(rlen))
            for i in range(nind):
                job.check()
                if fh.readinto(buf) != rlen:
                    raise RuntimeError(f"TGENO truncated at individual {i}")
                if i not in og_set and not row_of[i]:
                    if i % 100 == 0:
                        job.progress(i + 1)
                    continue
                gtypes = dosages(buf, panel.index)
                if i in og_set:
                    if i % 100 == 0:
                        job.progress(i + 1)
                    continue
                s = cat.samples[i]
                for k, dip in row_of[i]:
                    _add(gtypes, dip, alt[k], nct[k])
                    n_ind[k] += 1
                    left[k] -= 1
                    if left[k] == 0:
                        est, se, used = f3_blocks(
                            p_o, n_o, p_t, n_t, alt[k].astype(np.float64),
                            nct[k].astype(np.float64), panel.block, panel.n_blocks)
                        if used > 0:
                            groups[names[k]] = _record(names[k], est, se, int(n_ind[k]), used)
                need_person = score_people or (probe is not None and i == probe.i)
                if need_person:
                    (est, se, used), alt_p, n_p = _from_gtypes(
                        gtypes, s.diploid, p_o, n_o, p_t, n_t, panel)
                    if score_people and used > 0:
                        people[s.gid] = _record(s.gid, est, se, 1, used)
                    if probe and i == probe.i:
                        alt_h, n_h = alt_p, n_p
                now = time.monotonic()
                if now - last_save >= 1.5 and groups:
                    flush(True)
                if i % 100 == 0:
                    job.progress(i + 1)
            job.progress(nind)

        if probe is not None and alt_h is not None:
            job.set_stage("leave-one-out")
            check = _leave_one_out(probe, alt_h, n_h, names, gi, alt, nct, p_o, n_o, panel)
        job.set_stage("f4 clusters / PCA", len(groups))
        flush(False)
        if not kind:
            try:
                from .admix import verify_f3
                job.set_stage("admixtools2 check")
                extra = verify_f3(store, genome_path, job, g, cat, panel, p_t * n_t, n_t,
                                  groups, questionable)
                check = {**(check or {}), **extra}
                save_results(genome_path, cat.outgroup or "", n_snps, groups, people=people,
                             check=check, questionable=questionable, partial=False, kind=kind,
                             pca=pca, panel=panel_key)
            except (OSError, RuntimeError, ValueError) as exc:
                if check is None:
                    check = {}
                check["admixtools_error"] = str(exc)[:200]
                save_results(genome_path, cat.outgroup or "", n_snps, groups, people=people,
                             check=check, questionable=questionable, partial=False, kind=kind,
                             pca=pca, panel=panel_key)
    except Cancelled:
        flush(True)
        raise

    ranked = sorted((r for r in groups.values() if r.ok), key=lambda r: -r.est)
    top = ", ".join(r.name for r in ranked[:3]) if ranked else "none"
    loo = ""
    if check and check.get("gid"):
        loo = f" · LOO {check['gid']} → {check.get('group')} #{check.get('rank')}"
    people_bit = (f"{len(people)} people" if score_people
                  else f"0 people (skipped {n_keep:,} > {PEOPLE_F3_CAP:,})")
    return (f"{len(groups)} groups · {people_bit} · {n_snps:,} SNPs · top: {top}{loo}")
