"""Micronutrient profile from GWAS match (+ PGS when usable models exist).

Not medical advice / not a prescription. Direction is a coarse genetic signal
about circulating levels or related biomarkers on your chip coverage.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .gwas_match import Assoc, MatchResult, effect_dose, match_genome
from .store import Store

# (id, label, include_rx, exclude_rx) — exclude kills drug/false friends.
_NUTRIENT_DEFS: tuple[tuple[str, str, str, str], ...] = (
    (
        "vitamin_d", "Vitamin D",
        r"vitamin\s*d\b|25[- ]hydroxyvitamin|25[- ]hydroxy.?vitamin\s*d|25OHD|"
        r"calcidiol|serum\s*25|vitamin\s*d\s*insufficiency|vitamin\s*d\s*levels?",
        r"channel\s*blocker|thyroid|cholestasis",
    ),
    (
        "vitamin_b12", "Vitamin B12",
        r"vitamin\s*b\s*12|vitamin\s*b12|\bb12\b|cobalamin",
        r"channel\s*blocker",
    ),
    (
        "folate", "Folate / B9",
        r"\bfolate\b|folic\s*acid|vitamin\s*b\s*9|vitamin\s*b9",
        r"",
    ),
    (
        "iron", "Iron status",
        r"\bferritin\b|transferrin|serum\s*iron|iron\s*status|iron\s*levels?|"
        r"total\s*iron\s*binding|unsaturated\s*iron",
        r"",
    ),
    (
        "calcium", "Calcium",
        r"\bcalcium\s*levels?\b|\bserum\s*calcium\b|calcium\s*\(|calcium\s*\(mean|"
        r"calcium\s*\(maximum|calcium\s*\(UKB",
        r"channel\s*blocker|calcium\s*channel|stone|kidney\s*stone",
    ),
    (
        "magnesium", "Magnesium",
        r"\bmagnesium\s*levels?\b|serum\s*magnesium",
        r"",
    ),
    (
        "zinc", "Zinc",
        r"\bzinc\s*levels?\b|serum\s*zinc",
        r"",
    ),
    (
        "selenium", "Selenium",
        r"\bselenium\s*levels?\b|serum\s*selenium",
        r"",
    ),
    (
        "omega3", "Omega-3 fatty acids",
        r"omega[- ]?3|\bDHA\b|\bEPA\b|n[- ]?3\s*fatty",
        r"",
    ),
    (
        "omega6", "Omega-6 fatty acids",
        r"omega[- ]?6|n[- ]?6\s*fatty",
        r"",
    ),
)


@dataclass
class NutrientHit:
    nutrient_id: str
    label: str
    gwas_assocs: int = 0
    gwas_traits: int = 0
    gwas_rs: int = 0
    dose_2: int = 0
    dose_1: int = 0
    dose_0: int = 0
    dose_unknown: int = 0
    top_traits: list[tuple[str, int]] = field(default_factory=list)
    pgs: dict[str, Any] | None = None
    direction: str = "unknown"
    confidence: str = "none"
    evidence: list[str] = field(default_factory=list)
    would_change: list[str] = field(default_factory=list)
    disclaimer: str = (
        "NOT a prescription. Genetic association ≠ deficiency or need to supplement."
    )


def _compiled() -> list[tuple[str, str, re.Pattern[str], re.Pattern[str] | None]]:
    out = []
    for nid, label, inc, exc in _NUTRIENT_DEFS:
        out.append((
            nid, label,
            re.compile(inc, re.I),
            re.compile(exc, re.I) if exc.strip() else None,
        ))
    return out


def nutrient_for_trait(trait: str, mapped: str = "") -> str | None:
    text = f"{trait} {mapped or ''}"
    for nid, _lab, inc, exc in _compiled():
        if exc is not None and exc.search(text):
            continue
        if inc.search(text):
            return nid
    return None


def _direction_from_doses(n2: int, n1: int, n0: int, unknown: int) -> tuple[str, str]:
    known = n2 + n1 + n0
    if known < 5:
        return "unknown", "none" if known == 0 else "low"
    # Effect/risk allele copies on level traits: more copies often = reported risk/effect
    # allele — Catalog "risk" is frequently the trait-increasing allele for quantitative
    # biomarkers, but not guaranteed. Treat as weak directional hint only.
    weight = 2 * n2 + n1
    mid = known  # expected if all dose=1
    if weight >= mid * 1.25 and n2 >= n0:
        return "more_effect_alleles", "low"
    if weight <= mid * 0.75 and n0 >= n2:
        return "fewer_effect_alleles", "low"
    return "mixed_alleles", "low"


def _confidence(assocs: int, traits: int, known_doses: int, pgs_ok: bool) -> str:
    score = 0
    if assocs >= 30:
        score += 2
    elif assocs >= 10:
        score += 1
    if traits >= 3:
        score += 1
    if known_doses >= 15:
        score += 1
    if pgs_ok:
        score += 2
    if score >= 5:
        return "medium"
    if score >= 2:
        return "low"
    return "none"


_CONF_PLAIN = {
    "none": "almost no signal",
    "low": "weak signal",
    "medium": "moderate signal",
    "high": "fairly steady signal",
}

ORACLE_FOOTNOTE = (
    "This is the Oracle, not a doctor: a genetic hint from your chip and the "
    "catalog — not a diagnosis and not a supplement prescription. Decisions "
    "belong with labs and a clinician."
)


def _oracle_say(
    *,
    label: str,
    direction: str,
    confidence: str,
    assocs: int,
    pgs: dict[str, Any] | None,
) -> str:
    """One short human line; technical codes stay in ``direction``."""
    conf = _CONF_PLAIN.get(confidence, confidence)
    pgs_ok = bool(pgs and pgs.get("ok"))

    if direction == "no_signal" or (assocs == 0 and not pgs_ok):
        return (
            f"{label}: little to grab onto in the catalog on this chip ({conf}). "
            f"The Oracle stays quiet — food and bloodwork beat DNA here."
        )

    if direction == "pgs_lower_predicted_level":
        return (
            f"{label}: the polygenic score on this panel leans toward a lower "
            f"predicted level ({conf}). The Oracle whispers “check a blood test,” "
            f"not “chug a bottle.”"
        )
    if direction == "pgs_higher_predicted_level":
        return (
            f"{label}: the polygenic score leans toward a higher predicted "
            f"level ({conf}). The Oracle does not bless “just in case” pills."
        )
    if direction == "pgs_mid_predicted_level":
        return (
            f"{label}: the polygenic score sits near the middle of the reference "
            f"({conf}). Boring — and that is fine."
        )

    if direction == "more_effect_alleles":
        return (
            f"{label}: among matched GWAS rows you more often carry the catalog "
            f"effect/risk alleles ({conf}). For level biomarkers that is a weak "
            f"hint genetics may tug the level — not a prescription."
        )
    if direction == "fewer_effect_alleles":
        return (
            f"{label}: among matched rows you less often carry the catalog "
            f"effect/risk alleles ({conf}). Weak hint the other way — still "
            f"guesswork without a blood test."
        )
    if direction == "mixed_alleles":
        return (
            f"{label}: alleles pull both ways; the picture is mixed ({conf}). "
            f"The Oracle shrugs — lab first, supplement later."
        )

    return (
        f"{label}: catalog overlaps exist, but the direction is unclear ({conf}). "
        f"The Oracle offers curiosity, not a dosing schedule."
    )


def analyze_match(
    result: MatchResult,
    *,
    pgs_by_nutrient: dict[str, dict[str, Any]] | None = None,
    age: int | None = None,
    labs: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Build per-nutrient cards from an existing MatchResult (+ optional PGS/labs)."""
    pgs_by_nutrient = pgs_by_nutrient or {}
    labs = labs or {}
    buckets: dict[str, list[Assoc]] = defaultdict(list)
    for a in result.assocs:
        nid = nutrient_for_trait(a.trait, a.mapped_trait)
        if nid:
            buckets[nid].append(a)

    nutrients: list[dict[str, Any]] = []
    for nid, label, _inc, _exc in _compiled():
        rows = buckets.get(nid) or []
        trait_counts = Counter(a.trait for a in rows)
        rs = {a.rsid for a in rows}
        d2 = d1 = d0 = du = 0
        for a in rows:
            d = effect_dose(a.you, a.risk)
            if d == 2:
                d2 += 1
            elif d == 1:
                d1 += 1
            elif d == 0:
                d0 += 1
            else:
                du += 1
        pgs = pgs_by_nutrient.get(nid)
        pgs_ok = bool(pgs and pgs.get("ok"))
        direction, _ = _direction_from_doses(d2, d1, d0, du)
        conf = _confidence(len(rows), len(trait_counts), d2 + d1 + d0, pgs_ok)
        if not rows and not pgs_ok:
            direction, conf = "no_signal", "none"

        evidence = []
        if rows:
            evidence.append(
                f"GWAS Catalog on chip: {len(rows)} association rows · "
                f"{len(trait_counts)} trait names · {len(rs)} rsIDs (p≤{result.p_max})"
            )
            top = trait_counts.most_common(5)
            evidence.append("Top traits: " + "; ".join(f"{t} ({n})" for t, n in top))
            evidence.append(
                f"Effect-allele dosage on matched rows: 2×={d2}, 1×={d1}, 0×={d0}, ?={du}"
            )
        else:
            evidence.append("No GWAS Catalog hits for this nutrient pack on the active match.")

        if pgs_ok:
            evidence.append(
                f"PGS {pgs.get('pgs_id')}: score={pgs.get('score')} "
                f"EUR pct={pgs.get('pct_eur')} used={pgs.get('used')}"
            )
            direction = pgs.get("direction_hint") or direction
            conf = "medium" if conf in ("low", "none") else conf
        elif pgs and pgs.get("status") == "unavailable":
            evidence.append(pgs.get("message") or "No usable local PGS model for this nutrient.")

        lab_key = nid
        if lab_key in labs:
            evidence.append(f"User lab override: {lab_key}={labs[lab_key]}")
        if age is not None:
            evidence.append(f"User age provided: {age}")

        would_change = [
            "Serum/plasma lab for this nutrient (and season for vitamin D)",
            "Diet diary / supplement inventory",
            "Usable PGS model in local catalog + 1000G panel",
            "Clinician review if considering high-dose supplements",
        ]
        if nid == "vitamin_d":
            would_change.insert(0, "25(OH)D blood test + latitude/sun exposure")

        say = _oracle_say(
            label=label, direction=direction, confidence=conf,
            assocs=len(rows), pgs=pgs,
        )
        nutrients.append({
            "id": nid,
            "label": label,
            "say": say,
            "confidence_plain": _CONF_PLAIN.get(conf, conf),
            "gwas_assocs": len(rows),
            "gwas_traits": len(trait_counts),
            "gwas_rs": len(rs),
            "dose": {"2": d2, "1": d1, "0": d0, "unknown": du},
            "top_traits": [{"trait": t, "n": n} for t, n in trait_counts.most_common(8)],
            "pgs": pgs,
            "direction": direction,
            "confidence": conf,
            "evidence": evidence,
            "would_change": would_change,
            "disclaimer": ORACLE_FOOTNOTE,
        })

    return {
        "kind": "micronutrient_profile",
        "genome": result.genome,
        "p_max": result.p_max,
        "matched_rs_total": result.matched_rs,
        "age": age,
        "labs": labs,
        "pgs_note": (
            "Local PGS catalog currently has essentially no usable vitamin/mineral "
            "level scores; profile is GWAS-led until models are added/scored."
        ),
        "nutrients": nutrients,
        "oracle_footnote": ORACLE_FOOTNOTE,
        "global_disclaimer": ORACLE_FOOTNOTE,
    }


def _pgs_for_pack(store: Store, genome: Path) -> dict[str, dict[str, Any]]:
    """Attach PGS when a usable scored model exists; else mark unavailable."""
    from . import db
    from . import pgs as pgs_mod

    rows = db.fetchall(
        "SELECT pgs_id, theme, trait, status, usable FROM pgs.models"
    )
    results = pgs_mod.load_results(genome)
    out: dict[str, dict[str, Any]] = {}

    for nid, label, inc, exc in _compiled():
        cands: list[tuple[str, str, str, int | None]] = []
        for pid, _theme, trait, status, usable in rows:
            text = trait or ""
            if exc is not None and exc.search(text):
                continue
            if not inc.search(text):
                continue
            cands.append((pid, trait, status or "", usable))
        if not cands:
            out[nid] = {
                "ok": False,
                "status": "unavailable",
                "message": f"No local PGS model matched {label}.",
            }
            continue
        ok = [c for c in cands if c[2] == "ok"]
        pick = (ok or cands)[0]
        if pick[2] != "ok":
            out[nid] = {
                "ok": False,
                "status": pick[2] or "unusable",
                "pgs_id": pick[0],
                "trait": pick[1],
                "message": f"PGS {pick[0]} present but status={pick[2]}.",
            }
            continue
        res = results.get(pick[0])
        if res is None or res.error:
            out[nid] = {
                "ok": False,
                "status": "not_scored",
                "pgs_id": pick[0],
                "trait": pick[1],
                "message": f"PGS {pick[0]} ok in catalog but not scored for this genome (F4).",
            }
            continue
        pct = res.eur.pct if res.eur else None
        direction_hint = "unknown"
        if pct is not None:
            if pct >= 70:
                direction_hint = "pgs_higher_predicted_level"
            elif pct <= 30:
                direction_hint = "pgs_lower_predicted_level"
            else:
                direction_hint = "pgs_mid_predicted_level"
        out[nid] = {
            "ok": True,
            "pgs_id": pick[0],
            "trait": pick[1],
            "score": res.score,
            "used": res.used,
            "pct_eur": pct,
            "direction_hint": direction_hint,
        }
    return out


def build_profile(
    store: Store,
    *,
    genome: Path | None = None,
    gwas_dir: Path | None = None,
    p_max: float | None = 5e-8,
    use_cache: bool = True,
    age: int | None = None,
    labs: dict[str, float] | None = None,
) -> dict[str, Any]:
    """status-ish genome check → GWAS match → nutrient pack ∩ (+ PGS attach)."""
    from . import genome_db
    from .sources import GwasCatalog

    path = genome or genome_db.active_path()
    if path is None:
        raise RuntimeError("no_genome")
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"no_genome:{path}")

    folder = gwas_dir or store.path("gwas_catalog")
    if folder is None or not Path(folder).is_dir():
        raise RuntimeError("no_gwas")
    folder = Path(folder).resolve()
    if GwasCatalog().associations_tsv(folder) is None:
        raise RuntimeError("no_gwas_assoc")

    g = genome_db.get(path, store=store, set_active_flag=False)
    result = match_genome(folder, g, p_max=p_max, job=None, use_cache=use_cache)
    pgs_map = _pgs_for_pack(store, path)
    return analyze_match(result, pgs_by_nutrient=pgs_map, age=age, labs=labs)


__all__ = [
    "analyze_match", "build_profile", "nutrient_for_trait",
]
