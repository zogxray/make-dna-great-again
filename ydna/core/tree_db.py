"""F3 slim tree index + Y branches + кэши haplotree/discover в DuckDB (schema tree).

Тела Discover JSON остаются файлами; карточка гидратит по source_file.
"""

from __future__ import annotations

import json
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db

# kind для tree.index_blobs
KIND_HAPLOTREE = "haplotree"
KIND_DISCOVER = "discover"
KIND_OVERLAYS = "overlays"


def index_fingerprint(key: tuple) -> str:
    return "|".join(str(x) for x in key)


def load_index_blob(kind: str, key: tuple):
    """Распакованный payload из tree.index_blobs или None."""
    fp = index_fingerprint(key)
    row = db.fetchone(
        "SELECT payload FROM tree.index_blobs WHERE kind = ? AND fingerprint = ?",
        [kind, fp],
    )
    if not row or row[0] is None:
        return None
    try:
        return pickle.loads(bytes(row[0]))
    except Exception:
        return None


def save_index_blob(kind: str, key: tuple, source: Path | str, payload) -> None:
    """Сохранить payload; старые fingerprint того же kind+source — дроп."""
    fp = index_fingerprint(key)
    # Path → resolve; голый ярлык ("overlays") оставляем как есть.
    src = (str(source.expanduser().resolve()) if isinstance(source, Path)
           else str(source))
    blob = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    now = datetime.now(timezone.utc)
    with db.locked():
        con = db.connect()
        con.execute(
            "DELETE FROM tree.index_blobs WHERE kind = ? AND source = ? "
            "AND fingerprint <> ?",
            [kind, src, fp],
        )
        con.execute(
            "DELETE FROM tree.index_blobs WHERE kind = ? AND fingerprint = ?",
            [kind, fp],
        )
        con.execute("""
            INSERT INTO tree.index_blobs (kind, fingerprint, source, payload, built_at)
            VALUES (?, ?, ?, ?, ?)
        """, [kind, fp, src, blob, now])

# Тяжёлые Discover-поля: общие пустые (hydrate подменяет целиком, не mutate).
_EL: list = []
_ED: dict = {}


def find_release(fingerprint: str) -> int | None:
    row = db.fetchone(
        "SELECT id FROM tree.releases WHERE fingerprint = ?", [fingerprint]
    )
    return int(row[0]) if row else None


def release_status() -> dict | None:
    """Краткая сводка активного F3-индекса для F2 (или None)."""
    row = db.fetchone("""
        SELECT n_nodes, n_discover, built_at
        FROM tree.releases
        ORDER BY id DESC LIMIT 1
    """)
    if not row:
        return None
    return {
        "n_nodes": int(row[0] or 0),
        "n_discover": int(row[1] or 0),
        "built_at": row[2],
    }


def _next_id(con) -> int:
    row = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM tree.releases").fetchone()
    return int(row[0])


def _j(val: Any) -> str:
    return json.dumps(val, ensure_ascii=False)


def save_release(
    fingerprint: str,
    data: dict[str, Any],
    *,
    haplotree: Path,
    discover: Path | None,
) -> int:
    """Полная замена индекса: один активный release (единственный пользователь)."""
    import pyarrow as pa

    nodes: dict = data["nodes"]
    children: dict = data["children"]
    haystack: dict = data.get("haystack") or {}
    stats = data.get("stats") or {}
    issues = data.get("issues") or {}
    n_disc = sum(1 for n in nodes.values() if not n.get("placeholder"))

    names = list(nodes)
    n = len(names)

    def col(key, default=None, cast=lambda x: x):
        return [cast(nodes[nm].get(key, default)) for nm in names]

    def jcol(key, default):
        return [_j(nodes[nm].get(key, default)) for nm in names]

    node_table = pa.table({
        "name": pa.array(names, type=pa.string()),
        "parent": pa.array(col("parent"), type=pa.string()),
        "placeholder": pa.array(col("placeholder", True, bool), type=pa.bool_()),
        "haplotree": pa.array(col("haplotree", True, bool), type=pa.bool_()),
        "source_file": pa.array(col("source_file"), type=pa.string()),
        "root": pa.array([None if nodes[nm].get("root") is None else str(nodes[nm].get("root"))
                          for nm in names], type=pa.string()),
        "hg_id": pa.array([None if nodes[nm].get("hg_id") is None else str(nodes[nm].get("hg_id"))
                           for nm in names], type=pa.string()),
        "date_label": pa.array(col("date", ""), type=pa.string()),
        "date_mean": pa.array(col("date_mean"), type=pa.int64()),
        "date_range": pa.array(col("date_range"), type=pa.string()),
        "branch_date": pa.array(col("branch_date"), type=pa.int64()),
        "placed_samples": pa.array(col("placed_samples", 0, lambda x: int(x or 0)), type=pa.int32()),
        "uncertain_samples": pa.array(col("uncertain_samples", 0, lambda x: int(x or 0)), type=pa.int32()),
        "children_count": pa.array(col("children_count", 0, lambda x: int(x or 0)), type=pa.int32()),
        "descendants_count": pa.array(col("descendants_count", 0, lambda x: int(x or 0)), type=pa.int32()),
        "height": pa.array(col("height", 0, lambda x: int(x or 0)), type=pa.int32()),
        "rank": pa.array(col("rank"), type=pa.int32()),
        "rich": pa.array(col("rich", "normal", str), type=pa.string()),
        "crown": pa.array(col("crown", False, bool), type=pa.bool_()),
        "deep": pa.array(col("deep", False, bool), type=pa.bool_()),
        "countries": pa.array(jcol("countries", []), type=pa.string()),
        "flags": pa.array(jcol("flags", []), type=pa.string()),
        "markers": pa.array(jcol("markers", []), type=pa.string()),
        "formed": pa.array(jcol("formed", {}), type=pa.string()),
        "tmrca": pa.array(jcol("tmrca", {}), type=pa.string()),
        "tree_version": pa.array(
            [None if nodes[nm].get("tree_version") is None else str(nodes[nm]["tree_version"])
             for nm in names], type=pa.string()),
    })

    ch_parents: list[str] = []
    ch_ords: list[int] = []
    ch_kids: list[str] = []
    for parent, kids in children.items():
        for i, kid in enumerate(kids):
            ch_parents.append(parent)
            ch_ords.append(i)
            ch_kids.append(kid)
    child_table = pa.table({
        "parent": pa.array(ch_parents, type=pa.string()),
        "ord": pa.array(ch_ords, type=pa.int32()),
        "child": pa.array(ch_kids, type=pa.string()),
    }) if ch_parents else None

    s_names = list(haystack)
    search_table = pa.table({
        "name": pa.array(s_names, type=pa.string()),
        "haystack": pa.array([haystack[k] for k in s_names], type=pa.string()),
    }) if s_names else None

    with db.locked():
        con = db.connect()
        # один индекс на пользователя — сносим предыдущие
        con.execute("DELETE FROM tree.children")
        con.execute("DELETE FROM tree.search")
        con.execute("DELETE FROM tree.nodes")
        con.execute("DELETE FROM tree.releases")
        rid = _next_id(con)
        con.execute("""
            INSERT INTO tree.releases (
                id, fingerprint, haplotree_path, discover_path,
                n_nodes, n_discover, built_at, stats, issues
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?::JSON, ?::JSON)
        """, [
            rid, fingerprint, str(haplotree.resolve()),
            str(discover.resolve()) if discover else None,
            n, n_disc, datetime.now(timezone.utc),
            _j(stats), _j(issues),
        ])
        con.register("tree_nodes_in", node_table)
        try:
            con.execute(f"""
                INSERT INTO tree.nodes
                SELECT {rid}, name, parent, placeholder, haplotree, source_file,
                       root, hg_id, date_label, date_mean, date_range, branch_date,
                       placed_samples, uncertain_samples, children_count,
                       descendants_count, height, rank, rich, crown, deep,
                       countries::JSON, flags::JSON, markers::JSON,
                       formed::JSON, tmrca::JSON, tree_version
                FROM tree_nodes_in
            """)
        finally:
            con.unregister("tree_nodes_in")
        if child_table is not None:
            con.register("tree_ch_in", child_table)
            try:
                con.execute(f"""
                    INSERT INTO tree.children
                    SELECT {rid}, parent, ord, child FROM tree_ch_in
                """)
            finally:
                con.unregister("tree_ch_in")
        if search_table is not None:
            con.register("tree_s_in", search_table)
            try:
                con.execute(f"""
                    INSERT INTO tree.search
                    SELECT {rid}, name, haystack FROM tree_s_in
                """)
            finally:
                con.unregister("tree_s_in")
    return rid


def _parse_json(val: Any, default):
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (TypeError, ValueError):
        return default


def _pj_list(val: Any):
    """JSON-list колонка: пустые → общий _EL без json.loads."""
    if val is None or val == "[]" or val == []:
        return _EL
    if isinstance(val, list):
        return val if val else _EL
    try:
        parsed = json.loads(val)
    except (TypeError, ValueError):
        return _EL
    return parsed if parsed else _EL


def _pj_dict(val: Any):
    """JSON-object колонка: пустые → общий _ED без json.loads."""
    if val is None or val == "{}" or val == {}:
        return _ED
    if isinstance(val, dict):
        return val if val else _ED
    try:
        parsed = json.loads(val)
    except (TypeError, ValueError):
        return _ED
    return parsed if parsed else _ED


def load_release(release_id: int) -> dict[str, Any]:
    """Собрать nodes/children/roots/stats/issues/haystack (slim; heavy пустые)."""
    from collections import defaultdict

    rel = db.fetchone("""
        SELECT stats, issues FROM tree.releases WHERE id = ?
    """, [release_id])
    if not rel:
        raise KeyError(f"tree release {release_id}")
    stats = _parse_json(rel[0], _ED)
    issues = _parse_json(rel[1], _ED)

    rows = db.fetchall("""
        SELECT name, parent, placeholder, haplotree, source_file, root, hg_id,
               date_label, date_mean, date_range, branch_date,
               placed_samples, uncertain_samples, children_count,
               descendants_count, height, rank, rich, crown, deep,
               countries, flags, markers, formed, tmrca, tree_version
        FROM tree.nodes WHERE release_id = ?
    """, [release_id])
    nodes: dict[str, dict] = {}
    for r in rows:
        name = r[0]
        nodes[name] = {
            "name": name,
            "parent": r[1],
            "placeholder": bool(r[2]),
            "haplotree": bool(r[3]),
            "source_file": r[4],
            "root": r[5],
            "hg_id": r[6],
            "date": r[7] or "",
            "date_mean": r[8],
            "date_range": r[9],
            "branch_date": r[10],
            "placed_samples": int(r[11] or 0),
            "uncertain_samples": int(r[12] or 0),
            "children_count": int(r[13] or 0),
            "descendants_count": int(r[14] or 0),
            "height": int(r[15] or 0),
            "rank": r[16],
            "rich": r[17] or "normal",
            "crown": bool(r[18]),
            "deep": bool(r[19]),
            "countries": _pj_list(r[20]),
            "flags": _pj_list(r[21]),
            "markers": _pj_list(r[22]),
            "formed": _pj_dict(r[23]),
            "tmrca": _pj_dict(r[24]),
            "tree_version": r[25],
            "_hydrated": False,
            # heavy Discover — общие пустые; hydrate подменяет ключи
            "variants": _EL, "ancient": _EL, "countries_data": _EL,
            "geochart_countries": _EL, "ancestors": _EL, "upgrades": _ED,
            "famous": _EL, "projects": _EL, "badges": _EL,
            "globetrekker": _ED, "migrationmap": _ED, "placements": None,
            "declared_children": _EL,
        }

    ch_rows = db.fetchall("""
        SELECT parent, ord, child FROM tree.children
        WHERE release_id = ? ORDER BY parent, ord
    """, [release_id])
    children: dict[str, list[str]] = defaultdict(list)
    for parent, _ord, child in ch_rows:
        children[parent].append(child)
    children = dict(children)

    roots = [n for n, node in nodes.items()
             if not node.get("parent") or node["parent"] == n
             or node["parent"] not in nodes]
    # стабильный порядок как в finalize: по branch_date
    def root_key(name: str) -> tuple:
        y = nodes[name].get("branch_date")
        return (y is None, y if y is not None else 0, name.lower())
    roots.sort(key=root_key)

    hay = db.fetchall(
        "SELECT name, haystack FROM tree.search WHERE release_id = ?", [release_id]
    )
    haystack = {n: h for n, h in hay}

    return {
        "nodes": nodes, "children": children, "roots": roots,
        "stats": stats, "issues": issues, "haystack": haystack,
    }


def branch_fps(genome: Path, haplotree: Path | None, discover: Path | None) -> tuple[str, str, str]:
    import os

    genome = Path(genome)
    gmt = str(genome.stat().st_mtime_ns) if genome.is_file() else ""
    if haplotree and Path(haplotree).is_file():
        st = Path(haplotree).stat()
        tfp = f"{st.st_mtime_ns}:{st.st_size}"
    else:
        tfp = ""
    dfp = ""
    if discover and Path(discover).is_dir():
        n = size = newest = 0
        with os.scandir(discover) as it:
            for e in it:
                if e.name.endswith(".json") and e.is_file():
                    st = e.stat()
                    n += 1
                    size += st.st_size
                    if st.st_mtime_ns > newest:
                        newest = st.st_mtime_ns
        dfp = f"{n}:{size}:{newest}"
    return gmt, tfp, dfp


def load_branch(genome: Path, haplotree: Path | None, discover: Path | None):
    """YBranch из DB или None если промах / устарело."""
    from .ylink import YBranch

    genome = Path(genome).expanduser().resolve()
    gmt, tfp, dfp = branch_fps(genome, haplotree, discover)
    row = db.fetchone("""
        SELECT tip, path, der, anc, derived_marks, genome_mtime_ns, tree_fp, discover_fp
        FROM tree.branches WHERE genome_path = ?
    """, [str(genome)])
    if not row:
        return None
    if row[5] != gmt or row[6] != tfp or row[7] != dfp:
        return None
    path = _parse_json(row[1], [])
    der = _parse_json(row[2], {})
    anc = _parse_json(row[3], {})
    return YBranch(row[0], path, der, anc, int(row[4] or 0))


def save_branch(genome: Path, branch, haplotree: Path | None, discover: Path | None) -> None:
    genome = Path(genome).expanduser().resolve()
    gmt, tfp, dfp = branch_fps(genome, haplotree, discover)
    with db.locked():
        con = db.connect()
        con.execute("""
            INSERT INTO tree.branches (
                genome_path, tip, path, der, anc, derived_marks,
                genome_mtime_ns, tree_fp, discover_fp, updated_at
            ) VALUES (?, ?, ?::JSON, ?::JSON, ?::JSON, ?, ?, ?, ?, ?)
            ON CONFLICT (genome_path) DO UPDATE SET
                tip = excluded.tip,
                path = excluded.path,
                der = excluded.der,
                anc = excluded.anc,
                derived_marks = excluded.derived_marks,
                genome_mtime_ns = excluded.genome_mtime_ns,
                tree_fp = excluded.tree_fp,
                discover_fp = excluded.discover_fp,
                updated_at = excluded.updated_at
        """, [
            str(genome), branch.tip, _j(branch.path), _j(branch.der), _j(branch.anc),
            int(branch.derived_marks), gmt, tfp, dfp, datetime.now(timezone.utc),
        ])
