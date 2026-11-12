"""Вкладка LORE: якоря (genome / ancient / Seshat / children geo) + CODEX + Ask weave."""

from __future__ import annotations

import json

from ...base import click
from ....core.lore import build_lore
from . import Doc, tab
from .node import ask_cta, fit, put


def toggle_geo(ctx) -> None:
    ctx.lore_geo = not bool(getattr(ctx, "lore_geo", False))


def toggle_seshat(ctx) -> None:
    ctx.lore_seshat = not bool(getattr(ctx, "lore_seshat", False))


def toggle_raw(ctx) -> None:
    ctx.lore_raw = not bool(getattr(ctx, "lore_raw", False))


def _cache_get(ctx, name: str) -> dict | None:
    cache = getattr(ctx, "lore_cache", None)
    if not isinstance(cache, dict):
        return None
    hit = cache.get(name)
    return hit if isinstance(hit, dict) else None


def _sigil_ascii(kind: str) -> list[str]:
    art = {
        "solar_wheel": [
            "    .  *  .",
            "  *   \\|/   *",
            " ————— ◈ —————",
            "  *   /|\\   *",
            "    .  *  .",
        ],
        "valknut_hex": [
            "     /\\",
            "    /  \\",
            "   /_/\\_\\",
            "   \\    /",
            "    \\  /",
            "     \\/",
        ],
        "triskelion": [
            "    )  (",
            "   ( ◈ )",
            "  /  |  \\",
            " (___|___)",
        ],
        "octagram_star": [
            "    \\ | /",
            "   — ◈ —",
            "    / | \\",
        ],
        "labyrinth_spiral": [
            "   @@@@@@@",
            "  @  ___  @",
            "  @ @   @ @",
            "  @  \\_/  @",
            "   @@@@@@@",
        ],
        "vesica_piscis": [
            "   (\\   /)",
            "    \\ ◈ /",
            "     \\ /",
            "      V",
        ],
        "flower_of_life": [
            "   o o o",
            "  o ◈ ◈ o",
            "   o o o",
        ],
    }
    return art.get(kind, ["     ◈", "    ( )", "     *"])


@tab("LORE", keys={"g": toggle_geo, "s": toggle_seshat, "x": toggle_raw})
def lore(ctx, doc: Doc, node: dict) -> None:
    fit(ctx)
    name = str(node.get("name") or "?")
    brief = build_lore(ctx, node)
    # кэш brief на ctx для Ask GPT
    if not hasattr(ctx, "lore_briefs") or not isinstance(ctx.lore_briefs, dict):
        ctx.lore_briefs = {}
    ctx.lore_briefs[name] = brief

    show_geo = bool(getattr(ctx, "lore_geo", False))
    show_seshat = bool(getattr(ctx, "lore_seshat", False))
    show_raw = bool(getattr(ctx, "lore_raw", False))

    doc.title(f"LORE · {name}")
    put(doc, ("mythic lens on this clade · not a phylogenetic claim", "#8A8294"))
    ask_cta(doc)

    # SIGIL
    doc.title("SIGIL")
    for line in _sigil_ascii(brief.sigil):
        put(doc, (line, "#D4AD72"))
    put(doc, ("epoch  ", "#AEA5B8"), (brief.epoch, "#FBF6F0"))
    put(doc, ("culture  ", "#AEA5B8"),
        (brief.culture_top or "— (no codex match)", "#FBF6F0"))
    if brief.codex:
        put(doc, ("region  ", "#AEA5B8"), (brief.codex.region, "#AEA5B8"))
    seshat_sum = "—"
    if brief.seshat_hits:
        p, _ = brief.seshat_hits[0]
        seshat_sum = f"{p.region} · {p.name}"
    elif brief.seshat_lines:
        seshat_sum = brief.seshat_lines[0][:60]
    put(doc, ("Seshat  ", "#AEA5B8"), (seshat_sum, "#8A8294"))

    # ANCHORS
    doc.title("ANCHORS · machine brief · feeds Chat")
    put(doc, ("Genome  ", "#AEA5B8"), (brief.genome, "#FBF6F0"))
    put(doc, ("Formed  ", "#AEA5B8"), (brief.formed, "#FBF6F0"),
        ("   TMRCA  ", "#AEA5B8"), (brief.tmrca, "#FBF6F0"))
    put(doc, ("Parent  ", "#AEA5B8"), (brief.parent_line, "#AEA5B8"))

    put(doc, ("AADR/FTDNA ancient  ", "#AEA5B8"),
        (f"{len(brief.aadr_lines)} sample line(s)", "#FBF6F0"))
    if brief.cultures:
        top = Counter_top(brief.cultures, 4)
        put(doc, ("  cultures  ", "#8A8294"), (top, "#D4AD72"))
    for line in brief.aadr_lines[:5]:
        put(doc, (f"  · {line}", "#8A8294"))
    if len(brief.aadr_lines) > 5:
        put(doc, (f"  … +{len(brief.aadr_lines) - 5} more", "#71697A"))

    put(doc, ("Seshat  ", "#AEA5B8"),
        (f"{len(brief.seshat_hits)} polity hit(s)", "#FBF6F0"),
        ("   S detail", "#71697A"))
    for line in brief.seshat_lines[: (6 if show_seshat else 2)]:
        put(doc, (f"  · {line}", "#8A8294"))
    if not show_seshat and len(brief.seshat_lines) > 2:
        put(doc, ("  … press S for Seshat detail", "#71697A"))

    put(doc, ("Children geo  ", "#AEA5B8"),
        ("G expand" if not show_geo else "G collapse", "#71697A"))
    if brief.children_geo:
        strip = "  ".join(f"{g.code} {g.bar}" for g in brief.children_geo[:6])
        put(doc, (strip, "#49AE6B"))
        put(doc, (brief.children_note, "#71697A"))
        if show_geo:
            for g in brief.children_geo:
                put(doc, (f"  {g.code:<12} {g.count:>6,}  {g.bar}", "#AEA5B8"))
    else:
        put(doc, (f"  · {brief.children_note}", "#8A8294"))

    # PANTHEON / RITUAL / BOOK
    if brief.codex:
        c = brief.codex
        doc.title("PANTHEON")
        for g in c.gods:
            put(doc, (g.name, "bold #D4AD72"), (f"  {g.domain}", "#AEA5B8"))
            put(doc, (f"  {g.epithet}", "#71697A"))
        put(doc, ("⚠ reconstructed / comparative — not dug up on this clade’s tablet",
                  "#8BAB4A"))

        doc.title("RITUAL")
        put(doc, (c.ritual.title, "bold #FBF6F0"))
        put(doc, (c.ritual.timing, "#8A8294"))
        put(doc, (c.ritual.scene, "#AEA5B8"))
        if c.hard:
            put(doc, ("hard: " + " · ".join(c.hard), "#49AE6B"))

        doc.title("BOOK / SAGA")
        put(doc, (c.book, "#AEA5B8"))

        doc.title("SOUND")
        put(doc, (f"{c.hz} Hz · {c.scale} · {c.bpm} bpm", "#8A8294"))
    else:
        doc.title("PANTHEON")
        put(doc, ("No curated CODEX for this horizon yet.", "#8A8294"))
        put(doc, ("Ask GPT will still weave from anchors (HARD only if sparse).", "#71697A"))

    # GENERATE / LAST
    doc.title("GENERATE")
    put(doc,
        ("A", click("screen.ask_gpt", "bold reverse #D4AD72")),
        ("  weave LORE from anchors → F7  · skald + scientist · HARD / RECON / STORY",
         "#71697A"))

    cached = _cache_get(ctx, name)
    doc.title("LAST LORE · cached on node")
    if cached and cached.get("text"):
        preview = str(cached["text"]).strip().splitlines()
        for ln in preview[:6]:
            put(doc, (ln[:90], "#D4AD72"))
        if len(preview) > 6:
            put(doc, (f"… +{len(preview) - 6} lines", "#71697A"))
        meta = cached.get("meta") or {}
        if meta:
            put(doc, ("  " + " · ".join(f"{k} {v}" for k, v in meta.items()), "#71697A"))
    else:
        put(doc, ("— none yet · press A to weave into active chat", "#8A8294"))

    if show_raw:
        doc.title("RAW ANCHORS · debug")
        blob = {
            "genome": brief.genome,
            "cultures": brief.cultures,
            "culture_top": brief.culture_top,
            "aadr": brief.aadr_lines,
            "seshat": brief.seshat_lines,
            "children_geo": [{"code": g.code, "n": g.count} for g in brief.children_geo],
            "raw": brief.raw,
        }
        for ln in json.dumps(blob, ensure_ascii=False, indent=2).splitlines()[:80]:
            put(doc, (ln, "#5A5361"))

    put(doc, ("keys  A weave   G children geo   S Seshat   X raw anchors", "#5A5361"))
    put(doc, (brief.codex.note if brief.codex else
              "Lore is a cultural lens — not proof your Y carried this god.", "#71697A"))


def Counter_top(items: list[str], n: int) -> str:
    from collections import Counter
    c = Counter(items)
    return ", ".join(f"{k} ×{v}" for k, v in c.most_common(n))
