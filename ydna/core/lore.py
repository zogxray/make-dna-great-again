"""Сборка LORE-якорей: genome + AADR/ancient + Seshat + children geo → brief."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .lore_codex import (
    CodexEntry,
    date_bp_from_ce,
    extract_culture,
    pick_codex_from_cultures,
)
from .seshat import Polity, SeshatIndex, load_index


@dataclass
class GeoBar:
    code: str
    count: int
    bar: str


@dataclass
class LoreBrief:
    node: str
    genome: str
    aadr_lines: list[str]
    cultures: list[str]
    culture_top: str | None
    seshat_lines: list[str]
    seshat_hits: list[tuple[Polity, int]]
    children_geo: list[GeoBar]
    children_note: str
    parent_line: str
    codex: CodexEntry | None
    epoch: str
    sigil: str
    formed: str
    tmrca: str
    raw: dict[str, Any] = field(default_factory=dict)

    def weave_facts(self, limit_chars: int = 2800) -> str:
        """Компактный пакет фактов для Ask GPT (без UI-украшений)."""
        lines = [
            f"Clade: {self.node}",
            f"Genome: {self.genome}",
            f"Formed: {self.formed} · TMRCA: {self.tmrca}",
            f"Parent: {self.parent_line}",
            f"Culture lens: {self.culture_top or '—'}",
            f"Epoch: {self.epoch}",
        ]
        if self.aadr_lines:
            lines.append("Ancient anchors:")
            lines.extend(f"  - {x}" for x in self.aadr_lines[:12])
        if self.seshat_lines:
            lines.append("Seshat:")
            lines.extend(f"  - {x}" for x in self.seshat_lines[:8])
        if self.children_geo:
            lines.append("Children modern geography (FTDNA self-report):")
            lines.append("  " + " · ".join(
                f"{g.code}×{g.count}" for g in self.children_geo[:10]))
            lines.append(f"  note: {self.children_note}")
        if self.codex:
            c = self.codex
            lines.append(f"Codex: {c.name} · {c.period} · {c.region}")
            lines.append("Hard archaeology/genetics:")
            for h in c.hard:
                lines.append(f"  - {h}")
            lines.append("Pantheon (reconstructed / comparative):")
            for g in c.gods:
                lines.append(f"  - {g.name} — {g.domain} ({g.epithet})")
            lines.append(f"Ritual: {c.ritual.title} · {c.ritual.scene}")
            lines.append(f"Book hook: {c.book}")
            lines.append(f"Disclaimer: {c.note}")
        text = "\n".join(lines)
        if len(text) > limit_chars:
            text = text[: limit_chars - 20].rstrip() + "\n…[truncated]"
        return text


def _year_label(value: Any) -> str:
    if value is None:
        return "—"
    try:
        year = int(round(float(value)))
    except (TypeError, ValueError):
        return "—"
    if year < 0:
        return f"{abs(year):,} BCE"
    if year == 0:
        return "c. 1 BCE"
    return f"{year:,} CE"


def _mean_block(block: Any) -> Any:
    if isinstance(block, dict):
        return block.get("mean")
    return block


def _bar(n: int, peak: int, width: int = 8) -> str:
    if peak <= 0 or n <= 0:
        return "·" * max(1, width // 4)
    filled = max(1, int(round(n / peak * width)))
    return "█" * filled


def _geo_label(item: dict) -> str:
    """Короткий ярлык страны из geochart / countries_data."""
    for key in ("code", "id", "countryCode"):
        v = item.get(key)
        if v:
            return str(v).split("-")[0].upper()
    name = str(item.get("name") or item.get("country") or "").strip()
    if not name:
        return ""
    # компактно: United Kingdom → UK-ish short, иначе первые буквы / обрезок
    short = {
        "united kingdom": "UK", "united states": "US", "czech republic": "CZ",
        "russian federation": "RU", "south korea": "KR", "north macedonia": "MK",
        "bosnia and herzegovina": "BA", "faroe islands": "FO",
    }.get(name.lower())
    if short:
        return short
    if len(name) <= 12:
        return name
    return name[:11] + "…"


def _geo_from_node(node: dict) -> Counter:
    counts: Counter = Counter()
    gc = node.get("geochart_countries")
    if isinstance(gc, list):
        for item in gc:
            if isinstance(item, dict):
                label = _geo_label(item)
                try:
                    n = int(item.get("count") or item.get("value") or item.get("n") or 0)
                except (TypeError, ValueError):
                    n = 0
                if label and n:
                    counts[label] += n
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                counts[str(item[0])] += int(item[1] or 0)
    if not counts:
        for item in node.get("countries_data") or []:
            if not isinstance(item, dict):
                continue
            label = _geo_label(item)
            try:
                n = int(item.get("count") or item.get("value") or 0)
            except (TypeError, ValueError):
                n = 0
            if label and n:
                counts[label] += n
    return counts


def children_geography(ctx, node: dict, *, depth: int = 1,
                       top: int = 8) -> tuple[list[GeoBar], str]:
    """Агрегат geochart по детям (и при пустоте — сам узел)."""
    name = str(node.get("name") or "")
    kids = list(getattr(ctx, "children", {}).get(name) or [])
    total: Counter = Counter()
    used = 0
    missing = 0
    for kid in kids:
        kn = ctx.nodes.get(kid) if hasattr(ctx, "nodes") else None
        if not isinstance(kn, dict):
            missing += 1
            continue
        g = _geo_from_node(kn)
        if g:
            total.update(g)
            used += 1
        else:
            missing += 1
        if depth > 1:
            for grand in list(getattr(ctx, "children", {}).get(kid) or [])[:12]:
                gn = ctx.nodes.get(grand)
                if isinstance(gn, dict):
                    gg = _geo_from_node(gn)
                    if gg:
                        total.update(gg)
    source = "children Discover cards"
    if not total:
        total = _geo_from_node(node)
        source = "this clade card (children geo empty / not loaded)"
    if not total:
        return [], "no geochart on node or children (fetch Discover / open rich kids)"
    peak = max(total.values())
    bars = [
        GeoBar(code=c, count=n, bar=_bar(n, peak))
        for c, n in total.most_common(top)
    ]
    note = (f"FTDNA self-report · {source} · "
            f"{used} child card(s) with geo · {missing} without")
    return bars, note


def _age_num(s: dict) -> float | None:
    age = s.get("age_estimate_mean")
    try:
        return float(age) if age is not None else None
    except (TypeError, ValueError):
        return None


def _ancient_cultures(node: dict, tmrca_year: int | None = None
                      ) -> tuple[list[str], list[str], list[str]]:
    """→ (display lines, culture labels for CODEX, geo/culture hints for Seshat)."""
    lines: list[str] = []
    cultures: list[str] = []
    hints: list[str] = []
    samples = [s for s in (node.get("ancient") or []) if isinstance(s, dict)]

    def _key(s: dict):
        unc = 1 if s.get("uncertain_placement") else 0
        age_n = _age_num(s)
        # ближе к TMRCA — выше; без возраста — в конец
        if tmrca_year is not None and age_n is not None:
            dist = abs(age_n - tmrca_year)
        else:
            dist = 0 if age_n is not None else 10**9
        # выкидываем палеолит/несоизмеримое в хвост
        absurd = 0
        if age_n is not None and tmrca_year is not None and abs(age_n - tmrca_year) > 8000:
            absurd = 1
        if age_n is not None and age_n < -20000:
            absurd = 1
        return (absurd, unc, dist)

    samples = sorted(samples, key=_key)
    for s in samples[:16]:
        culture = s.get("culture") or s.get("culture_key") or ""
        place = " · ".join(
            x for x in (s.get("site"), s.get("geography"), s.get("country_name")) if x)
        age_n = _age_num(s)
        yhg = s.get("haplogroup_y") or ""
        label = extract_culture(str(culture) if culture else None,
                                date_bp_from_ce(age_n))
        absurd = (
            age_n is not None and tmrca_year is not None
            and abs(age_n - tmrca_year) > 8000
        ) or (age_n is not None and age_n < -20000)
        if culture and not absurd and not s.get("uncertain_placement"):
            cultures.append(str(culture))
        # Seshat hints: только культура + страна (не time_period — слишком общие слова)
        if not absurd:
            if culture:
                hints.append(str(culture))
            if s.get("country_name"):
                hints.append(str(s["country_name"]))
        bit = f"{s.get('name') or s.get('code') or '?'} · {label}"
        if age_n is not None:
            bit += f" · {_year_label(age_n)}"
        if place:
            bit += f" · {place}"
        if yhg:
            bit += f" · Y {yhg}"
        if s.get("uncertain_placement"):
            bit += " · ?"
        if absurd:
            bit += " · outlier"
        lines.append(bit)
    return lines, cultures, hints


def _genome_line(ctx, node: dict) -> str:
    tip = getattr(ctx, "tip", None)
    ours = getattr(ctx, "ours", None) or set()
    name = str(node.get("name") or "")
    bits = []
    if tip:
        bits.append(f"Our Y tip {tip}")
    if name in ours:
        bits.append("on path")
    elif ours:
        bits.append("off Our Y path")
    else:
        bits.append("Our Y not set")
    # near-tie / uncertain if packed on ctx
    branch = getattr(ctx, "ybranch", None) or getattr(ctx, "branch", None)
    if branch is not None:
        for attr, label in (("near_ties", "near-tie"), ("uncertain", "uncertain ?"),
                            ("pruned", "pruned ○")):
            val = getattr(branch, attr, None)
            if val:
                n = len(val) if hasattr(val, "__len__") else 1
                bits.append(f"{label}×{n}")
    return " · ".join(bits)


def _parent_line(ctx, node: dict) -> str:
    parent = node.get("parent")
    if not parent:
        return "— (root)"
    pn = ctx.nodes.get(parent) if hasattr(ctx, "nodes") else None
    if not isinstance(pn, dict):
        return str(parent)
    tm = _mean_block(pn.get("tmrca"))
    return f"{parent} · TMRCA {_year_label(tm)}"


def _seshat_brief(idx: SeshatIndex | None, hints: list[str],
                  year: int | None) -> tuple[list[str], list[tuple[Polity, int]]]:
    if idx is None:
        return ["Seshat workbook not found (F2 · History & lore · D)"], []
    hits = idx.match(hints, year=year, limit=5)
    if not hits:
        return ["no polity match for region×time (codes sparse for deep prehistory)",
                f"hints: {', '.join(hints[:6]) or '—'}"], []
    lines: list[str] = []
    for p, score in hits:
        years = ""
        if p.start_year is not None and p.end_year is not None:
            years = f"{p.start_year}…{p.end_year}"
        lines.append(
            f"{p.name} · {p.region} · {years} · score {score} · {p.religion_line()}")
        flags = p.cult_flags()
        if flags:
            lines.append("  " + " · ".join(flags[:6]))
    return lines, hits


def build_lore(ctx, node: dict) -> LoreBrief:
    name = str(node.get("name") or "?")
    formed = _year_label(_mean_block(node.get("formed")))
    tmrca_v = _mean_block(node.get("tmrca"))
    tmrca = _year_label(tmrca_v)
    year = None
    try:
        if tmrca_v is not None:
            year = int(round(float(tmrca_v)))
    except (TypeError, ValueError):
        year = None

    aadr_lines, cultures, _ = _ancient_cultures(node, year)
    # современная география: только top geochart / children — не весь countries dump
    geo_bars, geo_note = children_geography(ctx, node)

    codex, culture_top = pick_codex_from_cultures(cultures)
    if culture_top is None and cultures:
        culture_top = extract_culture(cultures[0])

    # Seshat: узкий пакет — top culture + geo bars (без diaspora countries / Balkan noise)
    seshat_hints: list[str] = []
    if culture_top:
        seshat_hints.append(culture_top)
    for c, _n in Counter(extract_culture(x) for x in cultures).most_common(2):
        if c not in seshat_hints:
            seshat_hints.append(c)
    if geo_bars:
        seshat_hints.extend(g.code for g in geo_bars[:6])
    else:
        for item in (node.get("geochart_countries") or [])[:6]:
            if isinstance(item, dict) and item.get("name"):
                seshat_hints.append(str(item["name"]))

    store = getattr(ctx, "store", None)
    try:
        idx = load_index(store=store)
    except Exception:  # noqa: BLE001
        idx = None
    seshat_lines, seshat_hits = _seshat_brief(idx, seshat_hints, year)

    epoch = codex.period if codex else (tmrca if tmrca != "—" else "—")
    sigil = codex.sigil if codex else "○"

    brief = LoreBrief(
        node=name,
        genome=_genome_line(ctx, node),
        aadr_lines=aadr_lines,
        cultures=[extract_culture(c) for c in cultures[:12]],
        culture_top=culture_top or (codex.key if codex else None),
        seshat_lines=seshat_lines,
        seshat_hits=seshat_hits,
        children_geo=geo_bars,
        children_note=geo_note,
        parent_line=_parent_line(ctx, node),
        codex=codex,
        epoch=epoch,
        sigil=sigil,
        formed=formed,
        tmrca=tmrca,
        raw={
            "hints": seshat_hints[:20],
            "year": year,
            "n_ancient": len(node.get("ancient") or []),
            "n_children": len(getattr(ctx, "children", {}).get(name) or []),
            "seshat_path": str(idx.path) if idx else None,
            "n_polities": len(idx.polities) if idx else 0,
        },
    )
    return brief
