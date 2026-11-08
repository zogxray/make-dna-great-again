"""PGS в DuckDB (schema pgs): models, menus/menu_items, runs/results."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db


def seed_menus(con=None) -> None:
    """Системные вкладки: catalog (все ok) + curated (selection из CURATED)."""
    from .pgs import CURATED

    menus = [
        ("curated", "CURATED", "selection", 0, True),
        ("catalog", "CATALOG", "catalog", 1, True),
    ]
    items = [
        (i, pid, label, theme)
        for i, (pid, theme, label) in enumerate(CURATED)
    ]
    sql_menus = """
        INSERT INTO pgs.menus (id, title, kind, sort_ord, active)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            title = excluded.title,
            kind = excluded.kind,
            sort_ord = excluded.sort_ord,
            active = excluded.active
    """
    if con is not None:
        con.executemany(sql_menus, menus)
        con.execute("DELETE FROM pgs.menu_items WHERE menu_id = 'curated'")
        con.executemany("""
            INSERT INTO pgs.menu_items (menu_id, ord, pgs_id, label, theme)
            VALUES ('curated', ?, ?, ?, ?)
        """, items)
    else:
        with db.locked():
            c = db.connect()
            c.executemany(sql_menus, menus)
            c.execute("DELETE FROM pgs.menu_items WHERE menu_id = 'curated'")
            c.executemany("""
                INSERT INTO pgs.menu_items (menu_id, ord, pgs_id, label, theme)
                VALUES ('curated', ?, ?, ?, ?)
            """, items)


def model_count() -> int:
    row = db.fetchone("SELECT COUNT(*) FROM pgs.models")
    return int(row[0]) if row else 0


def _catalog_mtime_ns(folder: Path) -> str:
    from .pgs import catalog_path
    return str(catalog_path(folder).stat().st_mtime_ns)


def catalog_fresh(folder: Path) -> bool:
    """True, если pgs.models уже синхронизированы с текущим catalog.tsv."""
    from .pgs import catalog_path
    folder = Path(folder)
    cat = catalog_path(folder)
    if not cat.is_file() or model_count() == 0:
        return False
    row = db.fetchone("SELECT value FROM pgs.meta WHERE key = 'catalog_mtime_ns'")
    if not row:
        return False
    try:
        return row[0] == str(cat.stat().st_mtime_ns)
    except OSError:
        return False


def sync_catalog(folder: Path) -> int:
    """Прочитать catalog.tsv → UPSERT pgs.models. Возвращает число моделей."""
    from .pgs import read_catalog_index

    folder = Path(folder)
    index = read_catalog_index(folder)
    data = folder / "data"
    # PGS001234.txt.gz → pgs_id (суффикс ".txt.gz" = 7 символов)
    have = {p.name[:-7] for p in data.glob("PGS*.txt.gz")} if data.is_dir() else set()
    now = datetime.now(timezone.utc)
    rows = []
    for m in index.values():
        rows.append((
            m.pgs_id, m.theme, m.trait, m.efo, m.variants, m.usable,
            m.cov_w, m.eur_pct, m.method, m.build, m.publication, m.pub_date,
            m.status, m.pgs_id in have, now,
        ))
    mtime = _catalog_mtime_ns(folder)
    with db.locked():
        con = db.connect()
        con.executemany("""
            INSERT INTO pgs.models (
                pgs_id, theme, trait, efo, variants, usable, cov_w, eur_pct,
                method, build, publication, pub_date, status, has_weights, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (pgs_id) DO UPDATE SET
                theme = excluded.theme,
                trait = excluded.trait,
                efo = excluded.efo,
                variants = excluded.variants,
                usable = excluded.usable,
                cov_w = excluded.cov_w,
                eur_pct = excluded.eur_pct,
                method = excluded.method,
                build = excluded.build,
                publication = excluded.publication,
                pub_date = excluded.pub_date,
                status = excluded.status,
                has_weights = excluded.has_weights,
                synced_at = excluded.synced_at
        """, rows)
        _write_catalog_meta(con, folder, mtime)
    seed_menus()
    return len(rows)


def _write_catalog_meta(con, folder: Path, mtime: str | None = None) -> None:
    if mtime is None:
        mtime = _catalog_mtime_ns(folder)
    con.execute("""
        INSERT INTO pgs.meta (key, value) VALUES ('catalog_mtime_ns', ?)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value
    """, [mtime])
    con.execute("""
        INSERT INTO pgs.meta (key, value) VALUES ('catalog_folder', ?)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value
    """, [str(Path(folder).resolve())])


def ensure_catalog(folder: Path, *, force: bool = False) -> int:
    """Sync TSV→DB только если force / пусто / catalog.tsv новее. Иначе — COUNT из DB."""
    folder = Path(folder)
    if not force and catalog_fresh(folder):
        return model_count()
    n = model_count()
    if not force and n > 0:
        # Модели уже есть (миграция), метки mtime ещё нет — не гоняем TSV снова.
        row = db.fetchone("SELECT value FROM pgs.meta WHERE key = 'catalog_mtime_ns'")
        if row is None:
            with db.locked():
                _write_catalog_meta(db.connect(), folder)
            return n
    return sync_catalog(folder)


def list_menus() -> list[dict[str, Any]]:
    rows = db.fetchall("""
        SELECT id, title, kind, sort_ord
        FROM pgs.menus WHERE active
        ORDER BY sort_ord, id
    """)
    return [
        {"id": r[0], "title": r[1], "kind": r[2], "sort_ord": r[3]}
        for r in rows
    ]


def _row_model(r) -> "Model":
    from .pgs import Model
    return Model(
        pgs_id=r[0], theme=r[1] or "", trait=r[2] or "", efo=r[3] or "",
        variants=int(r[4] or 0), usable=int(r[5] or 0),
        cov_w=r[6], eur_pct=r[7], method=r[8] or "", build=r[9] or "",
        publication=r[10] or "", pub_date=r[11] or "",
        status=r[12] or "",
    )


_MODEL_COLS = (
    "pgs_id, theme, trait, efo, variants, usable, cov_w, eur_pct, "
    "method, build, publication, pub_date, status"
)


def load_catalog_ok() -> list:
    """Модели status=ok, как бывший load_catalog (порядок theme / cov_w)."""
    from .pgs import THEMES
    order = {k: i for i, (k, _) in enumerate(THEMES)}
    rows = db.fetchall(f"""
        SELECT {_MODEL_COLS} FROM pgs.models WHERE status = 'ok'
    """)
    out = [_row_model(r) for r in rows]
    out.sort(key=lambda m: (order.get(m.theme, 99), -(m.cov_w or 0), m.pgs_id))
    return out


def load_menu_models(menu_id: str) -> list:
    """Модели вкладки selection (label/theme override из menu_items)."""
    from .pgs import Model

    rows = db.fetchall(f"""
        SELECT i.ord, i.pgs_id, i.label, i.theme,
               m.theme, m.trait, m.efo, m.variants, m.usable, m.cov_w, m.eur_pct,
               m.method, m.build, m.publication, m.pub_date, m.status
        FROM pgs.menu_items i
        LEFT JOIN pgs.models m ON m.pgs_id = i.pgs_id
        WHERE i.menu_id = ?
        ORDER BY i.ord
    """, [menu_id])
    out = []
    for r in rows:
        pid = r[1]
        label = r[2] or (r[5] or pid)
        theme = r[3] or (r[4] or "")
        status = r[15] or "missing"
        out.append(Model(
            pid, theme, label, r[6] or "", int(r[7] or 0), int(r[8] or 0),
            r[9], r[10], r[11] or "", r[12] or "", r[13] or "", r[14] or "",
            status=status,
        ))
    return out


def _next_run_id(con) -> int:
    row = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM pgs.runs").fetchone()
    return int(row[0])


def ensure_run(genome: Path) -> int:
    genome = Path(genome).expanduser().resolve()
    path_s = str(genome)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", genome.stem)[:60]
    with db.locked():
        con = db.connect()
        row = con.execute(
            "SELECT id FROM pgs.runs WHERE genome_path = ?", [path_s]
        ).fetchone()
        if row:
            return int(row[0])
        rid = _next_run_id(con)
        con.execute("""
            INSERT INTO pgs.runs (id, genome_path, genome_stem, updated_at, n_results)
            VALUES (?, ?, ?, ?, 0)
        """, [rid, path_s, stem, datetime.now(timezone.utc)])
        return rid


def save_result(genome: Path, res) -> None:
    """UPSERT одного Result (+ groups + hist)."""
    from .pgs import HIST_BINS

    run_id = ensure_run(genome)
    computed = None
    if res.computed:
        try:
            computed = datetime.fromisoformat(res.computed)
        except ValueError:
            computed = datetime.now(timezone.utc)
    else:
        computed = datetime.now(timezone.utc)

    with db.locked():
        con = db.connect()
        con.execute("""
            INSERT INTO pgs.results (
                run_id, pgs_id, trait, theme, weights, matched, used,
                score, hist_at, error, computed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, pgs_id) DO UPDATE SET
                trait = excluded.trait,
                theme = excluded.theme,
                weights = excluded.weights,
                matched = excluded.matched,
                used = excluded.used,
                score = excluded.score,
                hist_at = excluded.hist_at,
                error = excluded.error,
                computed = excluded.computed
        """, [
            run_id, res.pgs_id, res.trait, res.theme, res.weights, res.matched,
            res.used, res.score, res.hist_at, res.error or None, computed,
        ])
        con.execute(
            "DELETE FROM pgs.result_groups WHERE run_id = ? AND pgs_id = ?",
            [run_id, res.pgs_id],
        )
        g_rows = []
        for key, g in (res.groups or {}).items():
            g_rows.append((
                run_id, res.pgs_id, key, g.n, g.mean, g.sd, g.pct, g.z,
            ))
        if g_rows:
            con.executemany("""
                INSERT INTO pgs.result_groups
                    (run_id, pgs_id, group_key, n, mean, sd, pct, z)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, g_rows)
        con.execute(
            "DELETE FROM pgs.result_hist WHERE run_id = ? AND pgs_id = ?",
            [run_id, res.pgs_id],
        )
        hist = list(res.hist or [])
        if hist:
            # добиваем/обрезаем до HIST_BINS для стабильности
            if len(hist) < HIST_BINS:
                hist = hist + [0] * (HIST_BINS - len(hist))
            hist = hist[:HIST_BINS]
            con.executemany("""
                INSERT INTO pgs.result_hist (run_id, pgs_id, bin, count)
                VALUES (?, ?, ?, ?)
            """, [(run_id, res.pgs_id, i, int(c)) for i, c in enumerate(hist)])
        n = con.execute(
            "SELECT COUNT(*) FROM pgs.results WHERE run_id = ? AND (error IS NULL OR error = '')",
            [run_id],
        ).fetchone()[0]
        con.execute("""
            UPDATE pgs.runs SET updated_at = ?, n_results = ? WHERE id = ?
        """, [datetime.now(timezone.utc), int(n), run_id])


def load_results(genome: Path) -> dict[str, Any]:
    """dict[pgs_id, Result] для генома."""
    from .pgs import GroupStat, HIST_BINS, Result

    genome = Path(genome).expanduser().resolve()
    row = db.fetchone(
        "SELECT id FROM pgs.runs WHERE genome_path = ?", [str(genome)]
    )
    if not row:
        return {}
    run_id = int(row[0])
    res_rows = db.fetchall("""
        SELECT pgs_id, trait, theme, weights, matched, used, score, hist_at, error, computed
        FROM pgs.results WHERE run_id = ?
    """, [run_id])
    grp_rows = db.fetchall("""
        SELECT pgs_id, group_key, n, mean, sd, pct, z
        FROM pgs.result_groups WHERE run_id = ?
    """, [run_id])
    hist_rows = db.fetchall("""
        SELECT pgs_id, bin, count FROM pgs.result_hist WHERE run_id = ?
    """, [run_id])

    groups: dict[str, dict[str, GroupStat]] = {}
    for pgs_id, gkey, n, mean, sd, pct, z in grp_rows:
        groups.setdefault(pgs_id, {})[gkey] = GroupStat(
            int(n or 0), float(mean or 0), float(sd or 0),
            float(pct or 0), z,
        )
    hists: dict[str, list[int]] = {}
    for pgs_id, bn, cnt in hist_rows:
        h = hists.setdefault(pgs_id, [0] * HIST_BINS)
        if 0 <= int(bn) < HIST_BINS:
            h[int(bn)] = int(cnt or 0)

    out: dict[str, Result] = {}
    for r in res_rows:
        pid = r[0]
        computed = ""
        if r[9] is not None:
            computed = r[9].isoformat(timespec="seconds") if hasattr(r[9], "isoformat") else str(r[9])
        out[pid] = Result(
            pgs_id=pid, trait=r[1] or "", theme=r[2] or "",
            weights=int(r[3] or 0), matched=int(r[4] or 0), used=int(r[5] or 0),
            score=r[6], hist_at=r[7], error=r[8] or "",
            computed=computed,
            groups=groups.get(pid, {}),
            hist=hists.get(pid, []),
        )
    return out


def results_stamp(genome: Path) -> int | None:
    """Метка изменения run для polling (epoch ns); None если run нет."""
    genome = Path(genome).expanduser().resolve()
    row = db.fetchone(
        "SELECT CAST(epoch_ns(updated_at) AS BIGINT) FROM pgs.runs WHERE genome_path = ?",
        [str(genome)],
    )
    if not row or row[0] is None:
        return None
    return int(row[0])


def scored_genomes() -> list[Path]:
    rows = db.fetchall("SELECT genome_path FROM pgs.runs ORDER BY updated_at DESC NULLS LAST")
    out = []
    for (p,) in rows:
        path = Path(p)
        if path.is_file() and path not in out:
            out.append(path)
    return out


def import_results_json(path: Path) -> int:
    """Импорт одного старого ~/.config/.../pgs/*.json → DB. Число результатов."""
    import json
    from .pgs import Result

    data = json.loads(path.read_text(encoding="utf-8"))
    genome = Path(data["genome"])
    n = 0
    for _pid, raw in (data.get("results") or {}).items():
        save_result(genome, Result.from_dict(raw))
        n += 1
    return n
