"""F1 RAW → DuckDB (schema genome): ingest, list, load, active, build vote.

Парсер остаётся в genome.load; здесь — запись/чтение индекса и детект билда
через ATTACH F2 rsid_identity (без API).
"""

from __future__ import annotations

import threading
from array import array
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from . import db
from .genome import Chrom, Genome, Progress, chrom_key, load

if TYPE_CHECKING:
    from .store import Store

_BATCH = 50_000
# Порог голосов якорей и доля победителя для chrom build.
_BUILD_MIN_VOTES = 50
_BUILD_MIN_SHARE = 0.9

# Лёгкий process-кэш Genome: (abs path, mtime_ns) → объект.
_GENOME_CACHE_MAX = 3
_genome_lock = threading.Lock()
_genome_cache: dict[tuple[str, int], Genome] = {}
# Отдельный кэш Y-only: не кладём в полный — иначе F1 получит обрезок.
_y_genome_cache: dict[tuple[str, int], Genome] = {}


def _mtime_key(path: Path) -> tuple[str, int] | None:
    path = Path(path).expanduser().resolve()
    try:
        return (str(path), path.stat().st_mtime_ns)
    except OSError:
        return None


def _cache_get(path: Path) -> Genome | None:
    key = _mtime_key(path)
    if key is None:
        return None
    with _genome_lock:
        return _genome_cache.get(key)


def _cache_put(g: Genome) -> None:
    key = _mtime_key(g.path)
    if key is None:
        return
    with _genome_lock:
        if key in _genome_cache:
            _genome_cache[key] = g
            return
        _genome_cache[key] = g
        while len(_genome_cache) > _GENOME_CACHE_MAX:
            _genome_cache.pop(next(iter(_genome_cache)))


def _y_cache_get(path: Path) -> Genome | None:
    key = _mtime_key(path)
    if key is None:
        return None
    with _genome_lock:
        return _y_genome_cache.get(key)


def _y_cache_put(g: Genome) -> None:
    key = _mtime_key(g.path)
    if key is None:
        return
    with _genome_lock:
        _y_genome_cache.clear()
        _y_genome_cache[key] = g


def clear_genome_cache() -> None:
    with _genome_lock:
        _genome_cache.clear()
        _y_genome_cache.clear()


def _sql_path(p: Path) -> str:
    return str(p.resolve()).replace("'", "''")


def _next_file_id(con) -> int:
    row = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM genome.files").fetchone()
    return int(row[0])


def file_row(path: Path) -> tuple | None:
    """Строка genome.files по abs path или None."""
    path = Path(path).expanduser().resolve()
    return db.fetchone(
        "SELECT id, path, size, mtime, fmt, layout, member, build_header, "
        "build_majority, active, status, snps, malformed, ingested_at, error "
        "FROM genome.files WHERE path = ?",
        [str(path)],
    )


def list_files() -> list[dict]:
    rows = db.fetchall(
        "SELECT id, path, size, mtime, fmt, layout, build_header, build_majority, "
        "active, status, snps, malformed, ingested_at, error "
        "FROM genome.files ORDER BY id"
    )
    out = []
    for r in rows:
        out.append({
            "id": r[0], "path": Path(r[1]), "size": r[2], "mtime": r[3],
            "fmt": r[4], "layout": r[5], "build_header": r[6], "build_majority": r[7],
            "active": bool(r[8]), "status": r[9], "snps": r[10], "malformed": r[11],
            "ingested_at": r[12], "error": r[13],
        })
    return out


def listed_paths() -> list[Path]:
    """Пути из genome.files (порядок id); файл может уже отсутствовать на диске."""
    return [r["path"] for r in list_files()]


def active_path() -> Path | None:
    row = db.fetchone(
        "SELECT path FROM genome.files WHERE active LIMIT 1"
    )
    if not row:
        return None
    p = Path(row[0])
    return p if p.is_file() else None


def ensure_listed(path: Path) -> None:
    """Строка в genome.files без ingest (status=listed). Для списка F1 до парсинга."""
    path = Path(path).expanduser().resolve()
    if file_row(path):
        return
    st = path.stat() if path.is_file() else None
    with db.locked():
        con = db.connect()
        if con.execute(
            "SELECT 1 FROM genome.files WHERE path = ?", [str(path)]
        ).fetchone():
            return
        fid = _next_file_id(con)
        con.execute("""
            INSERT INTO genome.files (
                id, path, size, mtime, active, status
            ) VALUES (?, ?, ?, ?, FALSE, 'listed')
        """, [
            fid, str(path),
            st.st_size if st else None,
            st.st_mtime if st else None,
        ])


def is_fresh(path: Path) -> bool:
    """True, если в DB есть ready-запись с тем же size/mtime."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        return False
    st = path.stat()
    row = db.fetchone(
        "SELECT size, mtime, status FROM genome.files WHERE path = ?",
        [str(path)],
    )
    if not row:
        return False
    size, mtime, status = row
    return (
        status == "ready"
        and size == st.st_size
        and mtime is not None
        and abs(float(mtime) - st.st_mtime) < 1e-6
    )


def set_active(path: Path | None) -> None:
    with db.locked():
        con = db.connect()
        con.execute("UPDATE genome.files SET active = FALSE WHERE active")
        if path is not None:
            path = Path(path).expanduser().resolve()
            con.execute(
                "UPDATE genome.files SET active = TRUE WHERE path = ?",
                [str(path)],
            )


def remove_file(
    path: Path,
    *,
    delete_calls: bool = True,
    progress: Progress | None = None,
) -> None:
    """Удалить индекс файла из DuckDB одной транзакцией (calls → chrom_stats → files)."""
    path = Path(path).expanduser().resolve()
    with db.locked():
        con = db.connect()
        row = con.execute(
            "SELECT id FROM genome.files WHERE path = ?", [str(path)]
        ).fetchone()
        if not row:
            if progress:
                progress(1.0, 0)
            return
        fid = int(row[0])
        chroms: list[str] = []
        if delete_calls:
            chroms = [
                str(r[0]) for r in con.execute(
                    "SELECT chrom FROM genome.chrom_stats WHERE file_id = ? ORDER BY chrom",
                    [fid],
                ).fetchall()
            ]
        total = max(1, len(chroms) + 2)  # chrom deletes + stats + files
        step = 0

        def _tick(stage_lines: int = 0) -> None:
            nonlocal step
            step += 1
            if progress:
                progress(min(1.0, step / total), stage_lines)

        con.execute("BEGIN")
        try:
            if delete_calls:
                if chroms:
                    for chrom in chroms:
                        con.execute(
                            "DELETE FROM genome.calls WHERE file_id = ? AND chrom = ?",
                            [fid, chrom],
                        )
                        _tick()
                else:
                    con.execute("DELETE FROM genome.calls WHERE file_id = ?", [fid])
                    _tick()
                con.execute("DELETE FROM genome.chrom_stats WHERE file_id = ?", [fid])
                _tick()
            con.execute("DELETE FROM genome.files WHERE id = ?", [fid])
            _tick()
            con.execute("COMMIT")
        except Exception:
            try:
                con.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise
    # сбросить RAM по этому path (любой mtime)
    with _genome_lock:
        for key in [k for k in _genome_cache if k[0] == str(path)]:
            _genome_cache.pop(key, None)
        for key in [k for k in _y_genome_cache if k[0] == str(path)]:
            _y_genome_cache.pop(key, None)
    if progress:
        progress(1.0, 0)


def _vote_builds(con, file_id: int, store: Store | None) -> str | None:
    """Обновить chrom_stats.build / votes_*; вернуть build_majority или None."""
    if store is None:
        return None
    from .sources import rsid_identity_db
    rsid_db = rsid_identity_db(store)
    if rsid_db is None:
        return None
    alias = "rsid_id"
    # Снять прошлый ATTACH, если остался после сбоя.
    try:
        con.execute(f"DETACH {alias}")
    except Exception:
        pass
    con.execute(f"ATTACH '{_sql_path(rsid_db)}' AS {alias} (READ_ONLY)")
    try:
        con.execute("""
            UPDATE genome.chrom_stats SET votes_37 = 0, votes_38 = 0, build = NULL
            WHERE file_id = ?
        """, [file_id])
        con.execute(f"""
            UPDATE genome.chrom_stats cs
            SET
                votes_37 = v.v37,
                votes_38 = v.v38,
                build = CASE
                    WHEN v.v37 + v.v38 < {_BUILD_MIN_VOTES} THEN 'uncertain'
                    WHEN v.v37 * 1.0 / (v.v37 + v.v38) >= {_BUILD_MIN_SHARE} THEN '37'
                    WHEN v.v38 * 1.0 / (v.v37 + v.v38) >= {_BUILD_MIN_SHARE} THEN '38'
                    ELSE 'uncertain'
                END
            FROM (
                SELECT
                    c.chrom AS chrom,
                    SUM(CASE WHEN c.pos = r.pos37 THEN 1 ELSE 0 END)::INTEGER AS v37,
                    SUM(CASE WHEN c.pos = r.pos38 THEN 1 ELSE 0 END)::INTEGER AS v38
                FROM genome.calls c
                INNER JOIN {alias}.rsid_identity r
                    ON c.rsid = r.rsid AND c.chrom = r.chrom
                WHERE c.file_id = ? AND r.pos37 <> r.pos38
                GROUP BY c.chrom
            ) v
            WHERE cs.file_id = ? AND cs.chrom = v.chrom
        """, [file_id, file_id])
        # Majority по аутосомам (и X); Y/MT не решают общий билд файла.
        rows = con.execute("""
            SELECT build, COUNT(*) FROM genome.chrom_stats
            WHERE file_id = ? AND build IN ('37', '38')
              AND chrom NOT IN ('Y', 'MT')
            GROUP BY build
            ORDER BY COUNT(*) DESC, build
        """, [file_id]).fetchall()
        if not rows:
            rows = con.execute("""
                SELECT build, COUNT(*) FROM genome.chrom_stats
                WHERE file_id = ? AND build IN ('37', '38')
                GROUP BY build
                ORDER BY COUNT(*) DESC, build
            """, [file_id]).fetchall()
        return str(rows[0][0]) if rows else None
    finally:
        try:
            con.execute(f"DETACH {alias}")
        except Exception:
            pass


def _write_genome(con, file_id: int, g: Genome) -> None:
    con.execute("DELETE FROM genome.calls WHERE file_id = ?", [file_id])
    con.execute("DELETE FROM genome.chrom_stats WHERE file_id = ?", [file_id])

    appender = con.appender("genome.calls")
    batch: list[tuple] = []
    for chrom_name, c in g.chroms.items():
        for rsid, pos, gt in zip(c.ids, c.pos, c.gts):
            batch.append((file_id, chrom_name, int(pos), rsid, gt))
            if len(batch) >= _BATCH:
                appender.append_rows(batch)
                batch.clear()
        con.execute("""
            INSERT INTO genome.chrom_stats (
                file_id, chrom, n, n_rs, n_nocall, n_het, n_hom, n_hemi, n_other,
                pos_min, pos_max, build, votes_37, votes_38
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, 0)
        """, [
            file_id, chrom_name, c.n, c.rs,
            c.classes.get("nocall", 0), c.classes.get("het", 0),
            c.classes.get("hom", 0), c.classes.get("hemi", 0),
            c.classes.get("other", 0),
            c.pos_min, c.pos_max,
        ])
    if batch:
        appender.append_rows(batch)
    appender.flush()


def ingest(
    path: Path,
    progress: Progress | None = None,
    store: Store | None = None,
    *,
    set_active_flag: bool = False,
    genome: Genome | None = None,
) -> Genome:
    """Распарсить RAW (или взять готовый Genome), записать в DuckDB, проголосовать билд."""
    path = Path(path).expanduser().resolve()
    g = genome if genome is not None else load(path, progress)
    # Big Y CSV / VCF: позиции Known SNP из дерева; placeholder Y:pos → имена.
    # hg38: FTDNA haplotree; hg37: TheYtree hg19.
    if store is not None and "Y" in g.chroms:
        from .store import data_dir
        from .ylink import annotate_genome_y, load_haplotree, resolve_genome_y_positions

        tree = None
        ht = store.path("ftdna_haplotree")
        if ht is None or not ht.is_file():
            cand = data_dir() / "ftdna" / "haplotree" / "y-dna-haplotree.json"
            ht = cand if cand.is_file() else None
        if ht is not None and ht.is_file():
            try:
                tree = load_haplotree(ht)
            except Exception:  # noqa: BLE001
                tree = None
        overlays = None
        try:
            from .overlays import load_overlays
            overlays = load_overlays(store)
        except Exception:  # noqa: BLE001
            overlays = None
        if tree is not None or overlays is not None:
            resolve_genome_y_positions(g, tree=tree, overlays=overlays)
            annotate_genome_y(g, tree=tree, overlays=overlays)
    st = path.stat()
    header_text = "\n".join(g.header)

    with db.locked():
        con = db.connect()
        row = con.execute(
            "SELECT id FROM genome.files WHERE path = ?", [str(path)]
        ).fetchone()
        if row:
            file_id = int(row[0])
            con.execute("""
                UPDATE genome.files SET
                    size = ?, mtime = ?, fmt = ?, layout = ?, member = ?,
                    header = ?, build_header = ?, status = 'ingesting',
                    error = NULL, active = CASE WHEN ? THEN TRUE ELSE active END
                WHERE id = ?
            """, [
                st.st_size, st.st_mtime, g.fmt, g.layout, g.member,
                header_text, g.build, set_active_flag, file_id,
            ])
        else:
            file_id = _next_file_id(con)
            if set_active_flag:
                con.execute("UPDATE genome.files SET active = FALSE WHERE active")
            con.execute("""
                INSERT INTO genome.files (
                    id, path, size, mtime, fmt, layout, member, header,
                    build_header, active, status, snps, malformed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ingesting', ?, ?)
            """, [
                file_id, str(path), st.st_size, st.st_mtime, g.fmt, g.layout,
                g.member, header_text, g.build, set_active_flag, g.total, g.malformed,
            ])

        try:
            _write_genome(con, file_id, g)
            majority = _vote_builds(con, file_id, store)
            _attach_builds(con, file_id, g, majority)
            con.execute("""
                UPDATE genome.files SET
                    status = 'ready', snps = ?, malformed = ?,
                    build_majority = ?, ingested_at = ?, error = NULL
                WHERE id = ?
            """, [
                g.total, g.malformed, majority or g.build,
                datetime.now(timezone.utc), file_id,
            ])
            if set_active_flag:
                con.execute("UPDATE genome.files SET active = FALSE WHERE id <> ?", [file_id])
                con.execute("UPDATE genome.files SET active = TRUE WHERE id = ?", [file_id])
        except Exception as exc:
            con.execute("""
                UPDATE genome.files SET status = 'error', error = ? WHERE id = ?
            """, [str(exc), file_id])
            raise

    return g


def genome_from_db(path: Path, *, only: frozenset[str] | set[str] | None = None) -> Genome:
    """Собрать Genome из DuckDB (без повторного парса RAW).

    only — загружать только эти хромосомы (например {"Y"} для link/branch).
    """
    path = Path(path).expanduser().resolve()
    with db.locked():
        con = db.connect()
        row = con.execute("""
            SELECT id, size, member, header, layout, fmt, build_header, build_majority,
                   malformed, status
            FROM genome.files WHERE path = ?
        """, [str(path)]).fetchone()
        if not row:
            raise FileNotFoundError(f"not in genome.files: {path}")
        file_id, size, member, header, layout, fmt, build_header, build_majority, malformed, status = row
        if status != "ready":
            raise RuntimeError(f"genome file status={status}")

        stats = con.execute("""
            SELECT chrom, n, n_rs, n_nocall, n_het, n_hom, n_hemi, n_other,
                   pos_min, pos_max, build
            FROM genome.chrom_stats WHERE file_id = ?
        """, [file_id]).fetchall()
        stats_by = {r[0]: r for r in stats}

        chrom_names = sorted(stats_by, key=chrom_key)
        if only is not None:
            want = set(only)
            chrom_names = [c for c in chrom_names if c in want]

        chroms: dict[str, Chrom] = {}
        for chrom_name in chrom_names:
            rows = con.execute("""
                SELECT rsid, pos, gt FROM genome.calls
                WHERE file_id = ? AND chrom = ?
                ORDER BY pos
            """, [file_id, chrom_name]).fetchall()
            ids = [r[0] for r in rows]
            pos = array("l", (int(r[1]) for r in rows))
            gts = [r[2] for r in rows]
            c = Chrom(chrom_name, ids, pos, gts)
            s = stats_by[chrom_name]
            c.n = int(s[1])
            c.rs = int(s[2])
            c.classes = Counter({
                "nocall": int(s[3]), "het": int(s[4]), "hom": int(s[5]),
                "hemi": int(s[6]), "other": int(s[7]),
            })
            c.pos_min = s[8]
            c.pos_max = s[9]
            c.sorted_input = True
            c.genotypes = Counter(gts)
            c.lengths = Counter(len(gt) for gt in gts)
            c.prefixes = Counter()
            chroms[chrom_name] = c

        header_lines = header.split("\n") if header else []
        g = Genome(
            path=path, member=member, size=int(size or path.stat().st_size),
            header=header_lines, columns=None, layout=layout or "",
            fmt=fmt or "unknown", build=build_header,
            chroms=chroms, malformed=int(malformed or 0), malformed_sample=[],
        )
        _attach_builds(con, int(file_id), g, build_majority)
        return g


def ensure(
    path: Path,
    progress: Progress | None = None,
    store: Store | None = None,
    *,
    set_active_flag: bool = True,
) -> Genome:
    """Свежий → RAM-кэш / DB; иначе ingest. По умолчанию помечает файл active."""
    path = Path(path).expanduser().resolve()
    hit = _cache_get(path)
    if hit is not None:
        if set_active_flag:
            set_active(path)
        return hit
    if is_fresh(path):
        g = genome_from_db(path)
        _cache_put(g)
        if set_active_flag:
            set_active(path)
        return g
    g = ingest(path, progress, store, set_active_flag=set_active_flag)
    _cache_put(g)
    return g


def same_path(genome: Genome | None, path: Path) -> bool:
    if genome is None:
        return False
    return Path(genome.path).expanduser().resolve() == Path(path).expanduser().resolve()


def get(
    path: Path,
    genome: Genome | None = None,
    progress: Progress | None = None,
    store: Store | None = None,
    *,
    set_active_flag: bool = False,
) -> Genome:
    """Caller genome → RAM-кэш (path,mtime) → DuckDB ensure. RAW только если не ingested."""
    path = Path(path).expanduser().resolve()
    if same_path(genome, path):
        _cache_put(genome)  # type: ignore[arg-type]
        return genome  # type: ignore[return-value]
    return ensure(path, progress, store, set_active_flag=set_active_flag)


def get_y(
    path: Path,
    genome: Genome | None = None,
    progress: Progress | None = None,
    store: Store | None = None,
) -> Genome:
    """Genome с Y для link/branch — без выгрузки аутосом из DuckDB.

    Если уже есть полный Genome (caller / RAM-кэш) с Y — возвращаем его.
    Y-only результат в полный кэш не пишется.
    """
    path = Path(path).expanduser().resolve()
    if same_path(genome, path) and genome is not None and "Y" in genome.chroms:
        return genome
    hit = _cache_get(path)
    if hit is not None and "Y" in hit.chroms:
        return hit
    yhit = _y_cache_get(path)
    if yhit is not None:
        return yhit
    if not is_fresh(path):
        # ingest один раз целиком; дальше Y возьмём из полного кэша
        return ensure(path, progress, store, set_active_flag=False)
    g = genome_from_db(path, only=frozenset({"Y"}))
    _y_cache_put(g)
    return g


def y_rsids(path: Path) -> list[str] | None:
    """Только Y.calls.rsid из DuckDB (без полного Genome). None если файла нет/не ready."""
    path = Path(path).expanduser().resolve()
    if not is_fresh(path):
        return None
    rows = db.fetchall("""
        SELECT c.rsid
        FROM genome.calls c
        JOIN genome.files f ON f.id = c.file_id
        WHERE f.path = ? AND c.chrom = 'Y'
        ORDER BY c.pos
    """, [str(path)])
    return [str(r[0]) for r in rows]


def chrom_builds(path: Path) -> dict[str, str]:
    """chrom → build (37/38/uncertain) из chrom_stats."""
    path = Path(path).expanduser().resolve()
    rows = db.fetchall("""
        SELECT cs.chrom, cs.build
        FROM genome.chrom_stats cs
        JOIN genome.files f ON f.id = cs.file_id
        WHERE f.path = ? AND cs.build IS NOT NULL
    """, [str(path)])
    return {r[0]: r[1] for r in rows}


def _attach_builds(con, file_id: int, g: Genome, majority: str | None) -> None:
    rows = con.execute("""
        SELECT chrom, build, votes_37, votes_38
        FROM genome.chrom_stats WHERE file_id = ?
    """, [file_id]).fetchall()
    g.chrom_builds = {r[0]: r[1] for r in rows if r[1]}
    g.chrom_votes = {r[0]: (int(r[2] or 0), int(r[3] or 0)) for r in rows}
    g.build_detected = majority
    if majority and not g.build:
        g.build = majority
