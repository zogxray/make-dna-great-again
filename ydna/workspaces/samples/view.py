"""Shared F9 Samples view helpers (Textual + wgpu)."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text

from ...core import openhumans as oh
from ...core import openhumans_db as ohdb
from ...core.sources import human_size
from ..base import click


def filter_bar_text(flt: oh.OhFilters) -> Text:
    t = Text()
    t.append("FILTER  ", style="bold #D4AD72")
    t.append(flt.label() + "\n", style="#C3BCCC")
    t.append(
        "  F kind · W vendor · H have · K compat · Y y/bigy · X noise · / query",
        style="#71697A",
    )
    return t


def file_detail_text(
    f: oh.OhFile | None,
    *,
    folder: Path,
    marked: bool,
) -> Text:
    """FILE panel body with clickable download / open_local."""
    t = Text()
    if f is None:
        t.append("No rows match the filter.\n", style="#8A8294")
        t.append("Try Y (y/bigy), F kind=all_geno, or /bigy.", style="#71697A")
        return t

    local = f.is_local(folder)
    glyph = oh.COMPAT_GLYPH.get(f.compat, "?")
    label = oh.COMPAT_LABEL.get(f.compat, f.compat)
    style = oh.COMPAT_STYLE.get(f.compat, "#AEA5B8")
    t.append(f"{f.basename}\n", style="bold #FBF6F0")
    t.append(f"{glyph} {label}", style=f"bold {style}")
    t.append(f"  ·  {f.vendor}  ·  {f.kind}\n", style="#C3BCCC")

    t.append("\nDOWNLOAD\n", style="bold #D4AD72")
    t.append("  [D] ", style=click("screen.download", "bold #D4AD72"))
    if local:
        t.append("re-check / DuckDB this file\n", style=click("screen.download"))
    else:
        t.append("download this genome\n", style=click("screen.download"))
    if f.compat != oh.COMPAT_READY:
        t.append("  (not F1-ready — still allowed)\n", style="#8A8294")

    t.append("\nMETA\n", style="bold #D4AD72")
    for k, v in (
        ("id", str(f.id)),
        ("user", f"{f.username or '—'} · {f.user_name or ''}".strip(" ·")),
        ("source", f.source or "—"),
        ("ext", f.ext or "—"),
        ("tags", f.tags_join or "—"),
    ):
        t.append(f"  {k}: ", style="#AEA5B8")
        t.append(f"{v}\n", style="#C3BCCC")
    if f.description:
        t.append("  desc: ", style="#AEA5B8")
        t.append(f"{f.description}\n", style="#8A8294")

    t.append("\nCOMPAT\n", style="bold #D4AD72")
    if f.compat == oh.COMPAT_READY:
        t.append("  ● SUPPORTED — F1 parses 23andMe / AncestryDNA chip .txt.\n",
                 style="#53A94B")
    elif f.compat == oh.COMPAT_NEED:
        t.append("  ○ NOT YET — FTDNA / Big Y / VCF / zip need more code.\n",
                 style="#D4AD72")
        t.append("  Download is allowed; ingest to genome.* may fail.\n", style="#8A8294")
    elif f.compat == oh.COMPAT_PEEK:
        t.append("  △ UNKNOWN — download and inspect before coding.\n",
                 style="#8BAB4A")
    else:
        t.append("  ✖ SKIP noise — wearable/survey; still downloadable.\n",
                 style="#71697A")

    t.append("\nLOCAL\n", style="bold #D4AD72")
    dest = f.local_path(folder)
    if local:
        size = dest.stat().st_size
        t.append("  ● on disk  ", style="#53A94B")
        t.append(f"{human_size(size)}\n", style="#C3BCCC")
        t.append(f"  {dest}\n", style="#8A8294")
        t.append("\n  Enter → open in F1\n", style=click("screen.open_local", "#D4AD72"))
    else:
        t.append("  ○ not on disk\n", style="#8A8294")
        t.append(f"  will save as {dest.name}\n", style="#71697A")

    t.append("\nDUCKDB\n", style="bold #D4AD72")
    try:
        row = ohdb.get_row(f.id)
    except Exception:  # noqa: BLE001
        row = None
    if row:
        st = str(row.get("db_status") or ohdb.ST_CATALOG)
        t.append(f"  status: {st}", style="#C3BCCC")
        if row.get("snps"):
            t.append(f" · {row['snps']:,} SNPs", style="#53A94B")
        t.append("\n")
        if row.get("genome_file_id") is not None:
            t.append(f"  genome.files id: {row['genome_file_id']}\n", style="#8A8294")
        if row.get("error"):
            t.append(f"  error: {row['error']}\n", style="#B04A4A")
        if st == ohdb.ST_INGESTED:
            t.append("  already in DuckDB\n", style="#53A94B")
    else:
        t.append("  not in DuckDB yet\n", style="#8A8294")

    t.append("\nMARK\n", style="bold #D4AD72")
    t.append(
        ("  ▸ in multi-download set (Space)\n" if marked
         else "  · Space adds to multi-download set\n"),
        style="#D4AD72" if marked else "#8A8294",
    )
    return t


__all__ = ["file_detail_text", "filter_bar_text"]
