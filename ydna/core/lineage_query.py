"""Pure lineage/ancestry query helpers (no Textual, no workspaces).

Session loading (Discover tree + YLink) lives in ``ydna.cli.lineage_session``;
tree JSON lives in ``ydna.core.tree_data``. This module serializes and filters.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .aadr import EPOCH_LABEL, EPOCHS, Catalog, epoch_of, y_hits
from .lore import LoreBrief
from .ylink import Haplotree, YBranch


def media_from_node(node: dict[str, Any] | None, *, limit: int = 5) -> list[dict[str, Any]]:
    """Discover ancient / background image URLs when present (no invented links)."""
    if not node:
        return []
    out: list[dict[str, Any]] = []
    for a in node.get("ancient") or []:
        if not isinstance(a, dict):
            continue
        url = a.get("image_url") or a.get("image") or a.get("photo_url")
        if not url or not str(url).startswith(("http://", "https://")):
            continue
        caption = (
            a.get("name") or a.get("id") or a.get("culture")
            or a.get("site") or "ancient sample"
        )
        out.append({
            "source": "discover_ancient",
            "url": str(url),
            "caption": str(caption)[:120],
            "clade": node.get("name"),
        })
        if len(out) >= limit:
            break
    return out


def commons_search_url(query: str) -> str:
    from urllib.parse import quote
    q = quote((query or "").strip()[:80])
    return (
        "https://commons.wikimedia.org/w/index.php?"
        f"search={q}&title=Special:MediaSearch&type=image"
    )


def lore_to_dict(
    brief: LoreBrief,
    *,
    node: dict[str, Any] | None = None,
) -> dict[str, Any]:
    codex = None
    if brief.codex is not None:
        c = brief.codex
        codex = {
            "key": c.key,
            "name": c.name,
            "period": c.period,
            "region": c.region,
            "hard": list(c.hard)[:8],
            "gods": [
                {"name": g.name, "domain": g.domain, "epithet": g.epithet}
                for g in (c.gods or [])[:8]
            ],
            "ritual": {
                "title": c.ritual.title,
                "scene": c.ritual.scene,
            } if c.ritual else None,
            "note": c.note,
        }
    media = media_from_node(node)
    # Fallback cues: Commons search (labeled search, not a specific photo claim).
    if len(media) < 3 and brief.culture_top:
        media.append({
            "source": "commons_search",
            "url": commons_search_url(str(brief.culture_top)),
            "caption": f"Wikimedia search · {brief.culture_top}",
            "clade": brief.node,
        })
    if len(media) < 4 and brief.codex is not None:
        media.append({
            "source": "commons_search",
            "url": commons_search_url(brief.codex.name),
            "caption": f"Wikimedia search · {brief.codex.name}",
            "clade": brief.node,
        })
    return {
        "node": brief.node,
        "genome": brief.genome,
        "formed": brief.formed,
        "tmrca": brief.tmrca,
        "epoch": brief.epoch,
        "culture_top": brief.culture_top,
        "cultures": list(brief.cultures)[:12],
        "aadr_lines": list(brief.aadr_lines)[:12],
        "seshat_lines": list(brief.seshat_lines)[:8],
        "children_geo": [
            {"code": g.code, "count": g.count} for g in brief.children_geo[:10]
        ],
        "parent_line": brief.parent_line,
        "codex": codex,
        "media": media[:5],
        "facts_text": brief.weave_facts(),
        "raw": brief.raw,
    }


def path_from_branch(
    branch: YBranch,
    nodes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize Our Y tip→root (branch.path is root→tip)."""
    path = list(branch.path or [])
    tip_to_root = list(reversed(path))
    rows = []
    for i, name in enumerate(tip_to_root):
        node = (nodes or {}).get(name) or {}
        formed = node.get("formed")
        tmrca = node.get("tmrca")
        if isinstance(formed, dict):
            formed = formed.get("mean")
        if isinstance(tmrca, dict):
            tmrca = tmrca.get("mean")
        rows.append({
            "clade": name,
            "depth_from_tip": i,
            "formed": formed,
            "tmrca": tmrca,
            "der": branch.der.get(name, 0),
            "anc": branch.anc.get(name, 0),
            "on_path": True,
        })
    return {
        "tip": branch.tip,
        "n_path": len(path),
        "n_candidates": branch.n_candidates,
        "tie": branch.tie_label or None,
        "path_root_to_tip": path,
        "path_tip_to_root": tip_to_root,
        "steps": rows,
    }


def pick_milestones(
    path_tip_to_root: list[str],
    nodes: dict[str, Any] | None = None,
    *,
    limit: int = 12,
) -> list[str]:
    """Subsample a long tip→root path: tip, root, and age/name breakpoints."""
    if not path_tip_to_root:
        return []
    if len(path_tip_to_root) <= limit:
        return list(path_tip_to_root)

    chosen: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name and name not in seen:
            seen.add(name)
            chosen.append(name)

    add(path_tip_to_root[0])  # tip
    add(path_tip_to_root[-1])  # root / Adam side

    # Prefer nodes with Discover ages when present.
    aged: list[tuple[float, str]] = []
    for name in path_tip_to_root:
        node = (nodes or {}).get(name) or {}
        tm = node.get("tmrca")
        if isinstance(tm, dict):
            tm = tm.get("mean")
        try:
            if tm is not None:
                aged.append((float(tm), name))
        except (TypeError, ValueError):
            continue
    aged.sort()
    if aged:
        step = max(1, len(aged) // max(1, limit - 2))
        for _, name in aged[::step]:
            add(name)
            if len(chosen) >= limit:
                break

    # Uniform fill
    if len(chosen) < limit:
        step = max(1, len(path_tip_to_root) // limit)
        for name in path_tip_to_root[::step]:
            add(name)
            if len(chosen) >= limit:
                break

    # Preserve tip→root order
    order = {n: i for i, n in enumerate(path_tip_to_root)}
    return sorted(chosen, key=lambda n: order.get(n, 10**9))[:limit]


def aadr_hits_by_epoch(
    catalog: Catalog,
    tree: Haplotree,
    branch: YBranch,
    *,
    epochs: list[str] | None = None,
    limit_per_epoch: int = 8,
    relations: tuple[str, ...] = ("same", "ancestor", "downstream"),
) -> dict[str, Any]:
    groups, _people = y_hits(catalog, tree, None, branch)
    want = set(epochs) if epochs else {k for k, _, _ in EPOCHS}
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for gname, hit in groups.items():
        if hit.relation not in relations:
            continue
        g = catalog.groups.get(gname)
        if g is None:
            continue
        ep = epoch_of(g.bp)
        if ep not in want:
            continue
        buckets[ep].append({
            "group": gname,
            "relation": hit.relation,
            "y": hit.y,
            "bp": g.bp,
            "epoch": ep,
            "epoch_label": EPOCH_LABEL.get(ep, ep),
            "country": g.country,
            "locality": g.locality,
            "n": g.n,
        })

    out_epochs = []
    for key, label, _ in EPOCHS:
        if key not in want:
            continue
        rows = buckets.get(key) or []
        rows.sort(key=lambda r: (
            {"same": 0, "ancestor": 1, "downstream": 2}.get(r["relation"], 9),
            -(r["bp"] or 0),
            r["group"],
        ))
        out_epochs.append({
            "epoch": key,
            "label": label,
            "hits": len(rows),
            "groups": rows[:limit_per_epoch],
        })
    return {
        "tip": branch.tip,
        "n_group_hits": len(groups),
        "epochs": out_epochs,
    }


def affinity_top(
    rows: list[Any],
    *,
    limit: int = 15,
    source: str,
) -> dict[str, Any]:
    """Serialize cached f3-like rows (objects with name/est/z/snps)."""
    scored = []
    for r in rows:
        name = getattr(r, "name", None) or (r.get("name") if isinstance(r, dict) else None)
        if not name:
            continue
        est = getattr(r, "est", None)
        z = getattr(r, "z", None)
        snps = getattr(r, "snps", None)
        if isinstance(r, dict):
            est = r.get("est", est)
            z = r.get("z", z)
            snps = r.get("snps", snps)
        scored.append({"name": name, "est": est, "z": z, "snps": snps})
    scored.sort(key=lambda x: (x["z"] is None, -(x["z"] or 0), x["name"]))
    return {"source": source, "n": len(scored), "top": scored[:limit]}
