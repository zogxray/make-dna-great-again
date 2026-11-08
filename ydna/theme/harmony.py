"""Брендовая гармония цветов — та же логика, что HarmonyCompanion (HSV-якорь + интервалы).

Два зерна: brand_primary_dark + brand_accent_gold.
Из них — тоника (h0,s0,v0), дальше семантика темы как сдвиги по кругу
(диатоника 360/7, триада ±120°, комплемент +180°), с приглушёнными S/V под light/dark.
"""

from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass

from textual.theme import Theme

# Те же hex, что в HarmonyCompanion colors.xml
BRAND_PRIMARY_DARK = "#32273F"
BRAND_ACCENT_GOLD = "#D4AD72"

DIATONIC_HUE_STEP = 360.0 / 7.0


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#{:02X}{:02X}{:02X}".format(
        max(0, min(255, round(r * 255))),
        max(0, min(255, round(g * 255))),
        max(0, min(255, round(b * 255))),
    )


def _rgb_to_hsv(r: float, g: float, b: float) -> tuple[float, float, float]:
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return h * 360.0, s, v


def _hsv_to_hex(h: float, s: float, v: float) -> str:
    r, g, b = colorsys.hsv_to_rgb((h % 360.0) / 360.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return _rgb_to_hex(r, g, b)


def circular_mean_hue(h1: float, h2: float, weight_first: float = 0.55) -> float:
    w1 = max(0.0, min(1.0, weight_first))
    w2 = 1.0 - w1
    r1, r2 = math.radians(h1), math.radians(h2)
    x = w1 * math.cos(r1) + w2 * math.cos(r2)
    y = w1 * math.sin(r1) + w2 * math.sin(r2)
    if math.hypot(x, y) < 1e-4:
        return h1
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


@dataclass(frozen=True)
class BrandAnchor:
    h0: float
    s0: float
    v0: float
    dark_hex: str = BRAND_PRIMARY_DARK
    gold_hex: str = BRAND_ACCENT_GOLD

    @classmethod
    def from_brand(
        cls,
        dark_hex: str = BRAND_PRIMARY_DARK,
        gold_hex: str = BRAND_ACCENT_GOLD,
    ) -> BrandAnchor:
        hd, sd, vd = _rgb_to_hsv(*_hex_to_rgb(dark_hex))
        hg, sg, vg = _rgb_to_hsv(*_hex_to_rgb(gold_hex))
        h0 = circular_mean_hue(hd, hg, 0.55)
        s0 = max(0.0, min(1.0, sd * 0.55 + sg * 0.45))
        v0 = max(0.0, min(1.0, vd * 0.55 + vg * 0.45))
        return cls(h0=h0, s0=s0, v0=v0, dark_hex=dark_hex, gold_hex=gold_hex)


def muted_fill(
    anchor: BrandAnchor,
    *,
    dark_ui: bool,
    hue_offset: float = 0.0,
    s_extra: float = 0.0,
    v_extra: float = 0.0,
) -> str:
    """Приглушённая заливка вокруг якоря — порт mutedFillFromAnchor."""
    s_blend = max(0.34, min(0.52, anchor.s0 * 0.38 + 0.36))
    if dark_ui:
        v_btn = max(0.48, min(0.76, 0.54 + anchor.v0 * 0.2))
    else:
        v_btn = max(0.42, min(0.62, 0.46 + anchor.v0 * 0.14))
    s = max(0.28, min(0.58, s_blend + s_extra))
    v = max(0.0, min(1.0, v_btn + v_extra))
    return _hsv_to_hex(anchor.h0 + hue_offset, s, v)


def _surface_ladder(anchor: BrandAnchor, *, dark_ui: bool) -> tuple[str, str, str, str]:
    """background / surface / panel / boost из тёмного бренда (или осветлённого для light)."""
    hd, sd, vd = _rgb_to_hsv(*_hex_to_rgb(anchor.dark_hex))
    if dark_ui:
        background = _hsv_to_hex(hd, min(0.35, sd * 0.7), max(0.10, vd * 0.55))
        surface = _hsv_to_hex(hd, min(0.32, sd * 0.65), max(0.14, vd * 0.75))
        panel = _hsv_to_hex(hd, min(0.30, sd * 0.6), max(0.18, vd * 0.95))
        boost = _hsv_to_hex(hd, min(0.28, sd * 0.55), max(0.22, min(0.40, vd + 0.12)))
    else:
        background = _hsv_to_hex(hd, min(0.12, sd * 0.25), 0.94)
        surface = _hsv_to_hex(hd, min(0.10, sd * 0.22), 0.90)
        panel = _hsv_to_hex(hd, min(0.14, sd * 0.28), 0.86)
        boost = _hsv_to_hex(hd, min(0.16, sd * 0.3), 0.82)
    return background, surface, panel, boost


def build_theme(
    *,
    dark: bool = True,
    name: str | None = None,
    dark_hex: str = BRAND_PRIMARY_DARK,
    gold_hex: str = BRAND_ACCENT_GOLD,
) -> Theme:
    """Полная Textual Theme: все слоты ColorSystem из гармонического якоря."""
    anchor = BrandAnchor.from_brand(dark_hex, gold_hex)
    background, surface, panel, boost = _surface_ladder(anchor, dark_ui=dark)
    hd, sd, vd = _rgb_to_hsv(*_hex_to_rgb(dark_hex))

    # primary — сам бренд (приподнятый dark), не средний hue: рамки/кнопки остаются «фирменными»
    if dark:
        primary = _hsv_to_hex(hd, min(0.55, sd + 0.08), min(0.55, max(0.38, vd + 0.18)))
    else:
        primary = _hsv_to_hex(hd, min(0.45, sd), min(0.45, max(0.32, vd + 0.05)))
    # secondary / success / warning / error — интервалы вокруг гармонического якоря
    secondary = muted_fill(anchor, dark_ui=dark, hue_offset=DIATONIC_HUE_STEP, s_extra=0.02, v_extra=0.0)
    # accent — золото бренда (ink в HarmonyCompanion) в обеих темах
    accent = gold_hex
    success = muted_fill(anchor, dark_ui=dark, hue_offset=DIATONIC_HUE_STEP * 3.0, s_extra=0.04, v_extra=0.02)
    warning = muted_fill(anchor, dark_ui=dark, hue_offset=40.0, s_extra=0.08, v_extra=0.05)
    error = muted_fill(anchor, dark_ui=dark, hue_offset=120.0, s_extra=0.05, v_extra=0.03)  # триада Stop

    if dark:
        foreground = "#FBF6F0"  # LabelOnHarmonicFill из HarmonyCompanion
    else:
        foreground = muted_fill(anchor, dark_ui=False, hue_offset=0.0, s_extra=-0.1, v_extra=-0.35)
        # для light нужен тёмный текст — берём брендовый dark
        foreground = dark_hex

    # Семантика режимов F1–F6 как диатонические оттенки (доступны в CSS через $mode-*)
    mode_vars = {
        f"mode-{i + 1}": muted_fill(
            anchor, dark_ui=dark, hue_offset=DIATONIC_HUE_STEP * i, s_extra=0.02, v_extra=0.01,
        )
        for i in range(6)
    }

    return Theme(
        name=name or ("ydna-dark" if dark else "ydna-light"),
        primary=primary,
        secondary=secondary,
        accent=accent,
        warning=warning,
        error=error,
        success=success,
        foreground=foreground,
        background=background,
        surface=surface,
        panel=panel,
        boost=boost,
        dark=dark,
        luminosity_spread=0.12,
        text_alpha=0.95,
        variables={
            "brand-dark": dark_hex,
            "brand-gold": gold_hex,
            "footer-key-foreground": accent,
            "button-color-foreground": foreground if dark else dark_hex,
            "block-cursor-background": accent,
            "block-cursor-foreground": background,
            "input-selection-background": f"{secondary} 35%",
            **mode_vars,
        },
    )


YDNA_DARK = build_theme(dark=True)
YDNA_LIGHT = build_theme(dark=False)
