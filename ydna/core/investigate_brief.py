"""Dual-tier Investigate: Flash plans tools → local gather → Argon Oracle.

Domain-agnostic: the plan is a list of tool calls. GWAS handlers ship here;
Y/AADR/1000G handlers are injected from ``ydna.cli.lineage_session`` (cli layer).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from . import chat
from . import investigate as inv
from .jobs import Job
from .store import Store

ToolHandler = Callable[..., dict[str, Any]]

_PLAN_MAX_TOOLS = 16
_PLAN_MAX_SEARCH = 10
_PLAN_MAX_TRAITS = 8
_PLAN_MAX_PGS = 6
_PLAN_MAX_RS = 12
_TRAIT_ROW_LIMIT = 12
_SEARCH_LIMIT = 8

_LINEAGE_HINTS = re.compile(
    r"(?:"
    r"\b(?:adam|y[- ]?dna|haplo|clade|tmrca|aadr|1000g|thousand genomes|"
    r"ancestr\w*|bronze|neolithic|iron age|seshat|discover|paternal)\b|"
    r"родослов\w*|эпох\w*|культур\w*|религ\w*|отцовск\w*"
    r")",
    re.I,
)
_GWAS_HINTS = re.compile(
    r"\b(gwas|pgs|snp|rs\d+|vitamin|magnesium|anxiety|sleep|caffeine|"
    r"supplement|micronutrient|trait|disease)\b",
    re.I,
)


def _extract_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise RuntimeError("empty_plan")
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE)
    if fence:
        raw = fence.group(1)
    else:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start:end + 1]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("plan_not_object")
    return data


def _as_str_list(val: Any, *, limit: int) -> list[str]:
    if not isinstance(val, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in val:
        s = str(item or "").strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def _normalize_tools(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:_PLAN_MAX_TOOLS]:
        if isinstance(item, str):
            name = item.strip()
            if name:
                out.append({"tool": name, "args": {}})
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool") or item.get("name") or "").strip()
        if not name:
            continue
        args = item.get("args") or item.get("params") or {}
        if not isinstance(args, dict):
            args = {}
        out.append({"tool": name, "args": args})
    return out


def _heuristic_tools(question: str) -> list[dict[str, Any]]:
    q = question or ""
    lineage = bool(_LINEAGE_HINTS.search(q))
    gwas = bool(_GWAS_HINTS.search(q))
    tools: list[dict[str, Any]] = []
    if lineage or not gwas:
        tools.extend([
            {"tool": "y_path", "args": {}},
            {"tool": "y_milestones", "args": {"limit": 8, "with_lore": True}},
            {"tool": "aadr_y_hits", "args": {"limit_per_epoch": 6}},
            {"tool": "modern_affinity", "args": {"limit": 12}},
        ])
    if gwas or not lineage:
        toks = [t for t in re.split(r"[\s,;|/]+", q.lower()) if len(t) > 3]
        for t in toks[:6]:
            tools.append({"tool": "gwas_search", "args": {"query": t}})
        if any(x in q.lower() for x in ("vitamin", "magnesium", "iron", "omega", "supplement")):
            tools.append({"tool": "profile_micronutrients", "args": {}})
    if not tools:
        tools = [
            {"tool": "y_path", "args": {}},
            {"tool": "y_milestones", "args": {"limit": 8, "with_lore": True}},
            {"tool": "gwas_search", "args": {"query": "anxiety"}},
        ]
    return tools[:_PLAN_MAX_TOOLS]


def normalize_plan(data: dict[str, Any], *, question: str) -> dict[str, Any]:
    """Accept tool-list plans or legacy GWAS field plans."""
    focus = str(data.get("focus") or data.get("summary") or "").strip()
    tools = _normalize_tools(data.get("tools"))

    # Legacy GWAS fields → tools
    searches = _as_str_list(data.get("gwas_searches") or data.get("searches"), limit=_PLAN_MAX_SEARCH)
    traits = _as_str_list(data.get("gwas_traits") or data.get("traits"), limit=_PLAN_MAX_TRAITS)
    pgs = _as_str_list(data.get("pgs_searches") or data.get("pgs"), limit=_PLAN_MAX_PGS)
    rsids = _as_str_list(data.get("rsids") or data.get("genome_rs"), limit=_PLAN_MAX_RS)
    for q in searches:
        tools.append({"tool": "gwas_search", "args": {"query": q}})
    for t in traits:
        tools.append({"tool": "gwas_trait", "args": {"trait": t}})
    for q in pgs:
        tools.append({"tool": "pgs_search", "args": {"query": q}})
    if rsids:
        tools.append({"tool": "genome_rs", "args": {"rsids": rsids}})
    if data.get("include_micronutrients") is True:
        tools.append({"tool": "profile_micronutrients", "args": {}})

    # Deduplicate identical tool+args
    dedup: list[dict[str, Any]] = []
    seen: set[str] = set()
    for t in tools:
        key = json.dumps(t, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(t)
    tools = dedup[:_PLAN_MAX_TOOLS]

    if not tools:
        tools = _heuristic_tools(question)

    return {
        "focus": focus or question.strip().splitlines()[0][:120],
        "tools": tools,
        # Keep legacy keys for older facts_markdown / tests
        "gwas_searches": searches,
        "gwas_traits": traits,
        "pgs_searches": pgs,
        "rsids": rsids,
        "include_micronutrients": bool(data.get("include_micronutrients", False)),
    }


def plan_prompt(question: str, *, available_tools: list[str] | None = None) -> str:
    tools = available_tools or [
        "gwas_search", "gwas_trait", "pgs_search", "genome_rs", "profile_micronutrients",
        "y_path", "y_lore", "y_milestones", "aadr_y_hits", "modern_affinity",
    ]
    return "\n".join([
        "You plan a local genome investigation. Output ONLY one JSON object.",
        "No markdown outside the JSON. No apologies.",
        "The plan is a list of tool calls — not a fixed story template.",
        "Schema:",
        '{',
        '  "focus": "one-line scientific framing of THIS question",',
        '  "tools": [',
        '    {"tool": "TOOL_NAME", "args": {...}},',
        "    ... max 16",
        "  ]",
        "}",
        f"Available tools: {', '.join(tools)}",
        "",
        "Tool args cheat-sheet:",
        '- gwas_search: {"query": "vitamin D"}  (no 1-letter tokens)',
        '- gwas_trait: {"trait": "Vitamin D levels"}',
        '- pgs_search: {"query": "anxiety"}',
        '- genome_rs: {"rsids": ["rs1815739"]}',
        '- profile_micronutrients: {}',
        '- y_path: {}',
        '- y_lore: {"clade": "J-M172"}  (optional clade; default tip)',
        '- y_milestones: {"limit": 8, "with_lore": true}  (keep lean; lore is large)',
        '- aadr_y_hits: {"epochs": ["bronze","iron"], "limit_per_epoch": 6}  (epochs optional)',
        '- modern_affinity: {"limit": 12}  (cached f3 only)',
        "",
        "Rules:",
        "- Pick tools that answer THIS question; omit irrelevant domains.",
        "- Ancestry / epochs / cultures / religions / Adam / Y / AADR / 1000G → y_* + aadr_* + modern_affinity.",
        "- Traits / vitamins / disease / supplements → gwas_* / pgs_* / micronutrients.",
        "- Mixed questions may use both domains.",
        "- Do not invent results — only propose tool calls.",
        "",
        f"User question:\n{question.strip()}",
    ])


def _tool_data(facts: dict[str, Any], name: str) -> dict[str, Any] | None:
    for step in facts.get("tool_results") or []:
        if step.get("tool") == name and step.get("ok"):
            data = step.get("data")
            return data if isinstance(data, dict) else None
    return None


def _compact_lore(
    data: dict[str, Any] | None,
    *,
    include_facts_text: bool = False,
    max_facts: int = 900,
) -> dict[str, Any] | None:
    if not data:
        return None
    codex = data.get("codex")
    codex_out = None
    if isinstance(codex, dict):
        ritual = codex.get("ritual") if isinstance(codex.get("ritual"), dict) else None
        codex_out = {
            "key": codex.get("key"),
            "name": codex.get("name"),
            "period": codex.get("period"),
            "region": codex.get("region"),
            "hard": list(codex.get("hard") or [])[:3],
            "gods": list(codex.get("gods") or [])[:4],
            "ritual": (
                {"title": ritual.get("title"), "scene": ritual.get("scene")}
                if ritual else None
            ),
            "note": codex.get("note"),
        }
    out: dict[str, Any] = {
        "node": data.get("node"),
        "formed": data.get("formed"),
        "tmrca": data.get("tmrca"),
        "epoch": data.get("epoch"),
        "culture_top": data.get("culture_top"),
        "cultures": list(data.get("cultures") or [])[:6],
        "aadr_lines": list(data.get("aadr_lines") or [])[:6],
        "seshat_lines": list(data.get("seshat_lines") or [])[:6],
        "children_geo": list(data.get("children_geo") or [])[:6],
        "parent_line": data.get("parent_line"),
        "codex": codex_out,
        "media": list(data.get("media") or [])[:4],
    }
    if include_facts_text:
        facts_text = str(data.get("facts_text") or "")
        if len(facts_text) > max_facts:
            facts_text = facts_text[: max_facts - 20].rstrip() + "\n…[truncated]"
        if facts_text:
            out["facts_text"] = facts_text
    return out


def _compact_path(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not data:
        return None
    steps = []
    for s in data.get("steps") or []:
        if not isinstance(s, dict):
            continue
        steps.append({
            "clade": s.get("clade"),
            "depth_from_tip": s.get("depth_from_tip"),
            "formed": s.get("formed"),
            "tmrca": s.get("tmrca"),
            "der": s.get("der"),
            "anc": s.get("anc"),
        })
    return {
        "tip": data.get("tip"),
        "n_path": data.get("n_path"),
        "n_candidates": data.get("n_candidates"),
        "tie": data.get("tie"),
        "path_root_to_tip": list(data.get("path_root_to_tip") or []),
        "steps": steps,
    }


def _compact_milestones(
    data: dict[str, Any] | None,
    *,
    limit: int = 8,
) -> dict[str, Any] | None:
    if not data:
        return None
    raw_rows = [m for m in (data.get("milestones") or []) if isinstance(m, dict)]
    # Prefer tip + root + evenly spaced middle when over limit.
    if len(raw_rows) > limit:
        idxs = sorted({0, len(raw_rows) - 1} | {
            int(round(i * (len(raw_rows) - 1) / (limit - 1)))
            for i in range(limit)
        })
        raw_rows = [raw_rows[i] for i in idxs[:limit]]
    rows = []
    for i, m in enumerate(raw_rows):
        row: dict[str, Any] = {"clade": m.get("clade")}
        if m.get("error"):
            row["error"] = m.get("error")
        else:
            # Structured lore only — facts_text duplicates aadr/seshat/codex.
            row["lore"] = _compact_lore(
                m.get("lore"),
                include_facts_text=(i == 0),
                max_facts=700,
            )
        rows.append(row)
    return {
        "tip": data.get("tip"),
        "n_milestones": data.get("n_milestones") or len(rows),
        "milestones": rows,
        "need_from_user": list(data.get("need_from_user") or [])[:6],
    }


def _compact_aadr(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not data:
        return None
    epochs = []
    for ep in data.get("epochs") or []:
        if not isinstance(ep, dict):
            continue
        groups = []
        for g in ep.get("groups") or []:
            if not isinstance(g, dict):
                continue
            groups.append({
                "group": g.get("group"),
                "relation": g.get("relation"),
                "y": g.get("y"),
                "bp": g.get("bp"),
                "country": g.get("country"),
                "locality": g.get("locality"),
                "n": g.get("n"),
            })
        epochs.append({
            "epoch": ep.get("epoch"),
            "label": ep.get("label"),
            "hits": ep.get("hits"),
            "groups": groups[:8],
        })
    return {
        "tip": data.get("tip"),
        "n_group_hits": data.get("n_group_hits"),
        "epochs": epochs,
    }


def _compact_affinity(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not data:
        return None

    def _top(block: Any) -> dict[str, Any] | None:
        if not isinstance(block, dict):
            return None
        return {
            "source": block.get("source"),
            "n": block.get("n"),
            "top": list(block.get("top") or [])[:12],
        }

    return {
        "genome": data.get("genome"),
        "aadr": _top(data.get("aadr")),
        "kg": _top(data.get("kg")),
        "need_from_user": list(data.get("need_from_user") or [])[:6],
    }


def _compact_gwas_steps(facts: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in facts.get("tool_results") or []:
        name = step.get("tool")
        if name not in {
            "gwas_search", "gwas_trait", "pgs_search", "genome_rs", "profile_micronutrients",
        }:
            continue
        if not step.get("ok"):
            out.append({"tool": name, "ok": False, "error": step.get("error")})
            continue
        data = step.get("data") or {}
        if name == "gwas_trait" and isinstance(data, dict):
            data = _trait_compact(data)
        elif name == "gwas_search" and isinstance(data, dict):
            data = {
                "query": data.get("query"),
                "hits": data.get("hits"),
                "traits": list(data.get("traits") or [])[:10],
            }
        elif name == "pgs_search" and isinstance(data, dict):
            data = {
                "query": data.get("query"),
                "hits": data.get("hits"),
                "models": list(data.get("models") or data.get("rows") or [])[:8],
            }
        out.append({"tool": name, "ok": True, "data": data})
    return out


def lineage_tools_present(facts: dict[str, Any]) -> bool:
    names = {step.get("tool") for step in facts.get("tool_results") or []}
    return bool(names & {
        "y_path", "y_lore", "y_milestones", "aadr_y_hits", "modern_affinity",
    })


def compact_facts_pack(facts: dict[str, Any]) -> dict[str, Any]:
    """Oracle-sized digest: keep all domains, drop raw/redundant blobs."""
    pack: dict[str, Any] = {
        "plan_focus": (facts.get("plan") or {}).get("focus"),
        "need_from_user": list(facts.get("need_from_user") or [])[:12],
        "tools_ok": [
            step.get("tool") for step in facts.get("tool_results") or []
            if step.get("ok")
        ],
        "tools_err": [
            {"tool": step.get("tool"), "error": step.get("error")}
            for step in facts.get("tool_results") or []
            if not step.get("ok")
        ],
    }
    # Put epoch/affinity first so any residual truncation keeps the chronicle spine.
    aadr = _compact_aadr(_tool_data(facts, "aadr_y_hits"))
    if aadr:
        pack["aadr_y_hits"] = aadr
    aff = _compact_affinity(_tool_data(facts, "modern_affinity"))
    if aff:
        pack["modern_affinity"] = aff
    path = _compact_path(_tool_data(facts, "y_path"))
    if path:
        pack["y_path"] = path
    ms = _compact_milestones(_tool_data(facts, "y_milestones"))
    if ms:
        pack["y_milestones"] = ms
    lore = _compact_lore(_tool_data(facts, "y_lore"), include_facts_text=True, max_facts=900)
    if lore:
        pack["y_lore"] = lore
    gwas = _compact_gwas_steps(facts)
    if gwas:
        pack["gwas"] = gwas
    return pack


def _oracle_pack_json(facts: dict[str, Any], *, limit: int = 48_000) -> str:
    pack = compact_facts_pack(facts)
    text = json.dumps(pack, ensure_ascii=False, indent=2, default=str)
    if len(text) > limit:
        # Last resort: drop milestone lore facts_text / shrink milestones further.
        ms = pack.get("y_milestones")
        if isinstance(ms, dict):
            for m in ms.get("milestones") or []:
                if isinstance(m, dict) and isinstance(m.get("lore"), dict):
                    m["lore"].pop("facts_text", None)
            if len(ms.get("milestones") or []) > 6:
                ms["milestones"] = (ms.get("milestones") or [])[:6]
            text = json.dumps(pack, ensure_ascii=False, indent=2, default=str)
        if len(text) > limit:
            text = text[: limit - 20] + "\n…(truncated)"
    return text


def oracle_prompt(question: str, facts: dict[str, Any]) -> str:
    pack = _oracle_pack_json(facts)
    lineage = bool(_LINEAGE_HINTS.search(question or "")) or lineage_tools_present(facts)
    head = [
        "Task: write a scientific Oracle brief for ONE investigated genome.",
        "Third person. Markdown. Commit. No apologies in the body.",
        "Footnote ONLY as final ## Footnote (one sentence).",
        "",
        "User question:",
        question.strip(),
        "",
        "FACTS DIGEST (local catalogs / chip — cite many concrete numbers):",
        pack,
        "",
    ]
    if lineage:
        body = [
            "This is a deep-ancestry / epoch question. Answer with a FULL chronicle,",
            "not a short abstract. Prefer depth and concrete anchors over brevity.",
            "",
            "Required sections:",
            "1) ## Oracle — framing: tip, path length, modern geography (short).",
            "2) ## Epoch chronicle — one #### subsection for each epoch that appears",
            "   in FACTS (Paleolithic / Mesolithic / Neolithic / Bronze / Iron /",
            "   Medieval / Modern as present). In EVERY subsection include when known:",
            "   path clade(s)+formed/TMRCA, AADR groups (name, BP, country, Y relation),",
            "   cultures, religions / Seshat / codex pantheon+ritual. Skip empty epochs.",
            "3) ## Cultures & religions — cross-epoch map from Seshat/codex/geo;",
            "   distinguish measured anchors vs comparative reconstruction.",
            "4) ## Synthesis — most probable Adam→today narrative for THIS genome;",
            "   label hypotheses explicitly.",
            "5) ## Open / next — next measurements (BigY, F6 score, mtDNA, etc.).",
            "6) ## Footnote — one sentence.",
            "",
            "Hard rules: invent no PMIDs/sample sizes; use only FACTS DIGEST;",
            "coverage gaps are findings, not apologies;",
            "when the user asks Adam→today / epochs / cultures / religions,",
            "the epoch chronicle IS the answer — write it fully from the facts.",
        ]
    else:
        body = [
            "Required sections:",
            "1) ## Oracle — 4–8 sentences answering THIS question from the facts.",
            "2) ## Evidence — bullets with the strongest measured rows "
            "(clades/BP/epochs, GWAS dose/p/OR, PGS pct, f3/z as present).",
            "3) ## Synthesis — how domains connect for this question; label hypotheses.",
            "4) ## Open / next — what to measure or query next (labs, F6 score, clade).",
            "5) ## Footnote — one sentence.",
            "",
            "Hard rules: invent no PMIDs/sample sizes; use only FACTS DIGEST;",
            "coverage gaps are findings (e.g. no f3 cache), not apologies;",
            "do not force an Adam→today narrative unless the facts and question support it.",
        ]
    return "\n".join(head + body)


def _dose_hist(assocs: list[dict[str, Any]]) -> dict[str, int]:
    c: Counter[str] = Counter()
    for a in assocs:
        d = a.get("dose")
        c["?" if d is None else str(d)] += 1
    return dict(c)


def _trait_compact(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("assocs") or []
    known = [a for a in rows if a.get("dose") is not None]
    mean = (
        sum(float(a["dose"]) for a in known) / len(known)
        if known else None
    )
    return {
        "trait": payload.get("trait"),
        "exact": payload.get("exact"),
        "assocs_total": payload.get("assocs_total") or len(rows),
        "hist": _dose_hist(rows),
        "mean_dose": None if mean is None else round(mean, 2),
        "top": [
            {
                "rsid": a.get("rsid"),
                "dose": a.get("dose"),
                "you": a.get("you"),
                "risk": a.get("risk"),
                "p": a.get("p"),
                "gene": a.get("gene"),
                "or_beta": a.get("or_beta"),
            }
            for a in rows[:8]
        ],
        "candidates": payload.get("candidates") or [],
    }


def gwas_tool_handlers(
    store: Store,
    *,
    genome: Path | None = None,
    gwas_dir: Path | None = None,
    p_max: float | None = 5e-8,
    use_cache: bool = True,
    job: Job | None = None,
) -> dict[str, ToolHandler]:
    box: dict[str, Any] = {"result": None}

    def match():
        if box["result"] is None:
            if job is not None:
                job.stage = "gwas match"
                job.check()
            box["result"] = inv.ensure_match(
                store, genome=genome, gwas_dir=gwas_dir, p_max=p_max, use_cache=use_cache,
            )
        return box["result"]

    def gwas_search(query: str = "", **_k: Any) -> dict[str, Any]:
        return inv.gwas_search(match(), query, limit=_SEARCH_LIMIT)

    def gwas_trait(trait: str = "", **_k: Any) -> dict[str, Any]:
        return _trait_compact(inv.gwas_trait(match(), trait, limit=_TRAIT_ROW_LIMIT))

    def pgs_search(query: str = "", **_k: Any) -> dict[str, Any]:
        gpath = genome
        if gpath is None and box["result"] is not None:
            gpath = Path(box["result"].genome)
        return inv.pgs_search(query, limit=8, genome=gpath)

    def genome_rs(rsids: list[str] | None = None, **_k: Any) -> dict[str, Any]:
        return inv.genome_rs(store, rsids or [], genome=genome)

    def profile_micronutrients(**_k: Any) -> dict[str, Any]:
        from .profile_micronutrients import build_profile
        micro = build_profile(
            store, genome=genome, gwas_dir=gwas_dir, p_max=p_max, use_cache=use_cache,
        )
        return {
            "nutrients": [
                {
                    "id": n.get("id"),
                    "gwas_assocs": n.get("gwas_assocs"),
                    "gwas_traits": n.get("gwas_traits"),
                    "say": n.get("say"),
                    "direction": n.get("direction"),
                    "confidence": n.get("confidence"),
                }
                for n in (micro.get("nutrients") or [])
            ],
            "oracle_footnote": micro.get("oracle_footnote"),
        }

    return {
        "gwas_search": gwas_search,
        "gwas_trait": gwas_trait,
        "pgs_search": pgs_search,
        "genome_rs": genome_rs,
        "profile_micronutrients": profile_micronutrients,
    }


def gather_facts(
    store: Store,
    plan: dict[str, Any],
    *,
    genome: Path | None = None,
    gwas_dir: Path | None = None,
    p_max: float | None = 5e-8,
    use_cache: bool = True,
    job: Job | None = None,
    handlers: dict[str, ToolHandler] | None = None,
) -> dict[str, Any]:
    """Execute planned tool calls. Missing handlers → recorded errors."""
    def beat(msg: str) -> None:
        if job is not None:
            job.stage = msg
            job.check()

    h = dict(handlers or {})
    # Always ensure GWAS handlers exist unless caller replaced them.
    for name, fn in gwas_tool_handlers(
        store, genome=genome, gwas_dir=gwas_dir, p_max=p_max, use_cache=use_cache, job=job,
    ).items():
        h.setdefault(name, fn)

    results: list[dict[str, Any]] = []
    need: list[str] = []
    for step in plan.get("tools") or []:
        name = str(step.get("tool") or "")
        args = step.get("args") or {}
        beat(f"tool · {name}")
        fn = h.get(name)
        if fn is None:
            results.append({"tool": name, "ok": False, "error": f"unknown_tool:{name}"})
            continue
        try:
            data = fn(**args) if args else fn()
            if isinstance(data, dict):
                need.extend(data.get("need_from_user") or [])
            results.append({"tool": name, "ok": True, "args": args, "data": data})
        except Exception as exc:  # noqa: BLE001
            results.append({"tool": name, "ok": False, "args": args, "error": str(exc)})

    # Dedup need_from_user
    seen_n: set[str] = set()
    need_u = []
    for n in need:
        if n not in seen_n:
            seen_n.add(n)
            need_u.append(n)

    return {
        "plan": plan,
        "tool_results": results,
        "need_from_user": need_u,
    }


def facts_markdown(facts: dict[str, Any]) -> str:
    lines = [
        "### Facts · local (no LLM)",
        f"- Plan focus: {facts.get('plan', {}).get('focus') or '—'}",
        f"- Tools run: {len(facts.get('tool_results') or [])}",
    ]
    for n in facts.get("need_from_user") or []:
        lines.append(f"- need_from_user: {n}")
    for step in facts.get("tool_results") or []:
        name = step.get("tool")
        if not step.get("ok"):
            lines.append(f"- `{name}` ERROR: {step.get('error')}")
            continue
        data = step.get("data") or {}
        summary = ""
        if name == "y_path":
            summary = f"tip={data.get('tip')} n={data.get('n_path')}"
        elif name == "y_milestones":
            summary = f"n={data.get('n_milestones')} tip={data.get('tip')}"
        elif name == "aadr_y_hits":
            summary = f"group_hits={data.get('n_group_hits')}"
        elif name == "modern_affinity":
            summary = (
                f"aadr={'yes' if data.get('aadr') else 'no'} "
                f"kg={'yes' if data.get('kg') else 'no'}"
            )
        elif name == "gwas_search":
            summary = f"query={data.get('query')!r} hits={data.get('hits')}"
        elif name == "gwas_trait":
            summary = f"trait={data.get('trait')!r} N={data.get('assocs_total')}"
        else:
            summary = f"keys={list(data)[:6]}"
        lines.append(f"- `{name}` ok · {summary}")
    lines.append("")
    pack = compact_facts_pack(facts)
    lines.append(
        f"- Digest chars: {len(json.dumps(pack, ensure_ascii=False, default=str))}"
    )
    lines.append("")
    lines.append("```json")
    blob = json.dumps(pack, ensure_ascii=False, indent=2, default=str)
    if len(blob) > 18_000:
        blob = blob[:18_000] + "\n…(truncated for display)"
    lines.append(blob)
    lines.append("```")
    return "\n".join(lines)


def run_investigate(
    store: Store,
    thread: chat.Thread,
    job: Job,
    question: str,
    *,
    genome: Path | None = None,
    gwas_dir: Path | None = None,
    p_max: float | None = 5e-8,
    use_cache: bool = True,
    handlers: dict[str, ToolHandler] | None = None,
    with_story: bool = True,
    story_language: str | None = None,
) -> dict[str, Any]:
    """Flash plan → local tools → Argon Oracle → optional human Story. Mutates ``thread``."""
    q = (question or "").strip()
    if not q:
        raise RuntimeError("empty_query")

    if not thread.messages or thread.messages[-1].role != "user" or thread.messages[-1].text.strip() != q:
        thread.messages.append(chat.Message("user", q))
        if thread.title == "New chat":
            thread.title = q.splitlines()[0][:48]
        chat.save(thread)

    # Callers (CLI / F7) should inject lineage handlers; GWAS handlers merge in gather.
    h = dict(handlers or {})
    available = sorted(set(h) | {
        "gwas_search", "gwas_trait", "pgs_search", "genome_rs", "profile_micronutrients",
        "y_path", "y_lore", "y_milestones", "aadr_y_hits", "modern_affinity",
    })
    job.stage = "flash · plan"
    plan_raw = chat.send(
        store, thread, job,
        continue_chat=False,
        tier="fast",
        use_saved=False,
        input_text=plan_prompt(q, available_tools=available),
        append=False,
    )
    try:
        plan = normalize_plan(_extract_json(plan_raw), question=q)
    except Exception:
        plan = normalize_plan({}, question=q)

    plan_md = (
        f"### Plan · {chat.short_model(chat.resolve_model_for_send(store, tier='fast', use_saved=False))}\n"
        f"- Focus: {plan['focus']}\n"
        f"- Tools: {', '.join(t['tool'] for t in plan['tools']) or '—'}\n\n"
        f"```json\n{json.dumps(plan, ensure_ascii=False, indent=2)}\n```"
    )
    thread.messages.append(chat.Message("model", plan_md))
    chat.save(thread)
    job.draft = plan_md

    facts = gather_facts(
        store, plan,
        genome=genome, gwas_dir=gwas_dir, p_max=p_max, use_cache=use_cache,
        job=job, handlers=h,
    )
    facts_md = facts_markdown(facts)
    thread.messages.append(chat.Message("model", facts_md))
    chat.save(thread)
    job.draft = facts_md

    job.stage = "argon · oracle"
    oracle = chat.send(
        store, thread, job,
        continue_chat=False,
        tier="reasoning",
        use_saved=False,
        input_text=oracle_prompt(q, facts),
        append=False,
    )
    mid = chat.short_model(
        chat.resolve_model_for_send(store, tier="reasoning", use_saved=False)
    )
    oracle_md = f"### Oracle · {mid}\n\n{oracle.strip()}"
    # Keep Studio interaction id on the Oracle bubble so F7 ▶ can continue;
    # Vertex / missing id → send() bridges this text into the next prompt.
    iid = getattr(job, "_last_interaction_id", None)
    thread.messages.append(chat.Message("model", oracle_md, interaction_id=iid))
    if iid:
        thread.interaction_id = iid
    chat.save(thread)
    job.draft = oracle_md

    story = None
    if with_story:
        from .story_brief import run_story
        story = run_story(
            store, thread, job,
            question=q, oracle=oracle, facts=facts,
            language=story_language,
        )
    return {"plan": plan, "facts": facts, "oracle": oracle, "story": story}
