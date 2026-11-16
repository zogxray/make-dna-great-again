"""Track B playground: run real MDGA Textual workspaces on BOTH textual and textual-wgpu."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest


def _require_backend(backend: str) -> None:
    if backend == "textual_wgpu":
        pytest.importorskip("textual_wgpu")


@pytest.mark.parametrize("backend", ["textual", "textual_wgpu"])
def test_real_help_and_jobs_workspaces_on_both_backends(backend: str, tmp_path):
    """Boot real ``HelpWorkspace`` & ``JobsWorkspace`` under each backend in isolation."""
    _require_backend(backend)
    script = f"""
import asyncio, json, os
os.environ["XDG_CONFIG_HOME"] = {str(tmp_path / "cfg")!r}
os.environ["XDG_CACHE_HOME"] = {str(tmp_path / "cache")!r}

if {backend!r} == "textual_wgpu":
    from ydna.compat_wgpu import install_textual_wgpu_shim
    install_textual_wgpu_shim()

from textual.app import App
from ydna.theme import YDNA_DARK
from ydna.workspaces.help.workspace import HelpWorkspace
from ydna.workspaces.jobs.workspace import JobsWorkspace

class DualApp(App):
    def on_mount(self):
        self.register_theme(YDNA_DARK)
        self.theme = "ydna-dark"
        self.add_mode("help", HelpWorkspace)
        self.add_mode("jobs", JobsWorkspace)
        self.switch_mode("help")

async def main():
    app = DualApp()
    size = (100, 36) if {backend!r} == "textual" else (900, 600)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        await pilot.pause()
        help_scroll = app.screen.query_one("#help-scroll")
        res = app.switch_mode("jobs")
        if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
            await res
        await pilot.pause()
        jobs_list = app.screen.query_one("#jobs")
        print(json.dumps({{
            "ok": True,
            "backend": {backend!r},
            "mode": app.current_mode,
            "has_scroll": help_scroll is not None,
            "has_jobs": jobs_list is not None,
        }}))

asyncio.run(main())
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["ok"] is True
    assert data["mode"] == "jobs"
    assert data["has_scroll"] and data["has_jobs"]


def test_ydna_app_all_modes_on_textual_wgpu(tmp_path):
    """Boot the real ``YDnaApp`` on ``textual_wgpu`` and switch across all F1–F10 modes."""
    _require_backend("textual_wgpu")
    script = f"""
import asyncio, json, os
os.environ["XDG_CONFIG_HOME"] = {str(tmp_path / "cfg")!r}
os.environ["XDG_CACHE_HOME"] = {str(tmp_path / "cache")!r}

from ydna.compat_wgpu import install_textual_wgpu_shim
install_textual_wgpu_shim()

from ydna.tui.app import YDnaApp

async def main():
    app = YDnaApp()
    seen = {{}}
    async with app.run_test(size=(1000, 680)) as pilot:
        await pilot.pause()
        await pilot.pause()
        seen["boot"] = app.current_mode
        for key in ("f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f1"):
            await pilot.press(key)
            await pilot.pause()
            seen[key] = app.current_mode
    print(json.dumps({{"ok": True, "seen": seen}}))

asyncio.run(main())
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["ok"] is True
    assert data["seen"] == {
        "boot": "genome",
        "f2": "data",
        "f3": "tree",
        "f4": "pgs",
        "f5": "gwas",
        "f6": "reference",
        "f7": "chat",
        "f8": "jobs",
        "f9": "samples",
        "f10": "help",
        "f1": "genome",
    }


@pytest.mark.parametrize("backend", ["textual", "textual_wgpu"])
def test_ydna_app_interactive_flows_both_backends(backend: str, tmp_path):
    """Track C: test synthetic kit load, ModeBar click, Jobs filter, Confirm modal, Chat & Help."""
    _require_backend(backend)
    kit = tmp_path / "kit_23andme.txt"
    kit.write_text(
        "# rsid\tchromosome\tposition\tgenotype\n"
        "rs4477212\t1\t82154\tAA\n"
        "rs3094315\t1\t752566\tAG\n"
        "rs1801133\t1\t11856378\tAG\n",
        encoding="utf-8",
    )
    script = f"""
import asyncio, json, os
os.environ["XDG_CONFIG_HOME"] = {str(tmp_path / "cfg")!r}
os.environ["XDG_CACHE_HOME"] = {str(tmp_path / "cache")!r}

if {backend!r} == "textual_wgpu":
    from ydna.compat_wgpu import install_textual_wgpu_shim
    install_textual_wgpu_shim()

from ydna.tui.app import YDnaApp
from ydna.workspaces.base import ConfirmScreen

async def main():
    app = YDnaApp(genomes=[{str(kit)!r}])
    size = (120, 40) if {backend!r} == "textual" else (1100, 700)
    async with app.run_test(size=size) as pilot:
        # Let startup + F1 compose + F1 genotype import finish
        for _ in range(40):
            await asyncio.sleep(0.05)
            await pilot.pause()
            if app.current_mode == "genome" and type(app.screen).__name__ == "GenomeWorkspace":
                found_files = list(app.screen.query("#files"))
                if found_files and found_files[0].option_count >= 1:
                    break
        assert app.current_mode == "genome"
        files_ol = app.screen.query_one("#files")
        chroms = app.screen.query_one("#chroms")
        file_count = files_ol.option_count

        # 1) Rich ydna_click on ModeBar -> switch to F8 Jobs
        mb = app.screen.query_one("ModeBar")
        if {backend!r} == "textual":
            # ' F1 Genome  F2 Data  ...  F8 Jobs '
            plain = str(mb.render())
            col = plain.index("F8 Jobs") + 2
            await pilot.click("ModeBar", offset=(col, 0))
        else:
            from textual_wgpu.fonts import cell_width
            plain = mb.render_lines()[0].plain()
            col = plain.index("F8 Jobs") + 2
            app.on_pointer(mb.rect.x + 4 + col * cell_width(13), mb.rect.y + 4, 1)
        await pilot.pause()
        jobs_mode = app.current_mode

        # 2) Cycle Jobs filter with '/'
        prev_flt = app.screen.filter
        await pilot.press("/")
        await pilot.pause()
        next_flt = app.screen.filter

        # 3) Q -> ConfirmScreen modal -> Esc cancels back to JobsWorkspace
        await pilot.press("q")
        await pilot.pause()
        confirm_open = isinstance(app.screen, ConfirmScreen)
        await pilot.press("escape")
        await pilot.pause()
        after_esc = type(app.screen).__name__

        # 4) ChatWorkspace typing into #prompt
        await pilot.press("f7")
        await pilot.pause()
        prompt = app.screen.query_one("#prompt")
        prompt.focus()
        if hasattr(pilot, "type_chars"):
            await pilot.type_chars("hello")
        else:
            await pilot.press("h", "e", "l", "l", "o")
        await pilot.pause()
        prompt_text = prompt.text

        # 5) HelpWorkspace scroll keys
        await pilot.press("f10")
        await pilot.pause()
        await pilot.press("pagedown", "home")
        await pilot.pause()
        help_scroll_y = app.screen.query_one("#help-scroll").scroll_y

        print(json.dumps({{
            "ok": True,
            "backend": {backend!r},
            "has_chroms": chroms is not None,
            "file_count": file_count,
            "jobs_mode": jobs_mode,
            "filter_changed": prev_flt != next_flt,
            "confirm_open": confirm_open,
            "after_esc": after_esc,
            "prompt_text": prompt_text,
            "help_scroll_y": help_scroll_y,
        }}))

asyncio.run(main())
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["ok"] is True
    assert data["has_chroms"] is True
    assert data["file_count"] >= 1
    assert data["jobs_mode"] == "jobs"
    assert data["filter_changed"] is True
    assert data["confirm_open"] is True
    assert data["after_esc"] == "JobsWorkspace"
    assert data["prompt_text"] == "hello"
    assert data["help_scroll_y"] == 0


@pytest.mark.parametrize("backend", ["textual", "textual_wgpu"])
def test_ydna_app_modals_paste_navigation_and_screenshot_both_backends(backend: str, tmp_path):
    """Track D: verify OpenFileScreen modal, Paste, F2/F5/F9 navigation, and save_screenshot."""
    _require_backend(backend)
    kit = tmp_path / "kit_23andme.txt"
    kit.write_text(
        "# rsid\tchromosome\tposition\tgenotype\n"
        "rs4477212\t1\t82154\tAA\n"
        "rs3094315\t1\t752566\tAG\n",
        encoding="utf-8",
    )
    shot_dir = tmp_path / "shots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    script = f"""
import asyncio, json, os
from pathlib import Path
os.environ["XDG_CONFIG_HOME"] = {str(tmp_path / "cfg")!r}
os.environ["XDG_CACHE_HOME"] = {str(tmp_path / "cache")!r}

if {backend!r} == "textual_wgpu":
    from ydna.compat_wgpu import install_textual_wgpu_shim
    install_textual_wgpu_shim()

from ydna.tui.app import YDnaApp

async def main():
    app = YDnaApp(genomes=[{str(kit)!r}])
    size = (120, 40) if {backend!r} == "textual" else (1100, 700)
    async with app.run_test(size=size) as pilot:
        for _ in range(40):
            await asyncio.sleep(0.05)
            await pilot.pause()
            if app.current_mode == "genome" and type(app.screen).__name__ == "GenomeWorkspace":
                found = list(app.screen.query("#files"))
                if found and found[0].option_count >= 1:
                    break

        # 1) OpenFileScreen modal via 'o' -> dismiss via 'escape'
        await pilot.press("o")
        await pilot.pause()
        modal_name = type(app.screen).__name__
        await pilot.press("escape")
        await pilot.pause()
        after_modal = type(app.screen).__name__

        # 2) Filter input in F1 (#chrom-filter) + cursor move
        flt = app.screen.query_one("#chrom-filter")
        flt.focus()
        if hasattr(pilot, "type_chars"):
            await pilot.type_chars("1")
        else:
            await pilot.press("1")
        await pilot.press("left", "right")
        await pilot.pause()
        flt_val = flt.value

        # 3) Navigate F2 Data, F5 GWAS, F9 Samples with arrow keys
        nav = {{}}
        for key in ("f2", "f5", "f9"):
            await pilot.press(key)
            await pilot.pause()
            await pilot.press("down", "up")
            await pilot.pause()
            nav[key] = app.current_mode

        # 4) F7 Chat paste check
        await pilot.press("f7")
        await pilot.pause()
        prompt = app.screen.query_one("#prompt")
        prompt.focus()
        app.copy_to_clipboard("dna")
        if hasattr(prompt, "paste_clipboard"):
            prompt.paste_clipboard()
        else:
            await pilot.press("ctrl+v")
        await pilot.pause()
        pasted_text = prompt.text

        # 5) F10 Help + save_screenshot
        await pilot.press("f10")
        await pilot.pause()
        shot_path = app.save_screenshot("help.svg", path={str(shot_dir)!r})
        shot_ok = Path(shot_path).exists() and Path(shot_path).stat().st_size > 100

        print(json.dumps({{
            "ok": True,
            "backend": {backend!r},
            "modal_name": modal_name,
            "after_modal": after_modal,
            "flt_val": flt_val,
            "nav": nav,
            "pasted_text": pasted_text,
            "shot_ok": shot_ok,
        }}))

asyncio.run(main())
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["ok"] is True
    assert data["modal_name"] == "OpenFileScreen"
    assert data["after_modal"] == "GenomeWorkspace"
    assert data["flt_val"] == "1"
    assert data["nav"] == {"f2": "data", "f5": "gwas", "f9": "samples"}
    assert "dna" in data["pasted_text"]
    assert data["shot_ok"] is True
