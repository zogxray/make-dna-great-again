"""Launch textual-wgpu ModeApp with CRT (cool-retro-term MY) look + F1–F10 modes."""

from __future__ import annotations

import sys

from ..workspaces.base import MODE_LABELS


def _crt_theme():
    from textual_wgpu.theme import Theme

    from ..theme import crt

    return Theme.from_mapping(crt.theme_mapping())


def run_wgpu_poc() -> int:
    try:
        from textual_wgpu import fonts as tw_fonts
        from textual_wgpu.mode_app import ModeApp
    except ImportError as exc:
        print(
            "textual-wgpu is not installed in this environment.\n"
            "  pip install -e ../textual-wgpu\n"
            f"({exc})",
            file=sys.stderr,
        )
        return 1

    from ..theme import crt

    # Palette + Terminus-like mono + CRT post-FX (bloom / scanlines).
    crt.apply_styles()
    tw_fonts.configure(mono_paths=crt.MONO_PATHS, width_scale=crt.WIDTH_SCALE)

    from .wgpu_chat import ChatScreen
    from .wgpu_data import DataScreen
    from .wgpu_genome import GenomeScreen
    from .wgpu_gwas import GwasScreen
    from .wgpu_help import HelpScreen
    from .wgpu_jobs import JobsScreen
    from .wgpu_pgs import PgsScreen
    from .wgpu_reference import ReferenceScreen
    from .wgpu_samples import SamplesScreen
    from .wgpu_tree import TreeScreen

    theme = _crt_theme()
    factories = {
        "genome": GenomeScreen,
        "data": DataScreen,
        "tree": TreeScreen,
        "pgs": PgsScreen,
        "gwas": GwasScreen,
        "reference": ReferenceScreen,
        "chat": ChatScreen,
        "jobs": JobsScreen,
        "samples": SamplesScreen,
        "help": HelpScreen,
    }
    modes = [
        (key, title, factories[key])
        for key, title in MODE_LABELS
    ]

    ModeApp(
        modes,
        theme=theme,
        title="MDGA · CRT",
        start="genome",
        post_fx={"bloom": crt.BLOOM, "scanlines": crt.SCANLINES},
    ).run()
    return 0
