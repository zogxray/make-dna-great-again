"""Dual-tier Investigate: plan normalize + JSON extract (no live LLM)."""

from __future__ import annotations

from ydna.core.investigate_brief import (
    _extract_json, compact_facts_pack, facts_markdown, normalize_plan, oracle_prompt,
)
from ydna.core.lineage_query import pick_milestones


def test_extract_json_fence():
    raw = "here\n```json\n{\"tools\": [{\"tool\": \"y_path\", \"args\": {}}]}\n```\n"
    assert _extract_json(raw)["tools"][0]["tool"] == "y_path"


def test_normalize_plan_lineage_heuristic():
    plan = normalize_plan({}, question="родословная от Адама по эпохам и культурам")
    names = [t["tool"] for t in plan["tools"]]
    assert "y_path" in names
    assert "y_milestones" in names or "aadr_y_hits" in names
    assert "gwas_search" not in names


def test_normalize_plan_tool_list():
    plan = normalize_plan({
        "focus": "x",
        "tools": [
            {"tool": "y_path", "args": {}},
            {"tool": "gwas_search", "args": {"query": "anxiety"}},
            {"tool": "y_path", "args": {}},
        ],
    }, question="hi")
    assert plan["focus"] == "x"
    assert len(plan["tools"]) == 2


def test_normalize_plan_legacy_gwas_fields():
    plan = normalize_plan({
        "gwas_searches": ["magnesium", "Magnesium"],
        "include_micronutrients": True,
    }, question="vitamins")
    names = [t["tool"] for t in plan["tools"]]
    assert "gwas_search" in names
    assert "profile_micronutrients" in names


def test_facts_markdown_tool_results():
    md = facts_markdown({
        "plan": {"focus": "test", "tools": []},
        "need_from_user": ["score F6"],
        "tool_results": [
            {"tool": "y_path", "ok": True, "data": {"tip": "J-X", "n_path": 40}},
            {"tool": "gwas_search", "ok": False, "error": "no_gwas"},
        ],
    })
    assert "y_path" in md
    assert "ERROR" in md
    assert "score F6" in md


def test_pick_milestones_keeps_ends():
    path = [f"N{i}" for i in range(30)]
    ms = pick_milestones(path, {}, limit=8)
    assert ms[0] == "N0"
    assert ms[-1] == "N29"
    assert len(ms) <= 8


def test_compact_facts_pack_keeps_all_domains():
    huge_lore = {
        "node": "J-X",
        "formed": "1 BCE",
        "tmrca": "1 CE",
        "epoch": "Iron",
        "culture_top": "Test",
        "cultures": ["A"],
        "aadr_lines": ["anchor"],
        "seshat_lines": ["polity"],
        "children_geo": [{"code": "UA", "count": 1}],
        "parent_line": "parent",
        "codex": {
            "key": "x", "name": "X", "period": "Iron", "region": "Y",
            "hard": ["h1"], "gods": [{"name": "G", "domain": "d", "epithet": "e"}],
            "ritual": {"title": "t", "scene": "s"}, "note": "n",
        },
        "facts_text": "x" * 5000,
        "raw": {"noise": "y" * 20_000},
    }
    facts = {
        "plan": {"focus": "adam"},
        "need_from_user": [],
        "tool_results": [
            {
                "tool": "y_path", "ok": True,
                "data": {
                    "tip": "J-X", "n_path": 2,
                    "path_root_to_tip": ["A", "J-X"],
                    "steps": [
                        {"clade": "J-X", "depth_from_tip": 0, "formed": 1, "tmrca": 2, "der": 1, "anc": 0},
                    ],
                },
            },
            {
                "tool": "y_milestones", "ok": True,
                "data": {
                    "tip": "J-X", "n_milestones": 1,
                    "milestones": [{"clade": "J-X", "lore": huge_lore}],
                },
            },
            {
                "tool": "aadr_y_hits", "ok": True,
                "data": {
                    "tip": "J-X", "n_group_hits": 3,
                    "epochs": [{
                        "epoch": "bronze", "label": "Bronze", "hits": 1,
                        "groups": [{"group": "G1", "relation": "same", "y": "J", "bp": 3500,
                                    "country": "TR", "locality": "X", "n": 2}],
                    }],
                },
            },
            {
                "tool": "modern_affinity", "ok": True,
                "data": {
                    "aadr": {"source": "aadr_f3_cache", "n": 1, "top": [{"name": "Pop", "z": 3.0}]},
                    "kg": None,
                    "need_from_user": [],
                },
            },
        ],
    }
    pack = compact_facts_pack(facts)
    blob = __import__("json").dumps(pack)
    assert "aadr_y_hits" in pack
    assert pack["aadr_y_hits"]["epochs"][0]["groups"][0]["group"] == "G1"
    assert pack["modern_affinity"]["aadr"]["top"][0]["name"] == "Pop"
    tip_lore = pack["y_milestones"]["milestones"][0]["lore"] or {}
    assert "raw" not in tip_lore
    assert len(tip_lore.get("facts_text") or "") <= 700
    assert len(blob) < 20_000

    prompt = oracle_prompt("родословная от Адама по эпохам и религиям", facts)
    assert "Epoch chronicle" in prompt
    assert "aadr_y_hits" in prompt
    assert "modern_affinity" in prompt
    assert "4–8 sentences" not in prompt
    assert "…(truncated)" not in prompt
