"""Геном пользователя: чтение сырых файлов генотипирования и сводки по ним. Без зависимостей от UI.

Поддержано: 23andMe, Argenética, MyHeritage, FTDNA (4 колонки, TSV или CSV, кавычки допустимы),
AncestryDNA (5 колонок: allele1 + allele2). Файл может лежать внутри .zip или .gz.

TODO: VCF/BAM, индекс в SQLite, lookup(rsid | chrom:pos)."""

from __future__ import annotations

import gzip
import io
import re
import zipfile
from array import array
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import Callable, Iterator, TextIO

CHROM_ALIASES = {"23": "X", "24": "Y", "25": "XY", "26": "MT", "M": "MT", "MITO": "MT"}
SEX_CHROM_ORDER = {"X": 0, "Y": 1, "XY": 2, "MT": 3}
BASES = frozenset("ACGT")
NO_CALL = frozenset("-0?")

# Псевдоаутосомные районы. XY в файлах 23andMe-формата — PAR в координатах X.
PAR = {
    "37": {"X": ((60001, 2699520), (154931044, 155260560)),
           "Y": ((10001, 2649520), (59034050, 59363566))},
    "38": {"X": ((10001, 2781479), (155701383, 156030895)),
           "Y": ((10001, 2781479), (56887903, 57217415))},
}

Progress = Callable[[float | None, int], None]


def chrom_key(name: str) -> tuple:
    if name.isdigit():
        return (0, int(name), "")
    return (1, SEX_CHROM_ORDER.get(name, 9), name)


def gt_class(gt: str) -> str:
    """nocall · het · hom · hemi (одна буква) · other (индели D/I и прочее)."""
    if not gt or all(c in NO_CALL for c in gt):
        return "nocall"
    if len(gt) == 1:
        return "hemi" if gt in BASES else "other"
    if len(gt) == 2 and gt[0] in BASES and gt[1] in BASES:
        return "hom" if gt[0] == gt[1] else "het"
    return "other"


def id_prefix(rid: str) -> str:
    return "".join(ch for ch in rid if ch.isalpha())


@dataclass
class Chrom:
    name: str
    ids: list[str]
    pos: array
    gts: list[str]
    sorted_input: bool = True
    n: int = 0
    rs: int = 0
    classes: Counter = field(default_factory=Counter)
    lengths: Counter = field(default_factory=Counter)
    genotypes: Counter = field(default_factory=Counter)
    prefixes: Counter = field(default_factory=Counter)
    pos_min: int | None = None
    pos_max: int | None = None

    @property
    def named(self) -> int:
        return self.n - self.rs

    @property
    def nocall(self) -> int:
        return self.classes["nocall"]

    @property
    def called(self) -> int:
        return self.n - self.nocall

    def finalize(self) -> None:
        pos = self.pos
        if any(a > b for a, b in zip(pos, islice(pos, 1, None))):
            order = sorted(range(len(pos)), key=pos.__getitem__)
            self.ids = [self.ids[i] for i in order]
            self.gts = [self.gts[i] for i in order]
            self.pos = array("l", (pos[i] for i in order))
            self.sorted_input = False
        self.n = len(self.ids)
        if self.n:
            self.pos_min, self.pos_max = self.pos[0], self.pos[-1]
        self.genotypes = Counter(self.gts)
        for gt, c in self.genotypes.items():
            self.classes[gt_class(gt)] += c
            self.lengths[len(gt)] += c
        self.rs = sum(1 for r in self.ids if r.startswith("rs"))
        self.prefixes = Counter(id_prefix(r) for r in self.ids if not r.startswith("rs"))

    def zones(self, build: str | None) -> list[tuple[str, int]] | None:
        """PAR1 / вне PAR / PAR2 для X, Y и XY по границам сборки (по умолчанию GRCh37)."""
        ref = "X" if self.name == "XY" else self.name
        bounds = PAR.get(build or "37", PAR["37"]).get(ref)
        if not bounds:
            return None
        (a1, b1), (a2, b2) = bounds
        par1 = par2 = 0
        for p in self.pos:
            if a1 <= p <= b1:
                par1 += 1
            elif a2 <= p <= b2:
                par2 += 1
        return [("PAR1", par1), ("non-PAR", self.n - par1 - par2), ("PAR2", par2)]


@dataclass
class Genome:
    path: Path
    member: str | None
    size: int
    header: list[str]
    columns: str | None
    layout: str
    fmt: str
    build: str | None                    # из шапки RAW (может быть None)
    chroms: dict[str, Chrom]
    malformed: int
    malformed_sample: list[str]
    # Детект по якорям (DuckDB / allelix); пусто до ingest.
    chrom_builds: dict[str, str] = field(default_factory=dict)  # chrom → 37|38|uncertain
    chrom_votes: dict[str, tuple[int, int]] = field(default_factory=dict)  # → (v37, v38)
    build_detected: str | None = None    # majority (без Y/MT в приоритете)

    @property
    def total(self) -> int:
        return sum(c.n for c in self.chroms.values())

    def count(self, cls: str, autosomal: bool = False) -> int:
        return sum(c.classes[cls] for c in self.chroms.values() if not autosomal or c.name.isdigit())

    def chrom_build(self, name: str) -> str | None:
        """Билд для хромосомы: детект → шапка файла."""
        return self.chrom_builds.get(name) or self.build_detected or self.build

    def build_label(self) -> str:
        """Короткая метка для списка файлов / SUMMARY."""
        maj = self.build_detected or self.build
        if not maj:
            return "build ?"
        mixed = sorted(
            f"{c}:{b}" for c, b in self.chrom_builds.items()
            if b in ("37", "38") and b != maj
        )
        base = f"GRCh{maj}"
        if mixed:
            return f"{base} mixed ({', '.join(mixed)})"
        if self.build and self.build_detected and self.build != self.build_detected:
            return f"{base} detected (header GRCh{self.build})"
        if self.build_detected and not self.build:
            return f"{base} detected"
        return base

    def sex(self) -> tuple[str, str]:
        y, x = self.chroms.get("Y"), self.chroms.get("X")
        y_rate = y.called / y.n if y and y.n else 0.0
        x_dip = (x.classes["het"] + x.classes["hom"]) if x else 0
        x_het = x.classes["het"] / x_dip if x_dip else None
        note = f"Y called {y_rate:.0%}" + (f", X het {x_het:.1%}" if x_het is not None else "")
        if y_rate > 0.5 and (x_het is None or x_het < 0.02):
            return "male (XY)", note
        if y_rate < 0.1 and x_het is not None and x_het > 0.05:
            return "female (XX)", note
        return "uncertain", note


def detect_build(header: list[str]) -> str | None:
    text = "\n".join(header).lower()
    m = re.search(r"grch\s*(3[678])", text) or re.search(r"build\s*=?\s*(3[678])", text)
    if m:
        return m.group(1)
    if "hg19" in text or "b37" in text:
        return "37"
    if "hg38" in text:
        return "38"
    return None


KNOWN_FORMATS = (("23andme", "23andMe"), ("ancestrydna", "AncestryDNA"), ("myheritage", "MyHeritage"),
                 ("familytreedna", "FamilyTreeDNA"), ("argen", "Argenética"), ("livingdna", "LivingDNA"))


def detect_format(header: list[str], columns: str | None, ncols: int) -> str:
    text = "\n".join(header)
    m = re.search(r"format\s*=\s*(\S+)", text)
    if m:
        return m.group(1)
    low = text.lower()
    for key, label in KNOWN_FORMATS:
        if key in low:
            return label
    if ncols == 5:
        return "AncestryDNA-like"
    if columns and columns.replace('"', "").upper() == "RSID,CHROMOSOME,POSITION,RESULT":
        return "MyHeritage/FTDNA-like"
    return "unknown"


@contextmanager
def open_text(path: Path) -> Iterator[tuple[TextIO, str | None, int | None]]:
    """(поток, имя файла в архиве, размер распакованных данных или None)."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            files = [i for i in z.infolist() if not i.is_dir()]
            if not files:
                raise ValueError("zip archive is empty")
            info = max(files, key=lambda i: (i.filename.lower().endswith((".txt", ".csv", ".tsv")),
                                             i.file_size))
            with io.TextIOWrapper(z.open(info), encoding="utf-8-sig", errors="replace") as fh:
                yield fh, info.filename, info.file_size
    elif path.suffix.lower() == ".gz":
        with gzip.open(path, "rt", encoding="utf-8-sig", errors="replace") as fh:
            yield fh, None, None
    else:
        with path.open("r", encoding="utf-8-sig", errors="replace") as fh:
            yield fh, None, path.stat().st_size


def load(path: Path, progress: Progress | None = None) -> Genome:
    path = Path(path)
    header: list[str] = []
    columns: str | None = None
    store: dict[str, tuple[list[str], array, list[str]]] = {}
    chrom_cache: dict[str, str] = {}
    gt_cache: dict[str, str] = {}
    malformed, bad_sample = 0, []
    delim: str | None = ""
    ncols = 0
    lines = chars = 0

    with open_text(path) as (fh, member, total_chars):
        for raw in fh:
            lines += 1
            chars += len(raw)
            if progress and lines % 65536 == 0:
                progress(min(1.0, chars / total_chars) if total_chars else None, lines)
            if raw.startswith("#"):
                header.append(raw.rstrip("\r\n"))
                continue
            line = raw.strip()
            if not line:
                continue
            if delim == "":
                if any(h.startswith("##fileformat=VCF") for h in header):
                    raise ValueError("VCF is not supported yet")
                delim = "\t" if "\t" in line else ("," if "," in line else None)
            parts = line.split(delim)
            if '"' in line:
                parts = [p.strip().strip('"') for p in parts]
            n = len(parts)
            if n == 4:
                rid, ch, p, gt = parts
            elif n == 5:
                rid, ch, p, a, b = parts
                gt = a + b
            else:
                malformed += 1
                if len(bad_sample) < 20:
                    bad_sample.append(line)
                continue
            if not p.isdigit():
                if columns is None and not store:
                    columns = line
                    continue
                malformed += 1
                if len(bad_sample) < 20:
                    bad_sample.append(line)
                continue
            ncols = n
            name = chrom_cache.get(ch)
            if name is None:
                c = ch.strip().upper()
                c = c[3:] if c.startswith("CHR") else c
                name = chrom_cache[ch] = CHROM_ALIASES.get(c, c)
            slot = store.get(name)
            if slot is None:
                slot = store[name] = ([], array("l"), [])
            slot[0].append(rid)
            slot[1].append(int(p))
            g = gt_cache.get(gt)
            if g is None:
                g = gt_cache[gt] = gt
            slot[2].append(g)

    if not store:
        raise ValueError("no genotype rows found")
    if progress:
        progress(1.0, lines)

    chroms: dict[str, Chrom] = {}
    for name in sorted(store, key=chrom_key):
        ids, pos, gts = store[name]
        c = Chrom(name, ids, pos, gts)
        c.finalize()
        chroms[name] = c

    sep = {"\t": "TSV", ",": "CSV", None: "whitespace"}.get(delim, "?")
    return Genome(
        path=path, member=member, size=path.stat().st_size, header=header, columns=columns,
        layout=f"{sep}, {ncols} columns", fmt=detect_format(header, columns, ncols),
        build=detect_build(header), chroms=chroms, malformed=malformed, malformed_sample=bad_sample,
    )
