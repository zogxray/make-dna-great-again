"""Open Humans → DuckDB (schema data.openhumans_files).

Каталог (jsonl) синкается в таблицу; локальные samples и READY-ingest в genome.*
обновляются без повторного скачивания, если файл уже на диске и свеж.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import db
from . import openhumans as oh

if TYPE_CHECKING:
    from .jobs import Job
    from .store import Store

META_CATALOG_MTIME = "openhumans_catalog_mtime"
META_CATALOG_N = "openhumans_catalog_n"

# db_status values
ST_CATALOG = "catalog"       # metadata only
ST_DOWNLOADED = "downloaded"  # on disk, no genome ingest (NEED/PEEK) or pending
ST_INGESTED = "ingested"      # genome.files ready
ST_ERROR = "error"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _meta_get(con, key: str) -> str | None:
    row = con.execute(
        "SELECT value FROM data.meta WHERE key = ?", [key]
    ).fetchone()
    return str(row[0]) if row else None


def _meta_set(con, key: str, value: str) -> None:
    con.execute("""
        INSERT INTO data.meta (key, value) VALUES (?, ?)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value
    """, [key, value])


def catalog_synced() -> tuple[int, str | None]:
    """(n rows in DuckDB, last catalog mtime_ns as str or None)."""
    with db.locked():
        con = db.connect()
        n = con.execute("SELECT COUNT(*) FROM data.openhumans_files").fetchone()[0]
        return int(n), _meta_get(con, META_CATALOG_MTIME)


def try_load_catalog(path: Path, jsonl_mtime_ns: int) -> oh.OhCatalog | None:
    """Build OhCatalog from DuckDB when meta matches this jsonl mtime.

    Returns None if the table is missing, empty, or out of date — caller should
    fall back to jsonl parse + sync_catalog.
    """
    mtime = str(jsonl_mtime_ns)
    with db.locked():
        con = db.connect()
        if _meta_get(con, META_CATALOG_MTIME) != mtime:
            return None
        n_meta = _meta_get(con, META_CATALOG_N)
        rows = con.execute("""
            SELECT id, basename, download_url, source, user_id, username, user_name,
                   COALESCE(tags_join, ''), COALESCE(description, ''),
                   COALESCE(ext, ''), COALESCE(y_hint, FALSE),
                   COALESCE(vendor, ''), COALESCE(compat, ''), COALESCE(kind, '')
            FROM data.openhumans_files
            WHERE vendor IS NOT NULL AND vendor != ''
            ORDER BY id
        """).fetchall()
    if not rows:
        return None
    if n_meta is not None and int(n_meta) != len(rows):
        return None
    files: list[oh.OhFile] = []
    for r in rows:
        tags_join = str(r[7] or "")
        tags = tuple(t for t in tags_join.split("|") if t)
        files.append(oh.OhFile(
            id=int(r[0]),
            basename=str(r[1] or ""),
            download_url=str(r[2] or ""),
            source=str(r[3] or ""),
            user_id=str(r[4] or ""),
            username=str(r[5] or ""),
            user_name=str(r[6] or ""),
            tags=tags,
            description=str(r[8] or ""),
            ext=str(r[9] or ""),
            y_hint=bool(r[10]),
            vendor=str(r[11] or ""),
            compat=str(r[12] or ""),
            kind=str(r[13] or ""),
        ))
    claimed = None
    catalogued_at = ""
    summary = path / oh.CATALOG_SUMMARY
    if summary.is_file():
        try:
            import json
            data = json.loads(summary.read_text(encoding="utf-8"))
            catalogued_at = str(data.get("catalogued_at_utc") or "")
            n = data.get("n_files")
            if isinstance(n, int):
                claimed = n
        except (ValueError, TypeError, OSError):
            pass
    return oh.OhCatalog(files=files, path=path, claimed=claimed, catalogued_at=catalogued_at)


def stats() -> dict[str, int]:
    with db.locked():
        con = db.connect()
        out = {
            "catalog": int(con.execute(
                "SELECT COUNT(*) FROM data.openhumans_files").fetchone()[0]),
            "local": int(con.execute(
                "SELECT COUNT(*) FROM data.openhumans_files "
                "WHERE local_path IS NOT NULL AND local_size > 0"
            ).fetchone()[0]),
            "ingested": int(con.execute(
                "SELECT COUNT(*) FROM data.openhumans_files "
                "WHERE db_status = ?", [ST_INGESTED]
            ).fetchone()[0]),
            "downloaded": int(con.execute(
                "SELECT COUNT(*) FROM data.openhumans_files "
                "WHERE db_status = ?", [ST_DOWNLOADED]
            ).fetchone()[0]),
            "error": int(con.execute(
                "SELECT COUNT(*) FROM data.openhumans_files "
                "WHERE db_status = ?", [ST_ERROR]
            ).fetchone()[0]),
            "ready": int(con.execute(
                "SELECT COUNT(*) FROM data.openhumans_files WHERE compat = ?",
                [oh.COMPAT_READY],
            ).fetchone()[0]),
        }
        return out


def get_row(oh_id: int) -> dict[str, Any] | None:
    row = db.fetchone(
        "SELECT id, basename, vendor, compat, kind, local_path, local_size, "
        "db_status, genome_file_id, snps, error, downloaded_at, ingested_at "
        "FROM data.openhumans_files WHERE id = ?",
        [oh_id],
    )
    if not row:
        return None
    keys = ("id", "basename", "vendor", "compat", "kind", "local_path",
            "local_size", "db_status", "genome_file_id", "snps", "error",
            "downloaded_at", "ingested_at")
    return dict(zip(keys, row))


def status_map(ids: list[int] | None = None) -> dict[int, str]:
    """oh_id → db_status (optionally filtered)."""
    with db.locked():
        con = db.connect()
        if ids is None:
            rows = con.execute(
                "SELECT id, db_status FROM data.openhumans_files"
            ).fetchall()
        elif not ids:
            return {}
        else:
            rows = con.execute(
                "SELECT id, db_status FROM data.openhumans_files WHERE id IN "
                f"({','.join('?' * len(ids))})",
                list(ids),
            ).fetchall()
    return {int(r[0]): str(r[1] or ST_CATALOG) for r in rows}


def _write_stage_parquet(cat: oh.OhCatalog, now) -> Path:
    """Spill classified catalog to a temp parquet for DuckDB read_parquet()."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from .store import cache_dir

    files = cat.files
    table = pa.table({
        "id": pa.array([f.id for f in files], type=pa.int64()),
        "basename": pa.array([f.basename for f in files], type=pa.string()),
        "download_url": pa.array([f.download_url for f in files], type=pa.string()),
        "source": pa.array([f.source for f in files], type=pa.string()),
        "user_id": pa.array([f.user_id for f in files], type=pa.string()),
        "username": pa.array([f.username for f in files], type=pa.string()),
        "user_name": pa.array([f.user_name for f in files], type=pa.string()),
        "tags_join": pa.array([f.tags_join for f in files], type=pa.string()),
        "description": pa.array([f.description for f in files], type=pa.string()),
        "ext": pa.array([f.ext for f in files], type=pa.string()),
        "y_hint": pa.array([f.y_hint for f in files], type=pa.bool_()),
        "vendor": pa.array([f.vendor for f in files], type=pa.string()),
        "compat": pa.array([f.compat for f in files], type=pa.string()),
        "kind": pa.array([f.kind for f in files], type=pa.string()),
        "updated_at": pa.array([now] * len(files), type=pa.timestamp("us", tz="UTC")),
    })
    out = cache_dir() / "openhumans_catalog_stage.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out, compression="zstd")
    return out


def sync_catalog(cat: oh.OhCatalog | None = None, *, force: bool = False) -> int:
    """Sync catalog metadata into DuckDB. Skip if jsonl mtime unchanged (unless force).

    Stages via parquet + read_parquet, INSERT missing ids, UPDATE catalog columns
    for staged rows (local_*/ingest untouched). Returns staged row count (0 if skipped).
    """
    cat = cat or oh.load_catalog()
    jsonl = oh.catalog_jsonl(cat.path)
    if not jsonl.is_file() or not cat.files:
        return 0
    mtime = str(jsonl.stat().st_mtime_ns)
    now = _now()

    with db.locked():
        con = db.connect()
        prev = _meta_get(con, META_CATALOG_MTIME)
        n_db = int(con.execute(
            "SELECT COUNT(*) FROM data.openhumans_files").fetchone()[0])
        if not force and prev == mtime and n_db == cat.n and cat.n > 0:
            return 0

    stage = _write_stage_parquet(cat, now)
    stage_sql = str(stage).replace("'", "''")

    with db.locked():
        con = db.connect()
        n_db = int(con.execute(
            "SELECT COUNT(*) FROM data.openhumans_files").fetchone()[0])
        con.execute(
            f"CREATE OR REPLACE TEMP TABLE _oh_stage AS "
            f"SELECT * FROM read_parquet('{stage_sql}')"
        )
        if n_db == 0:
            con.execute("""
                INSERT INTO data.openhumans_files (
                    id, basename, download_url, source, user_id, username, user_name,
                    tags_join, description, ext, y_hint, vendor, compat, kind,
                    updated_at, db_status
                )
                SELECT id, basename, download_url, source, user_id, username, user_name,
                       tags_join, description, ext, y_hint, vendor, compat, kind,
                       updated_at, 'catalog'
                FROM _oh_stage
            """)
            written = cat.n
        else:
            # Insert new ids, then refresh catalog columns for everyone in stage.
            # (Keeps local_*/ingest. Needed whenever jsonl mtime changes — not only
            # when force=True — so try_load_catalog never serves stale classify.)
            con.execute("""
                INSERT INTO data.openhumans_files (
                    id, basename, download_url, source, user_id, username, user_name,
                    tags_join, description, ext, y_hint, vendor, compat, kind,
                    updated_at, db_status
                )
                SELECT s.id, s.basename, s.download_url, s.source, s.user_id, s.username,
                       s.user_name, s.tags_join, s.description, s.ext, s.y_hint,
                       s.vendor, s.compat, s.kind, s.updated_at, 'catalog'
                FROM _oh_stage s
                WHERE NOT EXISTS (
                    SELECT 1 FROM data.openhumans_files f WHERE f.id = s.id
                )
            """)
            con.execute("""
                UPDATE data.openhumans_files f SET
                    basename = s.basename,
                    download_url = s.download_url,
                    source = s.source,
                    user_id = s.user_id,
                    username = s.username,
                    user_name = s.user_name,
                    tags_join = s.tags_join,
                    description = s.description,
                    ext = s.ext,
                    y_hint = s.y_hint,
                    vendor = s.vendor,
                    compat = s.compat,
                    kind = s.kind,
                    updated_at = s.updated_at
                FROM _oh_stage s
                WHERE f.id = s.id
            """)
            written = cat.n
        con.execute("DROP TABLE IF EXISTS _oh_stage")
        _meta_set(con, META_CATALOG_MTIME, mtime)
        _meta_set(con, META_CATALOG_N, str(cat.n))
        return written


def touch_local(oh_id: int, path: Path, *, downloaded: bool = False) -> None:
    """Record local file presence (after download or scan)."""
    path = Path(path).expanduser().resolve()
    st = path.stat() if path.is_file() else None
    now = _now()
    with db.locked():
        con = db.connect()
        row = con.execute(
            "SELECT db_status FROM data.openhumans_files WHERE id = ?", [oh_id]
        ).fetchone()
        if not row:
            # catalog row missing — minimal insert
            con.execute("""
                INSERT INTO data.openhumans_files (
                    id, local_path, local_size, local_mtime, db_status,
                    downloaded_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [
                oh_id, str(path) if st else None,
                st.st_size if st else None,
                st.st_mtime if st else None,
                ST_DOWNLOADED if st else ST_CATALOG,
                now if (st and downloaded) else None, now,
            ])
            return
        cur_status = str(row[0] or ST_CATALOG)
        new_status = cur_status
        if st and cur_status in (ST_CATALOG, ST_ERROR):
            new_status = ST_DOWNLOADED
        elif not st:
            new_status = ST_CATALOG
        con.execute("""
            UPDATE data.openhumans_files SET
                local_path = ?, local_size = ?, local_mtime = ?,
                db_status = CASE
                    WHEN db_status = ? THEN db_status
                    ELSE ?
                END,
                downloaded_at = CASE
                    WHEN ? THEN COALESCE(downloaded_at, ?)
                    ELSE downloaded_at
                END,
                error = CASE WHEN ? THEN NULL ELSE error END,
                updated_at = ?
            WHERE id = ?
        """, [
            str(path) if st else None,
            st.st_size if st else None,
            st.st_mtime if st else None,
            ST_INGESTED, new_status,
            bool(st and downloaded), now,
            bool(st),
            now, oh_id,
        ])


def mark_ingested(oh_id: int, path: Path, *, genome_file_id: int | None,
                  snps: int | None) -> None:
    path = Path(path).expanduser().resolve()
    st = path.stat() if path.is_file() else None
    now = _now()
    with db.locked():
        con = db.connect()
        con.execute("""
            UPDATE data.openhumans_files SET
                local_path = ?, local_size = ?, local_mtime = ?,
                db_status = ?, genome_file_id = ?, snps = ?,
                error = NULL, ingested_at = ?, updated_at = ?
            WHERE id = ?
        """, [
            str(path) if st else None,
            st.st_size if st else None,
            st.st_mtime if st else None,
            ST_INGESTED, genome_file_id, snps, now, now, oh_id,
        ])


def mark_error(oh_id: int, message: str, *, path: Path | None = None) -> None:
    now = _now()
    local_path = local_size = local_mtime = None
    if path is not None:
        path = Path(path).expanduser().resolve()
        if path.is_file():
            st = path.stat()
            local_path, local_size, local_mtime = str(path), st.st_size, st.st_mtime
    with db.locked():
        con = db.connect()
        con.execute("""
            UPDATE data.openhumans_files SET
                db_status = ?, error = ?,
                local_path = COALESCE(?, local_path),
                local_size = COALESCE(?, local_size),
                local_mtime = COALESCE(?, local_mtime),
                updated_at = ?
            WHERE id = ?
        """, [ST_ERROR, message[:500], local_path, local_size, local_mtime, now, oh_id])


def scan_folder(folder: Path, cat: oh.OhCatalog | None = None) -> int:
    """Match `{id}_*` files in folder to catalog rows; update local_* without download."""
    folder = Path(folder)
    if not folder.is_dir():
        return 0
    by_id = {f.id: f for f in (cat or oh.load_catalog()).files}
    n = 0
    for p in folder.iterdir():
        if not p.is_file() or p.name.endswith(".part"):
            continue
        head = p.name.split("_", 1)[0]
        if not head.isdigit():
            continue
        oh_id = int(head)
        if oh_id not in by_id and not db.fetchone(
            "SELECT 1 FROM data.openhumans_files WHERE id = ?", [oh_id]
        ):
            continue
        touch_local(oh_id, p, downloaded=False)
        n += 1
    return n


def ensure_files(
    folder: Path,
    files: list[oh.OhFile],
    job: Job,
    store: Store | None = None,
) -> str:
    """Download missing → DuckDB row → READY ingest into genome.* (skip if fresh)."""
    from . import genome_db
    from .jobs import fetch, ensure_space

    if not files:
        return "nothing selected"
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    sync_catalog(force=False)  # insert any missing catalog ids only
    ensure_space(folder, max(64 << 20, len(files) * (8 << 20)))

    downloaded = skipped = ingested = ingest_skip = fail = 0
    errors: list[str] = []
    job.unit = "items"
    n = len(files)

    for i, f in enumerate(files):
        job.check()
        label = f.basename or str(f.id)
        dest = f.local_path(folder)
        # 1) download only if missing / empty
        if dest.is_file() and dest.stat().st_size > 0:
            job.set_stage(f"have · {label}", n, i)
            skipped += 1
            touch_local(f.id, dest, downloaded=False)
        else:
            job.set_stage(f"download · {label}", n, i)
            if not f.download_url:
                fail += 1
                errors.append(f"{label}: no download_url")
                mark_error(f.id, "no download_url")
                job.progress(i + 1)
                continue
            try:
                res = fetch(f.download_url, dest, job, label)
                if res == "skipped":
                    skipped += 1
                else:
                    downloaded += 1
                touch_local(f.id, dest, downloaded=True)
            except Exception as exc:  # noqa: BLE001
                fail += 1
                errors.append(f"{label}: {exc}")
                mark_error(f.id, str(exc), path=dest if dest.exists() else None)
                job.progress(i + 1)
                continue

        # 2) DuckDB / genome ingest
        if f.compat == oh.COMPAT_READY and dest.is_file() and dest.stat().st_size > 0:
            job.set_stage(f"DuckDB · {label}", n, i)
            try:
                if genome_db.is_fresh(dest):
                    row = genome_db.file_row(dest)
                    mark_ingested(
                        f.id, dest,
                        genome_file_id=int(row[0]) if row else None,
                        snps=int(row[11]) if row and row[11] is not None else None,
                    )
                    ingest_skip += 1
                else:
                    def prog(frac: float | None, lines: int) -> None:
                        job.check()
                        if frac is not None:
                            job.set_stage(
                                f"ingest · {label} · {frac:.0%}", n, i
                            )

                    g = genome_db.ensure(
                        dest, prog, store, set_active_flag=False
                    )
                    row = genome_db.file_row(dest)
                    mark_ingested(
                        f.id, dest,
                        genome_file_id=int(row[0]) if row else None,
                        snps=g.total,
                    )
                    ingested += 1
            except Exception as exc:  # noqa: BLE001
                fail += 1
                errors.append(f"{label}: ingest {exc}")
                mark_error(f.id, f"ingest: {exc}", path=dest)
        elif dest.is_file() and dest.stat().st_size > 0:
            # NEED / PEEK — on disk only
            touch_local(f.id, dest, downloaded=False)

        job.progress(i + 1)

    job.set_stage("done", n, n)
    msg = (
        f"{downloaded} new downloads · {skipped} kept · "
        f"{ingested} ingested · {ingest_skip} already in DB · {fail} failed"
    )
    if errors:
        msg += " · " + "; ".join(errors[:3])
        if len(errors) > 3:
            msg += f" · +{len(errors) - 3} more"
    if fail and not downloaded and not skipped and not ingested and not ingest_skip:
        raise RuntimeError(msg)
    return msg


def verify_local(folder: Path, job: Job | None = None) -> str:
    """Refresh catalog from API (new ids), sync DuckDB, scan folder, clear stale locals."""
    if job:
        job.set_stage("refresh catalog API")
    n_new, cat = oh.refresh_catalog_api(job)
    if job:
        job.set_stage("sync catalog → DuckDB")
    # Insert missing only — full metadata rewrite is rare and expensive on big DB.
    n_cat = sync_catalog(cat, force=False)
    if job:
        job.set_stage("scan samples")
    n_scan = scan_folder(folder, cat)
    if job:
        job.set_stage("check missing")
    cleared = 0
    with db.locked():
        con = db.connect()
        rows = con.execute(
            "SELECT id, local_path, db_status FROM data.openhumans_files "
            "WHERE local_path IS NOT NULL"
        ).fetchall()
        for oh_id, path_s, status in rows:
            p = Path(path_s)
            if p.is_file() and p.stat().st_size > 0:
                continue
            con.execute("""
                UPDATE data.openhumans_files SET
                    local_path = NULL, local_size = NULL, local_mtime = NULL,
                    db_status = CASE WHEN db_status = ? THEN ? ELSE ? END,
                    genome_file_id = NULL, snps = NULL,
                    error = 'local file missing', updated_at = ?
                WHERE id = ?
            """, [ST_INGESTED, ST_ERROR, ST_CATALOG, _now(), int(oh_id)])
            cleared += 1
    return (
        f"API +{n_new} new · DuckDB upsert {n_cat:,} · "
        f"scanned {n_scan} · cleared {cleared}"
    )


def new_missing(
    cat: oh.OhCatalog,
    folder: Path,
    flt: oh.OhFilters | None = None,
) -> list[oh.OhFile]:
    """Catalog rows matching filter that are not yet on disk (default: chip, missing)."""
    flt = flt or oh.OhFilters()
    # Force have=missing so we only pull new/absent samples.
    from dataclasses import replace
    flt = replace(flt, have="missing")
    return oh.apply_filters(cat.files, flt, folder)


def update_new_items(
    folder: Path,
    job: Job,
    store: Store | None = None,
    flt: oh.OhFilters | None = None,
) -> str:
    """Refresh API for new catalog ids, then download only missing (keep existing files).

    Default filter = chip raw (noise hidden). READY rows also go into genome.*.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if job:
        job.set_stage("refresh catalog API")
    n_new, cat = oh.refresh_catalog_api(job)
    sync_catalog(cat, force=False)
    scan_folder(folder, cat)
    want = new_missing(cat, folder, flt)
    if not want:
        return (
            f"API +{n_new} new catalog rows · nothing to download "
            f"(filter has no missing files)"
        )
    msg = ensure_files(folder, want, job, store=store)
    return f"API +{n_new} new · then {msg}"
