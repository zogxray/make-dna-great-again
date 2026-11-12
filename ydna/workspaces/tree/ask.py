"""Компактные промпты Ask GPT для F3 → F7 (лимит контекста)."""

from __future__ import annotations

from typing import Any

from .data import pick, year_label
from .tabs import TABS

# Жёсткий потолок символов на весь user-prompt (не токены, но держит пакет коротким).
_MAX_CHARS = 3500


def _dv(value: Any) -> str:
    return year_label(value) if value is not None else "—"


def _mean(block: Any) -> str:
    if isinstance(block, dict):
        return _dv(block.get("mean"))
    return _dv(block)


def _genome_line(ctx) -> str:
    tip = getattr(ctx, "tip", None)
    ours = getattr(ctx, "ours", None) or set()
    bits = []
    if tip:
        bits.append(f"Our Y tip: {tip}")
    if ours:
        bits.append(f"Our path nodes: {len(ours)}")
    return " · ".join(bits) if bits else "Our Y: not set (no active genome branch)"


def _node_core(node: dict, ctx) -> list[str]:
    name = str(node.get("name") or "?")
    parent = node.get("parent") or "—"
    lines = [
        f"Haplogroup: {name}",
        f"Parent: {parent}",
        f"On Our Y path: {'yes' if name in (getattr(ctx, 'ours', None) or set()) else 'no'}",
        f"Formed: {_mean(node.get('formed'))}",
        f"TMRCA: {_mean(node.get('tmrca'))}",
    ]
    countries = [str(c) for c in (node.get("countries") or [])[:8]]
    if countries:
        lines.append("Countries: " + ", ".join(countries))
    kids = list(getattr(ctx, "children", {}).get(name) or [])[:8]
    if kids:
        lines.append("Children (sample): " + ", ".join(kids))
    return lines


def _path_lines(ctx, name: str, limit: int = 12) -> list[str]:
    chain = ctx.lineage(name)
    # tip → root for reading
    chain = list(reversed(chain))[:limit]
    out = []
    for n in chain:
        node = ctx.nodes.get(n) or {}
        mark = " ← tip" if n == name else ""
        out.append(f"- {n} · formed {_mean(node.get('formed'))} · TMRCA {_mean(node.get('tmrca'))}{mark}")
    return out


def _ancestors_table(node: dict, limit: int = 15) -> list[str]:
    anc = [a for a in (node.get("ancestors") or []) if isinstance(a, dict)]
    ordered = list(reversed(anc))[:limit]
    lines = ["clade | age | living | aDNA"]
    for a in ordered:
        tm = pick(a, "tmrca")
        if isinstance(tm, dict):
            tm = tm.get("mean")
        modern = pick(a, "modern", "modern_count") or 0
        ancient = pick(a, "ancient", "ancient_count")
        short = pick(a, "short_name", "shortName", "short") or ""
        label = f"{a.get('name') or '?'} · {short}".strip(" ·")
        lines.append(f"- {label} | {_dv(tm)} | {modern} | {ancient if ancient is not None else '—'}")
    if len(anc) > limit:
        lines.append(f"… {len(anc) - limit} older steps omitted")
    return lines


def _snps_lines(node: dict, limit: int = 25) -> list[str]:
    variants = node.get("variants") or []
    lines = []
    for v in variants[:limit]:
        if isinstance(v, dict):
            lines.append(f"- {v.get('variant') or v.get('name') or v}")
        else:
            lines.append(f"- {v}")
    if len(variants) > limit:
        lines.append(f"… +{len(variants) - limit} more variants on card")
    markers = node.get("markers") or []
    if markers:
        lines.append(f"Markers listed: {len(markers)}")
    return lines or ["(no variants on card)"]


def _geo_lines(node: dict, limit: int = 10) -> list[str]:
    lines = []
    # countries[] are names; detail may be in countries data elsewhere
    for n in (node.get("countries") or [])[:limit]:
        lines.append(f"- {n}")
    world = node.get("google_countries")
    if isinstance(world, dict) and world:
        ranked = sorted(world.items(), key=lambda kv: (-kv[1], str(kv[0])))[:8]
        lines.append("World DB top:")
        for name, v in ranked:
            lines.append(f"- {name}: {v}")
    return lines or ["(no geography on card)"]


def _ancient_lines(node: dict, ctx, limit: int = 12) -> list[str]:
    samples = [s for s in (node.get("ancient") or []) if isinstance(s, dict)]
    # newest first by age_estimate_mean if present
    def key(s: dict) -> tuple:
        a = s.get("age_estimate_mean")
        return (a is None, -(a or 0))
    samples = sorted(samples, key=key)[:limit]
    lines = []
    for s in samples:
        when = _dv(s.get("age_estimate_mean"))
        place = s.get("site") or s.get("geography") or s.get("country_name") or "?"
        hg = s.get("haplogroup_y") or ""
        lines.append(f"- {when} · {place} · {hg}".rstrip(" ·"))
    if not lines:
        return ["(no ancient samples on card)"]
    total = len(node.get("ancient") or [])
    if total > limit:
        lines.append(f"… {total - limit} more omitted")
    return lines


def _migration_lines(node: dict, limit_hops: int = 20) -> list[str]:
    from .data import find_steps
    mm = node.get("migrationmap") or {}
    if not isinstance(mm, dict):
        return ["(no migration map)"]
    lines = []
    legend = mm.get("haplogroup_legend") or mm.get("legend") or []
    if isinstance(legend, list):
        bits = []
        for item in legend[:12]:
            if isinstance(item, dict) and item.get("name"):
                bits.append(str(item["name"]))
        if bits:
            lines.append("Legend haplogroups: " + ", ".join(bits))
    steps: list = []
    find_steps(mm, steps)
    if not steps:
        lines.append("(no route steps)")
        return lines
    # one chain sample
    hops = []
    for s in steps[:limit_hops]:
        if not isinstance(s, dict):
            continue
        a, b = s.get("startNode"), s.get("endNode")
        an = a.get("name") if isinstance(a, dict) else a
        bn = b.get("name") if isinstance(b, dict) else b
        hops.append(f"{an or '…'} → {bn or '…'}")
    lines.append(f"Route hops ({min(len(steps), limit_hops)} of {len(steps)}):")
    lines.extend(f"- {h}" for h in hops)
    return lines


def _matches_lines(node: dict) -> list[str]:
    # Prefer aggregates from upgrades; avoid kit identifiers.
    up = node.get("upgrades") if isinstance(node.get("upgrades"), dict) else {}
    ystr = up.get("ystr") if isinstance(up.get("ystr"), dict) else {}
    bigy = up.get("bigy") if isinstance(up.get("bigy"), dict) else {}
    lines = []
    for src, prefix, keys in (
        (ystr, "Y-STR", ("matches", "countries", "surnames", "haplogroups")),
        (bigy, "Big Y", ("matches", "countries", "subclades", "variants",
                         "academic_samples", "ancient_samples")),
    ):
        for key in keys:
            val = src.get(key)
            if val is None:
                continue
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                lines.append(f"{prefix} {key}: {int(val):,}")
            else:
                lines.append(f"{prefix} {key}: {val}")
    return lines or ["(no match aggregates on card)"]


_QUESTIONS = {
    "Overview": (
        "Give a scientific reading of this clade: phylogenetic placement, formation/TMRCA "
        "estimates and their uncertainty, and what the card evidence does and does not support."
    ),
    "Ancestors": (
        "Analyze the tip→root paternal path: how TMRCA / living / aDNA counts change with depth, "
        "and what that implies (or fails to imply) about lineage age and sampling bias."
    ),
    "SNPs": (
        "Discuss the listed defining variants: derived vs ancestral calls, phylogenetic role, "
        "and limits of inference from this SNP set alone."
    ),
    "Geography": (
        "Interpret modern geography as a biased sample (self-report / FTDNA ascertainment): "
        "what patterns are plausible vs over-read."
    ),
    "Ancient DNA": (
        "Relate the listed ancient samples to this clade (date, place, phylogenetic fit). "
        "Separate secure placements from weak or incidental associations."
    ),
    "Migration Map": (
        "Critique the migration path as a model: directionality, legend/hops, and where the "
        "map is hypothesis rather than observation."
    ),
    "Matches": (
        "Interpret match aggregates only (no individuals): geography / surname / haplogroup "
        "mix as population-genetic signal vs database artifact."
    ),
    "Famous": (
        "Place the listed historical figures relative to this clade with strict provenance "
        "limits — do not inflate celebrity claims."
    ),
    "Projects": (
        "Which FTDNA projects are most relevant analytically for this clade, and why "
        "(match rate, public size, phylogenetic focus)?"
    ),
    "LORE": (
        "Weave a short LORE text for this clade from the anchors only.\n"
        "Structure the answer with clear labels:\n"
        "  HARD — archaeology / genetics / Seshat codes actually present in the brief\n"
        "  RECONSTRUCTED — comparative mythology / PIE / analogy (say so)\n"
        "  STORY — skaldic scene (1–2 short paragraphs) that does not invent new hard facts\n"
        "Voice: scientist + skald. Do not claim the tester’s Y carried a god. "
        "If Seshat/ancient/codex is empty, say the gap — do not fake temples."
    ),
    "Raw JSON": "Ignore any raw dump; reason only from the structured facts above.",
}

_FRAME = (
    "The asker is a researcher working with Y-chromosome phylogenetics / archaeogenetics, "
    "not a consumer tester seeking reassurance.\n"
    "Respond in scientific mode: precise terminology, explicit assumptions, uncertainties, "
    "and sampling biases. Do not smooth over gaps, do not market, do not moralize.\n"
    "Use ONLY the facts below. If evidence is missing or weak, say so. Prefer concise "
    "structured reasoning over storytelling."
)

_FRAME_LORE = (
    "The asker wants a mythic lens on a Y clade, grounded in a machine brief "
    "(genome path, ancient samples, Seshat polity codes, children geography, curated codex).\n"
    "Stay honest about reconstruction. Never upgrade STORY into HARD. "
    "Modern children geography is FTDNA self-report, not a census or ethnic proof.\n"
    "Use ONLY the anchors below."
)


def build_ask_prompt(ctx, node: dict, tab_title: str) -> str:
    """Собрать короткий user-prompt для F6. Без Raw JSON / полных деревьев."""
    tab = tab_title if tab_title in _QUESTIONS else "Overview"
    question = _QUESTIONS[tab]
    name = str(node.get("name") or "?")

    if tab == "LORE":
        return _build_lore_prompt(ctx, node, question)

    parts: list[str] = [
        _FRAME,
        "",
        f"Active tab context: {tab}",
        "Data source context: FTDNA Haplotree / Discover card fields (commercial tree; treat as data, not gospel).",
        _genome_line(ctx),
        "",
        "=== NODE ===",
        *_node_core(node, ctx),
    ]

    if tab in ("Overview", "Ancestors"):
        parts += ["", "=== PATH (tip → root, truncated) ===", *_path_lines(ctx, name, 12)]
    if tab == "Ancestors":
        parts += ["", "=== ANCESTORS TABLE ===", *_ancestors_table(node, 15)]
    elif tab == "SNPs":
        parts += ["", "=== SNPs ===", *_snps_lines(node, 25)]
    elif tab == "Geography":
        parts += ["", "=== GEOGRAPHY ===", *_geo_lines(node, 10)]
    elif tab == "Ancient DNA":
        parts += ["", "=== ANCIENT DNA ===", *_ancient_lines(node, ctx, 12)]
    elif tab == "Migration Map":
        parts += ["", "=== MIGRATION ===", *_migration_lines(node, 20)]
    elif tab == "Matches":
        parts += ["", "=== MATCHES (aggregates only) ===", *_matches_lines(node)]
    elif tab == "Famous":
        fam = [f for f in (node.get("famous") or []) if isinstance(f, dict)][:10]
        parts += ["", "=== FAMOUS ==="]
        if fam:
            for f in fam:
                parts.append(f"- {f.get('name') or f}")
        else:
            parts.append("(none)")
    elif tab == "Projects":
        projects = [p for p in (node.get("projects") or []) if isinstance(p, dict)][:5]
        parts += ["", "=== PROJECTS (top) ==="]
        for p in projects:
            parts.append(
                f"- {p.get('Name') or '?'} · match {p.get('ProportionMatch')} · "
                f"{p.get('HaplogroupPublicMembers')}/{p.get('TotalPublicMembers')}"
            )
        if not projects:
            parts.append("(none)")
    else:
        # Overview extras: short genetic counts
        parts += [
            "",
            "=== CARD COUNTS ===",
            f"Ancient samples: {len(node.get('ancient') or [])}",
            f"Famous: {len(node.get('famous') or [])}",
            f"Variants: {len(node.get('variants') or [])}",
        ]

    # overlay ages one-liners if present
    try:
        ov = ctx.overlays()
        if ov:
            ages = ov.age(name)
            if ages:
                parts.append("")
                parts.append("=== OVERLAY AGES ===")
                from ...core.overlays import format_age_line
                for a in ages[:2]:
                    parts.append(f"- {format_age_line(a)}")
    except Exception:  # noqa: BLE001
        pass

    parts += ["", "=== QUESTION ===", question]
    text = "\n".join(parts)
    if len(text) > _MAX_CHARS:
        text = text[: _MAX_CHARS - 20].rstrip() + "\n…[truncated]"
    return text


def _build_lore_prompt(ctx, node: dict, question: str) -> str:
    from ...core.lore import build_lore
    name = str(node.get("name") or "?")
    briefs = getattr(ctx, "lore_briefs", None)
    brief = briefs.get(name) if isinstance(briefs, dict) else None
    if brief is None:
        brief = build_lore(ctx, node)
    parts = [
        _FRAME_LORE,
        "",
        f"Active tab context: LORE · {name}",
        "",
        "=== LORE ANCHORS ===",
        brief.weave_facts(_MAX_CHARS - 900),
        "",
        "=== QUESTION ===",
        question,
    ]
    text = "\n".join(parts)
    if len(text) > _MAX_CHARS:
        text = text[: _MAX_CHARS - 20].rstrip() + "\n…[truncated]"
    return text


def tab_title_at(section: int) -> str:
    if 0 <= section < len(TABS):
        return TABS[section].title
    return "Overview"
