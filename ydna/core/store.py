"""Настройки приложения: config_dir, data_dir; пути/секреты/settings — в DuckDB (schema data)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

APP_TITLE = "Make DNA Great Again"
APP_SLUG = "make-dna-great-again"
_LEGACY_SLUG = "ydna-explorer"


def _xdg_app_dir(kind: str) -> Path:
    """XDG config/cache: новый slug; один раз переименовать legacy ydna-explorer → mdga."""
    if kind == "cache":
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    new = base / APP_SLUG
    old = base / _LEGACY_SLUG
    if not new.exists() and old.exists():
        try:
            old.rename(new)
        except OSError:
            return old
    return new


def config_dir() -> Path:
    return _xdg_app_dir("config")


def cache_dir() -> Path:
    return _xdg_app_dir("cache")


def project_root() -> Path:
    """Корень репозитория make-dna-great-again/ (родитель пакета ydna/)."""
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Локальный склад данных: genomes, ftdna, aadr, kg, pgs, cache."""
    return project_root() / "data"


class Store:
    """Пути к источникам, секреты и settings — только DuckDB (data.*)."""

    def __init__(self) -> None:
        # Гарантируем schema + seed каталога F2.
        from . import db
        db.connect()

    def path(self, key: str) -> Path | None:
        from . import sources_db
        return sources_db.get_source_path(key)

    def set_path(self, key: str, path: Path | None) -> None:
        from . import sources_db
        sources_db.set_source_path(key, path.resolve() if path else None)

    def secret(self, key: str) -> str | None:
        from . import db
        row = db.fetchone("SELECT value FROM data.secrets WHERE key = ?", [key])
        if not row:
            return None
        v = (row[0] or "").strip()
        return v or None

    def set_secret(self, key: str, value: str | None) -> None:
        from . import db
        v = (value or "").strip()
        with db.locked():
            con = db.connect()
            if v:
                con.execute("""
                    INSERT INTO data.secrets (key, value, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT (key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                """, [key, v, datetime.now(timezone.utc)])
            else:
                con.execute("DELETE FROM data.secrets WHERE key = ?", [key])

    def setting(self, key: str) -> str | None:
        from . import db
        row = db.fetchone("SELECT value FROM data.settings WHERE key = ?", [key])
        if not row:
            return None
        v = (row[0] or "").strip()
        return v or None

    def set_setting(self, key: str, value: str | None) -> None:
        from . import db
        v = (value or "").strip()
        with db.locked():
            con = db.connect()
            if v:
                con.execute("""
                    INSERT INTO data.settings (key, value) VALUES (?, ?)
                    ON CONFLICT (key) DO UPDATE SET value = excluded.value
                """, [key, v])
            else:
                con.execute("DELETE FROM data.settings WHERE key = ?", [key])
