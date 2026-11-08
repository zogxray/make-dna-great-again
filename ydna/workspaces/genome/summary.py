"""Тексты сводок: строка и подробности хромосомы (средняя панель), общая сводка файла (правая)."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text

from ...core.genome import Chrom, Genome
from ...core.ylink import ANC, DER, MISMATCH, NOCALL, YLink
from ...theme.styles import (BAD, BRIGHT, BRIGHT_BOLD, CLASS_STYLE as _CLASS, DIM, FAINT,
                             GHOST, HI, HI_BOLD, HI_REV, INK, MUTED, OK, OK_BOLD, SECONDARY,
                             SHADOW, WARN, Y_STATE_STYLE)
from ..base import click

STATE_LABEL = {DER: "DER", ANC: "anc", MISMATCH: "≠", NOCALL: "—"}
STATE_WORD = {DER: "derived", ANC: "ancestral", MISMATCH: "matches neither allele", NOCALL: "no-call"}
STATE_STYLE = {DER: Y_STATE_STYLE["der"], ANC: Y_STATE_STYLE["anc"],
               MISMATCH: Y_STATE_STYLE["mismatch"], NOCALL: Y_STATE_STYLE["nocall"]}
CLASS_STYLE = dict(_CLASS)
CLASS_LABEL = (("het", "het"), ("hom", "hom"), ("hemi", "hemi"), ("other", "indel/other"),
               ("nocall", "no-call"))
EIGHTHS = " ▏▎▍▌▋▊▉█"


def pct(a: int, b: int, nd: int = 1) -> str:
    return f"{100 * a / b:.{nd}f}%" if b else "—"


def bar(frac: float, width: int) -> str:
    cells = max(0.0, min(1.0, frac)) * width
    full = int(cells)
    rest = EIGHTHS[int((cells - full) * 8)] if full < width else ""
    return ("█" * full + rest).ljust(width)


def span(c: Chrom) -> str:
    return f"{c.pos_min:,} – {c.pos_max:,}" if c.n else "—"


def link_lines(link: YLink) -> list[Text]:
    """Сводка связи Y-маркеров с Haplotree и Discover."""
    out: list[Text] = []
    t = Text()
    t.append("haplotree ", style=MUTED)
    t.append(f"{link.found:,} of {link.named:,} named SNPs are tree variants")
    t.append(f" · same position {link.pos_ok:,}", style=DIM)
    if link.published:
        t.append(f" · tree {link.published[:10]}", style=DIM)
    out.append(t)
    t = Text()
    t.append("alleles ", style=MUTED)
    for i, s in enumerate((DER, ANC, MISMATCH, NOCALL)):
        if i:
            t.append(" · ", style=GHOST)
        t.append(f"{STATE_WORD[s]} {link.states[s]:,}", style=STATE_STYLE[s])
    out.append(t)
    t = Text()
    t.append("Discover ", style=MUTED)
    if link.discover:
        t.append(f"{link.nodes_downloaded:,} of {len(link.nodes):,} linked haplogroups downloaded")
        if link.nodes_downloaded < len(link.nodes):
            t.append(" · fetch the rest in F2", style=DIM)
    else:
        t.append("folder not set (F2)", style=DIM)
    out.append(t)
    t = Text()
    t.append("legend ", style=MUTED)
    t.append("DER", style=STATE_STYLE[DER])
    t.append(" derived · ", style=DIM)
    t.append("anc", style=STATE_STYLE[ANC])
    t.append(" ancestral · ", style=DIM)
    t.append("≠", style=STATE_STYLE[MISMATCH])
    t.append(" neither allele · ", style=DIM)
    t.append("●", style=OK)
    t.append(" in Discover · ", style=DIM)
    t.append("○", style=DIM)
    t.append(" not yet · N next derived", style=DIM)
    out.append(t)
    return out


def chrom_detail(c: Chrom, build: str | None, link: YLink | None = None,
                 *, votes: tuple[int, int] | None = None) -> list[Text]:
    """Строки сводки, которые показываются под раскрытой хромосомой."""
    out: list[Text] = []

    t = Text()
    t.append("build ", style=MUTED)
    if build in ("37", "38"):
        t.append(f"GRCh{build}", style=OK if build else BRIGHT)
    elif build == "uncertain":
        t.append("uncertain", style=WARN)
    else:
        t.append("unknown", style=DIM)
    if votes and (votes[0] or votes[1]):
        t.append(f"  · anchors 37={votes[0]:,}  38={votes[1]:,}", style=DIM)
    out.append(t)

    t = Text()
    t.append("calls ", style=MUTED)
    parts = [(lbl, c.classes[k], CLASS_STYLE[k]) for k, lbl in CLASS_LABEL if c.classes[k]]
    for i, (lbl, v, st) in enumerate(parts):
        if i:
            t.append(" · ", style=GHOST)
        t.append(f"{lbl} {v:,}", style=st)
        t.append(f" ({pct(v, c.n)})", style=DIM)
    out.append(t)

    t = Text()
    t.append("genotype length ", style=MUTED)
    t.append(" · ".join(f"{k} → {v:,}" for k, v in sorted(c.lengths.items())))
    out.append(t)

    t = Text()
    t.append("top genotypes ", style=MUTED)
    t.append(" · ".join(f"{g or '∅'} {pct(v, c.n, 0)}" for g, v in c.genotypes.most_common(8)))
    out.append(t)

    t = Text()
    t.append("IDs ", style=MUTED)
    t.append(f"rsID {c.rs:,} · named {c.named:,}")
    if c.prefixes:
        t.append("  (" + ", ".join(f"{p or '?'} {v:,}" for p, v in c.prefixes.most_common(8))
                 + (", …" if len(c.prefixes) > 8 else "") + ")", style=DIM)
    out.append(t)

    zones = c.zones(build)
    if zones:
        t = Text()
        t.append(f"regions (GRCh{build or '37'}) ", style=MUTED)
        t.append(" · ".join(f"{z} {v:,}" for z, v in zones))
        out.append(t)

    if not c.sorted_input:
        out.append(Text("rows were not sorted by position in the file; shown sorted", style=WARN))
    if link and c.name == "Y":
        out += link_lines(link)
    return out


# ───────────────────────── правая панель ─────────────────────────

class _Doc:
    def __init__(self) -> None:
        self.t = Text()

    def title(self, s: str) -> None:
        self.t.append(f"\n{s}\n", style=HI_BOLD)

    def kv(self, key: str, value: object, style: str = "") -> None:
        self.t.append(f"  {key}: ", style=MUTED)
        self.t.append(f"{value}\n", style=style)

    def line(self, s: str, style: str = "") -> None:
        self.t.append(f"  {s}\n", style=style)


def onboarding_summary() -> Text:
    """Пустой F1: первый экран релиза — куда идти дальше по F2–F6."""
    d = _Doc()
    d.t.append(" Y-DNA EXPLORER ", style=HI_REV)
    d.t.append("  start here\n", style=BRIGHT_BOLD)
    d.line("Add a genotype file, then set data sources, then open the tree.", DIM)
    d.title("1 · GENOME (F1)")
    d.line("O / Ins   add a chip file (23andMe, Ancestry, FTDNA…)")
    d.line("Enter     load the highlighted file")
    d.line("Del       remove from the list (file stays on disk)", DIM)
    d.title("2 · DATA (F2)")
    d.line("P         set Haplotree (required for F3) / Discover / AADR")
    d.line("D         fetch missing haplogroup JSON for your Y line", DIM)
    d.title("3 · TREE (F3)")
    d.line("Browse the full haplotree; Discover JSON enriches cards where present")
    d.title("THEN")
    d.line("F4 PGS · F5 Reference · F6 Chat", DIM)
    return d.t


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def stage_banner(stage: str, path_name: str, detail: str = "") -> Text:
    """Шапка этапа: Reading / Loading DuckDB / Linking Y — не путать с зависанием."""
    t = Text()
    if stage == "reading":
        t.append(" READING FILE ", style=f"bold reverse {WARN}")
        t.append(f"  {path_name}", style=BRIGHT_BOLD)
        if detail:
            t.append(f"  · {detail}", style=WARN)
        t.append("\n")
        t.append("  Parsing RAW → DuckDB index. Chromosome view stays empty until done.\n", style=DIM)
        t.append("  Next: Linking Y markers to the haplotree.\n", style=DIM)
    elif stage == "loading":
        t.append(" LOADING DUCKDB ", style=HI_REV)
        t.append(f"  {path_name}", style=BRIGHT_BOLD)
        if detail:
            t.append(f"  · {detail}", style=HI)
        t.append("\n")
        t.append("  Fresh index in DuckDB — no RAW re-parse.\n", style=DIM)
        t.append("  Next: Linking Y markers to the haplotree.\n", style=DIM)
    elif stage == "linking":
        t.append(" LINKING Y ", style=HI_REV)
        t.append(f"  {path_name}", style=BRIGHT_BOLD)
        if detail:
            t.append(f"  · {detail}", style=HI)
        t.append("\n")
        t.append("  File is ready — chromosomes are shown. Matching named Y SNPs…\n", style=DIM)
    t.append("\n")
    return t


def reading_summary(path: Path, detail: str = "", *, from_db: bool = False) -> Text:
    """Правая панель, пока файла ещё нет в памяти: кто выбран и что идёт."""
    d = _Doc()
    d.t.append_text(stage_banner("loading" if from_db else "reading", path.name, detail))
    d.title("FILE")
    d.kv("Name", path.name, "bold")
    d.kv("Folder", str(path.parent))
    if path.exists():
        d.kv("Size", human_size(path.stat().st_size))
    d.line("Loading from DuckDB…" if from_db else "Parsing genotype rows → DuckDB…", WARN)
    return d.t


def linking_summary(g: Genome) -> Text:
    """Файл уже прочитан: полная сводка + баннер Linking Y сверху."""
    t = stage_banner("linking", g.path.name)
    t.append_text(summary_text(g, "linking"))
    return t


def link_section(d: _Doc, g: Genome, link: YLink | str | None) -> None:
    d.title("Y · HAPLOTREE & DISCOVER")
    if link == "linking":
        d.line("Linking named Y markers to the haplotree…", HI)
        d.line("This can take a few seconds on a large tree.", DIM)
        return
    if not isinstance(link, YLink):
        d.line(link or "not linked", MUTED)
        return
    y = g.chroms["Y"]
    d.kv("Named SNPs", f"{link.named:,}")
    d.kv("In haplotree", f"{link.found:,} ({pct(link.found, link.named)})"
         + (f" · tree published {link.published[:10]}" if link.published else ""))
    off = link.found - link.pos_ok
    d.kv("Same position", f"{link.pos_ok:,}" + (f" ({off:,} differ)" if off else ""),
         "" if off * 10 < link.found else WARN)
    for s in (DER, ANC, MISMATCH, NOCALL):
        d.kv(STATE_WORD[s].capitalize(), f"{link.states[s]:,}", STATE_STYLE[s])
    d.kv("Linked haplogroups", f"{len(link.nodes):,}")
    if link.discover:
        miss = len(link.nodes) - link.nodes_downloaded
        d.kv("Downloaded (Discover)", f"{link.nodes_downloaded:,} ({pct(link.nodes_downloaded, len(link.nodes))})"
             + (f" · {miss:,} not yet, fetch in F2" if miss else ""))
    else:
        d.kv("Discover", "folder not set (F2)", DIM)
    if link.derived:
        d.line("Derived markers (click to show):", MUTED)
        for i in link.derived:
            m = link.marks[i]
            act = f"screen.goto_snp('Y', {i})"
            d.t.append("    ")
            d.t.append(f"{y.ids[i]:<14}", style=click(act, OK_BOLD))
            d.t.append(f" {m.anc}→{m.der}  ", style=click(act, MUTED))
            d.t.append(m.node, style=click(act))
            d.t.append(" ●\n" if m.downloaded else " ○\n", style=OK if m.downloaded else DIM)


def summary_text(g: Genome, link: YLink | str | None = None) -> Text:
    d = _Doc()
    total = g.total

    d.title("FILE")
    d.kv("Name", g.path.name, "bold")
    d.kv("Folder", str(g.path.parent))
    if g.member:
        d.kv("Inside archive", g.member)
    d.kv("Size", human_size(g.size))
    d.kv("Format", g.fmt)
    d.kv("Layout", g.layout)
    d.kv("Build", g.build_label(), "bold")
    if g.build and g.build_detected and g.build != g.build_detected:
        d.kv("Header build", f"GRCh{g.build}", DIM)
    if g.columns:
        d.kv("Column header", g.columns)

    d.title("TOTALS")
    d.kv("SNPs", f"{total:,}", "bold")
    d.kv("Chromosomes", f"{len(g.chroms)}  ({', '.join(g.chroms)})")
    d.kv("Malformed rows", f"{g.malformed:,}", BAD if g.malformed else "")
    for s in g.malformed_sample[:5]:
        d.line(f"  {s}", DIM)

    d.title("CALLS")
    for k, lbl in CLASS_LABEL:
        v = g.count(k)
        if v:
            d.kv(lbl, f"{v:,} ({pct(v, total, 2)})", CLASS_STYLE[k])
    auto_dip = g.count("het", True) + g.count("hom", True)
    d.kv("Autosomal heterozygosity", pct(g.count("het", True), auto_dip, 2))
    sex, note = g.sex()
    d.kv("Sex estimate", f"{sex}  ({note})", "bold")

    d.title("BY CHROMOSOME")
    top = max((c.n for c in g.chroms.values()), default=1)
    for c in g.chroms.values():
        b = g.chrom_build(c.name)
        d.t.append(f"  {c.name:>3} ", style="bold")
        d.t.append(bar(c.n / top, 16), style=SECONDARY)
        d.t.append(f" {c.n:>8,}", style="")
        d.t.append(f"  {('GRCh' + b) if b in ('37', '38') else (b or '—'):>6}",
                   style=WARN if b and b != (g.build_detected or g.build) else DIM)
        d.t.append(f"  nc {pct(c.nocall, c.n)}\n", style=DIM)

    d.title("GENOTYPE LENGTH")
    by_len: dict[tuple, list[str]] = {}
    for c in g.chroms.values():
        by_len.setdefault(tuple(sorted(c.lengths)), []).append(c.name)
    for lens, names in by_len.items():
        d.kv(" / ".join(f"{n} letter{'s' if n != 1 else ''}" for n in lens) or "empty", ", ".join(names))

    y = g.chroms.get("Y")
    if y:
        d.title("Y-DNA")
        d.kv("SNPs", f"{y.n:,}", "bold")
        d.kv("Position range", span(y))
        d.kv("Called", f"{y.called:,} ({pct(y.called, y.n)})")
        if y.classes["het"]:
            d.kv("Heterozygous on Y", f"{y.classes['het']:,} (unexpected in a male sample)", WARN)
        d.kv("IDs", f"rsID {y.rs:,} · named {y.named:,}")
        y_build = g.chrom_build("Y")
        votes = g.chrom_votes.get("Y")
        if votes and (votes[0] or votes[1]):
            d.kv("Build anchors", f"37={votes[0]:,} · 38={votes[1]:,}")
        zones = y.zones(y_build)
        if zones:
            d.kv(f"Regions (GRCh{y_build or '37'})", " · ".join(f"{z} {v:,}" for z, v in zones))
        if y.prefixes:
            d.line("Named SNP prefixes:", MUTED)
            d.t.append("    " + "  ".join(f"{p or '?'} {v:,}" for p, v in y.prefixes.most_common(24)) + "\n")
        if y.named:
            link_section(d, g, link)

    for name, label in (("X", "X CHROMOSOME"), ("XY", "PSEUDOAUTOSOMAL (XY)"), ("MT", "mtDNA")):
        c = g.chroms.get(name)
        if not c:
            continue
        d.title(label)
        d.kv("SNPs", f"{c.n:,}")
        d.kv("Position range", span(c))
        dip = c.classes["het"] + c.classes["hom"]
        if dip:
            d.kv("Heterozygous", f"{c.classes['het']:,} ({pct(c.classes['het'], dip, 2)})")
        c_build = g.chrom_build(name)
        zones = c.zones(c_build)
        if zones:
            d.kv(f"Regions (GRCh{c_build or '37'})", " · ".join(f"{z} {v:,}" for z, v in zones))

    if g.header:
        d.title("HEADER")
        for h in g.header:
            d.line(h, MUTED)
    return d.t
