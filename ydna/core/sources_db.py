"""F2 каталог в DuckDB (schema data): seed, upsert статуса, list."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db
from .sources import SOURCE_GROUPS, SOURCES, Source, Status


def _group_map() -> dict[str, str]:
    out: dict[str, str] = {}
    for title, keys in SOURCE_GROUPS:
        for k in keys:
            out[k] = title
    return out


def seed_sources(con=None) -> None:
    """INSERT OR IGNORE всех SOURCES (title/group/kind/flags)."""
    groups = _group_map()
    rows = []
    for s in SOURCES:
        rows.append((
            s.key, s.title, groups.get(s.key), s.kind,
            bool(s.can_download), bool(s.can_verify),
        ))
    sql = """
        INSERT INTO data.sources (
            key, title, group_name, kind, can_download, can_verify, state
        ) VALUES (?, ?, ?, ?, ?, ?, 'unset')
        ON CONFLICT (key) DO UPDATE SET
            title = excluded.title,
            group_name = excluded.group_name,
            kind = excluded.kind,
            can_download = excluded.can_download,
            can_verify = excluded.can_verify
    """
    if con is not None:
        con.executemany(sql, rows)
    else:
        with db.locked():
            db.connect().executemany(sql, rows)


def _fact(facts: list[tuple[str, str]], *names: str) -> str | None:
    low = {n.lower(): n for n in names}
    for k, v in facts:
        if k.lower() in low:
            return str(v)
    return None


def _item_count(key: str, st: Status) -> int | None:
    """Грубая эвристика числа объектов из summary/facts."""
    for label in ("Nodes", "SNP relations", "Variants", "Samples", "Models (status ok)",
                  "Rows", "rsIDs", "Resources"):
        v = _fact(st.facts, label)
        if v:
            digits = "".join(ch for ch in v if ch.isdigit() or ch == ",")
            digits = digits.replace(",", "")
            if digits.isdigit():
                return int(digits)
    # Discover: "N resources"
    if key == "ftdna_discover" and "resource" in (st.summary or "").lower():
        head = (st.summary or "").split("·")[0]
        digits = "".join(ch for ch in head if ch.isdigit() or ch == ",")
        digits = digits.replace(",", "")
        if digits.isdigit():
            return int(digits)
    return None


def upsert_status(
    src: Source,
    st: Status,
    path: Path | None,
    *,
    downloaded: bool = False,
    manifest: dict | None = None,
) -> None:
    """Записать результат check/download в data.sources + data.files."""
    facts_json = json.dumps([[k, v] for k, v in st.facts], ensure_ascii=False)
    version = _fact(st.facts, "Release", "Version", "Data date", "Published")
    upstream = _fact(st.facts, "Zenodo", "Upstream", "DOI", "Source")
    bytes_total = sum(f.size or 0 for f in st.files if f.ok and f.size)
    item_count = _item_count(src.key, st)
    man_json = json.dumps(manifest, ensure_ascii=False) if manifest else None
    now = datetime.now(timezone.utc)
    path_s = str(path) if path else None

    with db.locked():
        con = db.connect()
        err = st.summary if st.state in ("missing", "partial") else None
        if src.kind == "secret":
            path_s = None
        con.execute("""
            UPDATE data.sources SET
                path = ?,
                state = ?,
                summary = ?,
                version = COALESCE(?, version),
                upstream = COALESCE(?, upstream),
                bytes_total = ?,
                item_count = COALESCE(?, item_count),
                facts_json = ?,
                manifest_json = COALESCE(?, manifest_json),
                checked_at = ?,
                downloaded_at = CASE WHEN ? THEN ? ELSE downloaded_at END,
                error = ?
            WHERE key = ?
        """, [
            path_s, st.state, st.summary, version, upstream,
            bytes_total or None, item_count, facts_json, man_json,
            now, downloaded, now, err, src.key,
        ])

        con.execute("DELETE FROM data.files WHERE source_key = ?", [src.key])
        if st.files:
            rows = [
                (src.key, f.name, f.size, None, bool(f.ok), f.note or "")
                for f in st.files
            ]
            con.executemany("""
                INSERT INTO data.files (source_key, relpath, size, mtime, ok, note)
                VALUES (?, ?, ?, ?, ?, ?)
            """, rows)


def set_source_path(key: str, path: Path | None) -> None:
    with db.locked():
        con = db.connect()
        if path is None:
            con.execute("""
                UPDATE data.sources SET
                    path = NULL, state = 'unset', summary = 'path not set',
                    checked_at = NULL, error = NULL
                WHERE key = ?
            """, [key])
            con.execute("DELETE FROM data.files WHERE source_key = ?", [key])
        else:
            con.execute("""
                UPDATE data.sources SET path = ?, state = 'unset',
                    summary = 'path set · not checked yet'
                WHERE key = ?
            """, [str(path), key])


def get_source_path(key: str) -> Path | None:
    row = db.fetchone("SELECT path FROM data.sources WHERE key = ?", [key])
    if not row or not row[0]:
        return None
    return Path(str(row[0]))


def list_sources() -> list[dict[str, Any]]:
    rows = db.fetchall("""
        SELECT key, title, group_name, kind, path, state, summary, version,
               upstream, bytes_total, item_count, facts_json, can_download,
               can_verify, checked_at, downloaded_at, error
        FROM data.sources
        ORDER BY group_name NULLS LAST, key
    """)
    out = []
    for r in rows:
        facts = []
        if r[11]:
            try:
                facts = [tuple(x) for x in json.loads(r[11])]
            except (TypeError, ValueError):
                pass
        out.append({
            "key": r[0], "title": r[1], "group_name": r[2], "kind": r[3],
            "path": Path(r[4]) if r[4] else None, "state": r[5], "summary": r[6],
            "version": r[7], "upstream": r[8], "bytes_total": r[9],
            "item_count": r[10], "facts": facts,
            "can_download": bool(r[12]), "can_verify": bool(r[13]),
            "checked_at": r[14], "downloaded_at": r[15], "error": r[16],
        })
    return out


def list_files(source_key: str) -> list[dict[str, Any]]:
    rows = db.fetchall("""
        SELECT relpath, size, mtime, ok, note
        FROM data.files WHERE source_key = ? ORDER BY relpath
    """, [source_key])
    return [
        {"relpath": r[0], "size": r[1], "mtime": r[2], "ok": bool(r[3]), "note": r[4]}
        for r in rows
    ]
