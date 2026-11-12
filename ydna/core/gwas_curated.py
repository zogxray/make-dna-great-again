"""Curated GWAS trait shortlist (F5 CUR) — theme patterns over Catalog DISEASE/TRAIT.

ALL = every matched trait. CUR = traits whose names match these themes (same
spirit as PGS CUR: intelligence/sleep/body/…, not the full clinical dump).
"""

from __future__ import annotations

import re
from collections import defaultdict

from .gwas_match import MatchResult, traits_sorted

# Same theme keys/labels as PGS shortlist — familiar across F4/F5.
THEMES: tuple[tuple[str, str], ...] = (
    ("intelligence_emotions", "Intelligence & emotions"),
    ("sleep", "Sleep"),
    ("behavior", "Behavior"),
    ("brain", "Brain"),
    ("appearance", "Appearance"),
    ("body", "Body"),
    ("fitness", "Fitness"),
    ("diet", "Diet"),
)
THEME_LABEL = dict(THEMES)
THEME_ORDER = {k: i for i, (k, _) in enumerate(THEMES)}

# First matching theme wins. Patterns target GWAS Catalog trait wording.
_CURATED_RX: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("intelligence_emotions", re.compile(
        r"intelligence|cognitive|educational attainment|years of (?:education|schooling)|"
        r"neuroticism|anxiety|depress(?:ion|ive)|well[- ]?being|happiness|subjective well|"
        r"risk[- ]taking|loneliness|irritability|mood|worry|fatigue|lethargy|"
        r"social (?:activity|support)|friendship",
        re.I,
    )),
    ("sleep", re.compile(
        r"chronotype|morningness|eveningness|sleep duration|insomnia|daytime sleepiness|"
        r"\bnapp(?:ing|ing)\b|sleep quality|ease of getting up|sleep efficiency",
        re.I,
    )),
    ("behavior", re.compile(
        r"alcohol|smoking|smoker|nicotine|cannabis|marijuana|coffee|caffeine|"
        r"physical activity|sedentary|exercise|age at first (?:sexual|intercourse)|"
        r"risk taking|adventure",
        re.I,
    )),
    ("brain", re.compile(
        r"brain volume|intracranial|hippocamp|cortical|thalam|caudate|putamen|"
        r"amygdala|brainstem|white matter|grey matter|gray matter|ventricul|"
        r"nucleus accumbens|globus pallidus|basal ganglia|head size",
        re.I,
    )),
    ("appearance", re.compile(
        r"hair (?:colou?r|pigment)|eye (?:colou?r|pigment)|skin (?:colou?r|pigment|tone)|"
        r"baldness|male pattern|tanning|sunburn|freckle|facial (?:age|aging)|"
        r"perceived age",
        re.I,
    )),
    ("body", re.compile(
        r"\bheight\b|sitting height|standing height|body mass index|\bBMI\b|"
        r"\bweight\b|waist|hip circumference|body fat|fat[- ]free mass|"
        r"lean body|obesity|overweight",
        re.I,
    )),
    ("fitness", re.compile(
        r"grip strength|hand grip|forced (?:vital|expiratory)|FEV1|FVC|"
        r"VO2|cardiorespiratory|athletic|muscle strength|walking pace",
        re.I,
    )),
    ("diet", re.compile(
        r"vitamin [Dd]|25[- ]hydroxy|cholesterol|\bLDL\b|\bHDL\b|triglyceride|"
        r"caffeine|coffee consumption|tea consumption|diet(?:ary)?|"
        r"omega[- ]3|polyunsaturated",
        re.I,
    )),
)


def theme_for(trait: str) -> str | None:
    """Theme key if trait belongs on CUR, else None."""
    for key, rx in _CURATED_RX:
        if rx.search(trait or ""):
            return key
    return None


def curated_traits(result: MatchResult) -> list[tuple[str, str, int]]:
    """(theme, trait, n) for matched curated traits, theme order then -n then name."""
    by_theme: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for trait, n in traits_sorted(result):
        theme = theme_for(trait)
        if theme is None:
            continue
        by_theme[theme].append((trait, n))
    out: list[tuple[str, str, int]] = []
    for theme, _lab in THEMES:
        rows = by_theme.get(theme) or []
        rows.sort(key=lambda kv: (-kv[1], kv[0].lower()))
        for trait, n in rows:
            out.append((theme, trait, n))
    return out


def build_trait_index(
    result: MatchResult,
) -> tuple[dict[str, str], dict[str, list]]:
    """Once per match: trait haystacks for ``/`` search + trait → assoc rows.

    Avoids rescanning ``result.assocs`` on every keystroke / trait highlight.
    """
    from .gwas_match import Assoc

    bags: dict[str, set[str]] = defaultdict(set)
    by_trait: dict[str, list[Assoc]] = defaultdict(list)
    for a in result.assocs:
        by_trait[a.trait].append(a)
        s = bags[a.trait]
        s.add(a.trait.lower())
        if a.mapped_trait:
            s.add(a.mapped_trait.lower())
        if a.gene:
            s.add(a.gene.lower())
        if a.rsid:
            s.add(a.rsid.lower())
        if a.study:
            s.add(a.study.lower())
        if a.pubmed:
            s.add(a.pubmed.lower())
    for trait in result.trait_counts:
        bags.setdefault(trait, set()).add(trait.lower())
        by_trait.setdefault(trait, [])
    haystacks = {trait: "\n".join(sorted(parts)) for trait, parts in bags.items()}
    return haystacks, dict(by_trait)


def build_trait_haystacks(result: MatchResult) -> dict[str, str]:
    """Back-compat: haystacks only."""
    return build_trait_index(result)[0]


def filter_traits(
    result: MatchResult,
    *,
    curated: bool,
    query: str = "",
    haystacks: dict[str, str] | None = None,
) -> list[tuple[str | None, str, int]]:
    """Rows for TRAITS list: (theme_or_None, trait, n).

    CUR: curated only, with theme for headers. ALL: theme None, count order.
    ``query`` matches trait haystacks (or trait name if no index).
    """
    q = (query or "").strip().lower()
    if curated:
        rows = curated_traits(result)
        base: list[tuple[str | None, str, int]] = [(th, t, n) for th, t, n in rows]
    else:
        base = [(None, t, n) for t, n in traits_sorted(result)]

    if not q:
        return base

    if haystacks is not None:
        return [
            (th, t, n) for th, t, n in base
            if q in haystacks.get(t, t.lower())
        ]
    # Fallback: trait name only (avoid O(assocs) per keystroke).
    return [(th, t, n) for th, t, n in base if q in t.lower()]


__all__ = [
    "THEME_LABEL", "THEMES", "build_trait_haystacks", "build_trait_index",
    "curated_traits", "filter_traits", "theme_for",
]
