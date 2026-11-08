"""Тексты F5: строка группы, карточка, карта по эпохам."""

from __future__ import annotations

from rich.text import Text

from ...core.aadr import (EPOCH_LABEL, F3, Catalog, Group, Hit, ON_BRANCH, Sample,
                         date_range, hapconx_label, place_of)
from ..base import click, fit
from ..tree.tabs.worldmap import scatter_map

GRADIENT = [27, 33, 39, 45, 49, 48, 41, 77, 113, 149, 185, 220, 214, 208, 202, 196]
LIST_W = 52
REL_MARK = {"same": "●", "ancestor": "▲", "downstream": "▼", "other": "○"}
REL_STYLE = {"same": "bold #53A94B", "ancestor": "#D4AD72",
             "downstream": "#B04A4A", "other": "#C3BCCC"}


class _Doc:
    def __init__(self) -> None:
        self.t = Text()

    def title(self, s: str) -> None:
        self.t.append(f"\n{s}\n", style="bold #D4AD72")

    def kv(self, key: str, value: object, style: str = "") -> None:
        self.t.append(f"  {key}: ", style="#AEA5B8")
        self.t.append(f"{value}\n", style=style)


def pct_style(p: float) -> str:
    return f"color({GRADIENT[min(int(p / 100 * len(GRADIENT)), len(GRADIENT) - 1)]})"


def f3_pct(r: F3 | None, ranks: dict[str, float]) -> float | None:
    if r is None or not r.ok:
        return None
    return ranks.get(r.name)


def ranks_of(results: dict[str, F3], field: str = "cluster") -> dict[str, float]:
    """Цвет шкалы по lo = est − 2·se (тот же ключ, что сортировка списка)."""
    del field
    ok = sorted((r for r in results.values() if r.ok), key=lambda r: r.est - 2 * r.se)
    n = max(1, len(ok) - 1)
    return {r.name: 100.0 * i / n for i, r in enumerate(ok)}


def epoch_bar(epoch: str, present: list[str], playing: bool,
              labels: dict[str, str] | None = None) -> Text:
    t = Text(no_wrap=True)
    keys = ["all", *present]
    lab = labels or EPOCH_LABEL
    for key in keys:
        if key == "all":
            label = "all"
        else:
            raw = lab.get(key, key)
            label = raw.split("(")[0].strip()
        style = "bold reverse #D4AD72" if key == epoch else "#AEA5B8"
        t.append(f" {label} ", style=click(f"screen.epoch({key!r})", style))
        t.append(" ")
    t.append("◀▶", style=click("screen.epoch_step(1)", "#8A8294"))
    if playing:
        t.append("  ■", style=click("screen.toggle_play", "bold #8BAB4A"))
    else:
        t.append("  ▶", style=click("screen.toggle_play", "bold #53A94B"))
    return t


def source_bar(source: str) -> Text:
    t = Text(no_wrap=True)
    for key, lab in (("aadr", "AADR"), ("kg", "1000G")):
        style = "bold reverse #D4AD72" if source == key else "#AEA5B8"
        t.append(f" {lab} ", style=click(f"screen.source({key!r})", style))
        t.append(" ")
    return t


def region_bar(region: str, present: list[str]) -> Text:
    t = Text(no_wrap=True)
    for key in ["all", *present]:
        label = key if key != "all" else "all regions"
        style = "bold reverse #D4AD72" if key == region else "#AEA5B8"
        t.append(f" {label} ", style=click(f"screen.region({key!r})", style))
        t.append(" ")
    return t


def tab_bar(tab: str) -> Text:
    t = Text(no_wrap=True)
    for key, lab in (("map", "MAP"), ("y", "Y"), ("tree", "TREE"), ("layers", "LAYERS")):
        style = "bold reverse #D4AD72" if tab == key else "#AEA5B8"
        t.append(f" {lab} ", style=click(f"screen.tab({key!r})", style))
        t.append(" ")
    t.append("V", style="#8A8294")
    return t


def _snp(n: int) -> str:
    return f"{n / 1000:.0f}k" if n >= 10_000 else f"{n:,}"


CARD_W = 52
ACCENT = "bold #D4AD72"
MUTED = "#8A8294"
BODY = "#C3BCCC"
Y_RANK = {"same": 0, "ancestor": 1, "downstream": 2, "other": 3}


def _filled(s: str | None) -> str:
    t = (s or "").strip().strip('"')
    low = t.lower()
    if not t or low in (".", "..", "n/a", "na") or low.startswith("n/a"):
        return ""
    return t


def _uniq(xs: list[str], n: int = 8) -> str:
    out: list[str] = []
    for x in xs:
        x = _filled(x)
        if x and x not in out:
            out.append(x)
    if not out:
        return ""
    extra = len(out) - n
    body = ", ".join(out[:n])
    return f"{body} +{extra}" if extra > 0 else body


def _minmax(vals: list[float], fmt) -> str:
    if not vals:
        return ""
    a, b = min(vals), max(vals)
    return fmt(a) if abs(a - b) < 1e-9 else f"{fmt(a)}–{fmt(b)}"


def _doi(s: str) -> str:
    s = _filled(s)
    for p in ("https://doi.org/", "http://doi.org/"):
        if s.lower().startswith(p):
            return s[len(p):]
    return s


def _y_mark(hit: Hit | None) -> tuple[str, str]:
    if hit and hit.relation in REL_MARK:
        return REL_MARK[hit.relation], REL_STYLE[hit.relation]
    return "·", "#71697A"


def _y_rank(hit: Hit | None) -> int:
    if not hit:
        return 4
    return Y_RANK.get(hit.relation, 4)


def sort_tuple(g: Group, r: F3 | None, hit: Hit | None) -> tuple:
    """Ключи списка: ok · lo · Z · SNP · Y · name."""
    yr = _y_rank(hit)
    if r and r.ok:
        return (0, -(r.est - 2 * r.se), -r.z, -r.snps, yr, g.name)
    if r and r.snps:
        return (1, -r.snps, yr, g.name)
    return (2, yr, -g.n, g.name)


def decide_key(prev: tuple | None, cur: tuple, r: F3 | None) -> str:
    """Какой ключ решил место относительно предыдущей строки."""
    if not (r and r.ok):
        return "too few" if (r and r.snps) else "not scored"
    if prev is None or prev[0] != 0:
        return "lo"
    # cur/prev: (0, -lo, -Z, -SNP, yr, name)
    if cur[1] != prev[1]:
        return "lo"
    if cur[2] != prev[2]:
        return "Z"
    if cur[3] != prev[3]:
        return "SNP"
    if cur[4] != prev[4]:
        return "Y"
    return "name"


def _bar(p: float | None) -> tuple[str, str]:
    if p is None:
        return "", MUTED
    style = pct_style(p)
    pos = min(5, max(0, int(p / 100 * 5 + 0.5)))
    return "█" * pos + "░" * (5 - pos), style


def group_prompt(g: Group, r: F3 | None, hit: Hit | None, rank: float | None,
                 *, place: int | None = None, decide: str = "lo",
                 outside_head: bool = False) -> Text:
    t = Text(no_wrap=True)
    if outside_head:
        t.append("── outside ranking ──\n", style="bold #8BAB4A")
    mark, msty = _y_mark(hit)
    if r and r.ok:
        lo = r.est - 2 * r.se
        bar, bsty = _bar(rank)
        num = f"{place}" if place is not None else "·"
        name = fit(g.name, 22)
        t.append(f"{num:>2} ", style=MUTED)
        t.append(f"{mark} ", style=msty)
        t.append(f"{name:<22} ", style="bold")
        if decide == "lo":
            t.append(f"⟦lo {lo:.4f}⟧ ", style=ACCENT)
        else:
            t.append(f"lo {lo:.4f} ", style=MUTED)
        if decide == "Z":
            t.append(f"⟦Z {r.z:.1f}⟧ ", style=ACCENT)
        if decide == "SNP":
            t.append(f"⟦{_snp(r.snps)} SNP⟧ ", style=ACCENT)
        if decide == "Y":
            t.append(f"⟦{mark}⟧ ", style=ACCENT)
        if decide == "name":
            t.append("⟦name⟧ ", style=ACCENT)
        t.append(bar + "\n", style=bsty)
        bits = [f"{r.est:.4f}±{r.se:.4f}"]
        if decide != "Z":
            bits.append(f"Z {r.z:.1f}")
        if decide != "SNP":
            bits.append(f"{_snp(r.snps)} SNP")
        bits.append(f"n={g.n}")
        t.append("   " + " · ".join(bits), style=MUTED)
    elif r and r.snps:
        t.append("— ", style=MUTED)
        t.append(f"{mark} ", style=msty)
        t.append(fit(g.name, 28) + " ", style="#AEA5B8")
        t.append("⟦too few⟧\n", style="bold #8BAB4A")
        t.append(f"   {r.snps:,} SNP", style=MUTED)
    else:
        t.append("— ", style=MUTED)
        t.append(f"{mark} ", style=msty)
        t.append(fit(g.name, 28) + " ", style="#AEA5B8")
        t.append("⟦not scored⟧\n", style="bold #8BAB4A")
        t.append("   no f3 estimate", style=MUTED)
    return t


def _sec(d: Text, title: str, *, major: bool = True) -> None:
    fill = "═" if major else "─"
    sty = "bold #D4AD72" if major else MUTED
    head = f"{fill * 2} {title} "
    d.append(head + fill * max(0, CARD_W - len(head)) + "\n", style=sty)


def _row(d: Text, key: str, value: str, *, accent: bool = False,
         style: str = BODY) -> None:
    if not value:
        return
    d.append(f" {key:<8} ", style=MUTED)
    d.append(fit(value, CARD_W - 10) + "\n", style=ACCENT if accent else style)


def _anno_get(s: Sample, key: str) -> str:
    return _filled((s.anno or {}).get(key) or "")


def _qpadm_bits(name: str, qpadm: list | None) -> str:
    best = None
    for row in qpadm or []:
        if not row.get("feasible"):
            continue
        for w in row.get("weights") or []:
            if w.get("pop") != name:
                continue
            p = row.get("p") or 0
            if best is None or p > best[0]:
                best = (p, w.get("w") or 0, w.get("se") or 0)
    if not best:
        return ""
    line = f"{best[1]:.0%} ± {best[2]:.0%}"
    if best[0]:
        line += f"   p={best[0]:.3g}"
    return line


def _paint_bits(name: str, paint: dict | None) -> str:
    pops = (paint or {}).get("pops") or {}
    bits = []
    if name in pops:
        bits.append(f"{pops[name]:.0%}")
    near = next((row for row in (paint or {}).get("nearest") or []
                 if row.get("iid") == name), None)
    if near:
        bits.append(f"nearest {near.get('pop', '')}")
    return " · ".join(bits)


def _our_block(d: Text, r: F3 | None, hit: Hit | None, tip: str | None,
               name: str, n: int, qpadm: list | None, paint: dict | None,
               *, place: int | None, ok_total: int, decide: str,
               rank: float | None) -> None:
    _sec(d, "OUR")
    if r and r.ok:
        lo = r.est - 2 * r.se
        where = f"#{place} of {ok_total} · decided by {decide}" if place else f"decided by {decide}"
        _row(d, "rank", where)
        bar, _ = _bar(rank)
        _row(d, "lo", f"{lo:.4f} = {r.est:.4f} − 2×{r.se:.4f}   {bar}".rstrip(),
             accent=(decide == "lo"))
        _row(d, "f3", f"{r.est:.4f} ± {r.se:.4f}")
        zline = f"{r.z:.1f}"
        if r.f4_z:
            zline = f"{r.z:.1f}            f4    {r.f4_z:.1f}"
        _row(d, "Z", zline, accent=(decide == "Z"))
        _row(d, "SNP", f"{_snp(r.snps)}            n     {n}",
             accent=(decide == "SNP"))
    elif r and r.snps:
        _row(d, "rank", "outside ranking · too few", accent=True)
        _row(d, "SNP", f"{r.snps:,}", style=MUTED)
    else:
        _row(d, "rank", "outside ranking · not scored", accent=True)
    if hit:
        mark = REL_MARK.get(hit.relation, "·")
        yv = f"{mark} {hit.node}"
        if hit.relation == "same":
            yv += f" · same as {tip or hit.y}"
        elif hit.relation in ON_BRANCH:
            yv += f" · {hit.relation} of {tip or hit.y}"
        elif tip:
            yv += f" · not your {tip}"
        _row(d, "Y", yv, accent=(decide == "Y"),
             style=REL_STYLE.get(hit.relation, BODY))
    qp = _qpadm_bits(name, qpadm)
    if qp:
        _row(d, "qpAdm", qp)
    pt = _paint_bits(name, paint)
    if pt:
        _row(d, "paint", pt)


def _card_group(d: Text, g: Group, keep: list[Sample],
                r: F3 | None, hit: Hit | None, tip: str | None,
                qpadm: list | None, paint: dict | None,
                place: int | None, ok_total: int, decide: str,
                rank: float | None) -> None:
    n = g.n if g.n else len(keep)
    _our_block(d, r, hit, tip, g.name, n, qpadm, paint,
               place=place, ok_total=ok_total, decide=decide, rank=rank)
    d.append("\n")
    place_s = place_of(g.locality, g.country)
    span = date_range(keep, g.bp)
    ep = EPOCH_LABEL.get(g.epoch, g.epoch)
    if place_s or g.lat is not None or (span and span != "?") or ep:
        _sec(d, "WHERE · WHEN")
        _row(d, "place", place_s)
        if g.lat is not None and g.lon is not None:
            _row(d, "coord", f"{g.lat:.2f} N  {g.lon:.2f} E")
        if span and span != "?":
            _row(d, "date", span)
        if ep:
            _row(d, "era", ep)
        d.append("\n")
    ys = _uniq([_anno_get(s, "y_term") or s.y for s in keep] or g.y)
    isos = _uniq([_anno_get(s, "y_isogg") or s.y_isogg for s in keep])
    mts = _uniq([_anno_get(s, "mt") or s.mt for s in keep] or g.mt)
    if ys or mts:
        _sec(d, "GENETICS")
        if ys:
            yline = ys
            if isos and isos != ys:
                yline += f" / {isos}"
            _row(d, "Y", yline)
        if mts:
            _row(d, "mt", mts)
        d.append("\n")
    if keep:
        _sec(d, f"PEOPLE · {len(keep)}")
        for s in keep[:8]:
            y = _anno_get(s, "y_term") or s.y or "—"
            mt = _anno_get(s, "mt") or s.mt or "—"
            cov = f"{s.coverage:.2f}×" if s.coverage else ""
            assess = _anno_get(s, "assess")
            bits = [fit(s.gid, 12), s.sex or "", y, mt, cov, assess]
            d.append(" " + fit("  ".join(x for x in bits if x), CARD_W - 1) + "\n",
                     style=BODY)
        if len(keep) > 8:
            _row(d, "…", f"{len(keep) - 8} more", style=MUTED)
        d.append("\n")
    snps = [float(s.snps) for s in keep if s.snps]
    covs = [s.coverage for s in keep if s.coverage]
    types = _uniq([_anno_get(s, "data_type") for s in keep], 3)
    libs = _uniq([_anno_get(s, "lib_type") for s in keep], 3)
    suf = _uniq([_anno_get(s, "suffix") for s in keep], 3)
    counts: dict[str, int] = {}
    for s in keep:
        k = _anno_get(s, "assess") or ("Questionable" if s.questionable else "Pass" if s.keep else "")
        if k:
            counts[k] = counts.get(k, 0) + 1
    if snps or covs or types or counts:
        _sec(d, "DATA", major=False)
        if snps or covs:
            bits = []
            if snps:
                bits.append(_minmax(snps, lambda v: f"{v / 1000:.0f}k"))
            if covs:
                bits.append(_minmax(covs, lambda v: f"{v:.2f}×"))
            _row(d, "1240k", " · ".join(bits), style=MUTED)
        tech = " · ".join(x for x in (suf, types, libs) if x)
        if tech:
            _row(d, "type", tech, style=MUTED)
        if counts:
            _row(d, "Pass", "  ".join(f"{k} {n}" for k, n in counts.items()), style=MUTED)
        d.append("\n")
    pubs = _uniq([_anno_get(s, "pub") or s.pub for s in keep], 4)
    dois = _uniq([_doi(_anno_get(s, "doi")) for s in keep], 2)
    repos = _uniq([_anno_get(s, "repo") for s in keep], 2)
    if pubs or dois or repos:
        _sec(d, "SOURCE", major=False)
        if pubs:
            _row(d, "pub", pubs, style=MUTED)
        if dois:
            _row(d, "doi", dois, style=MUTED)
        if repos:
            _row(d, "repo", repos, style=MUTED)


def _card_person(d: Text, s: Sample, r: F3 | None, hit: Hit | None,
                 tip: str | None, qpadm: list | None, paint: dict | None,
                 place: int | None, ok_total: int, decide: str,
                 rank: float | None) -> None:
    _our_block(d, r, hit, tip, s.gid, 1, qpadm, paint,
               place=place, ok_total=ok_total, decide=decide, rank=rank)
    d.append("\n")
    _sec(d, "WHO")
    grp = _anno_get(s, "group") or s.group
    _row(d, "group", grp)
    who = " · ".join(x for x in (_anno_get(s, "sex") or s.sex,
                                 _anno_get(s, "skel"),
                                 _anno_get(s, "skel_code")) if x)
    _row(d, "sex", who)
    _row(d, "age", _anno_get(s, "age"))
    y_bits = []
    term = _anno_get(s, "y_term") or s.y
    iso = _anno_get(s, "y_isogg") or s.y_isogg
    man = _anno_get(s, "y_manual")
    if term:
        y_bits.append(term)
    if iso and iso != term:
        y_bits.append(f"ISOGG {iso}")
    if man and man not in (term, iso):
        y_bits.append(f"manual {man}")
    if y_bits:
        _row(d, "Y", " · ".join(y_bits))
    mt = _anno_get(s, "mt") or s.mt
    if mt:
        mt_line = mt
        if _anno_get(s, "mt_cov"):
            mt_line += f"  {_anno_get(s, 'mt_cov')}×"
        if _anno_get(s, "mt_match"):
            mt_line += f"  match {_anno_get(s, 'mt_match')}"
        _row(d, "mt", mt_line)
    d.append("\n")
    place_s = place_of(s.locality, s.country)
    span = _filled(s.date_span) or _anno_get(s, "date_full")
    method = _anno_get(s, "date_method")
    date_bp = _anno_get(s, "date")
    date_sd = _anno_get(s, "date_sd")
    if place_s or span or method or date_bp:
        _sec(d, "WHERE · WHEN")
        _row(d, "place", place_s)
        if s.lat is not None and s.lon is not None:
            _row(d, "coord", f"{s.lat} N  {s.lon} E")
        if span:
            _row(d, "date", span)
        if date_bp:
            _row(d, "direct",
                 f"{method + ' · ' if method else ''}{date_bp}"
                 + (f" ± {date_sd} BP" if date_sd else " BP"))
        elif method:
            _row(d, "method", method)
        d.append("\n")
    _sec(d, "DATA", major=False)
    if s.snps or s.coverage:
        bits = []
        if s.snps:
            bits.append(f"{s.snps:,}")
        if s.coverage:
            bits.append(f"{s.coverage:.2f}×")
        _row(d, "1240k", " · ".join(bits), style=MUTED)
    extra = []
    for key, lab in (("snps_ho", "HO"), ("snps_2m", "2M")):
        v = _anno_get(s, key)
        if v:
            try:
                extra.append(f"{lab} {float(v.replace(',', '')):,.0f}")
            except ValueError:
                extra.append(f"{lab} {v}")
    if extra:
        _row(d, "extra", " · ".join(extra), style=MUTED)
    off = _anno_get(s, "cov_off")
    if off:
        try:
            _row(d, "off-tgt", f"{float(off):.2f}×", style=MUTED)
        except ValueError:
            _row(d, "off-tgt", off, style=MUTED)
    tech = " · ".join(x for x in (_anno_get(s, "data_type"), _anno_get(s, "suffix"),
                                  _anno_get(s, "lib_type")) if x)
    if tech:
        _row(d, "libs", tech, style=MUTED)
    pull = _anno_get(s, "pulldown")
    if pull:
        _row(d, "pulldown", pull, style=MUTED)
    d.append("\n")
    hx = hapconx_label(s.hapconx) or _anno_get(s, "hapconx")
    assess = _anno_get(s, "assess")
    qc_keys = (
        ("roh", "ROH"), ("damage", "damage"), ("sex_ratio", "sex ratio"),
        ("angsd", "ANGSD"), ("family", "family"), ("libraries", "libraries"),
        ("endogenous", "endogenous"), ("assess_warn", "warn"),
    )
    has_qc = hx or assess or any(_anno_get(s, k) for k, _ in qc_keys)
    if has_qc:
        _sec(d, "QC", major=False)
        if hx or assess:
            _row(d, "hapConX", "  ".join(x for x in (hx, assess) if x), style=MUTED)
        for key, lab in qc_keys:
            v = _anno_get(s, key)
            if v:
                _row(d, lab, v, style=MUTED)
        d.append("\n")
    pub = _anno_get(s, "pub") or s.pub
    first = _anno_get(s, "pub_first")
    doi = _doi(_anno_get(s, "doi"))
    repo = _anno_get(s, "repo")
    ids = " · ".join(x for x in (
        _anno_get(s, "pid") and f"pid {_anno_get(s, 'pid')}",
        _anno_get(s, "iid") and f"iid {_anno_get(s, 'iid')}",
    ) if x)
    if pub or first or doi or repo or ids:
        _sec(d, "SOURCE", major=False)
        if pub:
            _row(d, "pub", pub, style=MUTED)
        if first and first != pub:
            _row(d, "first", first, style=MUTED)
        if doi:
            _row(d, "doi", doi, style=MUTED)
        if repo:
            _row(d, "repo", repo, style=MUTED)
        if ids:
            _row(d, "ids", ids, style=MUTED)


def card(g: Group | None, *, sample: Sample | None = None,
         cat: Catalog | None = None, hit: Hit | None = None,
         branch: str | None = None, questionable: bool = False,
         qpadm: list | None = None, paint: dict | None = None,
         result: F3 | None = None, place: int | None = None,
         ok_total: int = 0, decide: str = "lo",
         rank: float | None = None) -> Text:
    d = Text()
    if g is None:
        d.append(" No selection.\n", style="#AEA5B8")
        return d
    keep: list[Sample] = []
    if cat:
        keep = [s for s in (cat.samples[i] for i in g.samples)
                if s.keep or (questionable and s.questionable)]
    if sample is not None:
        _card_person(d, sample, result, hit, branch, qpadm, paint,
                     place, ok_total, decide, rank)
    else:
        _card_group(d, g, keep, result, hit, branch, qpadm, paint,
                    place, ok_total, decide, rank)
    return d


def _rank_color(p: float) -> str:
    return f"color({GRADIENT[min(int(p / 100 * len(GRADIENT)), len(GRADIENT) - 1)]})"


def draw_map(groups: list[Group], results: dict[str, F3], ranks: dict[str, float],
             selected: str | None, title: str,
             world: bool, cols: int, rows: int) -> Text:
    doc = _Doc()
    doc.t = Text(no_wrap=True, overflow="crop")
    points = []
    for g in groups:
        if g.lat is None or g.lon is None or (g.lat == 0 and g.lon == 0):
            continue
        r = results.get(g.name)
        p = f3_pct(r, ranks)
        if p is not None:
            color, glyph = _rank_color(p), None
        elif r and r.snps:
            color, glyph = "#555555", None
        elif g.name == selected:
            color, glyph = "#5A7CB3", None
        else:
            continue
        points.append({"id": g.name, "lon": g.lon, "lat": g.lat, "color": color,
                       "glyph": glyph, "label": g.name.split("_")[0]})
    scatter_map(doc, points, title, world=world, cols=cols, max_rows=rows, selected=selected)
    return doc.t


def draw_y_map(groups: list[Group], results: dict[str, F3], ranks: dict[str, float],
               hits: dict[str, Hit], ours_other: set[str], selected: str | None,
               title: str, world: bool, cols: int, rows: int) -> Text:
    doc = _Doc()
    doc.t = Text(no_wrap=True, overflow="crop")
    points = []
    for g in groups:
        if g.lat is None or g.lon is None or (g.lat == 0 and g.lon == 0):
            continue
        h = hits.get(g.name)
        if not h:
            continue
        r = results.get(g.name)
        p = f3_pct(r, ranks)
        if h.relation in ON_BRANCH:
            color, glyph = "#53A94B", REL_MARK[h.relation]
        elif g.name in ours_other and p is not None:
            color, glyph = _rank_color(p), "○"
        else:
            continue
        points.append({"id": g.name, "lon": g.lon, "lat": g.lat, "color": color,
                       "glyph": glyph, "label": g.name.split("_")[0]})
    scatter_map(doc, points, title, world=world, cols=cols, max_rows=rows, selected=selected)
    return doc.t


REGIONS = ("Americas", "Africa", "Europe", "W.Asia", "E.Asia", "Oceania")


def region_of(lat: float | None, lon: float | None) -> str | None:
    if lat is None or lon is None:
        return None
    if lon < -30:
        return "Americas"
    if lon < 40:
        return "Africa" if lat < 37 else "Europe"
    if lon < 90:
        return "W.Asia"
    if lon < 150:
        return "E.Asia"
    return "Oceania"


def tree_view(groups: list[Group], results: dict[str, F3], ranks: dict[str, float],
              hits: dict[str, Hit], qpadm: list | None, epochs: list[str],
              epoch_labels: dict[str, str], selected: str | None,
              paint: dict | None = None) -> Text:
    d = _Doc()
    d.t.append("Time ↓  ·  region branches  ·  qpAdm mergers  ·  Y branch / other Y\n",
               style="#AEA5B8")
    by: dict[tuple[str, str], list[Group]] = {}
    y_by_ep: dict[str, list[Group]] = {}
    y_off: dict[str, list[Group]] = {}
    for g in groups:
        h = hits.get(g.name)
        if h and h.relation in ON_BRANCH:
            y_by_ep.setdefault(g.epoch, []).append(g)
        elif h and h.relation == "other":
            y_off.setdefault(g.epoch, []).append(g)
        reg = region_of(g.lat, g.lon)
        if not reg:
            continue
        by.setdefault((g.epoch, reg), []).append(g)
    q_by_ep: dict[str, dict] = {}
    for row in qpadm or []:
        if row.get("feasible") and row.get("epoch") and row["epoch"] not in q_by_ep:
            q_by_ep[row["epoch"]] = row
    n_ep = len(epochs)
    for ei, ep in enumerate(epochs):
        last_ep = ei == n_ep - 1
        d.t.append(f"\n{'└' if last_ep else '├'}─ ", style="#8A8294")
        d.t.append(f"{epoch_labels.get(ep, ep)}\n", style="bold #D4AD72")
        regs = [r for r in REGIONS if by.get((ep, r))]
        for ri, reg in enumerate(regs):
            last_r = (ri == len(regs) - 1 and ep not in y_by_ep and ep not in y_off
                      and ep not in q_by_ep)
            rows = sorted(by[(ep, reg)], key=lambda g: -(results[g.name].est
                         if results.get(g.name) and results[g.name].ok else -1))
            branch = "    └─ " if last_r else "    ├─ "
            d.t.append(branch, style="#8A8294")
            d.t.append(f"{reg:<10} ", style="#AEA5B8")
            for i, g in enumerate(rows[:2]):
                r = results.get(g.name)
                star = "★" if r and r.ok and r.epoch_cluster == 0 else ""
                f3s = f"{r.est:.3f}" if r and r.ok else ""
                sty = "bold #FBF6F0" if g.name == selected else (
                    "#B04A4A" if star else "")
                if i:
                    d.t.append(" · ", style="#71697A")
                d.t.append(f"{star}{g.name[:20]} {f3s}".strip(),
                           style=click(f"screen.select({g.name!r})", sty))
            d.t.append("\n")
        if ep in y_by_ep:
            d.t.append("    ├─ ", style="#8A8294")
            d.t.append("Y branch  ", style="bold #53A94B")
            for i, g in enumerate(y_by_ep[ep][:3]):
                h = hits[g.name]
                mark = REL_MARK.get(h.relation, "●")
                if i:
                    d.t.append("  ", style="")
                d.t.append(f"{mark} {g.name[:18]}",
                           style=click(f"screen.select({g.name!r})",
                                       REL_STYLE.get(h.relation, "")))
            d.t.append("\n")
        if ep in y_off:
            d.t.append("    ├─ ", style="#8A8294")
            d.t.append("other Y   ", style="#C3BCCC")
            for i, g in enumerate(y_off[ep][:3]):
                h = hits[g.name]
                if i:
                    d.t.append("  ", style="")
                d.t.append(f"○ {g.name[:18]}",
                           style=click(f"screen.select({g.name!r})", "#C3BCCC"))
            d.t.append("\n")
        if ep in q_by_ep:
            row = q_by_ep[ep]
            left = " + ".join(row.get("left") or [])
            wts = " ".join(f"{w.get('pop')} {w.get('w', 0):.0%}"
                           for w in row.get("weights") or [])
            p = row.get("p")
            d.t.append("    └─ merge    ", style="#8A8294")
            d.t.append(left, style="bold")
            if wts:
                d.t.append(f"  {wts}", style="#C3BCCC")
            if p is not None:
                d.t.append(f"  p={p:.3g}",
                           style="#53A94B" if row.get("feasible") else "#B04A4A")
            d.t.append("\n")
    best = None
    for row in qpadm or []:
        if row.get("feasible"):
            best = row
            break
    if best is None and qpadm:
        best = qpadm[0]
    d.t.append("\nYOU", style="bold #53A94B")
    if best:
        left = " + ".join(best.get("left") or [])
        d.t.append("  ←  ", style="#8A8294")
        d.t.append(left, style="bold")
        p = best.get("p")
        if p is not None:
            d.t.append(f"   p={p:.3g}",
                       style="#53A94B" if best.get("feasible") else "#B04A4A")
        d.t.append("\n")
    else:
        d.t.append("  · no qpAdm yet  ", style="#AEA5B8")
        d.t.append("[LAYERS]", style=click("screen.tab('layers')", "bold #D4AD72"))
        d.t.append("\n")
    if paint and paint.get("pops"):
        d.t.append("\nChromoPainter  ", style="bold #D4AD72")
        bits = [f"{k} {v:.0%}" for k, v in list(paint["pops"].items())[:5]]
        d.t.append("  ".join(bits) + "\n", style="#C3BCCC")
        for row in (paint.get("nearest") or [])[:4]:
            d.t.append(f"  {row.get('iid')}  {row.get('pop')}  {row.get('chunks', 0):.1f}\n",
                       style="#AEA5B8")
    return d.t


def layers_view(rows: list[dict], chip: dict[str, F3], imputed: dict[str, F3]) -> Text:
    d = _Doc()
    d.t.append("Layers  ·  click a missing step to run it\n", style="#AEA5B8")
    for row in rows:
        mark = {"done": "●", "ready": "●", "running": "⚙", "missing": "○",
                "n/a": "·"}.get(row["status"], "○")
        color = {"done": "bold #53A94B", "ready": "#D4AD72",
                 "running": "#B04A4A", "missing": "#8A8294", "n/a": "#71697A"}.get(
            row["status"], "#8A8294")
        d.t.append(f"\n  {mark}  {row['id']:>3}  ", style=color)
        title_style = click(row["action"], "bold") if row.get("action") else "bold"
        d.t.append(row["title"], style=title_style)
        d.t.append(f"\n       {row.get('detail') or ''}\n", style="#AEA5B8")
        if row.get("action"):
            d.t.append("       ", style="")
            d.t.append("run", style=click(row["action"], "bold reverse #D4AD72"))
            d.t.append("\n")
    names = sorted({*chip, *imputed},
                   key=lambda n: -(chip[n].est if n in chip and chip[n].ok else -1))
    if names and imputed:
        d.title("CHIP vs IMPUTED  ·  outgroup-f3")
        d.t.append(f"  {'group':<24} {'chip':>12} {'imputed':>12} {'Δ':>9}\n", style="#8A8294")
        for name in names[:24]:
            a, b = chip.get(name), imputed.get(name)
            d.t.append(f"  {name:<24.24} ", style="#C3BCCC")
            d.t.append(f"{a.est:12.5f}" if a and a.ok else f"{'—':>12}", style="#C3BCCC")
            d.t.append(" ")
            d.t.append(f"{b.est:12.5f}" if b and b.ok else f"{'—':>12}", style="#C3BCCC")
            if a and b and a.ok and b.ok:
                d.t.append(f"  {a.est - b.est:+.5f}", style="#8A8294")
            d.t.append("\n")
    return d.t
