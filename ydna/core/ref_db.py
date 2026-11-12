"""F6 в DuckDB (schema ref): runs / f3 / run_blobs + кэш AADR-каталога + Y badges + summaries."""

from __future__ import annotations

import hashlib
import json
import pickle
import re
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db

KEEP = object()  # не трогать blob / people при save


# ─── F6 SUMMARY briefs (linked to chat.REF_THREAD_ID) ───

def load_summaries() -> dict[str, dict]:
    """subject → {text, model, ts} for the GROUP card cache."""
    rows = db.fetchall(
        "SELECT subject, text, model, updated FROM ref.summaries"
    )
    out: dict[str, dict] = {}
    for subject, text, model, updated in rows:
        out[str(subject)] = {
            "text": str(text or ""),
            "model": str(model or ""),
            "ts": str(updated or ""),
        }
    return out


def load_summary(subject: str) -> dict | None:
    row = db.fetchone(
        "SELECT text, model, updated FROM ref.summaries WHERE subject = ?",
        [subject],
    )
    if not row:
        return None
    text, model, updated = row
    return {"text": str(text or ""), "model": str(model or ""), "ts": str(updated or "")}


def save_summary(subject: str, *, text: str, model: str, thread_id: str,
                 ts: str | None = None) -> None:
    """Upsert brief and point it at the shared Reference chat thread."""
    from .chat import REF_THREAD_ID, now as chat_now

    tid = thread_id or REF_THREAD_ID
    stamp = ts or chat_now()
    with db.locked():
        con = db.connect()
        prev = con.execute(
            "SELECT created FROM ref.summaries WHERE subject = ?", [subject]
        ).fetchone()
        created = str(prev[0]) if prev and prev[0] else stamp
        con.execute(
            """
            INSERT INTO ref.summaries
                (subject, thread_id, text, model, created, updated)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (subject) DO UPDATE SET
                thread_id = excluded.thread_id,
                text = excluded.text,
                model = excluded.model,
                updated = excluded.updated
            """,
            [subject, tid, text, model or "", created, stamp],
        )


# ─── AADR catalog cache ───

def catalog_fingerprint(key: tuple) -> str:
    """Стабильный ключ кэша: (folder, ind_mtime_ns, anno_mtime_ns, version)."""
    folder, ind_m, anno_m, ver = key
    return f"{folder}|{ind_m}|{anno_m}|{ver}"


def _path_fp(path: list[str]) -> str:
    return hashlib.sha1("\0".join(path).encode()).hexdigest()[:16]


def _link_fp(link) -> str:
    """Отпечаток derived-узлов YLink (для y_ours_other); пусто без link."""
    if link is None:
        return ""
    derived = getattr(link, "derived", None) or ()
    marks = getattr(link, "marks", None) or {}
    names = sorted({marks[i].node for i in derived if i in marks and marks[i].node})
    if not names:
        return ""
    return hashlib.sha1("\0".join(names).encode()).hexdigest()[:16]


def _hit_to_json(hit) -> dict:
    return {"node": hit.node, "relation": hit.relation, "y": hit.y}


def _hit_from_json(raw: dict):
    from .aadr import Hit
    return Hit(str(raw.get("node") or ""), str(raw.get("relation") or ""),
               str(raw.get("y") or ""))


def load_y_badges(genome: Path, catalog_fp: str, tree_fp: str, discover_fp: str,
                  tip: str, path: list[str], link) -> dict | None:
    """Кэш Y-бейджей F6 или None при промахе / устаревании."""
    genome = Path(genome).expanduser().resolve()
    row = db.fetchone("""
        SELECT catalog_fp, tree_fp, discover_fp, tip, path_fp, link_fp,
               hits, hits_people, y_ours_other
        FROM ref.y_badges WHERE genome_path = ?
    """, [str(genome)])
    if not row:
        return None
    want_link = _link_fp(link)
    # Без link в руках (чистый F6 cold) — доверяем сохранённым бейджам;
    # если link есть и отпечаток другой — промах (y_ours_other мог смениться).
    if (row[0] != catalog_fp or row[1] != tree_fp or row[2] != discover_fp
            or row[3] != tip or row[4] != _path_fp(path)
            or (want_link and row[5] != want_link)):
        return None
    try:
        hits_raw = row[6] if isinstance(row[6], dict) else json.loads(row[6] or "{}")
        people_raw = row[7] if isinstance(row[7], dict) else json.loads(row[7] or "{}")
        other_raw = row[8] if isinstance(row[8], (list, set)) else json.loads(row[8] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    hits = {k: _hit_from_json(v) for k, v in hits_raw.items() if isinstance(v, dict)}
    hits_people = {k: _hit_from_json(v) for k, v in people_raw.items()
                   if isinstance(v, dict)}
    y_ours_other = {str(x) for x in other_raw}
    return {"hits": hits, "hits_people": hits_people, "y_ours_other": y_ours_other}


def save_y_badges(genome: Path, catalog_fp: str, tree_fp: str, discover_fp: str,
                  tip: str, path: list[str], link, hits: dict, hits_people: dict,
                  y_ours_other: set[str]) -> None:
    genome = Path(genome).expanduser().resolve()
    payload_hits = {k: _hit_to_json(v) for k, v in hits.items()}
    payload_people = {k: _hit_to_json(v) for k, v in hits_people.items()}
    now = datetime.now(timezone.utc)
    with db.locked():
        con = db.connect()
        con.execute("""
            INSERT INTO ref.y_badges (
                genome_path, catalog_fp, tree_fp, discover_fp, tip, path_fp, link_fp,
                hits, hits_people, y_ours_other, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?::JSON, ?::JSON, ?::JSON, ?)
            ON CONFLICT (genome_path) DO UPDATE SET
                catalog_fp = excluded.catalog_fp,
                tree_fp = excluded.tree_fp,
                discover_fp = excluded.discover_fp,
                tip = excluded.tip,
                path_fp = excluded.path_fp,
                link_fp = excluded.link_fp,
                hits = excluded.hits,
                hits_people = excluded.hits_people,
                y_ours_other = excluded.y_ours_other,
                updated_at = excluded.updated_at
        """, [
            str(genome), catalog_fp, tree_fp, discover_fp, tip, _path_fp(path),
            _link_fp(link), json.dumps(payload_hits, ensure_ascii=False),
            json.dumps(payload_people, ensure_ascii=False),
            json.dumps(sorted(y_ours_other), ensure_ascii=False), now,
        ])


def load_catalog_cache(key: tuple):
    """Catalog из ref.catalogs или None."""
    fp = catalog_fingerprint(key)
    row = db.fetchone(
        "SELECT payload FROM ref.catalogs WHERE fingerprint = ?", [fp]
    )
    if not row or row[0] is None:
        return None
    try:
        return pickle.loads(bytes(row[0]))
    except Exception:
        return None


def save_catalog_cache(key: tuple, catalog) -> None:
    """Сохранить Catalog в ref.catalogs; старые fingerprint того же folder — дроп."""
    fp = catalog_fingerprint(key)
    folder = str(Path(key[0]).expanduser().resolve())
    payload = pickle.dumps(catalog, protocol=pickle.HIGHEST_PROTOCOL)
    now = datetime.now(timezone.utc)
    with db.locked():
        con = db.connect()
        con.execute(
            "DELETE FROM ref.catalogs WHERE folder = ? AND fingerprint <> ?",
            [folder, fp],
        )
        con.execute("DELETE FROM ref.catalogs WHERE fingerprint = ?", [fp])
        con.execute("""
            INSERT INTO ref.catalogs (
                fingerprint, folder, prefix, nind, nsnp, payload, built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [
            fp, folder, getattr(catalog, "prefix", None),
            getattr(catalog, "nind", None), getattr(catalog, "nsnp", None),
            payload, now,
        ])


@dataclass
class StoredBundle:
    groups: dict[str, Any]
    people: dict[str, Any]
    partial: bool = False
    snps: int = 0
    outgroup: str = ""
    questionable: bool = False
    blobs: dict[str, Any] | None = None  # pca|qpadm|paint|phase|check
    updated_ns: int | None = None


def _f3_cls():
    from .aadr import F3
    return F3


def _f3_from_row(name: str, est, se, z, n, snps, cluster, f4_z, epoch_cluster):
    F3 = _f3_cls()
    return F3(
        name=name,
        est=float(est or 0),
        se=float(se or 0),
        z=float(z or 0),
        n=int(n or 0),
        snps=int(snps or 0),
        cluster=int(cluster or 0),
        f4_z=float(f4_z or 0),
        epoch_cluster=int(epoch_cluster if epoch_cluster is not None else 1),
    )


def _f3_from_dict(raw: dict):
    F3 = _f3_cls()
    if "est" not in raw:
        return None
    keys = {f.name for f in fields(F3)}
    return F3(**{k: raw[k] for k in keys if k in raw})


def _stem(genome: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", genome.stem)[:60]


def _next_run_id(con) -> int:
    row = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM ref.runs").fetchone()
    return int(row[0])


def find_run_id(genome: Path, panel: str, kind: str = "",
                questionable: bool = False) -> int | None:
    genome = Path(genome).expanduser().resolve()
    row = db.fetchone("""
        SELECT id FROM ref.runs
        WHERE genome_path = ? AND panel = ? AND kind = ? AND questionable = ?
    """, [str(genome), panel, kind or "", bool(questionable)])
    return int(row[0]) if row else None


def results_stamp(genome: Path, panel: str, kind: str = "",
                  questionable: bool = False) -> int:
    """epoch_ns updated_at или 0."""
    genome = Path(genome).expanduser().resolve()
    row = db.fetchone("""
        SELECT CAST(epoch_ns(updated_at) AS BIGINT) FROM ref.runs
        WHERE genome_path = ? AND panel = ? AND kind = ? AND questionable = ?
    """, [str(genome), panel, kind or "", bool(questionable)])
    if not row or row[0] is None:
        return 0
    return int(row[0])


def genome_run_stamps(
    genome: Path, questionable: bool = False,
) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int, int, int]]:
    """One DuckDB round-trip: aadr panel, kg panel, and layers stamps.

    Returns ((aadr_chip, aadr_imp), (kg_chip, kg_imp),
             (aadr_chip, aadr_imp, kg_chip, kg_imp)).
    """
    genome = Path(genome).expanduser().resolve()
    rows = db.fetchall("""
        SELECT panel, kind, CAST(epoch_ns(updated_at) AS BIGINT)
        FROM ref.runs
        WHERE genome_path = ?
          AND kind IN ('', 'imp')
          AND (
            (panel = 'aadr' AND questionable = ?)
            OR (panel = 'kg' AND questionable = FALSE)
          )
    """, [str(genome), bool(questionable)]) or []
    got: dict[tuple[str, str], int] = {}
    for panel, kind, ts in rows:
        got[(str(panel), str(kind or ""))] = int(ts or 0)

    def g(panel: str, kind: str) -> int:
        return got.get((panel, kind), 0)

    aadr_p = (g("aadr", ""), g("aadr", "imp"))
    kg_p = (g("kg", ""), g("kg", "imp"))
    return aadr_p, kg_p, (*aadr_p, *kg_p)


def panel_stamps(genome: Path, panel: str, questionable: bool = False) -> tuple[int, int]:
    """(chip, imp) stamps для UI polling."""
    aadr_p, kg_p, _ = genome_run_stamps(genome, questionable)
    return aadr_p if panel == "aadr" else kg_p


def layer_stamps(genome: Path, questionable: bool = False) -> tuple[int, int, int, int]:
    """aadr chip, aadr imp, kg chip, kg imp."""
    _, _, layers = genome_run_stamps(genome, questionable)
    return layers


def load_bundle(genome: Path, panel: str, kind: str = "",
                questionable: bool = False) -> StoredBundle:
    genome = Path(genome).expanduser().resolve()
    row = db.fetchone("""
        SELECT id, partial, snps, outgroup, questionable,
               CAST(epoch_ns(updated_at) AS BIGINT)
        FROM ref.runs
        WHERE genome_path = ? AND panel = ? AND kind = ? AND questionable = ?
    """, [str(genome), panel, kind or "", bool(questionable)])
    if not row:
        return StoredBundle(groups={}, people={}, questionable=questionable)
    run_id = int(row[0])
    f3_rows = db.fetchall("""
        SELECT scope, name, est, se, z, n, snps, cluster, f4_z, epoch_cluster
        FROM ref.f3 WHERE run_id = ?
    """, [run_id])
    groups: dict[str, Any] = {}
    people: dict[str, Any] = {}
    for scope, name, est, se, z, n, snps, cluster, f4_z, epoch_cluster in f3_rows:
        rec = _f3_from_row(name, est, se, z, n, snps, cluster, f4_z, epoch_cluster)
        if scope == "person":
            people[name] = rec
        else:
            groups[name] = rec
    blob_rows = db.fetchall(
        "SELECT kind, payload FROM ref.run_blobs WHERE run_id = ?", [run_id]
    )
    blobs: dict[str, Any] = {}
    for bkind, payload in blob_rows:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                continue
        blobs[bkind] = payload
    return StoredBundle(
        groups=groups, people=people,
        partial=bool(row[1]), snps=int(row[2] or 0), outgroup=row[3] or "",
        questionable=bool(row[4]), blobs=blobs,
        updated_ns=int(row[5]) if row[5] is not None else None,
    )


def _insert_f3(con, run_id: int, scope: str, items: dict) -> None:
    """Bulk INSERT через Arrow (executemany в DuckDB 2.x — десятки секунд на 10k)."""
    if not items:
        return
    import pyarrow as pa

    names = list(items.keys())
    recs = list(items.values())
    n = len(names)
    table = pa.table({
        "run_id": pa.array([run_id] * n, type=pa.int32()),
        "scope": pa.array([scope] * n, type=pa.string()),
        "name": pa.array(names, type=pa.string()),
        "est": pa.array([float(r.est) for r in recs], type=pa.float64()),
        "se": pa.array([float(r.se) for r in recs], type=pa.float64()),
        "z": pa.array([float(r.z) for r in recs], type=pa.float64()),
        "n": pa.array([int(r.n) for r in recs], type=pa.int32()),
        "snps": pa.array([int(r.snps) for r in recs], type=pa.int32()),
        "cluster": pa.array([int(getattr(r, "cluster", 0)) for r in recs], type=pa.int32()),
        "f4_z": pa.array([float(getattr(r, "f4_z", 0.0)) for r in recs], type=pa.float64()),
        "epoch_cluster": pa.array(
            [int(getattr(r, "epoch_cluster", 1)) for r in recs], type=pa.int32()),
    })
    tag = f"ref_f3_{run_id}_{scope}"
    con.register(tag, table)
    try:
        con.execute(f"INSERT INTO ref.f3 SELECT * FROM {tag}")
    finally:
        try:
            con.unregister(tag)
        except Exception:  # noqa: BLE001
            pass


def save_bundle(
    genome: Path,
    *,
    panel: str,
    kind: str = "",
    questionable: bool = False,
    partial: bool = False,
    outgroup: str = "",
    snps: int = 0,
    groups: dict | None = None,
    people: Any = KEEP,
    blobs: dict[str, Any] | None = None,
) -> None:
    """UPSERT run + f3. people/blobs: KEEP = не трогать существующее.

    blobs: { 'pca': ..., 'qpadm': ... }; ключ отсутствует или KEEP → не менять.
    partial=True не затирает уже complete run.
    """
    genome = Path(genome).expanduser().resolve()
    path_s = str(genome)
    kind = kind or ""
    groups = groups or {}
    blobs = blobs or {}

    with db.locked():
        con = db.connect()
        row = con.execute("""
            SELECT id, partial FROM ref.runs
            WHERE genome_path = ? AND panel = ? AND kind = ? AND questionable = ?
        """, [path_s, panel, kind, bool(questionable)]).fetchone()
        if row and partial and not bool(row[1]):
            # уже есть complete — partial flush пропускаем
            return
        if row:
            run_id = int(row[0])
        else:
            run_id = _next_run_id(con)
            con.execute("""
                INSERT INTO ref.runs (
                    id, genome_path, genome_stem, panel, kind, questionable,
                    partial, outgroup, snps, n_groups, n_people, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
            """, [run_id, path_s, _stem(genome), panel, kind, bool(questionable),
                  bool(partial), outgroup, int(snps),
                  datetime.now(timezone.utc)])

        # people: KEEP → оставить; иначе REPLACE
        if people is KEEP:
            n_people = con.execute(
                "SELECT COUNT(*) FROM ref.f3 WHERE run_id = ? AND scope = 'person'",
                [run_id],
            ).fetchone()[0]
            con.execute(
                "DELETE FROM ref.f3 WHERE run_id = ? AND scope = 'group'", [run_id]
            )
            _insert_f3(con, run_id, "group", groups)
        else:
            people = people or {}
            con.execute("DELETE FROM ref.f3 WHERE run_id = ?", [run_id])
            _insert_f3(con, run_id, "group", groups)
            _insert_f3(con, run_id, "person", people)
            n_people = len(people)

        for bkind, payload in blobs.items():
            if payload is KEEP:
                continue
            if payload is None:
                con.execute(
                    "DELETE FROM ref.run_blobs WHERE run_id = ? AND kind = ?",
                    [run_id, bkind],
                )
                continue
            con.execute("""
                INSERT INTO ref.run_blobs (run_id, kind, payload)
                VALUES (?, ?, ?::JSON)
                ON CONFLICT (run_id, kind) DO UPDATE SET payload = excluded.payload
            """, [run_id, bkind, json.dumps(payload, ensure_ascii=False)])

        con.execute("""
            UPDATE ref.runs SET
                partial = ?, outgroup = ?, snps = ?,
                n_groups = ?, n_people = ?, updated_at = ?,
                genome_stem = ?
            WHERE id = ?
        """, [
            bool(partial), outgroup, int(snps), len(groups), int(n_people),
            datetime.now(timezone.utc), _stem(genome), run_id,
        ])


def import_json_file(path: Path, panel: str) -> int:
    """Импорт одного старого JSON → DB. Число f3-строк."""
    data = json.loads(path.read_text(encoding="utf-8"))
    genome_s = data.get("genome")
    if not genome_s:
        return 0
    genome = Path(genome_s)
    kind = data.get("kind") or ""
    if kind is None:
        kind = ""
    questionable = bool(data.get("questionable")) if panel == "aadr" else False
    groups = {k: r for k, v in (data.get("groups") or {}).items()
              if (r := _f3_from_dict(v))}
    people = {k: r for k, v in (data.get("people") or {}).items()
              if (r := _f3_from_dict(v))}
    if not groups and not people and not any(
        data.get(k) for k in ("pca", "qpadm", "paint", "phase", "check")
    ):
        return 0
    blobs: dict[str, Any] = {}
    for k in ("pca", "qpadm", "paint", "phase", "check"):
        if data.get(k) is not None:
            blobs[k] = data[k]
    save_bundle(
        genome, panel=panel, kind=kind, questionable=questionable,
        partial=bool(data.get("partial")), outgroup=data.get("outgroup") or "",
        snps=int(data.get("snps") or 0), groups=groups, people=people, blobs=blobs,
    )
    return len(groups) + len(people)
