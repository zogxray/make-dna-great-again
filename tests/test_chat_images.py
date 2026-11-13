"""F7 image split / media URL heuristics (network only in optional resolve tests)."""

from __future__ import annotations

from ydna.core.media_cache import looks_like_image_url
from ydna.workspaces.chat.images import (
    extract_line_images, resolve_display_url, split_markdown_with_images,
)


def test_looks_like_image_url():
    assert looks_like_image_url("https://x.com/a.png")
    assert looks_like_image_url("https://x.com/a.JPG")
    assert not looks_like_image_url(
        "https://commons.wikimedia.org/w/index.php?search=x&title=Special:MediaSearch"
    )


def test_extract_bullet_image_line():
    line = (
        "* ![Denisova 8](https://discover.familytreedna.com/x/Denisova.png) "
        "— restored molar."
    )
    rest, imgs = extract_line_images(line)
    assert len(imgs) == 1
    assert imgs[0][0] == "Denisova 8"
    assert imgs[0][1].endswith(".png")
    assert "restored molar" in rest
    assert "![" not in rest


def test_split_story_pictures_section():
    body = "\n".join([
        "## Pictures & places",
        "",
        "* ![Denisova 8](https://example.com/a.png) — tooth.",
        "* ![Satsurblia](https://commons.wikimedia.org/w/index.php?"
        "search=Satsurblia&title=Special:MediaSearch&type=image) — cave.",
        "* 🖼 [Molar](https://example.com/b.jpg) note",
    ])
    parts = split_markdown_with_images(body)
    urls = [u for _t, u, _c in parts if u]
    assert urls[0] == "https://example.com/a.png"
    assert "MediaSearch" in urls[1]
    assert urls[2] == "https://example.com/b.jpg"
    # Heading kept as text; no raw ![ left in text chunks.
    text_blob = "\n".join(t for t, u, _c in parts if u is None)
    assert "## Pictures" in text_blob
    assert "![" not in text_blob


def test_resolve_display_url_passthrough_file():
    u = "https://example.com/a.png"
    assert resolve_display_url(u) == u
