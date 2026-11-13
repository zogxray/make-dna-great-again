"""Download / resolve chat media for F7 inline previews (Discover + Commons thumbs)."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .store import cache_dir

_UA = "MakeDNAGreatAgain/0.1 (local TUI; +https://github.com/)"
_IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")
_MAX_BYTES = 4_000_000


def media_dir() -> Path:
    d = cache_dir() / "chat_media"
    d.mkdir(parents=True, exist_ok=True)
    return d


def looks_like_image_url(url: str) -> bool:
    u = (url or "").strip().lower()
    if not u.startswith(("http://", "https://")):
        return False
    path = urllib.parse.urlparse(u).path
    if any(path.endswith(ext) for ext in _IMG_EXT):
        return True
    # Commons Special:MediaSearch / wiki pages are HTML, not images.
    if "commons.wikimedia.org" in u and (
        "special:mediasearch" in u or "special:search" in u or "/wiki/" in u
    ):
        return False
    return False


def _cache_path(url: str, *, suffix: str = "") -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    ext = suffix
    if not ext:
        path = urllib.parse.urlparse(url).path
        for e in _IMG_EXT:
            if path.lower().endswith(e):
                ext = e
                break
        else:
            ext = ".img"
    return media_dir() / f"{digest}{ext}"


def _fetch(url: str, *, timeout: float = 25.0) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        data = resp.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise RuntimeError("media_too_large")
    return data, ctype


def resolve_commons_thumbnail(query: str, *, width: int = 640) -> str | None:
    """Best-effort first Commons search hit → thumb URL (not a search page)."""
    q = (query or "").strip()
    if len(q) < 3:
        return None
    # Skip bare sample IDs / haplogroup codes — useless as image queries.
    if re.fullmatch(r"I\d{3,}", q) or re.fullmatch(r"[A-Z]{1,3}-[A-Z0-9]+", q):
        return None
    api = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": q,
        "gsrlimit": 1,
        "gsrnamespace": 6,  # File:
        "prop": "imageinfo",
        "iiprop": "url",
        "iiurlwidth": int(width),
    })
    try:
        raw, _ctype = _fetch(api, timeout=20.0)
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeError):
        return None
    pages = (data.get("query") or {}).get("pages") or {}
    for page in pages.values():
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        thumb = info.get("thumburl") or info.get("url")
        if thumb and str(thumb).startswith("http"):
            return str(thumb)
    return None


def ensure_local_image(url: str, *, timeout: float = 25.0) -> Path | None:
    """Download ``url`` into cache; return path or None."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    if not looks_like_image_url(url) and "upload.wikimedia.org" not in url:
        # Allow wikimedia upload CDN even without extension in path.
        if "upload.wikimedia.org" not in url:
            return None
    path = _cache_path(url)
    meta = path.with_suffix(path.suffix + ".ok")
    if path.is_file() and path.stat().st_size > 32 and meta.is_file():
        return path
    try:
        data, ctype = _fetch(url, timeout=timeout)
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError):
        return None
    if ctype and not ctype.startswith("image/") and not data[:8].startswith(
        (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF")
    ):
        return None
    # Fix extension from content when needed.
    if path.suffix == ".img":
        if data.startswith(b"\x89PNG"):
            path = path.with_suffix(".png")
        elif data.startswith(b"\xff\xd8\xff"):
            path = path.with_suffix(".jpg")
        elif data.startswith(b"GIF8"):
            path = path.with_suffix(".gif")
        elif data[:4] == b"RIFF":
            path = path.with_suffix(".webp")
    path.write_bytes(data)
    meta.write_text(url, encoding="utf-8")
    return path


def materialize_media_slots(slots: list[dict[str, Any]], *, limit: int = 10) -> list[dict[str, Any]]:
    """Turn slots into displayable image URLs (resolve Commons search → thumb)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for slot in slots:
        if len(out) >= limit:
            break
        if not isinstance(slot, dict):
            continue
        url = str(slot.get("url") or "").strip()
        caption = str(slot.get("caption") or "media")
        source = str(slot.get("source") or "")
        resolved = None
        if looks_like_image_url(url) or "upload.wikimedia.org" in url:
            resolved = url
        elif source == "commons_search" or "mediasearch" in url.lower():
            # Prefer caption query after "·"
            q = caption
            if "·" in q:
                q = q.split("·", 1)[-1].strip()
            q = re.sub(r"^Wikimedia search\s*", "", q, flags=re.I).strip()
            resolved = resolve_commons_thumbnail(q)
        if not resolved or resolved in seen:
            continue
        # Prefetch into cache so F7 paint is sync/fast-ish.
        local = ensure_local_image(resolved)
        if local is None:
            continue
        seen.add(resolved)
        out.append({
            **slot,
            "url": resolved,
            "local_path": str(local),
            "source": source or "image",
            "caption": caption[:140],
        })
    return out
