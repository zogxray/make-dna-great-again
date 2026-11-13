"""Terminal image previews for F7 (half-block Unicode via Pillow)."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from rich.console import Group, RenderableType
from rich.text import Text

from ...core.media_cache import (
    ensure_local_image, looks_like_image_url, resolve_commons_thumbnail,
)
from ...theme.styles import DIM, MUTED

# Image / picture-link anywhere on a line (list bullets OK).
_IMG_MD = re.compile(
    r"(?:🖼\s*)?!\[([^\]]*)\]\((https?://[^)\s]+)\)",
)
_LINK_FILE = re.compile(
    r"(?:🖼\s*)?\[([^\]]*)\]\((https?://[^)\s]+\.(?:png|jpe?g|webp|gif)(?:\?[^)]*)?)\)",
    re.I,
)

_HALF_CACHE: dict[tuple[str, int], Text] = {}
_HALF_CACHE_MAX = 32


def _rgb_style(r: int, g: int, b: int) -> str:
    return f"rgb({r},{g},{b})"


def halfblock_image(path: Path, *, max_cols: int = 56, max_rows: int = 18) -> Text | None:
    """Render an image as ▄ half-blocks (2 pixels per terminal row)."""
    try:
        from PIL import Image
    except ImportError:
        return None
    key = (str(path.resolve()), max_cols)
    cached = _HALF_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        im = Image.open(path)
        im = im.convert("RGBA")
    except OSError:
        return None
    w, h = im.size
    if w < 2 or h < 2:
        return None
    tw = max(8, min(max_cols, w))
    th_px = max(4, int(round(h * (tw / w))))
    if th_px % 2:
        th_px += 1
    max_px = max_rows * 2
    if th_px > max_px:
        th_px = max_px
        tw = max(8, int(round(w * (th_px / h))))
    im = im.resize((tw, th_px), Image.Resampling.LANCZOS)
    px = im.load()
    out = Text()
    for y in range(0, th_px, 2):
        out.append("  ")
        for x in range(tw):
            r1, g1, b1, a1 = px[x, y]
            r2, g2, b2, a2 = px[x, y + 1] if y + 1 < th_px else (0, 0, 0, 0)
            if a1 < 16 and a2 < 16:
                out.append(" ")
                continue
            if a1 < 16:
                out.append("▄", style=_rgb_style(r2, g2, b2))
            elif a2 < 16:
                out.append("▀", style=_rgb_style(r1, g1, b1))
            else:
                out.append(
                    "▄",
                    style=f"{_rgb_style(r2, g2, b2)} on {_rgb_style(r1, g1, b1)}",
                )
        out.append("\n")
    if len(_HALF_CACHE) >= _HALF_CACHE_MAX:
        _HALF_CACHE.pop(next(iter(_HALF_CACHE)), None)
    _HALF_CACHE[key] = out
    return out


def _commons_query_from_url(url: str) -> str | None:
    u = urlparse(url)
    if "commons.wikimedia.org" not in (u.netloc or ""):
        return None
    qs = parse_qs(u.query)
    if qs.get("search"):
        return unquote(qs["search"][0]).strip() or None
    # /wiki/File:Name
    path = unquote(u.path or "")
    if "/wiki/File:" in path or "/wiki/File%3A" in path:
        name = path.split("File:", 1)[-1].split("File%3A", 1)[-1]
        return name.replace("_", " ").strip() or None
    return None


def resolve_display_url(url: str, caption: str = "") -> str | None:
    """Map Story media href → a downloadable image URL (or None)."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    if looks_like_image_url(url) or "upload.wikimedia.org" in url or "thumb.wikimedia.org" in url:
        return url
    q = _commons_query_from_url(url)
    if not q and caption:
        q = re.sub(r"^Wikimedia search\s*[·•-]?\s*", "", caption, flags=re.I).strip()
    if q:
        return resolve_commons_thumbnail(q)
    return None


def extract_line_images(line: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (line_without_image_markdown, [(caption, url), ...])."""
    found: list[tuple[str, str]] = []

    def take(m: re.Match[str]) -> str:
        cap = (m.group(1) or "").strip() or "image"
        url = m.group(2)
        found.append((cap, url))
        return ""

    # Prefer explicit image markdown, then file links.
    cleaned = _IMG_MD.sub(take, line)
    if not found:
        cleaned = _LINK_FILE.sub(take, line)
    # Tidy leftover list markers / em-dashes.
    cleaned = re.sub(r"^\s*[*\-•]\s*", "", cleaned)
    cleaned = re.sub(r"^\s*[—–-]\s*", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned, found


def split_markdown_with_images(body: str) -> list[tuple[str, str | None, str]]:
    """Split markdown into (text_chunk, image_url|None, caption) segments."""
    chunks: list[tuple[str, str | None, str]] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            chunks.append(("\n".join(buf), None, ""))
            buf.clear()

    for line in (body or "").splitlines():
        rest, images = extract_line_images(line)
        if not images:
            buf.append(line)
            continue
        flush()
        for cap, url in images:
            chunks.append(("", url, cap))
        if rest:
            # Keep trailing description as plain text under the preview.
            chunks.append((rest, None, ""))
    flush()
    return chunks


def render_model_body(body: str, *, md_render, width: int) -> RenderableType:
    """Markdown text + inline half-block previews (resolve Commons search on the fly)."""
    parts = split_markdown_with_images(body)
    has_img = any(u for _t, u, _c in parts)
    if not has_img:
        return md_render(body, width=width)

    renderables: list[RenderableType] = []
    for text, url, caption in parts:
        if text.strip():
            renderables.append(md_render(text, width=width))
        if not url:
            continue
        resolved = resolve_display_url(url, caption)
        cap = Text()
        cap.append("  🖼 ", style=MUTED)
        cap.append(caption or url, style=DIM)
        cap.append("\n")
        renderables.append(cap)
        if not resolved:
            miss = Text()
            miss.append("  (no image file for this link)\n", style=MUTED)
            miss.append(f"  {url}\n", style=DIM)
            renderables.append(miss)
            continue
        local = ensure_local_image(resolved)
        if local is None:
            miss = Text()
            miss.append("  (could not load image)\n", style=MUTED)
            miss.append(f"  {resolved}\n", style=DIM)
            renderables.append(miss)
            continue
        img = halfblock_image(local, max_cols=min(56, max(24, width - 4)))
        if img is None:
            miss = Text()
            miss.append("  (image preview unavailable — install pillow)\n", style=MUTED)
            renderables.append(miss)
        else:
            renderables.append(img)
        link = Text()
        link.append(f"  {resolved}\n", style=DIM)
        renderables.append(link)
    return Group(*renderables) if renderables else md_render(body, width=width)
