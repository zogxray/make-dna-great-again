"""Track A playground: headless Pilot smoke tests for MDGA wgpu screens."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual_wgpu")
from textual_wgpu.mode_app import ModeApp
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import Digits, MarkdownViewer, Rule, Sparkline

from ydna.tui.wgpu_help import HelpScreen
from ydna.tui.wgpu_jobs import JobsScreen
from ydna.tui.wgpu_samples import SamplesScreen


def _theme() -> Theme:
    return Theme.from_mapping({
        "background": "#000000",
        "surface": "#0C0E12",
        "panel": "#12151C",
        "boost": "#1A1F2A",
        "foreground": "#B8D4F0",
        "muted": "#6A849E",
        "accent": "#729FCF",
        "primary": "#3D5A7A",
    })


def test_wgpu_help_markdown_viewer_and_jobs_widgets(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    async def _run():
        app = ModeApp(
            [
                ("jobs", "Jobs", JobsScreen),
                ("samples", "Samples", SamplesScreen),
                ("help", "Help", HelpScreen),
            ],
            theme=_theme(),
            start="jobs",
        )
        async with app.run_test(size=(1000, 680)) as pilot:
            await pilot.pause()
            # F8 Jobs exercises Digits + Sparkline + Rule
            dg = pilot.query_one("#jobs-count", Digits)
            sp = pilot.query_one("#jobs-spark", Sparkline)
            rl = pilot.query_one("#jobs-rule", Rule)
            assert dg.value == "0"
            assert sp is not None and rl is not None

            # Switch to Help (F3 in this 3-mode harness) -> MarkdownViewer + expanded TOC tree
            await pilot.press("f3")
            await pilot.pause()
            mv = pilot.query_one("#help-viewer", MarkdownViewer)
            assert mv.show_table_of_contents is True
            assert len(mv.table_of_contents.root.children) == 1
            h1 = mv.table_of_contents.root.children[0]
            assert h1.is_expanded is True
            titles = [c.label for c in h1.children]
            assert "F1 · Genome" in titles and "F2 · Data" in titles and "F10 · Help" in titles
            # F2 · Data contains nested bold subtitles
            f2_node = next(c for c in h1.children if c.label == "F2 · Data")
            assert f2_node.is_expanded is True
            assert any("GWAS Catalog" in sub.label for sub in f2_node.children)
            # Anchor jumps land on the exact heading line (0px drift)
            from textual_wgpu.fonts import cell_height

            sc = mv._scroll
            ch = cell_height(mv.document.FONT_SIZE)
            strips = mv.document.render_lines()
            for slug, expected in (("f1-genome", "F1 · Genome"), ("gwas-catalog", "GWAS Catalog"), ("f5-gwas", "F5 · GWAS")):
                assert mv.goto_anchor(slug) is True
                landed = int(round(sc.scroll_py)) // ch
                assert expected.lower() == strips[landed].plain().strip().lower()
            # Smooth pixel scroll via wheel & 1-line up/down
            sc.scroll_to(y=0)
            app.on_wheel(sc.rect.x + 20, sc.rect.y + 20, 0, 12.0)
            assert 0 < sc.scroll_py < sc._row_h
            await pilot.press("t")
            assert mv.show_table_of_contents is False
            await pilot.press("t")
            assert mv.show_table_of_contents is True

            # F8 Jobs(X) badge + harmony crimson color when active jobs exist
            from textual_wgpu.widgets import ModeBar
            from ydna.core.jobs import JOBS, Job
            from ydna.theme.styles import RED
            from ydna.tui.app_api import _hex_rgba

            mb = pilot.query_one(ModeBar)
            assert "F8 Jobs " in mb.render_lines()[0].plain()
            JOBS.items["_test_active"] = Job("_test_active", "Test job", status="running")
            try:
                strips_mb = mb.render_lines()
                plain_mb = strips_mb[0].plain()
                assert "F8 Jobs(1)" in plain_mb
                red_rgba = _hex_rgba(RED)
                assert any(
                    "Jobs(1)" in seg.text and seg.style == red_rgba
                    for seg in strips_mb[0].segments
                )
            finally:
                JOBS.items.pop("_test_active", None)


def test_wgpu_chat_has_scrollbar_and_scrolls_to_bottom_on_enter(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    from textual_wgpu.widgets import Scrollbar, VerticalScroll
    from ydna.core import chat
    from ydna.tui.wgpu_chat import ChatScreen

    t = chat.new_thread()
    t.title = "Long thread"
    for i in range(25):
        t.messages.append(chat.Message("user" if i % 2 == 0 else "model", f"Message line {i}\n" * 2))
    chat.save(t)

    async def _run():
        app = ModeApp(
            [
                ("help", "Help", HelpScreen),
                ("chat", "Chat", ChatScreen),
            ],
            theme=_theme(),
            start="chat",
        )
        async with app.run_test(size=(1000, 600)) as pilot:
            await pilot.pause()
            app.screen.reload(keep=t.id)
            await pilot.pause()
            sc = pilot.query_one("#thread-scroll", VerticalScroll)
            assert isinstance(sc._scrollbar, Scrollbar)
            assert sc._max_scroll() > 0
            assert sc.scroll_y == sc._max_scroll()
            # Re-entering Chat from Help also scrolls to bottom
            sc.scroll_home()
            assert sc.scroll_y == 0
            await pilot.press("f1")
            await pilot.pause()
            await pilot.press("f2")
            await pilot.pause()
            assert sc.scroll_y == sc._max_scroll()

            # F7 Chat has titled CHATS / CHAT · ... / MESSAGE / NOTES panels & clean Tab / Shift+Tab
            from textual_wgpu.widgets import Panel

            panels = {p.id: p for p in app.screen.walk_descendants() if isinstance(p, Panel)}
            assert panels["chats-box"].title == "CHATS"
            assert panels["thread-box"].title.startswith("CHAT")
            assert panels["prompt-box"].title == "MESSAGE"
            assert panels["notes-box"].title == "NOTES"
            assert panels["thread-box"].rect.h > 200
            assert sc.rect.h > 150
            assert panels["prompt-box"].rect.h == 132
            assert panels["chats-box"].rect.h > 200
            assert panels["notes-box"].rect.h > 200

            stops = [getattr(w, "id", None) for w in app._focusables()]
            assert stops == ["chats", "thread-scroll", "prompt", "notes"]
            # Cycle forward with Tab and backward with Shift+Tab
            app.set_focus(pilot.query_one("#chats"))
            await pilot.press("tab")
            assert getattr(app._focus, "id", None) == "thread-scroll"
            await pilot.press("tab")
            assert getattr(app._focus, "id", None) == "prompt"
            await pilot.press("shift+tab")
            assert getattr(app._focus, "id", None) == "thread-scroll"

            # Mouse click inside CHAT body / title focuses #thread-scroll, and wheel scrolls it
            md_w = pilot.query_one("#thread-md")
            app.set_focus(pilot.query_one("#notes"))
            app.on_pointer(md_w.rect.x + 20, md_w.rect.y + 20, 1)
            app.on_pointer_up(md_w.rect.x + 20, md_w.rect.y + 20, 1)
            assert getattr(app._focus, "id", None) == "thread-scroll"

            sc.scroll_home()
            assert sc.scroll_y == 0
            app.on_wheel(md_w.rect.x + 20, md_w.rect.y + 20, 0.0, 120.0)
            assert sc.scroll_y > 0

            # F10 Help Tab switches focus cleanly between md-toc (CONTENTS) and md-scroll (HELP)
            await pilot.press("f1")
            await pilot.pause()
            help_stops = [getattr(w, "id", None) for w in app._focusables()]
            assert help_stops == ["md-toc", "md-scroll"]
            await pilot.press("tab")
            assert getattr(app._focus, "id", None) == "md-toc"
            await pilot.press("tab")
            assert getattr(app._focus, "id", None) == "md-scroll"

    asyncio.run(_run())


def test_wgpu_chat_renders_native_images_not_halfblocks(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    from PIL import Image, ImageDraw
    from textual_wgpu.widgets import VerticalScroll
    from ydna.core import chat, media_cache
    from ydna.tui.wgpu_chat import ChatScreen
    from ydna.workspaces.chat import images as chat_images

    img_path = tmp_path / "swatch.png"
    src = Image.new("RGBA", (180, 90), (220, 40, 40, 255))
    for x in range(90, 180):
        for y in range(90):
            src.putpixel((x, y), (40, 90, 230, 255))
    src.save(img_path)
    monkeypatch.setattr(media_cache, "ensure_local_image", lambda url, **kw: img_path)
    monkeypatch.setattr(chat_images, "ensure_local_image", lambda url, **kw: img_path)

    t = chat.new_thread()
    t.title = "Native image chat"
    t.messages.append(chat.Message("user", "Show image"))
    t.messages.append(chat.Message("model", "Preview:\n\n![Swatch](https://example.org/swatch.png)\n"))
    chat.save(t)

    async def _run():
        app = ModeApp([("chat", "Chat", ChatScreen)], theme=_theme(), start="chat")
        async with app.run_test(size=(1100, 720)) as pilot:
            await pilot.pause()
            app.screen.reload(keep=t.id)
            await pilot.pause()
            md = pilot.query_one("#thread-md")
            sc = pilot.query_one("#thread-scroll", VerticalScroll)
            strips = md.render_lines()
            assert any(seg.meta and "@image" in seg.meta for st in strips for seg in st.segments)
            assert not any("▄" in st.plain() or "▀" in st.plain() for st in strips)
            assert any(act == "screen.edit_msg(0)" for *_, act in md._click_spans)

            frame = Image.new("RGBA", (1100, 720))
            app.screen.paint(ImageDraw.Draw(frame))
            reds = sum(
                1
                for y in range(sc.rect.y, sc.rect.y1)
                for x in range(sc.rect.x, sc.rect.x1 - 10)
                if frame.getpixel((x, y))[:3] == (220, 40, 40)
            )
            blues = sum(
                1
                for y in range(sc.rect.y, sc.rect.y1)
                for x in range(sc.rect.x, sc.rect.x1 - 10)
                if frame.getpixel((x, y))[:3] == (40, 90, 230)
            )
            assert reds > 1000 and blues > 1000

    asyncio.run(_run())
