"""Curated culture → pantheon / ritual / book hooks for LORE.

Порт идей gods-and-rituals/lib/cultures-codex.ts + extract_culture.
Английский слой для TUI; это реконструкция / аналогия, не dug-up tablet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class God:
    name: str
    epithet: str
    domain: str


@dataclass(frozen=True)
class Ritual:
    title: str
    timing: str
    scene: str  # one-line burial / rite scene


@dataclass(frozen=True)
class CodexEntry:
    key: str
    name: str
    period: str
    region: str
    sigil: str
    hz: int
    scale: str
    bpm: int
    gods: tuple[God, ...]
    ritual: Ritual
    book: str  # hook line + why
    hard: tuple[str, ...] = ()
    note: str = "reconstructed / comparative · not a phylogenetic claim"


# ───────────────────────── extract culture ─────────────────────────

def extract_culture(pop_name: str | None, date_bp: float | None = None) -> str:
    """Ярлык культуры из AADR Group ID / FTDNA culture string (как POC)."""
    name = (pop_name or "").lower()
    if not name.strip():
        return "Archaeological sample"

    if "magdalenian" in name:
        return "Magdalenian (Upper Paleolithic)"
    if "federmesser" in name:
        return "Federmesser (Final Paleolithic)"
    if "gravettian" in name:
        return "Gravettian (Paleolithic)"
    if "aurignacian" in name:
        return "Aurignacian (Paleolithic)"
    if "yamnaya" in name or "ямн" in name:
        return "Yamnaya (Bronze Age Steppe)"
    if "corded" in name or "cwc" in name:
        return "Corded Ware (Bronze Age)"
    if "sintashta" in name or "синташт" in name:
        return "Sintashta (Bronze Age)"
    if "afanasievo" in name:
        return "Afanasievo (Bronze Age)"
    if "srubnaya" in name or "срубн" in name:
        return "Srubnaya (Bronze Age)"
    if "botai" in name:
        return "Botai (Eneolithic)"
    if "whg" in name:
        return "Western Hunter-Gatherers (WHG)"
    if "ehg" in name:
        return "Eastern Hunter-Gatherers (EHG)"
    if "chg" in name:
        return "Caucasus Hunter-Gatherers (CHG)"
    if "anatolia" in name:
        return "Anatolian Farmers"
    if "scythian" in name or "скиф" in name:
        return "Scythians (Iron Age)"
    if "sarmatian" in name or "сармат" in name:
        return "Sarmatians (Iron Age)"
    if "celt" in name:
        return "Celts (Iron Age)"
    if "roman" in name or "rome" in name:
        if date_bp is not None and date_bp > 2500:
            return "Pre-Roman Iron Age / La Tène"
        return "Roman / Classical Antiquity"
    if "slav" in name:
        return "Early Slavs (Medieval)"
    if "viking" in name or "norse" in name:
        return "Norse / Viking Age"
    if "bell beaker" in name or "beaker" in name:
        return "Bell Beaker"
    if "hunter" in name or "mesolithic" in name:
        return "Hunter-Gatherer (Mesolithic)"
    if "bronze" in name:
        return "Bronze Age horizon"
    if "iron" in name:
        return "Iron Age horizon"
    # FTDNA style "Iron Age Scandinavian"
    parts = [p for p in re_split(pop_name or "") if p]
    if len(parts) >= 2:
        return " · ".join(parts[:4])
    return (pop_name or "Archaeological sample").strip()


def re_split(text: str) -> list[str]:
    import re
    return [p for p in re.split(r"[_\s/]+", text.strip()) if p]


def date_bp_from_ce(year_ce: float | None) -> float | None:
    """Discover/FTDNA mean year (BCE negative) → rough BP."""
    if year_ce is None:
        return None
    # BP ≈ 1950 - year_ce; year_ce=-2000 → 3950 BP
    return 1950.0 - float(year_ce)


# ───────────────────────── slim codex ─────────────────────────

CODEX: dict[str, CodexEntry] = {
    "Yamnaya (Bronze Age Steppe)": CodexEntry(
        key="Yamnaya (Bronze Age Steppe)",
        name="Yamnaya Horizon (Early Bronze Age Steppe)",
        period="3300–2600 BCE",
        region="Pontic–Caspian Steppe",
        sigil="solar_wheel",
        hz=432, scale="Dorian steppe drone (D–A–C–D)", bpm=72,
        gods=(
            God("*Dyḗus Ph₂tḗr", "Daylight Vault", "sky · oaths · sovereign order"),
            God("*Perkʷūnos", "Oak-Striker", "thunder · rains · warrior bands"),
            God("*H₂éwsōs & Divine Twins", "Dawn Riders", "dawn · horses · safe passage"),
        ),
        ritual=Ritual(
            "Ochre pit-grave under the kurgan",
            "death of a herder-kin",
            "supine, flexed knees, red ochre, timber roof — wagon world sealed in earth",
        ),
        book="Rigveda sky-father echoes · analogy for PIE steppe, not a Yamnaya tablet",
        hard=(
            "mobile pastoralism · wagons · kurgans · dairy in calculus",
            "WSH ancestry vector across Eurasia",
        ),
    ),
    "Corded Ware (Bronze Age)": CodexEntry(
        key="Corded Ware (Bronze Age)",
        name="Corded Ware / Battle-Axe Horizon",
        period="2900–2350 BCE",
        region="Rhine to Upper Volga",
        sigil="valknut_hex",
        hz=417, scale="Hypodorian pentatonic", bpm=80,
        gods=(
            God("Lord of the Boat-Axe", "Sky-Splitter", "lightning · consecrated axes"),
            God("Cord-Spinner of Fate", "Binder", "twisted cord · beaker · lineage"),
        ),
        ritual=Ritual(
            "Corded beaker & amber sun-disc",
            "funerary banquet / harvest",
            "men right-side, women left-side, both facing south sun; battle-axe as thunder-token",
        ),
        book="Edda / Baltic Perkūnas as later rhyme — not Corded scripture",
        hard=(
            "cord-impressed beakers · gender-solar burial alignment",
            "~¾ steppe + farmer admixture matrix of later Europe",
        ),
    ),
    "Sintashta (Bronze Age)": CodexEntry(
        key="Sintashta (Bronze Age)",
        name="Sintashta–Petrovka (Arkaim chariots)",
        period="2100–1800 BCE",
        region="Southern Urals",
        sigil="octagram_star",
        hz=440, scale="Chariot fifths", bpm=96,
        gods=(
            God("*H₁éḱwos Lord", "Horse Covenant", "chariot · horse sacrifice"),
            God("Fire of the Fort", "Circular Stronghold", "smelters · walls · kin fire"),
        ),
        ritual=Ritual(
            "Chariot burial with horse pairs",
            "warrior death",
            "spoke-wheeled carts, cheek-pieces, weapons — Indo-Iranian prelude staged in earth",
        ),
        book="Avestan / early Vedic horse rites as soft rhyme to Sintashta kit",
        hard=("spoke-wheel chariots · fortified settlements · metallurgy",),
    ),
    "Western Hunter-Gatherers (WHG)": CodexEntry(
        key="Western Hunter-Gatherers (WHG)",
        name="Western Hunter-Gatherers",
        period="Mesolithic W. Europe",
        region="Atlantic–Central Europe",
        sigil="labyrinth_spiral",
        hz=396, scale="Open fifth drone", bpm=60,
        gods=(
            God("Forest Twin", "Night Tracker", "deer · rivers · dark canopy"),
            God("Bone Mother", "Shell Memory", "antler · ochre · caves"),
        ),
        ritual=Ritual(
            "Ochre and antler at the water’s edge",
            "seasonal kill / burial",
            "isolated graves, personal ornaments — no temples, only places of return",
        ),
        book="No written saga — Cheddar / Loschbour as human faces of the horizon",
        hard=("WHG genetic cluster · Mesolithic foragers",),
    ),
    "Eastern Hunter-Gatherers (EHG)": CodexEntry(
        key="Eastern Hunter-Gatherers (EHG)",
        name="Eastern Hunter-Gatherers",
        period="Mesolithic E. Europe",
        region="Baltic–Volga–Karelia",
        sigil="vesica_piscis",
        hz=408, scale="Boreal fourths", bpm=66,
        gods=(
            God("Lake Father", "Ice Breaker", "fish · seals · winter night"),
            God("Elk Woman", "Northern Path", "migration · antler · fire"),
        ),
        ritual=Ritual(
            "Island cemetery of the north",
            "kin burial",
            "Yuzhny Oleniy Ostrov-style collective memory — beads, ochre, water-roads",
        ),
        book="Finno-Ugric / Baltic forest motifs as later soft analogy only",
        hard=("EHG cluster · contribution to later steppe mixes",),
    ),
    "Scythians (Iron Age)": CodexEntry(
        key="Scythians (Iron Age)",
        name="Royal Scythians & Saka",
        period="Iron Age steppe",
        region="Pontic–Caspian to Altai",
        sigil="triskelion",
        hz=444, scale="Animal-style modes", bpm=88,
        gods=(
            God("Tabiti", "Hearth Fire", "royal fire · camp center"),
            God("Papaios & Api", "Sky & Earth", "oath · pasture · blood"),
            God("Ares of the Scythians", "Sword-in-Earth", "war · sacrifice mound"),
        ),
        ritual=Ritual(
            "Kurgan with horses and gold beasts",
            "royal funeral",
            "Herodotus’ blood-oath and hemp-vapour tents — hard kit meets soft reportage",
        ),
        book="Herodotus IV — ethnographic theatre, use with caution",
        hard=("animal style gold · horse gear · kurgans",),
    ),
    "Celts (Iron Age)": CodexEntry(
        key="Celts (Iron Age)",
        name="Hallstatt & La Tène",
        period="Iron Age W/C Europe",
        region="Gaul · Alps · Isles",
        sigil="triskelion",
        hz=428, scale="Pentatonic harp", bpm=76,
        gods=(
            God("Taranis", "Thunder Wheel", "sky · wheel · storm"),
            God("Epona / Rhiannon-type", "Horse Lady", "roads · mounts · sovereignty"),
            God("Cernunnos-type", "Horned One", "wild · torque · underworld gate"),
        ),
        ritual=Ritual(
            "Votive deposit in water & bog",
            "seasonal / war offering",
            "swords bent, torcs sunk — gifts to the otherworld, not museum labels",
        ),
        book="Insular sagas & Caesar’s digressions — later lenses on La Tène",
        hard=("La Tène metalwork · oppida · bog offerings",),
    ),
    "Norse / Viking Age": CodexEntry(
        key="Norse / Viking Age",
        name="Norse / Viking Age Scandinavians",
        period="c. 750–1100 CE",
        region="Scandinavia + diaspora",
        sigil="valknut_hex",
        hz=432, scale="Lyre drone", bpm=84,
        gods=(
            God("Óðinn", "Hanged God", "wisdom · dead · poetry"),
            God("Þórr", "Oak Hammer", "thunder · protection · oaths"),
            God("Freyja", "Seiðr Lady", "love · war-choosing · magic"),
        ),
        ritual=Ritual(
            "Ship / mound burial with gear",
            "high-status death",
            "boat, weapons, animals — journey staged for the living’s story",
        ),
        book="Poetic Edda — written late, sung as if older",
        hard=("ship burials · runestones · diaspora archaeology",),
    ),
    "Early Slavs (Medieval)": CodexEntry(
        key="Early Slavs (Medieval)",
        name="Early Slavs (Korchak / Penkovka & kin)",
        period="Early Medieval",
        region="Forest-steppe E. Europe",
        sigil="solar_wheel",
        hz=420, scale="Open drone fifths", bpm=70,
        gods=(
            God("Perun", "Oak Thunder", "oaths · war · skyfire"),
            God("Veles", "Cattle & Underworld", "wealth · waters · trickster"),
            God("Mokosh", "Moist Earth", "fate · spinning · women"),
        ),
        ritual=Ritual(
            "Cremation then mound / urn",
            "kin funeral",
            "fire returns the body; feast binds the living — chroniclers wrote as outsiders",
        ),
        book="Primary Chronicle digressions — Christian ink over older smoke",
        hard=("Prague-Korchak pottery horizons · forest-zone expansion",),
    ),
    "Roman / Classical Antiquity": CodexEntry(
        key="Roman / Classical Antiquity",
        name="Roman Imperial / Classical horizon",
        period="Classical–Imperial",
        region="Mediterranean & frontiers",
        sigil="octagram_star",
        hz=440, scale="Dorian / Phrygian colour", bpm=90,
        gods=(
            God("Iuppiter", "Sky Father", "oath · state · thunder"),
            God("Mars", "Field & War", "borders · season · blood"),
            God("Vesta", "Hearth Fire", "city heart · continuity"),
        ),
        ritual=Ritual(
            "Sacrifice at the altar before the campaign",
            "state calendar",
            "public victim, private lar shrine — empire as ritual machine",
        ),
        book="Livy / Virgil — Rome telling itself what gods require",
        hard=("inscriptions · temples · military diplomas",),
    ),
    "Anatolian Farmers": CodexEntry(
        key="Anatolian Farmers",
        name="Anatolian Neolithic Farmers",
        period="Neolithic",
        region="Anatolia → Europe",
        sigil="flower_of_life",
        hz=396, scale="Earth drone", bpm=58,
        gods=(
            God("House Mother", "Seated Clay", "birth · grain · plastered skulls"),
            God("Bull of the Walls", "Horned Threshold", "strength · enclosure"),
        ),
        ritual=Ritual(
            "House burial under the floor",
            "household death",
            "dead stay in the dwelling — continuity of clay and kin",
        ),
        book="No epic — Çatalhöyük / Barcın as dug silence with figurines",
        hard=("early farming package · plastered skulls · mudbrick tells",),
    ),
    "Bell Beaker": CodexEntry(
        key="Bell Beaker",
        name="Bell Beaker phenomenon",
        period="c. 2800–1800 BCE",
        region="Atlantic–Central Europe",
        sigil="solar_wheel",
        hz=424, scale="Maritime pentatonic", bpm=78,
        gods=(
            God("Archer of the West", "Bow & Beaker", "mobility · archery · copper"),
            God("Sun-Disc Bearer", "Gold Dawn", "solar discs · amber routes"),
        ),
        ritual=Ritual(
            "Beaker, bow, and copper knife in the grave",
            "individual burial",
            "single grave under round barrow — person staged as traveler",
        ),
        book="No native book — later Celtic/Atlantic myths are soft afterglow only",
        hard=("bell-shaped pots · archery kits · steppe-related influx in many regions",),
    ),
}

# Fallback by keyword when extract_culture key not exact.
_FALLBACK_ORDER: list[tuple[str, str]] = [
    ("yamnaya", "Yamnaya (Bronze Age Steppe)"),
    ("corded", "Corded Ware (Bronze Age)"),
    ("sintashta", "Sintashta (Bronze Age)"),
    ("scyth", "Scythians (Iron Age)"),
    ("sarmat", "Scythians (Iron Age)"),
    ("celt", "Celts (Iron Age)"),
    ("viking", "Norse / Viking Age"),
    ("norse", "Norse / Viking Age"),
    ("slav", "Early Slavs (Medieval)"),
    ("roman", "Roman / Classical Antiquity"),
    ("whg", "Western Hunter-Gatherers (WHG)"),
    ("ehg", "Eastern Hunter-Gatherers (EHG)"),
    ("anatolia", "Anatolian Farmers"),
    ("beaker", "Bell Beaker"),
    ("hunter", "Western Hunter-Gatherers (WHG)"),
    ("bronze", "Corded Ware (Bronze Age)"),
]


def get_codex(culture_key: str | None) -> CodexEntry | None:
    if not culture_key:
        return None
    if culture_key in CODEX:
        return CODEX[culture_key]
    low = culture_key.lower()
    for needle, key in _FALLBACK_ORDER:
        if needle in low:
            return CODEX[key]
    return None


def pick_codex_from_cultures(cultures: list[str]) -> tuple[CodexEntry | None, str | None]:
    """Первый culture label, для которого есть CODEX."""
    counts: dict[str, int] = {}
    for c in cultures:
        key = extract_culture(c)
        counts[key] = counts.get(key, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    for key, _ in ranked:
        entry = get_codex(key)
        if entry:
            return entry, key
    top = ranked[0][0] if ranked else None
    return None, top
