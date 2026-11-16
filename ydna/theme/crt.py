"""CRT look inspired by cool-retro-term profile ``MY``.

Not a full CRT shader stack — palette + typography hooks + light bloom/scanlines
wired from ``--ui wgpu``. Textual keeps the gold brand theme.
"""

from __future__ import annotations

# cool-retro-term MY phosphor
PHOSPHOR = "#729fcf"
BG = "#000000"
# Cool near-black ladder (readable panels on pure black)
SURFACE = "#0C0E12"
PANEL = "#12151C"
BOOST = "#1A1F2A"
INK = "#B8D4F0"          # soft phosphor ink (not pure white)
MUTED = "#6A849E"
PRIMARY = "#3D5A7A"      # borders / subdued chrome
ACCENT = PHOSPHOR

# Post-FX strengths (~ MY bloom 0.5, mild scanlines; no curvature/noise)
BLOOM = 0.45
SCANLINES = 0.10

# Typography: keep DejaVu (readable + Braille via FreeMono fallback).
# Pixel fonts (3270/VGA) lack Braille and shrink the cell grid → tiny UI + □ map.
MONO_PATHS: tuple[str, ...] = ()
WIDTH_SCALE = 1.0


def theme_mapping() -> dict[str, str]:
    return {
        "background": BG,
        "surface": SURFACE,
        "panel": PANEL,
        "boost": BOOST,
        "foreground": INK,
        "muted": MUTED,
        "accent": ACCENT,
        "primary": PRIMARY,
    }


def apply_styles() -> None:
    """Retarget ``ydna.theme.styles`` tokens used by wgpu screens."""
    from . import styles

    styles.BG = BG
    styles.SURFACE = SURFACE
    styles.PANEL = PANEL
    styles.BOOST = BOOST
    styles.INK = INK
    styles.BRIGHT = INK
    styles.MUTED = MUTED
    styles.HI = ACCENT
    styles.GOLD = ACCENT
    styles.PRIMARY = PRIMARY
    styles.HI_BOLD = f"bold {ACCENT}"
    styles.HI_REV = f"bold reverse {ACCENT}"
    styles.BRIGHT_BOLD = f"bold {INK}"
