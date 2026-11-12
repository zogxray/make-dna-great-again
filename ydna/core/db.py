"""Единый DuckDB-файл приложения: ~/.config/make-dna-great-again/ydna.duckdb.

Схемы: chat (F6), genome (F1 RAW), data (F2), pgs (F4), ref (F6 results).
Все обращения к соединению — под _lock (UI + фоновые JOBS).
"""

from __future__ import annotations

import threading
from pathlib import Path

from .store import config_dir

SCHEMA_VERSION = 6  # chat.threads.messages_json (avoid ART fatal on chat.messages)
GENOME_SCHEMA_VERSION = 3  # + genome.y_links (F1 YLink cache)
DATA_SCHEMA_VERSION = 2  # + data.openhumans_files (OH catalog / samples)
PGS_SCHEMA_VERSION = 1
REF_SCHEMA_VERSION = 5  # + ref.summaries (F5 SUMMARY ↔ chat.threads)
TREE_SCHEMA_VERSION = 2  # + tree.index_blobs (haplotree / discover caches)
_DB_NAME = "ydna.duckdb"

_lock = threading.RLock()
_con = None  # duckdb.DuckDBPyConnection | None


def db_path() -> Path:
    return config_dir() / _DB_NAME


def _ensure_chat_schema(con) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS chat")
    con.execute("""
        CREATE TABLE IF NOT EXISTS chat.meta (
            key   VARCHAR PRIMARY KEY,
            value VARCHAR NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS chat.threads (
            id              VARCHAR PRIMARY KEY,
            title           VARCHAR NOT NULL,
            created         VARCHAR NOT NULL,
            updated         VARCHAR NOT NULL,
            interaction_id  VARCHAR,
            notes           VARCHAR NOT NULL DEFAULT ''
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS chat.messages (
            thread_id       VARCHAR NOT NULL,
            seq             INTEGER NOT NULL,
            role            VARCHAR NOT NULL,
            text            VARCHAR NOT NULL,
            created_at      VARCHAR NOT NULL,
            interaction_id  VARCHAR,
            PRIMARY KEY (thread_id, seq)
        )
    """)
    # Secondary ART indexes on chat.* caused FatalException on UPDATE/DELETE
    # («Failed to delete all rows from index») — do not recreate them.
    con.execute("DROP INDEX IF EXISTS chat.idx_chat_messages_role")
    con.execute("DROP INDEX IF EXISTS chat.idx_chat_threads_updated")
    _ensure_chat_messages_json(con)
    row = con.execute(
        "SELECT value FROM chat.meta WHERE key = 'schema_version'"
    ).fetchone()
    if not row:
        con.execute(
            "INSERT INTO chat.meta (key, value) VALUES ('schema_version', ?)",
            [str(SCHEMA_VERSION)],
        )
    elif int(row[0]) < SCHEMA_VERSION:
        con.execute(
            "UPDATE chat.meta SET value = ? WHERE key = 'schema_version'",
            [str(SCHEMA_VERSION)],
        )


def _chat_threads_columns(con) -> set[str]:
    rows = con.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'chat' AND table_name = 'threads'
    """).fetchall()
    return {str(r[0]).lower() for r in rows}


def _ensure_chat_messages_json(con) -> None:
    """Persist messages on the thread row — mutating chat.messages trips DuckDB ART."""
    cols = _chat_threads_columns(con)
    if "messages_json" not in cols:
        con.execute("ALTER TABLE chat.threads ADD COLUMN messages_json JSON")
    if "user_turns" not in cols:
        con.execute(
            "ALTER TABLE chat.threads ADD COLUMN user_turns INTEGER DEFAULT 0"
        )
    # One-shot migrate from legacy chat.messages into messages_json.
    pending = con.execute("""
        SELECT id FROM chat.threads
        WHERE messages_json IS NULL
    """).fetchall()
    if not pending:
        return
    import json
    for (tid,) in pending:
        try:
            msgs = con.execute("""
                SELECT role, text, created_at, interaction_id
                FROM chat.messages WHERE thread_id = ? ORDER BY seq
            """, [tid]).fetchall()
        except Exception:  # noqa: BLE001 — corrupted ART on legacy messages
            msgs = []
        payload = [
            {
                "role": r, "text": t, "at": a or "",
                "interaction_id": iid,
            }
            for r, t, a, iid in msgs
        ]
        n_user = sum(1 for m in payload if m["role"] == "user")
        con.execute("""
            UPDATE chat.threads
            SET messages_json = ?::JSON, user_turns = ?
            WHERE id = ?
        """, [json.dumps(payload, ensure_ascii=False), n_user, tid])


def _ensure_genome_schema(con) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS genome")
    con.execute("""
        CREATE TABLE IF NOT EXISTS genome.meta (
            key   VARCHAR PRIMARY KEY,
            value VARCHAR NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS genome.files (
            id              INTEGER PRIMARY KEY,
            path            VARCHAR UNIQUE NOT NULL,
            size            BIGINT,
            mtime           DOUBLE,
            sha1            VARCHAR,
            fmt             VARCHAR,
            layout          VARCHAR,
            member          VARCHAR,
            header          VARCHAR,
            build_header    VARCHAR,
            build_majority  VARCHAR,
            active          BOOLEAN NOT NULL DEFAULT FALSE,
            status          VARCHAR NOT NULL DEFAULT 'ready',
            snps            INTEGER,
            malformed       INTEGER,
            ingested_at     TIMESTAMP,
            error           VARCHAR
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS genome.chrom_stats (
            file_id         INTEGER NOT NULL,
            chrom           VARCHAR NOT NULL,
            n               INTEGER NOT NULL,
            n_rs            INTEGER NOT NULL,
            n_nocall        INTEGER NOT NULL,
            n_het           INTEGER NOT NULL,
            n_hom           INTEGER NOT NULL,
            n_hemi          INTEGER NOT NULL,
            n_other         INTEGER NOT NULL,
            pos_min         INTEGER,
            pos_max         INTEGER,
            build           VARCHAR,
            votes_37        INTEGER NOT NULL DEFAULT 0,
            votes_38        INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (file_id, chrom)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS genome.calls (
            file_id         INTEGER NOT NULL,
            chrom           VARCHAR NOT NULL,
            pos             INTEGER NOT NULL,
            rsid            VARCHAR NOT NULL,
            gt              VARCHAR NOT NULL
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_calls_locus "
        "ON genome.calls (file_id, chrom, pos)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_calls_rsid "
        "ON genome.calls (file_id, rsid)"
    )
    # F1 Linking Y cache: marks + fingerprints (genome / haplotree / Discover).
    con.execute("""
        CREATE TABLE IF NOT EXISTS genome.y_links (
            genome_path       VARCHAR PRIMARY KEY,
            payload           JSON NOT NULL,
            genome_mtime_ns   VARCHAR NOT NULL,
            tree_fp           VARCHAR NOT NULL,
            discover_fp       VARCHAR NOT NULL,
            updated_at        TIMESTAMP
        )
    """)
    # Не индексируем genome.files(active): DuckDB 2.x ART падает fatal на
    # UPDATE … SET active=FALSE WHERE active (Failed to delete all rows from index).
    # Файлов мало — seq scan дешёвый.
    row = con.execute(
        "SELECT value FROM genome.meta WHERE key = 'schema_version'"
    ).fetchone()
    if not row:
        con.execute(
            "INSERT INTO genome.meta (key, value) VALUES ('schema_version', ?)",
            [str(GENOME_SCHEMA_VERSION)],
        )
    elif int(row[0]) < GENOME_SCHEMA_VERSION:
        if int(row[0]) < 2:
            con.execute("DROP INDEX IF EXISTS genome.idx_files_active")
        con.execute(
            "UPDATE genome.meta SET value = ? WHERE key = 'schema_version'",
            [str(GENOME_SCHEMA_VERSION)],
        )


def _ensure_data_schema(con) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS data")
    con.execute("""
        CREATE TABLE IF NOT EXISTS data.meta (
            key   VARCHAR PRIMARY KEY,
            value VARCHAR NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS data.settings (
            key   VARCHAR PRIMARY KEY,
            value VARCHAR NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS data.secrets (
            key         VARCHAR PRIMARY KEY,
            value       VARCHAR NOT NULL,
            updated_at  TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS data.sources (
            key             VARCHAR PRIMARY KEY,
            title           VARCHAR NOT NULL,
            group_name      VARCHAR,
            kind            VARCHAR NOT NULL,
            path            VARCHAR,
            state           VARCHAR NOT NULL DEFAULT 'unset',
            summary         VARCHAR,
            version         VARCHAR,
            upstream        VARCHAR,
            bytes_total     BIGINT,
            item_count      BIGINT,
            facts_json      VARCHAR,
            manifest_json   VARCHAR,
            can_download    BOOLEAN NOT NULL DEFAULT FALSE,
            can_verify      BOOLEAN NOT NULL DEFAULT FALSE,
            checked_at      TIMESTAMP,
            downloaded_at   TIMESTAMP,
            error           VARCHAR
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS data.files (
            source_key      VARCHAR NOT NULL,
            relpath         VARCHAR NOT NULL,
            size            BIGINT,
            mtime           DOUBLE,
            ok              BOOLEAN NOT NULL DEFAULT TRUE,
            note            VARCHAR,
            PRIMARY KEY (source_key, relpath)
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_sources_state ON data.sources (state)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_sources_group ON data.sources (group_name)"
    )
    # Open Humans public catalog + local/genome ingest status (F2).
    con.execute("""
        CREATE TABLE IF NOT EXISTS data.openhumans_files (
            id              BIGINT PRIMARY KEY,
            basename        VARCHAR,
            download_url    VARCHAR,
            source          VARCHAR,
            user_id         VARCHAR,
            username        VARCHAR,
            user_name       VARCHAR,
            tags_join       VARCHAR,
            description     VARCHAR,
            ext             VARCHAR,
            y_hint          BOOLEAN,
            vendor          VARCHAR,
            compat          VARCHAR,
            kind            VARCHAR,
            local_path      VARCHAR,
            local_size      BIGINT,
            local_mtime     DOUBLE,
            db_status       VARCHAR NOT NULL DEFAULT 'catalog',
            genome_file_id  INTEGER,
            snps            INTEGER,
            error           VARCHAR,
            downloaded_at   TIMESTAMP,
            ingested_at     TIMESTAMP,
            updated_at      TIMESTAMP
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_oh_compat ON data.openhumans_files (compat)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_oh_db_status ON data.openhumans_files (db_status)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_oh_vendor ON data.openhumans_files (vendor)"
    )
    row = con.execute(
        "SELECT value FROM data.meta WHERE key = 'schema_version'"
    ).fetchone()
    if not row:
        con.execute(
            "INSERT INTO data.meta (key, value) VALUES ('schema_version', ?)",
            [str(DATA_SCHEMA_VERSION)],
        )
    elif int(row[0]) < DATA_SCHEMA_VERSION:
        con.execute(
            "UPDATE data.meta SET value = ? WHERE key = 'schema_version'",
            [str(DATA_SCHEMA_VERSION)],
        )


def _ensure_pgs_schema(con) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS pgs")
    con.execute("""
        CREATE TABLE IF NOT EXISTS pgs.meta (
            key   VARCHAR PRIMARY KEY,
            value VARCHAR NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS pgs.models (
            pgs_id          VARCHAR PRIMARY KEY,
            theme           VARCHAR,
            trait           VARCHAR,
            efo             VARCHAR,
            variants        INTEGER,
            usable          INTEGER,
            cov_w           DOUBLE,
            eur_pct         DOUBLE,
            method          VARCHAR,
            build           VARCHAR,
            publication     VARCHAR,
            pub_date        VARCHAR,
            status          VARCHAR,
            has_weights     BOOLEAN NOT NULL DEFAULT FALSE,
            synced_at       TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS pgs.menus (
            id              VARCHAR PRIMARY KEY,
            title           VARCHAR NOT NULL,
            kind            VARCHAR NOT NULL,
            sort_ord        INTEGER NOT NULL,
            active          BOOLEAN NOT NULL DEFAULT TRUE
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS pgs.menu_items (
            menu_id         VARCHAR NOT NULL,
            ord             INTEGER NOT NULL,
            pgs_id          VARCHAR NOT NULL,
            label           VARCHAR,
            theme           VARCHAR,
            PRIMARY KEY (menu_id, ord),
            UNIQUE (menu_id, pgs_id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS pgs.runs (
            id              INTEGER PRIMARY KEY,
            genome_path     VARCHAR UNIQUE NOT NULL,
            genome_stem     VARCHAR,
            file_id         INTEGER,
            updated_at      TIMESTAMP,
            n_results       INTEGER
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS pgs.results (
            run_id          INTEGER NOT NULL,
            pgs_id          VARCHAR NOT NULL,
            trait           VARCHAR,
            theme           VARCHAR,
            weights         INTEGER,
            matched         INTEGER,
            used            INTEGER,
            score           DOUBLE,
            hist_at         INTEGER,
            error           VARCHAR,
            computed        TIMESTAMP,
            PRIMARY KEY (run_id, pgs_id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS pgs.result_groups (
            run_id          INTEGER NOT NULL,
            pgs_id          VARCHAR NOT NULL,
            group_key       VARCHAR NOT NULL,
            n               INTEGER,
            mean            DOUBLE,
            sd              DOUBLE,
            pct             DOUBLE,
            z               DOUBLE,
            PRIMARY KEY (run_id, pgs_id, group_key)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS pgs.result_hist (
            run_id          INTEGER NOT NULL,
            pgs_id          VARCHAR NOT NULL,
            bin             INTEGER NOT NULL,
            count           INTEGER NOT NULL,
            PRIMARY KEY (run_id, pgs_id, bin)
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_models_status_theme "
        "ON pgs.models (status, theme)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_models_theme_cov "
        "ON pgs.models (theme, cov_w DESC)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_menu_items_pgs "
        "ON pgs.menu_items (pgs_id)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_results_pgs "
        "ON pgs.results (pgs_id)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_groups_key "
        "ON pgs.result_groups (run_id, group_key)"
    )
    row = con.execute(
        "SELECT value FROM pgs.meta WHERE key = 'schema_version'"
    ).fetchone()
    if not row:
        con.execute(
            "INSERT INTO pgs.meta (key, value) VALUES ('schema_version', ?)",
            [str(PGS_SCHEMA_VERSION)],
        )
    elif int(row[0]) < PGS_SCHEMA_VERSION:
        con.execute(
            "UPDATE pgs.meta SET value = ? WHERE key = 'schema_version'",
            [str(PGS_SCHEMA_VERSION)],
        )


def _ensure_ref_schema(con) -> None:
    """F5: результаты (f3/…) + кэш AADR-каталога (ref.catalogs)."""
    con.execute("CREATE SCHEMA IF NOT EXISTS ref")
    con.execute("""
        CREATE TABLE IF NOT EXISTS ref.meta (
            key   VARCHAR PRIMARY KEY,
            value VARCHAR NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS ref.runs (
            id              INTEGER PRIMARY KEY,
            genome_path     VARCHAR NOT NULL,
            genome_stem     VARCHAR,
            panel           VARCHAR NOT NULL,
            kind            VARCHAR NOT NULL DEFAULT '',
            questionable    BOOLEAN NOT NULL DEFAULT FALSE,
            partial         BOOLEAN NOT NULL DEFAULT FALSE,
            outgroup        VARCHAR,
            snps            INTEGER,
            n_groups        INTEGER,
            n_people        INTEGER,
            updated_at      TIMESTAMP,
            UNIQUE (genome_path, panel, kind, questionable)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS ref.f3 (
            run_id          INTEGER NOT NULL,
            scope           VARCHAR NOT NULL,
            name            VARCHAR NOT NULL,
            est             DOUBLE,
            se              DOUBLE,
            z               DOUBLE,
            n               INTEGER,
            snps            INTEGER,
            cluster         INTEGER,
            f4_z            DOUBLE,
            epoch_cluster   INTEGER,
            PRIMARY KEY (run_id, scope, name)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS ref.run_blobs (
            run_id          INTEGER NOT NULL,
            kind            VARCHAR NOT NULL,
            payload         JSON NOT NULL,
            PRIMARY KEY (run_id, kind)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS ref.catalogs (
            fingerprint     VARCHAR PRIMARY KEY,
            folder          VARCHAR NOT NULL,
            prefix          VARCHAR,
            nind            INTEGER,
            nsnp            INTEGER,
            payload         BLOB NOT NULL,
            built_at        TIMESTAMP
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_ref_runs_genome "
        "ON ref.runs (genome_path, panel)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_ref_catalogs_folder "
        "ON ref.catalogs (folder)"
    )
    con.execute("""
        CREATE TABLE IF NOT EXISTS ref.y_badges (
            genome_path     VARCHAR PRIMARY KEY,
            catalog_fp      VARCHAR NOT NULL,
            tree_fp         VARCHAR NOT NULL,
            discover_fp     VARCHAR NOT NULL,
            tip             VARCHAR NOT NULL,
            path_fp         VARCHAR NOT NULL,
            link_fp         VARCHAR NOT NULL,
            hits            JSON NOT NULL,
            hits_people     JSON NOT NULL,
            y_ours_other    JSON NOT NULL,
            updated_at      TIMESTAMP
        )
    """)
    # F5 SUMMARY briefs: one shared chat.threads row (chat.REF_THREAD_ID).
    con.execute("""
        CREATE TABLE IF NOT EXISTS ref.summaries (
            subject         VARCHAR PRIMARY KEY,
            thread_id       VARCHAR NOT NULL,
            text            VARCHAR NOT NULL,
            model           VARCHAR NOT NULL DEFAULT '',
            created         VARCHAR NOT NULL,
            updated         VARCHAR NOT NULL
        )
    """)
    # PK (run_id, scope, name) хватает; secondary ART + DELETE run_id ломал DuckDB.
    row = con.execute(
        "SELECT value FROM ref.meta WHERE key = 'schema_version'"
    ).fetchone()
    if not row:
        con.execute(
            "INSERT INTO ref.meta (key, value) VALUES ('schema_version', ?)",
            [str(REF_SCHEMA_VERSION)],
        )
    elif int(row[0]) < REF_SCHEMA_VERSION:
        if int(row[0]) < 2:
            con.execute("DROP INDEX IF EXISTS ref.idx_ref_f3_rank")
        con.execute(
            "UPDATE ref.meta SET value = ? WHERE key = 'schema_version'",
            [str(REF_SCHEMA_VERSION)],
        )


def _ensure_tree_schema(con) -> None:
    """F3: slim tree + Y branches + кэши haplotree/discover (index_blobs)."""
    con.execute("CREATE SCHEMA IF NOT EXISTS tree")
    con.execute("""
        CREATE TABLE IF NOT EXISTS tree.meta (
            key   VARCHAR PRIMARY KEY,
            value VARCHAR NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tree.releases (
            id              INTEGER PRIMARY KEY,
            fingerprint     VARCHAR UNIQUE NOT NULL,
            haplotree_path  VARCHAR,
            discover_path   VARCHAR,
            n_nodes         INTEGER,
            n_discover      INTEGER,
            built_at        TIMESTAMP,
            stats           JSON,
            issues          JSON
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tree.nodes (
            release_id          INTEGER NOT NULL,
            name                VARCHAR NOT NULL,
            parent              VARCHAR,
            placeholder         BOOLEAN NOT NULL,
            haplotree           BOOLEAN NOT NULL,
            source_file         VARCHAR,
            root                VARCHAR,
            hg_id               VARCHAR,
            date_label          VARCHAR,
            date_mean           INTEGER,
            date_range          VARCHAR,
            branch_date         INTEGER,
            placed_samples      INTEGER,
            uncertain_samples   INTEGER,
            children_count      INTEGER,
            descendants_count   INTEGER,
            height              INTEGER,
            rank                INTEGER,
            rich                VARCHAR,
            crown               BOOLEAN,
            deep                BOOLEAN,
            countries           JSON,
            flags               JSON,
            markers             JSON,
            formed              JSON,
            tmrca               JSON,
            tree_version        VARCHAR,
            PRIMARY KEY (release_id, name)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tree.children (
            release_id      INTEGER NOT NULL,
            parent          VARCHAR NOT NULL,
            ord             INTEGER NOT NULL,
            child           VARCHAR NOT NULL,
            PRIMARY KEY (release_id, parent, ord)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tree.search (
            release_id      INTEGER NOT NULL,
            name            VARCHAR NOT NULL,
            haystack        VARCHAR NOT NULL,
            PRIMARY KEY (release_id, name)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tree.branches (
            genome_path     VARCHAR PRIMARY KEY,
            tip             VARCHAR NOT NULL,
            path            JSON NOT NULL,
            der             JSON,
            anc             JSON,
            derived_marks   INTEGER,
            genome_mtime_ns VARCHAR,
            tree_fp         VARCHAR,
            discover_fp     VARCHAR,
            updated_at      TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tree.index_blobs (
            kind            VARCHAR NOT NULL,
            fingerprint     VARCHAR NOT NULL,
            source          VARCHAR NOT NULL,
            payload         BLOB NOT NULL,
            built_at        TIMESTAMP,
            PRIMARY KEY (kind, fingerprint)
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_tree_nodes_parent "
        "ON tree.nodes (release_id, parent)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_tree_nodes_rich "
        "ON tree.nodes (release_id, rich)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_tree_children_child "
        "ON tree.children (release_id, child)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_tree_index_blobs_source "
        "ON tree.index_blobs (kind, source)"
    )
    row = con.execute(
        "SELECT value FROM tree.meta WHERE key = 'schema_version'"
    ).fetchone()
    if not row:
        con.execute(
            "INSERT INTO tree.meta (key, value) VALUES ('schema_version', ?)",
            [str(TREE_SCHEMA_VERSION)],
        )
    elif int(row[0]) < TREE_SCHEMA_VERSION:
        con.execute(
            "UPDATE tree.meta SET value = ? WHERE key = 'schema_version'",
            [str(TREE_SCHEMA_VERSION)],
        )


def _ensure_schema(con) -> None:
    _ensure_chat_schema(con)
    _ensure_genome_schema(con)
    _ensure_data_schema(con)
    _ensure_pgs_schema(con)
    _ensure_ref_schema(con)
    _ensure_tree_schema(con)


def connect():
    """Вернуть живое соединение (создаёт файл и schema при первом вызове)."""
    global _con
    import duckdb

    with _lock:
        if _con is None:
            path = db_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                _con = duckdb.connect(str(path))
            except Exception as exc:
                from .single import db_busy_message, is_db_busy_error
                if is_db_busy_error(exc):
                    raise RuntimeError(db_busy_message()) from None
                raise
            _ensure_schema(_con)
            # Сид каталога F2 / меню PGS (ленивый import — без циклов).
            from . import sources_db
            sources_db.seed_sources(_con)
            from . import pgs_db
            pgs_db.seed_menus(_con)
        return _con


def _reset() -> None:
    """Закрыть invalidated connection (после DuckDB FatalException)."""
    global _con
    if _con is not None:
        try:
            _con.close()
        except Exception:  # noqa: BLE001
            pass
        _con = None


def locked():
    """Контекст: захват lock вокруг пачки SQL."""
    return _lock


def close() -> None:
    with _lock:
        _reset()


def _is_invalidated(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "invalidated" in msg or "fatal" in type(exc).__name__.lower()


def execute(sql: str, params: list | tuple | None = None):
    """Выполнить SQL под lock. Для чтения предпочтительны fetchone/fetchall —
    они забирают результат, пока соединение ещё занято только этим вызовом."""
    with _lock:
        try:
            con = connect()
            if params is None:
                return con.execute(sql)
            return con.execute(sql, list(params))
        except Exception as exc:
            if _is_invalidated(exc):
                _reset()
                con = connect()
                if params is None:
                    return con.execute(sql)
                return con.execute(sql, list(params))
            raise


def executemany(sql: str, params_seq) -> None:
    with _lock:
        try:
            con = connect()
            con.executemany(sql, params_seq)
        except Exception as exc:
            if _is_invalidated(exc):
                _reset()
                connect().executemany(sql, params_seq)
            else:
                raise


def fetchall(sql: str, params: list | tuple | None = None) -> list:
    with _lock:
        try:
            con = connect()
            if params is None:
                return con.execute(sql).fetchall()
            return con.execute(sql, list(params)).fetchall()
        except Exception as exc:
            if _is_invalidated(exc):
                _reset()
                con = connect()
                if params is None:
                    return con.execute(sql).fetchall()
                return con.execute(sql, list(params)).fetchall()
            raise


def fetchone(sql: str, params: list | tuple | None = None):
    with _lock:
        try:
            con = connect()
            if params is None:
                return con.execute(sql).fetchone()
            return con.execute(sql, list(params)).fetchone()
        except Exception as exc:
            if _is_invalidated(exc):
                _reset()
                con = connect()
                if params is None:
                    return con.execute(sql).fetchone()
                return con.execute(sql, list(params)).fetchone()
            raise
