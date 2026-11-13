"""stdio MCP server — thin wrapper over ``ydna.cli.ops`` for investigation."""

from __future__ import annotations

import json
from typing import Any

from ..cli import ops
from ..cli.jsonio import CliError, _default


def _dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=_default)


def _tool_result(data: dict[str, Any] | None = None, *, error: CliError | None = None) -> str:
    return _dumps(ops.envelope(data, error=error))


def _wrap(fn):
    try:
        return _tool_result(fn())
    except CliError as exc:
        return _tool_result(error=exc)
    except Exception as exc:  # noqa: BLE001
        return _tool_result(error=CliError("internal", str(exc)))


def build_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise SystemExit(
            "MCP extra required:  pip install -e '.[mcp]'\n"
            f"({exc})"
        ) from exc

    mcp = FastMCP(
        "make-dna-great-again",
        instructions=(
            "Local genotype investigation tools. Prefer an iterative loop: "
            "status → gwas_search / pgs_search → gwas_trait → genome_rs → "
            "ask the user for labs/context → search again. "
            "profile_micronutrients is an optional shortcut, not the only path. "
            "JSON envelope {ok,data} or {ok:false,error}. Shared flock with the TUI."
        ),
    )

    @mcp.tool()
    def status() -> str:
        """App status: version, lock, F2 sources, listed/active genomes."""
        return _wrap(ops.status)

    @mcp.tool()
    def genome_summary(genome: str | None = None, ingest: bool = False) -> str:
        """Summarize active F1 genome or a kit path."""
        return _wrap(lambda: ops.genome_summary(genome=genome, ingest=ingest))

    @mcp.tool()
    def genome_rs(rsids: list[str], genome: str | None = None) -> str:
        """Look up genotypes for rsIDs on the active/given kit."""
        return _wrap(lambda: ops.genome_rs(rsids, genome=genome))

    @mcp.tool()
    def gwas_match(
        genome: str | None = None,
        gwas_dir: str | None = None,
        p_max: float = 5e-8,
        no_p_cut: bool = False,
        use_cache: bool = True,
    ) -> str:
        """Full GWAS Catalog match (large). Prefer gwas_search for investigation."""
        cut = None if no_p_cut or p_max == 0 else p_max
        return _wrap(lambda: ops.gwas_match(
            genome=genome, gwas_dir=gwas_dir, p_max=cut, use_cache=use_cache,
        ))

    @mcp.tool()
    def gwas_search(
        query: str,
        genome: str | None = None,
        gwas_dir: str | None = None,
        p_max: float = 5e-8,
        no_p_cut: bool = False,
        use_cache: bool = True,
        limit: int = 40,
    ) -> str:
        """Search matched GWAS traits/genes/rs on the chip. Returns next_queries."""
        cut = None if no_p_cut or p_max == 0 else p_max
        return _wrap(lambda: ops.gwas_search(
            query, genome=genome, gwas_dir=gwas_dir, p_max=cut,
            use_cache=use_cache, limit=limit,
        ))

    @mcp.tool()
    def gwas_trait(
        trait: str,
        genome: str | None = None,
        gwas_dir: str | None = None,
        p_max: float = 5e-8,
        no_p_cut: bool = False,
        use_cache: bool = True,
        limit: int = 80,
    ) -> str:
        """Association rows for one DISEASE/TRAIT (exact or candidates)."""
        cut = None if no_p_cut or p_max == 0 else p_max
        return _wrap(lambda: ops.gwas_trait(
            trait, genome=genome, gwas_dir=gwas_dir, p_max=cut,
            use_cache=use_cache, limit=limit,
        ))

    @mcp.tool()
    def pgs_search(query: str, genome: str | None = None, limit: int = 40) -> str:
        """Search local PGS models; flags whether scored for this genome."""
        return _wrap(lambda: ops.pgs_search(query, genome=genome, limit=limit))

    @mcp.tool()
    def profile_micronutrients(
        genome: str | None = None,
        gwas_dir: str | None = None,
        p_max: float = 5e-8,
        no_p_cut: bool = False,
        use_cache: bool = True,
        age: int | None = None,
        labs_json: str | None = None,
    ) -> str:
        """Optional curated vitamin pack shortcut — not the only investigation path."""
        def _run():
            labs = json.loads(labs_json) if labs_json else None
            if labs is not None and not isinstance(labs, dict):
                raise CliError("usage", "labs_json must be a JSON object")
            cut = None if no_p_cut or p_max == 0 else p_max
            return ops.profile_micronutrients(
                genome=genome, gwas_dir=gwas_dir, p_max=cut,
                use_cache=use_cache, age=age, labs=labs,
            )
        return _wrap(_run)

    @mcp.tool()
    def investigate(
        question: str,
        genome: str | None = None,
        gwas_dir: str | None = None,
        p_max: float = 5e-8,
        no_p_cut: bool = False,
        use_cache: bool = True,
        facts_only: bool = False,
        with_story: bool = True,
        story_language: str | None = None,
    ) -> str:
        """Flash → gather → Argon Oracle → optional human Story Markdown.

        facts_only=true skips LLMs. with_story=false stops after Oracle.
        """
        cut = None if no_p_cut or p_max == 0 else p_max
        return _wrap(lambda: ops.investigate(
            question, genome=genome, gwas_dir=gwas_dir, p_max=cut,
            use_cache=use_cache, facts_only=facts_only,
            with_story=with_story, story_language=story_language,
        ))

    @mcp.tool()
    def story(thread_id: str | None = None, language: str | None = None) -> str:
        """Human Story Markdown from the latest Oracle in a chat thread."""
        return _wrap(lambda: ops.story(thread_id=thread_id, language=language))

    @mcp.tool()
    def y_path(genome: str | None = None) -> str:
        """Our Y tip→root path on the FTDNA haplotree."""
        return _wrap(lambda: ops.y_path(genome=genome))

    @mcp.tool()
    def y_lore(clade: str | None = None, genome: str | None = None) -> str:
        """LORE brief (culture/Seshat/codex/ancients) for tip or a clade."""
        return _wrap(lambda: ops.y_lore(clade, genome=genome))

    @mcp.tool()
    def y_milestones(
        genome: str | None = None, limit: int = 12, with_lore: bool = True,
    ) -> str:
        """Subsampled paternal milestones (+ lore) for epoch-scale questions."""
        return _wrap(lambda: ops.y_milestones(
            genome=genome, limit=limit, with_lore=with_lore,
        ))

    @mcp.tool()
    def aadr_y_hits(
        genome: str | None = None,
        epochs: list[str] | None = None,
        limit_per_epoch: int = 8,
    ) -> str:
        """AADR groups matching Our Y branch, bucketed by epoch."""
        return _wrap(lambda: ops.aadr_y_hits(
            genome=genome, epochs=epochs, limit_per_epoch=limit_per_epoch,
        ))

    @mcp.tool()
    def modern_affinity(genome: str | None = None, limit: int = 15) -> str:
        """Top cached AADR + 1000G f3 affinity (no live scoring)."""
        return _wrap(lambda: ops.modern_affinity(genome=genome, limit=limit))

    return mcp


def main() -> None:
    try:
        from ..core.llm import load_dotenv
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
