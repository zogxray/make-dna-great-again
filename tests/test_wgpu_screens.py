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

            # Switch to Help (F3 in this 3-mode harness) -> MarkdownViewer + TOC toggle
            await pilot.press("f3")
            await pilot.pause()
            mv = pilot.query_one("#help-viewer", MarkdownViewer)
            assert mv.show_table_of_contents is True
            assert len(mv.table_of_contents.root.children) >= 1
            await pilot.press("t")
            assert mv.show_table_of_contents is False
            await pilot.press("t")
            assert mv.show_table_of_contents is True

    asyncio.run(_run())
