"""Domain ops shared by CLI and MCP — return plain dicts, raise CliError."""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .jsonio import EXIT_MISSING, CliError
from .session import cli_session


def _pkg_version() -> str:
    try:
        return version("make-dna-great-again")
    except PackageNotFoundError:
        return "0.0.0+unknown"


def status() -> dict[str, Any]:
    with cli_session(take_lock=True) as store:
        from ..core import genome_db, sources_db
        from ..core.single import lock_path
        from ..core.store import cache_dir, config_dir, data_dir

        sources = []
        for row in sources_db.list_sources():
            path = row.get("path")
            sources.append({
                "key": row["key"],
                "title": row["title"],
                "state": row.get("state"),
                "path": str(path) if path else None,
                "path_exists": bool(path and path.exists()),
                "summary": row.get("summary") or None,
            })

        files = genome_db.list_files()
        active = genome_db.active_path()
        return {
            "version": _pkg_version(),
            "config_dir": str(config_dir()),
            "cache_dir": str(cache_dir()),
            "data_dir": str(data_dir()),
            "lock": {
                "path": str(lock_path()),
                "held_by": "cli",
                "pid": os.getpid(),
            },
            "genome": {
                "active": str(active) if active else None,
                "files": [
                    {
                        "path": str(f["path"]),
                        "status": f["status"],
                        "snps": f["snps"],
                        "fmt": f["fmt"],
                        "build_header": f["build_header"],
                        "build_majority": f["build_majority"],
                        "active": f["active"],
                    }
                    for f in files
                ],
            },
            "sources": sources,
        }


def genome_summary(*, genome: str | Path | None = None, ingest: bool = False) -> dict[str, Any]:
    with cli_session() as store:
        from ..core import genome_db

        path = Path(genome).expanduser() if genome else None
        if path is None:
            path = genome_db.active_path()
        if path is None:
            raise CliError(
                "no_genome",
                "No genome path and no active F1 genome in the store.",
                exit_code=EXIT_MISSING,
            )
        path = path.resolve()
        if not path.is_file():
            raise CliError(
                "no_genome",
                f"Genome file not found: {path}",
                exit_code=EXIT_MISSING,
            )

        if ingest:
            g = genome_db.ensure(path, store=store, set_active_flag=False)
        else:
            g = genome_db.ensure_meta(path, set_active_flag=False)

        row = genome_db.file_row(path)
        chroms = [
            {
                "chrom": name,
                "snps": c.n,
                "calls_loaded": c.calls_loaded,
                "build": g.chrom_build(name),
            }
            for name, c in g.chroms.items()
        ]
        return {
            "path": str(path),
            "fmt": g.fmt,
            "layout": g.layout,
            "member": g.member,
            "build_header": g.build,
            "build_detected": g.build_detected,
            "build_label": g.build_label(),
            "snps_total": g.total,
            "malformed": g.malformed,
            "chroms": chroms,
            "db": None if row is None else {
                "status": row[10],
                "snps": row[11],
                "active": bool(row[9]),
                "ingested_at": row[13],
                "error": row[14],
            },
        }


def gwas_match(
    *,
    genome: str | Path | None = None,
    gwas_dir: str | Path | None = None,
    p_max: float | None = 5e-8,
    use_cache: bool = True,
) -> dict[str, Any]:
    with cli_session() as store:
        from ..core import genome_db, gwas_match as gm

        path = Path(genome).expanduser() if genome else None
        if path is None:
            path = genome_db.active_path()
        if path is None:
            raise CliError(
                "no_genome",
                "No genome path and no active F1 genome in the store.",
                exit_code=EXIT_MISSING,
            )
        path = path.resolve()
        if not path.is_file():
            raise CliError(
                "no_genome",
                f"Genome file not found: {path}",
                exit_code=EXIT_MISSING,
            )

        folder = Path(gwas_dir).expanduser() if gwas_dir else store.path("gwas_catalog")
        if folder is None or not Path(folder).is_dir():
            raise CliError(
                "no_gwas",
                "GWAS Catalog folder missing — pass gwas_dir or set it in F2.",
                exit_code=EXIT_MISSING,
            )
        folder = Path(folder).resolve()

        g = genome_db.get(path, store=store, set_active_flag=False)
        result = gm.match_genome(
            folder, g, p_max=p_max, job=None, use_cache=use_cache,
        )
        return {
            "genome": result.genome,
            "catalog": result.catalog,
            "catalog_date": result.catalog_date,
            "genome_rs": result.genome_rs,
            "catalog_rs": result.catalog_rs,
            "matched_rs": result.matched_rs,
            "traits_hit": result.traits_hit,
            "p_max": result.p_max,
            "chip_pct": result.chip_pct,
            "trait_counts": result.trait_counts,
            "assocs": [
                {
                    "rsid": a.rsid, "you": a.you, "risk": a.risk, "p": a.p,
                    "or_beta": a.or_beta, "trait": a.trait,
                    "mapped_trait": a.mapped_trait, "gene": a.gene,
                    "chrom": a.chrom, "pos": a.pos, "study": a.study,
                    "pubmed": a.pubmed, "author": a.author, "date": a.date,
                }
                for a in result.assocs
            ],
        }


def envelope(data: dict[str, Any] | None = None, *, error: CliError | None = None) -> dict[str, Any]:
    if error is not None:
        return {"ok": False, "error": {"code": error.code, "message": error.message}}
    return {"ok": True, "data": data}


def _map_runtime(exc: RuntimeError) -> CliError:
    msg = str(exc)
    if msg.startswith("no_genome") or msg == "no_genome":
        return CliError("no_genome", msg, exit_code=EXIT_MISSING)
    if msg.startswith("no_gwas"):
        return CliError("no_gwas", msg, exit_code=EXIT_MISSING)
    if msg.startswith("no_haplotree") or msg.startswith("no_ylink") or msg.startswith("no_y_branch"):
        return CliError("no_y", msg, exit_code=EXIT_MISSING)
    if msg.startswith("no_aadr"):
        return CliError("no_aadr", msg, exit_code=EXIT_MISSING)
    if msg.startswith("no_clade"):
        return CliError("no_clade", msg, exit_code=EXIT_MISSING)
    if msg in ("empty_query",) or msg.startswith("empty_query"):
        return CliError("usage", "empty query", exit_code=2)
    if msg.startswith("no_oracle") or msg.startswith("no_thread"):
        return CliError("no_oracle", msg, exit_code=EXIT_MISSING)
    return CliError("internal", msg)


def profile_micronutrients(
    *,
    genome: str | Path | None = None,
    gwas_dir: str | Path | None = None,
    p_max: float | None = 5e-8,
    use_cache: bool = True,
    age: int | None = None,
    labs: dict[str, float] | None = None,
) -> dict[str, Any]:
    """GWAS∩vitamin pack (+ PGS attach when available). Raises CliError."""
    with cli_session() as store:
        from ..core.profile_micronutrients import build_profile

        try:
            return build_profile(
                store,
                genome=Path(genome) if genome else None,
                gwas_dir=Path(gwas_dir) if gwas_dir else None,
                p_max=p_max,
                use_cache=use_cache,
                age=age,
                labs=labs,
            )
        except RuntimeError as exc:
            raise _map_runtime(exc) from exc


def gwas_search(
    query: str,
    *,
    genome: str | Path | None = None,
    gwas_dir: str | Path | None = None,
    p_max: float | None = 5e-8,
    use_cache: bool = True,
    limit: int = 40,
) -> dict[str, Any]:
    with cli_session() as store:
        from ..core import investigate as inv

        try:
            result = inv.ensure_match(
                store,
                genome=Path(genome) if genome else None,
                gwas_dir=Path(gwas_dir) if gwas_dir else None,
                p_max=p_max,
                use_cache=use_cache,
            )
            return inv.gwas_search(result, query, limit=limit)
        except RuntimeError as exc:
            raise _map_runtime(exc) from exc


def gwas_trait(
    trait: str,
    *,
    genome: str | Path | None = None,
    gwas_dir: str | Path | None = None,
    p_max: float | None = 5e-8,
    use_cache: bool = True,
    limit: int = 80,
) -> dict[str, Any]:
    with cli_session() as store:
        from ..core import investigate as inv

        try:
            result = inv.ensure_match(
                store,
                genome=Path(genome) if genome else None,
                gwas_dir=Path(gwas_dir) if gwas_dir else None,
                p_max=p_max,
                use_cache=use_cache,
            )
            return inv.gwas_trait(result, trait, limit=limit)
        except RuntimeError as exc:
            raise _map_runtime(exc) from exc


def pgs_search(
    query: str,
    *,
    genome: str | Path | None = None,
    limit: int = 40,
) -> dict[str, Any]:
    with cli_session() as store:
        from ..core import investigate as inv

        try:
            return inv.pgs_search(
                query,
                limit=limit,
                genome=Path(genome) if genome else None,
            )
        except RuntimeError as exc:
            raise _map_runtime(exc) from exc


def genome_rs(
    rsids: list[str],
    *,
    genome: str | Path | None = None,
) -> dict[str, Any]:
    with cli_session() as store:
        from ..core import investigate as inv

        try:
            return inv.genome_rs(
                store,
                rsids,
                genome=Path(genome) if genome else None,
            )
        except RuntimeError as exc:
            raise _map_runtime(exc) from exc


def _lineage_handlers(store, *, genome: Path | None, job=None):
    from .lineage_session import lineage_tool_handlers
    return lineage_tool_handlers(store, genome=genome, job=job)


def investigate(
    question: str,
    *,
    genome: str | Path | None = None,
    gwas_dir: str | Path | None = None,
    p_max: float | None = 5e-8,
    use_cache: bool = True,
    facts_only: bool = False,
    with_story: bool = True,
    story_language: str | None = None,
) -> dict[str, Any]:
    """Dual-tier Investigate (GWAS + Y/AADR/KG tools). Raises CliError."""
    with cli_session() as store:
        from ..core import chat
        from ..core.investigate_brief import (
            gather_facts, normalize_plan, run_investigate,
        )
        from ..core.jobs import Job

        gpath = Path(genome) if genome else None
        try:
            job = Job("cli-investigate", "Investigate")
            handlers = _lineage_handlers(store, genome=gpath, job=job)
            if facts_only:
                plan = normalize_plan({}, question=question)
                facts = gather_facts(
                    store, plan,
                    genome=gpath,
                    gwas_dir=Path(gwas_dir) if gwas_dir else None,
                    p_max=p_max,
                    use_cache=use_cache,
                    job=job,
                    handlers=handlers,
                )
                return {"plan": plan, "facts": facts, "oracle": None, "story": None}

            t = chat.new_thread()
            t.title = f"Investigate · {question.strip().splitlines()[0][:36]}"
            chat.save(t)
            return run_investigate(
                store, t, job, question,
                genome=gpath,
                gwas_dir=Path(gwas_dir) if gwas_dir else None,
                p_max=p_max,
                use_cache=use_cache,
                handlers=handlers,
                with_story=with_story,
                story_language=story_language,
            )
        except RuntimeError as exc:
            raise _map_runtime(exc) from exc


def story(
    *,
    thread_id: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Human Story from the latest Oracle in a chat thread (default: newest)."""
    with cli_session() as store:
        from ..core import chat
        from ..core.jobs import Job
        from ..core.story_brief import find_last_oracle, run_story

        try:
            t = None
            if thread_id:
                t = chat.load(thread_id)
            else:
                for row in chat.list_threads():
                    if chat.is_shared_thread(row.id):
                        continue
                    cand = chat.load(row.id)
                    if cand and find_last_oracle(cand):
                        t = cand
                        break
            if t is None:
                raise RuntimeError("no_thread")
            if not find_last_oracle(t):
                raise RuntimeError("no_oracle: open a chat that already has an Oracle")
            job = Job("cli-story", "Story")
            text = run_story(store, t, job, language=language)
            return {"thread_id": t.id, "story": text}
        except RuntimeError as exc:
            raise _map_runtime(exc) from exc


def y_path(*, genome: str | Path | None = None) -> dict[str, Any]:
    with cli_session() as store:
        from .lineage_session import open_session, y_path as _y_path
        try:
            return _y_path(open_session(store, genome=Path(genome) if genome else None))
        except RuntimeError as exc:
            raise _map_runtime(exc) from exc


def y_lore(clade: str | None = None, *, genome: str | Path | None = None) -> dict[str, Any]:
    with cli_session() as store:
        from .lineage_session import open_session, y_lore as _y_lore
        try:
            return _y_lore(
                open_session(store, genome=Path(genome) if genome else None), clade,
            )
        except RuntimeError as exc:
            raise _map_runtime(exc) from exc


def y_milestones(
    *,
    genome: str | Path | None = None,
    limit: int = 12,
    with_lore: bool = True,
) -> dict[str, Any]:
    with cli_session() as store:
        from .lineage_session import open_session, y_milestones as _ym
        try:
            return _ym(
                open_session(store, genome=Path(genome) if genome else None),
                limit=limit, with_lore=with_lore,
            )
        except RuntimeError as exc:
            raise _map_runtime(exc) from exc


def aadr_y_hits(
    *,
    genome: str | Path | None = None,
    epochs: list[str] | None = None,
    limit_per_epoch: int = 8,
) -> dict[str, Any]:
    with cli_session() as store:
        from .lineage_session import aadr_y_hits as _hits, open_session
        try:
            return _hits(
                open_session(store, genome=Path(genome) if genome else None),
                epochs=epochs, limit_per_epoch=limit_per_epoch,
            )
        except RuntimeError as exc:
            raise _map_runtime(exc) from exc


def modern_affinity(
    *,
    genome: str | Path | None = None,
    limit: int = 15,
) -> dict[str, Any]:
    with cli_session() as store:
        from .lineage_session import modern_affinity as _aff, open_session
        try:
            return _aff(
                open_session(store, genome=Path(genome) if genome else None),
                limit=limit,
            )
        except RuntimeError as exc:
            raise _map_runtime(exc) from exc

