"""Семантические Rich-стили из брендовой гармонии (замена bright_cyan / greyNN / …).

Все hex считаются из того же якоря, что и Theme. Импортируйте токены (`HI`, `MUTED`, …)
или готовые словари (`DATE_STYLES`, `JOB_STYLE`, …).
"""

from __future__ import annotations

from .harmony import (
    BRAND_ACCENT_GOLD,
    BRAND_PRIMARY_DARK,
    DIATONIC_HUE_STEP,
    BrandAnchor,
    _hex_to_rgb,
    _hsv_to_hex,
    _rgb_to_hsv,
    _surface_ladder,
    muted_fill,
)

_ANCHOR = BrandAnchor.from_brand()
_BG, _SURFACE, _PANEL, _BOOST = _surface_ladder(_ANCHOR, dark_ui=True)
_HD, _SD, _VD = _rgb_to_hsv(*_hex_to_rgb(BRAND_PRIMARY_DARK))


def _tint(v: float, s: float = 0.12) -> str:
    """Приглушённый текст на брендовом hue (вместо нейтральных greyNN)."""
    return _hsv_to_hex(_HD, min(0.22, s), max(0.18, min(0.92, v)))


def _fill(offset: float, *, s: float = 0.0, v: float = 0.0) -> str:
    return muted_fill(_ANCHOR, dark_ui=True, hue_offset=offset, s_extra=s, v_extra=v)


# ── база ──────────────────────────────────────────────────────────────
INK = "#FBF6F0"                      # основной светлый текст
BRAND = BRAND_PRIMARY_DARK
GOLD = BRAND_ACCENT_GOLD
BG = _BG
SURFACE = _SURFACE
PANEL = _PANEL
BOOST = _BOOST

HI = GOLD                            # акцент / бывший bright_cyan
HI_BOLD = f"bold {HI}"
HI_REV = f"bold reverse {HI}"        # активная вкладка / режим

PRIMARY = _hsv_to_hex(_HD, min(0.55, _SD + 0.08), min(0.55, max(0.38, _VD + 0.18)))
SECONDARY = _fill(DIATONIC_HUE_STEP)

OK = _fill(DIATONIC_HUE_STEP * 3.0, s=0.04, v=0.02)       # success
OK_BOLD = f"bold {OK}"
WARN = _fill(40.0, s=0.08, v=0.05)
WARN_BOLD = f"bold {WARN}"
BAD = _fill(120.0, s=0.05, v=0.03)                        # triad / error
BAD_BOLD = f"bold {BAD}"
ALERT = _fill(DIATONIC_HUE_STEP * 6.0, s=0.08, v=0.04)     # фиолетовый акцент / mismatch

# ── шкала приглушения (вместо grey85…grey23) ──────────────────────────
MUTED = _tint(0.72, 0.10)            # ~grey62
DIM = _tint(0.58, 0.12)              # ~grey50
FAINT = _tint(0.48, 0.14)            # ~grey42
GHOST = _tint(0.38, 0.14)            # ~grey37
SHADOW = _tint(0.28, 0.12)           # ~grey30
SOFT = _tint(0.80, 0.08)             # ~grey70/78
BRIGHT = INK                         # ~bright_white
BRIGHT_BOLD = f"bold {INK}"

# ── даты дерева (от древних к новым — диатоническая лестница) ─────────
DATE_STYLES = {
    "deep": _fill(DIATONIC_HUE_STEP * 5.0, s=0.06, v=-0.02),      # был bright_magenta
    "purple": _fill(0.0, s=0.04, v=0.0),                          # якорь
    "blue": _fill(DIATONIC_HUE_STEP * 5.5, s=0.02, v=0.02),
    "cyan": HI,
    "green": OK,
    "unknown": DIM,
}

# ── богатство веток ───────────────────────────────────────────────────
SIZE_STYLE = {
    "hub": WARN_BOLD,
    "rich": WARN,
    "normal": MUTED,
    "dead": FAINT,
}
NAME_STYLE = {
    "hub": BRIGHT_BOLD,
    "rich": BRIGHT_BOLD,
    "normal": "",
    "dead": _tint(0.55, 0.10),
}

# ── статусы источников / джобов ───────────────────────────────────────
STATE_ICON_STYLE = {
    "ok": OK,
    "partial": WARN,
    "missing": BAD,
    "unset": DIM,
    "busy": HI,
}
JOB_STYLE = {
    "running": HI,
    "paused": WARN,
    "queued": DIM,
    "done": OK,
    "failed": BAD,
    "cancelled": WARN,
}

# ── Y-маркеры (genome) ────────────────────────────────────────────────
Y_STATE_STYLE = {
    "der": OK_BOLD,
    "anc": MUTED,
    "mismatch": ALERT,
    "nocall": FAINT,
}
CLASS_STYLE = {
    "het": WARN,
    "hom": BRIGHT,
    "hemi": BRIGHT,
    "nocall": FAINT,
    "other": ALERT,
}

# ── сравнение A/B (PGS) ───────────────────────────────────────────────
A_STYLE = _fill(DIATONIC_HUE_STEP * 5.0, s=0.05, v=0.04)   # холоднее
B_STYLE = _fill(DIATONIC_HUE_STEP * 1.0, s=0.05, v=0.04)   # теплее

# ── карта (ночь на бренде + гармонические акценты) ────────────────────
MAP = {
    "bg": BG,
    "coast": _fill(DIATONIC_HUE_STEP * 5.0, s=0.08, v=0.12),
    "border": _hsv_to_hex(_HD, 0.35, 0.35),
    "ring": _hsv_to_hex(_HD, 0.30, 0.28),
    "grid": _hsv_to_hex(_HD, 0.28, 0.22),
    "route": BAD,
    "label": _fill(DIATONIC_HUE_STEP * 5.0, s=0.04, v=0.14),
    "axis": _fill(DIATONIC_HUE_STEP * 5.0, s=-0.02, v=0.06),
    "muted": MUTED,
    "node": INK,
    "origin": OK,
    "contact": WARN,
    "contact_soft": _fill(40.0, s=0.04, v=0.10),
    "help_bg": _hsv_to_hex(_HD, 0.32, 0.26),
    "select_on": f"on {_hsv_to_hex(_HD, 0.28, 0.32)}",
    "select_dim": f"on {_hsv_to_hex(_HD, 0.26, 0.24)}",
}


def bold(style: str) -> str:
    style = style.strip()
    return style if style.startswith("bold ") else f"bold {style}"
