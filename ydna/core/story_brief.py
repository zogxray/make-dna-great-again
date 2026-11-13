"""Human Story pass: Argon turns Oracle + facts into readable Markdown with media slots."""

from __future__ import annotations

import json
import re
from typing import Any

from . import chat
from .investigate_brief import compact_facts_pack, lineage_tools_present
from .jobs import Job
from .lineage_query import commons_search_url
from .media_cache import materialize_media_slots
from .store import Store

_ORACLE_HEAD = re.compile(r"^###\s*Oracle\b", re.I | re.M)
_STORY_HEAD = re.compile(r"^###\s*Story\b", re.I | re.M)


def _oracle_body(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    # Drop "### Oracle · model" wrapper if present.
    raw = re.sub(r"^###\s*Oracle[^\n]*\n+", "", raw, count=1, flags=re.I).strip()
    return raw


def find_last_oracle(thread: chat.Thread) -> str | None:
    for m in reversed(thread.messages):
        if m.role != "model":
            continue
        if _STORY_HEAD.search(m.text or ""):
            continue
        if _ORACLE_HEAD.search(m.text or "") or m.text.strip().startswith("## Oracle"):
            body = _oracle_body(m.text)
            if len(body) > 200:
                return body
    return None


def find_last_question(thread: chat.Thread) -> str:
    for m in reversed(thread.messages):
        if m.role == "user" and (m.text or "").strip():
            return m.text.strip()
    return ""


def _as_pack(facts: dict[str, Any]) -> dict[str, Any]:
    """Accept raw gather facts or an already-compacted digest."""
    if any(k in facts for k in ("y_path", "y_milestones", "aadr_y_hits", "y_lore")):
        return facts
    if facts.get("tool_results"):
        return compact_facts_pack(facts)
    return facts


def find_facts_pack(thread: chat.Thread) -> dict[str, Any] | None:
    """Recover compact facts digest from the Facts bubble in the thread."""
    for m in reversed(thread.messages):
        if m.role != "model" or "### Facts" not in (m.text or ""):
            continue
        fence = re.search(r"```json\s*(\{.*\})\s*```", m.text, re.DOTALL)
        if not fence:
            continue
        try:
            data = json.loads(fence.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data:
            return data
    return None


def collect_media_slots(facts: dict[str, Any] | None, *, limit: int = 12) -> list[dict[str, Any]]:
    """Flatten Discover URLs + Commons search cues from lore/milestones/AADR."""
    if not facts:
        return []
    pack = _as_pack(facts)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: dict[str, Any]) -> None:
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            return
        seen.add(url)
        out.append({
            "source": item.get("source") or "cue",
            "url": url,
            "caption": str(item.get("caption") or "media")[:140],
            "epoch": item.get("epoch"),
            "clade": item.get("clade"),
        })

    for block_name in ("y_lore",):
        block = pack.get(block_name)
        if isinstance(block, dict):
            for m in block.get("media") or []:
                if isinstance(m, dict):
                    add({**m, "epoch": block.get("epoch"), "clade": block.get("node")})

    ms = pack.get("y_milestones") or {}
    for row in ms.get("milestones") or []:
        if not isinstance(row, dict):
            continue
        lore = row.get("lore") if isinstance(row.get("lore"), dict) else {}
        for m in lore.get("media") or []:
            if isinstance(m, dict):
                add({
                    **m,
                    "epoch": lore.get("epoch"),
                    "clade": row.get("clade") or lore.get("node"),
                })
        # Site/culture search cues from AADR lore lines (first token-ish).
        for line in (lore.get("aadr_lines") or [])[:2]:
            s = str(line)
            # e.g. "Sarakenos 37 (Bronze Age …" → search Sarakenos
            name = re.split(r"[\s(·,]", s, maxsplit=1)[0].strip()
            if len(name) >= 4 and not re.fullmatch(r"I\d{3,}", name):
                add({
                    "source": "commons_search",
                    "url": commons_search_url(name),
                    "caption": f"Wikimedia search · {name}",
                    "epoch": lore.get("epoch"),
                    "clade": row.get("clade"),
                })

    aadr = pack.get("aadr_y_hits") or {}
    for ep in aadr.get("epochs") or []:
        if not isinstance(ep, dict):
            continue
        for g in (ep.get("groups") or [])[:2]:
            if not isinstance(g, dict):
                continue
            loc = g.get("locality") or g.get("country")
            if not loc or re.fullmatch(r"I\d{3,}", str(loc)):
                continue
            add({
                "source": "commons_search",
                "url": commons_search_url(str(loc)),
                "caption": f"Wikimedia search · {loc} ({ep.get('label') or ep.get('epoch')})",
                "epoch": ep.get("label") or ep.get("epoch"),
                "clade": g.get("y"),
            })

    return out[:limit]


def story_prompt(
    question: str,
    *,
    oracle: str,
    facts: dict[str, Any] | None,
    media: list[dict[str, Any]],
    language: str | None = None,
) -> str:
    digest = ""
    if facts:
        digest = json.dumps(
            compact_facts_pack(facts), ensure_ascii=False, indent=2, default=str,
        )
        if len(digest) > 24_000:
            digest = digest[:24_000] + "\n…(truncated)"
    media_json = json.dumps(media, ensure_ascii=False, indent=2)
    lang_line = (
        f"Write the Story in {language.strip()}."
        if (language or "").strip()
        else "Write the Story in the same language as the User question when clear; "
             "otherwise English."
    )
    lineage = bool(
        (facts and lineage_tools_present(facts))
        or (facts and any(
            k in facts for k in ("y_path", "y_milestones", "aadr_y_hits", "y_lore")
        ))
        or re.search(r"adam|эпох|родослов|ancestr|haplo", question or "", re.I)
    )
    structure = [
        "Required Markdown structure:",
        "1) ## Story — one short hook paragraph (who/where this genome's paternal line lands).",
        "2) ## Timeline — epoch-by-epoch chapters (#### Paleolithic … #### Modern as facts allow).",
        "   In each chapter: human narration (places, peoples, beliefs) + 1–3 concrete anchors",
        "   (clade+date, AADR site, Seshat polity). Weave, don't dump SNP lists.",
        "3) ## Pictures & places — for EACH media slot below, emit ONE markdown IMAGE line",
        "   exactly as: `![caption](url)` on its own line, then one sentence why it fits.",
        "   Use only the provided urls (already real image files / thumbs). Do not invent URLs.",
        "   Do not wrap images as ordinary links; F7 renders `![…](url)` as a terminal preview.",
        "4) ## What is solid vs story — 5–8 bullets separating chip/AADR measurements",
        "   from Seshat/codex reconstructions.",
        "5) ## Footnote — one sentence.",
    ] if lineage else [
        "Required Markdown structure:",
        "1) ## Story — plain-language answer to the question.",
        "2) ## What the data say — concrete numbers from Oracle/facts.",
        "3) ## Pictures & places — for each media slot: `![caption](url)` then one sentence.",
        "4) ## Open questions — what is still thin.",
        "5) ## Footnote — one sentence.",
    ]
    return "\n".join([
        "Task: rewrite the scientific Oracle into a HUMAN Story report in Markdown.",
        "Third person or warm documentary voice. Commit. No apologies in the body.",
        "Do not invent dates, sites, PMIDs, or sample counts — only Oracle + FACTS + MEDIA.",
        "Historical color and religion/culture are allowed when FACTS include Seshat/codex;",
        "always mark reconstruction vs measurement in ## What is solid vs story.",
        lang_line,
        "",
        "User question:",
        question.strip() or "(see Oracle)",
        "",
        "ORACLE (scientific spine):",
        oracle.strip()[:28_000],
        "",
        "FACTS DIGEST (optional extra anchors):",
        digest or "(none — rely on Oracle)",
        "",
        "MEDIA SLOTS (use these URLs only — do not invent new image URLs):",
        media_json or "[]",
        "",
        *structure,
    ])


def run_story(
    store: Store,
    thread: chat.Thread,
    job: Job,
    *,
    question: str | None = None,
    oracle: str | None = None,
    facts: dict[str, Any] | None = None,
    language: str | None = None,
) -> str:
    """Argon Story from Oracle (+ optional facts). Appends to ``thread``."""
    q = (question or find_last_question(thread) or "").strip()
    body = (oracle or find_last_oracle(thread) or "").strip()
    if len(body) < 80:
        raise RuntimeError("no_oracle: run Investigate first (need an Oracle in this chat)")

    if facts is None:
        facts = find_facts_pack(thread)
    media = collect_media_slots(facts)
    # If facts missing, still offer a place-name Commons resolve (not haplogroup codes).
    if not media:
        media = [{
            "source": "commons_search",
            "url": commons_search_url("Catalhoyuk Neolithic"),
            "caption": "Wikimedia search · Catalhoyuk Neolithic",
        }]
    job.stage = "media · resolve"
    media = materialize_media_slots(media, limit=10)
    # Drop local_path from prompt (model only needs url + caption).
    media_for_llm = [
        {k: v for k, v in m.items() if k != "local_path"}
        for m in media
    ]

    job.stage = "argon · story"
    story = chat.send(
        store, thread, job,
        continue_chat=False,
        tier="reasoning",
        use_saved=False,
        input_text=story_prompt(
            q, oracle=body, facts=facts, media=media_for_llm, language=language,
        ),
        append=False,
    )
    mid = chat.short_model(
        chat.resolve_model_for_send(store, tier="reasoning", use_saved=False)
    )
    md = f"### Story · {mid}\n\n{story.strip()}"
    iid = getattr(job, "_last_interaction_id", None)
    thread.messages.append(chat.Message("model", md, interaction_id=iid))
    if iid:
        thread.interaction_id = iid
    chat.save(thread)
    job.draft = md
    return story
