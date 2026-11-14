"""Headless Y / AADR / 1000G session for Investigate + CLI/MCP atoms.

Uses ``core.tree_data`` (UI-free) so ``cli ↛ workspaces`` stays intact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from ..core import aadr as aadr_mod
from ..core import genome_db
from ..core import kg as kg_mod
from ..core import lineage_query as lq
from ..core import lore as lore_mod
from ..core import ylink
from ..core.jobs import Job
from ..core.store import Store
from ..core.tree_data import graft_path, hydrate_node, load_or_build, relink_children


@dataclass
class LineageSession:
    store: Store
    genome: Path
    tree: ylink.Haplotree
    link: ylink.YLink
    branch: ylink.YBranch
    ctx: Any  # duck-typed for lore.build_lore (nodes/children/tip/ours/store)
    discover: Path | None = None
    aadr_catalog: aadr_mod.Catalog | None = None
    _need: list[str] = field(default_factory=list)

    @property
    def need_from_user(self) -> list[str]:
        return list(self._need)


def _haplotree_path(store: Store) -> Path:
    p = store.path("ftdna_haplotree")
    if p is None or not Path(p).is_file():
        raise RuntimeError("no_haplotree: set FTDNA Haplotree in F2")
    return Path(p)


def _discover_path(store: Store) -> Path | None:
    p = store.path("ftdna_discover")
    if p is None:
        return None
    path = Path(p)
    return path if path.is_dir() else None


def open_session(
    store: Store,
    *,
    genome: Path | None = None,
    job: Job | None = None,
) -> LineageSession:
    def beat(msg: str) -> None:
        if job is not None:
            job.stage = msg
            job.check()

    path = genome or genome_db.active_path()
    if path is None:
        raise RuntimeError("no_genome")
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"no_genome:{path}")

    beat("haplotree")
    ht = _haplotree_path(store)
    disc = _discover_path(store)
    tree = ylink.load_haplotree(ht)

    beat("ylink")
    g = genome_db.get(path, store=store, set_active_flag=False)
    link = ylink.link_genome(g, store)
    if isinstance(link, str):
        raise RuntimeError(f"no_ylink:{link}")
    if link is None:
        raise RuntimeError("no_ylink")

    beat("our_branch")
    branch = ylink.our_branch(link, tree, g, disc)
    if branch is None:
        raise RuntimeError("no_y_branch: insufficient derived Y markers on haplotree")

    beat("tree_cache")
    def prog(stage: str, done: int = 0, total: int = 1) -> None:
        beat(f"tree · {stage}")

    data, _cached = load_or_build(ht, disc, prog)
    nodes = data["nodes"]
    children = data.get("children") or {}
    stats = data.get("stats") or {}
    graft_path(nodes, tree, branch.path)
    children, _roots = relink_children(nodes)
    ctx = SimpleNamespace(
        nodes=nodes,
        children=children,
        folder=disc or Path("."),
        stats=stats,
        store=store,
        tip=branch.tip,
        ours=set(branch.path),
        ybranch=branch,
    )

    # Hydrate Discover cards along the path for lore/ancients.
    if disc is not None:
        for name in branch.path:
            node = nodes.get(name)
            if node:
                hydrate_node(node, disc)

    need: list[str] = []
    if disc is None:
        need.append("FTDNA Discover folder missing — lore/ancients will be thin (F2).")

    return LineageSession(
        store=store, genome=path, tree=tree, link=link, branch=branch,
        ctx=ctx, discover=disc, _need=need,
    )


def y_path(session: LineageSession) -> dict[str, Any]:
    return lq.path_from_branch(session.branch, session.ctx.nodes)


def y_lore(session: LineageSession, clade: str | None = None) -> dict[str, Any]:
    name = (clade or session.branch.tip or "").strip()
    if not name:
        raise RuntimeError("empty_query")
    node = session.ctx.nodes.get(name)
    if node is None:
        raise RuntimeError(f"no_clade:{name}")
    if session.discover is not None:
        hydrate_node(node, session.discover)
    brief = lore_mod.build_lore(session.ctx, node)
    return lq.lore_to_dict(brief, node=node)


def y_milestones(
    session: LineageSession,
    *,
    limit: int = 12,
    with_lore: bool = True,
) -> dict[str, Any]:
    path = y_path(session)
    names = lq.pick_milestones(
        path["path_tip_to_root"], session.ctx.nodes, limit=limit,
    )
    rows = []
    for name in names:
        entry: dict[str, Any] = {"clade": name}
        if with_lore:
            try:
                entry["lore"] = y_lore(session, name)
            except RuntimeError as exc:
                entry["error"] = str(exc)
        rows.append(entry)
    return {
        "tip": session.branch.tip,
        "n_milestones": len(rows),
        "milestones": rows,
        "need_from_user": session.need_from_user,
    }


def _ensure_aadr(session: LineageSession, job: Job | None = None) -> aadr_mod.Catalog:
    if session.aadr_catalog is not None:
        return session.aadr_catalog
    folder = session.store.path("aadr")
    if folder is None or not Path(folder).is_dir():
        raise RuntimeError("no_aadr: set AADR in F2")
    if job is not None:
        job.stage = "aadr catalog"
        job.check()
    session.aadr_catalog = aadr_mod.load_catalog(Path(folder), job=job)
    return session.aadr_catalog


def aadr_y_hits(
    session: LineageSession,
    *,
    epochs: list[str] | None = None,
    limit_per_epoch: int = 8,
    job: Job | None = None,
) -> dict[str, Any]:
    catalog = _ensure_aadr(session, job=job)
    return lq.aadr_hits_by_epoch(
        catalog, session.tree, session.branch,
        epochs=epochs, limit_per_epoch=limit_per_epoch,
    )


def modern_affinity(
    session: LineageSession,
    *,
    limit: int = 15,
) -> dict[str, Any]:
    """Cached f3 only — never starts a long score job."""
    out: dict[str, Any] = {"genome": str(session.genome), "need_from_user": []}
    aadr_b = aadr_mod.load_bundle(session.genome)
    if aadr_b.groups:
        out["aadr"] = lq.affinity_top(
            list(aadr_b.groups.values()), limit=limit, source="aadr_f3_cache",
        )
    else:
        out["aadr"] = None
        out["need_from_user"].append(
            "No AADR f3 cache — run F6 score for modern/ancient affinity.",
        )

    kg_b = kg_mod.load_bundle(session.genome)
    if kg_b.groups:
        out["kg"] = lq.affinity_top(
            list(kg_b.groups.values()), limit=limit, source="1000g_f3_cache",
        )
    else:
        out["kg"] = None
        out["need_from_user"].append("No 1000G f3 cache — run F6 (G) score.")
    return out


def local_ref(session: LineageSession) -> dict[str, Any]:
    """Our mt tip, UYSD (Y tip), AmtDB parallels, ancIBD index presence."""
    from ..core import ancibd as ancibd_mod
    from ..core import mtdna as mtdna_mod
    from ..core import uysd as uysd_mod

    out: dict[str, Any] = {"need_from_user": []}
    g = genome_db.get(session.genome, store=session.store, set_active_flag=False)
    tip = mtdna_mod.tip_from_genome(g, session.store) if g is not None else None
    if tip:
        out["our_mt"] = {"tip": tip[0], "score": round(float(tip[1]), 3)}
        amt = mtdna_mod.load_amtdb(session.store)
        if amt is not None:
            out["amtdb"] = [
                {
                    "id": s.identifier,
                    "mt": s.mt_hg,
                    "culture": s.culture,
                    "country": s.country,
                    "epoch": s.epoch,
                    "year_from": s.year_from,
                    "year_to": s.year_to,
                }
                for s in amt.match(tip[0], limit=8)
            ]
        else:
            out["amtdb"] = None
            out["need_from_user"].append("AmtDB missing — F2 amtdb · D")
    else:
        out["our_mt"] = None
        out["amtdb"] = None
        out["need_from_user"].append(
            "Our mt tip unavailable — F2 PhyloTree + F1 MT calls on kit"
        )

    ytip = session.branch.tip
    idx = uysd_mod.load_index(session.store)
    if idx is not None and ytip:
        rows = idx.frequencies(ytip, limit=8, fetch=False)
        out["uysd"] = {
            "haplogroup": ytip,
            "top": [{"label": r.label, "pct": r.pct, "n": r.n} for r in rows],
        }
        if not rows:
            out["need_from_user"].append(
                f"UYSD cache empty for {ytip} — open F6 O % once to fetch"
            )
    else:
        out["uysd"] = None
        if idx is None:
            out["need_from_user"].append("UYSD missing — F2 uysd · D")

    aidx = ancibd_mod.load_index(session.store)
    if aidx is not None:
        out["ancibd"] = {"ready": True, "n_ids": len(aidx.neighbors)}
    else:
        out["ancibd"] = {"ready": False, "n_ids": 0}
        out["need_from_user"].append("ancIBD index missing — F2 ancibd · D")
    return out


def lineage_tool_handlers(
    store: Store,
    *,
    genome: Path | None = None,
    job: Job | None = None,
) -> dict[str, Callable[..., dict[str, Any]]]:
    """Build Investigate tool handlers bound to one lazy session."""
    box: dict[str, LineageSession | None] = {"s": None}

    def session() -> LineageSession:
        if box["s"] is None:
            box["s"] = open_session(store, genome=genome, job=job)
        return box["s"]

    def tool_y_path(**_kwargs: Any) -> dict[str, Any]:
        return y_path(session())

    def tool_y_lore(clade: str | None = None, **_kwargs: Any) -> dict[str, Any]:
        return y_lore(session(), clade)

    def tool_y_milestones(
        limit: int = 12, with_lore: bool = True, **_kwargs: Any,
    ) -> dict[str, Any]:
        return y_milestones(session(), limit=int(limit), with_lore=bool(with_lore))

    def tool_aadr_y_hits(
        epochs: list[str] | None = None,
        limit_per_epoch: int = 8,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return aadr_y_hits(
            session(), epochs=epochs, limit_per_epoch=int(limit_per_epoch), job=job,
        )

    def tool_modern_affinity(limit: int = 15, **_kwargs: Any) -> dict[str, Any]:
        return modern_affinity(session(), limit=int(limit))

    def tool_local_ref(**_kwargs: Any) -> dict[str, Any]:
        return local_ref(session())

    return {
        "y_path": tool_y_path,
        "y_lore": tool_y_lore,
        "y_milestones": tool_y_milestones,
        "aadr_y_hits": tool_aadr_y_hits,
        "modern_affinity": tool_modern_affinity,
        "local_ref": tool_local_ref,
    }
