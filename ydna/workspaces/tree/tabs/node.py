"""Вкладки узла: Overview, Ancestors, SNPs, Geography, Ancient DNA, Famous, …

Визуальная версия: шкалы, гистограммы, бейджи, флаги, лестница предков, JSON.

Верстка · Magazine:
  секция (CAPS title) → одна пустая строка → тело (kv/put indent=1);
  карточки ╭│╰ только у сущностей (образец, персона, проект, бейдж, маршрут);
  стат-блоки (Y-STR / Big Y counters) — плоские секции без рамки.

Порядок дат/списков: от актуального к древнему; oldest/youngest чинит _fix.
Перенос: put() по ширине панели (_WIDTH), рамка карточки на каждой строке.
"""

from __future__ import annotations

import html as _html
import json
import math
import re
from contextlib import contextmanager
from typing import Any

from rich.cells import cell_len
from rich.text import Text  # noqa: F401  (doc.t — rich.Text)

from ...base import click
from ..data import _dict, country_code, find_steps, load_resource, pick, year_label
from . import Doc, dump, tab

RAW_LINES = 600
SPARK = "▁▂▃▄▅▆▇█"
EIGHTHS = " ▏▎▍▌▋▊▉█"
CARD_W = 72                  # длина нижней линии карточки (не шире панели)
PATH_NEWEST_FIRST = True     # маршрут миграции: от новых узлов к древним (False — от корня)


# ───────────────────────── helpers ─────────────────────────

_RAIL: list[str | None] = [None]   # стиль левой рамки текущей карточки (None — вне карточки)
_WIDTH: list[int] = [60]           # ширина панели в колонках
PANEL_FALLBACK = 60                # если ctx.panel_width не задан — берём узкое безопасное значение
PANEL_MARGIN = 3                   # запас на полосу прокрутки и внутренние отступы панели


def fit(ctx) -> None:
    """Вызывать в начале вкладки: берём ширину панели инспектора.
    Если ctx.panel_width отсутствует или нереалистичен — безопасное узкое значение,
    чтобы терминал не переносил строки поверх рамки."""
    try:
        w = int(getattr(ctx, "panel_width", 0))
    except (TypeError, ValueError):
        w = 0
    if w < 20:
        w = PANEL_FALLBACK
    _WIDTH[0] = max(30, w - PANEL_MARGIN)


def dv(value: Any) -> str:
    return year_label(value) if value is not None else "unknown"


def span(a: Any, b: Any) -> str:
    """Диапазон дат «новое – древнее» независимо от порядка аргументов."""
    lo, hi = sorted((a, b))
    return f"{dv(hi)} – {dv(lo)}"


def _cut(text: str, room: int) -> tuple[str, str]:
    """Режет строку так, чтобы голова занимала не больше room ячеек терминала."""
    used = 0
    for i, ch in enumerate(text):
        cw = cell_len(ch)
        if used + cw > room:
            return text[:i], text[i:]
        used += cw
    return text, ""


def _fit_cells(text: str, width: int) -> str:
    """Обрезать / дополнить пробелами справа до width ячеек (эмодзи — две ячейки)."""
    head, _ = _cut(str(text), width)
    return head + " " * (width - cell_len(head))


def _rcells(text: str, width: int) -> str:
    """Обрезать / дополнить пробелами слева до width ячеек (числа в колонке)."""
    head, _ = _cut(str(text), width)
    return " " * (width - cell_len(head)) + head


def _wrap_segs(segs: list[tuple[str, str]], avail: int) -> list[list[tuple[str, str]]]:
    """Перенос стилизованных кусков по словам с учётом ширины в ячейках;
    слишком длинные слова (URL) режутся."""
    lines: list[list[tuple[str, str]]] = [[]]
    w = 0
    first = True

    def newline() -> None:
        nonlocal w, first
        lines.append([])
        w, first = 0, False

    for text, style in segs:
        for tok in re.findall(r"\s+|\S+", text):
            tw = cell_len(tok)
            if tok.isspace():
                if w == 0 and not first:
                    continue                      # пробелы в начале строки-продолжения
                if w + tw > avail:
                    newline()
                    continue
                lines[-1].append((tok, style))
                w += tw
                continue
            if w and w + tw > avail:
                newline()
            while cell_len(tok) > avail:          # длинный URL без пробелов
                head, tok = _cut(tok, avail - w)
                if head:
                    lines[-1].append((head, style))
                newline()
            if tok:
                lines[-1].append((tok, style))
                w += cell_len(tok)
    return lines


def put(doc: Doc, *parts: Any, indent: int = 1) -> None:
    """Строка из кусочков (текст, стиль). Внутри карточки слева рисуется рамка │,
    при переносе она и отступ повторяются на каждой строке."""
    rail = _RAIL[0]
    if rail and indent >= 1:
        pre = [("  ", ""), ("│ ", rail), ("  " * (indent - 1), "")]
        pw = 4 + 2 * (indent - 1)
    else:
        pre = [("  " * indent, "")]
        pw = 2 * indent
    segs = [(p[0], p[1]) if isinstance(p, tuple) else (str(p), "") for p in parts]
    for line in _wrap_segs(segs, max(10, _WIDTH[0] - pw)):
        for t, s in pre:
            doc.t.append(t, s)
        for t, s in line:
            doc.t.append(t, s)
        doc.t.append("\n")


def put_row(doc: Doc, *parts: Any, indent: int = 1) -> None:
    """Как put, но без переноса — для таблиц с фиксированными колонками."""
    rail = _RAIL[0]
    if rail and indent >= 1:
        doc.t.append("  ")
        doc.t.append("│ ", rail)
        if indent > 1:
            doc.t.append("  " * (indent - 1))
    else:
        doc.t.append("  " * indent)
    for p in parts:
        if isinstance(p, tuple):
            doc.t.append(p[0], p[1])
        else:
            doc.t.append(str(p))
    doc.t.append("\n")


@contextmanager
def card(doc: Doc, *head: Any, rail: str = "#5A5361"):
    """Карточка сущности: ╭─ заголовок / │ тело / ╰──── + ровно одна пустая строка.
    Для стат-блоков Magazine не использовать — там doc.title + put/kv."""
    doc._ensure_section_gap()
    segs = [(p[0], p[1]) if isinstance(p, tuple) else (str(p), "") for p in head]
    for i, line in enumerate(_wrap_segs(segs, max(10, _WIDTH[0] - 5))):
        doc.t.append("  ")
        doc.t.append("╭─ " if i == 0 else "│  ", rail)
        for t, st in line:
            doc.t.append(t, st)
        doc.t.append("\n")
    _RAIL[0] = rail
    try:
        yield
    finally:
        _RAIL[0] = None
        doc.t.append("  ")
        doc.t.append("╰" + "─" * max(4, min(CARD_W, _WIDTH[0] - 4)) + "\n", rail)
        doc.t.append("\n")  # воздух до следующей секции / карточки


# Subdivision → short badge (emoji tag-flags / regional indicators break on Linux TUIs).
_SUBDIV_BADGE = {"ENG": "EN", "SCT": "SC", "WLS": "WA", "NIR": "NI"}


def flag(code: str | None) -> str:
    """ISO-2 → текстовый бейдж ``[GB]``.

    Regional-indicator emoji flags не склеиваются в большинстве Linux-терминалов
    (даже с Noto Color Emoji) — показываются как буквы в пунктирных квадратах.
    Без системных пакетов надёжный вариант: ASCII/ISO-бейдж фиксированной ширины.
    """
    code = (code or "").strip().upper()
    if not code:
        return "[··]"
    for part in (code, code.split("-")[-1]):
        if part in _SUBDIV_BADGE:
            return f"[{_SUBDIV_BADGE[part]}]"
    head = code.split("-")[0]
    if len(head) == 2 and head.isalpha():
        return f"[{head}]"
    if 2 <= len(head) <= 3 and head.isalpha():
        return f"[{head}]"
    return "[··]"


def bar(frac: float, width: int = 20) -> tuple[str, str]:
    """Дробная горизонтальная шкала → (заполненная часть, пустая часть)."""
    frac = max(0.0, min(1.0, frac))
    cells = frac * width
    full = int(cells)
    part = EIGHTHS[int((cells - full) * 8)] if full < width else ""
    filled = "█" * full + (part if part != " " else "")
    return filled, "░" * (width - len(filled))


def put_bar(doc: Doc, label: str, frac: float, tail: str, width: int = 22,
            style: str = "#D4AD72", label_w: int = 24, indent: int = 1) -> None:
    f, e = bar(frac, width)
    put(doc, (_fit_cells(label, label_w), ""), " ", (f, style), (e, "#433F47"), "  ",
        (tail, "#AEA5B8"), indent=indent)


def sparkline(values: list[float], bins: int = 40, transform=None) -> tuple[str, float, float]:
    """Гистограмма в одну строку (слева — меньшие значения). transform — функция шкалы
    (например, _tt для логарифмической оси времени); подписи lo/hi — в исходных единицах."""
    raw = [v for v in values if v is not None]
    if not raw:
        return "", 0, 0
    vals = [transform(v) for v in raw] if transform else raw
    lo, hi = min(vals), max(vals)
    if lo == hi:
        return SPARK[-1], min(raw), max(raw)
    counts = [0] * bins
    for v in vals:
        counts[min(bins - 1, int((v - lo) / (hi - lo) * bins))] += 1
    peak = max(counts)
    line = "".join("·" if c == 0 else SPARK[min(7, int(c / peak * 7))] for c in counts)
    return line, min(raw), max(raw)


def banner(doc: Doc, name: str, subtitle: str = "") -> None:
    """Шапка вкладки один раз (Overview) — без лишнего воздуха сверху."""
    line = "━" * max(4, min(64, _WIDTH[0] - 4))
    doc.t.append(f"  {line}\n", "#433F47")
    doc.t.append("   ◆ ", "bold #B04A4A")
    doc.t.append(f"{name}", "bold #D4AD72")
    if subtitle:
        doc.t.append(f"   {subtitle}", "#AEA5B8")
    doc.t.append("\n")
    doc.t.append(f"  {line}\n", "#433F47")
    doc.t.append("\n")  # одна пустая до первой секции


def ask_cta(doc: Doc) -> None:
    """Клик / A → F6 с компактным промптом по активной вкладке."""
    put(doc,
        ("Ask GPT  ", "#8A8294"),
        ("A", click("screen.ask_gpt", "bold reverse #D4AD72")),
        ("  · short prompt for this tab → F6 Chat", "#71697A"))


def norm_tmrca(d: dict) -> tuple[dict, bool]:
    """Границы бывают перепутаны (oldest > youngest) — ставим по возрастанию лет."""
    out, swapped = dict(d), False
    for a, b in (("oldest", "youngest"), ("oldest68", "youngest68"), ("oldest99", "youngest99")):
        x, y = d.get(a), d.get(b)
        if x is not None and y is not None and x > y:
            out[a], out[b] = y, x
            swapped = True
    return out, swapped


def _fix(d: Any) -> Any:
    """norm_tmrca для любого значения: словарь исправляем, остальное возвращаем как есть."""
    return norm_tmrca(d)[0] if isinstance(d, dict) else d


def conf_legend(doc: Doc, median: bool = False, indent: int = 1) -> None:
    legend = [("├─┤ 99%   ", "#8A8294"), ("━ 68%   ", "#D4AD72"), ("◆ mean", "bold #B04A4A")]
    if median:
        legend.append(("   ◇ median", "bold #49AE6B"))
    put(doc, *legend, indent=indent)


def conf_bar(doc: Doc, data: Any, width: int = 48, median: Any = None, indent: int = 1,
             legend: bool = True) -> None:
    """Шкала: ├──━━━◆━━━──┤  (99% / 68% / mean). Слева — новое, справа — древнее."""
    if not isinstance(data, dict):
        return
    data = _fix(data)
    lo99 = data.get("oldest99", data.get("oldest"))
    hi99 = data.get("youngest99", data.get("youngest"))
    mean = data.get("mean")
    if lo99 is None or hi99 is None or mean is None or hi99 <= lo99:
        return
    lo68, hi68 = data.get("oldest68"), data.get("youngest68")
    width = max(12, min(width, _WIDTH[0] - 4 - 2 * indent))   # не шире панели

    def pos(x: float) -> int:
        return max(0, min(width - 1, round((hi99 - x) / (hi99 - lo99) * (width - 1))))

    cells = ["─"] * width
    if lo68 is not None and hi68 is not None:
        for i in range(pos(hi68), pos(lo68) + 1):
            cells[i] = "━"
    cells[0], cells[-1] = "├", "┤"
    if median is not None and lo99 <= median <= hi99:
        cells[pos(median)] = "◇"
    cells[pos(mean)] = "◆"
    sty = {"─": "#8A8294", "━": "#D4AD72", "◆": "bold #B04A4A", "◇": "bold #49AE6B"}
    put(doc, *[(c, sty.get(c, "#8A8294")) for c in cells], indent=indent)
    a, b = dv(hi99), dv(lo99)
    put(doc, (a, "#AEA5B8"), " " * max(1, width - len(a) - len(b)), (b, "#AEA5B8"), indent=indent)
    if legend:
        conf_legend(doc, median is not None, indent)


def date_block(doc: Doc, title: str, data: Any) -> None:
    if not isinstance(data, dict) or not data:
        return
    data = _fix(data)
    doc.title(title)
    doc.kv("Mean", dv(data.get("mean")))
    low, high = data.get("oldest"), data.get("youngest")
    if low is not None and high is not None:
        doc.kv("Range", span(low, high))
    for conf in ("68", "99"):
        lo, hi = data.get(f"oldest{conf}"), data.get(f"youngest{conf}")
        if lo is not None and hi is not None:
            doc.kv(f"{conf}%", span(lo, hi))
    conf_bar(doc, data)


def country_label(item: dict[str, Any]) -> str:
    name = item.get("name") or "Unknown"
    code = str(item.get("code") or "").upper()
    label = f"{flag(code)} {name}" if code else str(name)
    c, t = item.get("count"), item.get("total")
    try:
        if c is not None and t:
            label += f" · {c} of {int(t):,} ({100 * float(c) / float(t):.1f}%)"
        elif c is not None:
            label += f" · {c}"
    except (TypeError, ValueError):
        label += f" · {c}"
    return label


def _num(x: Any, nd: int = 0) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    out = f"{v:,.{nd}f}"
    return out.rstrip("0").rstrip(".") if nd and "." in out else out


def strip_html(s: Any) -> str:
    t = re.sub(r"<[^>]+>", " ", str(s or ""))
    return re.sub(r"\s+", " ", _html.unescape(t)).strip()


def excerpt(s: Any, n: int = 200) -> str:
    t = strip_html(s)
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"


def _tt(x: float) -> float:
    # знаковая лог-шкала: годы от -800000 до +2000 на одной оси
    return math.copysign(math.log10(1 + abs(x) / 100), x)


def _log_frac(frac: float, lo_exp: int = -5) -> float:
    """Доля 1e-5 … 1 → 0 … 1 в логарифмической шкале (иначе малые доли не видны)."""
    if frac <= 0:
        return 0.0
    return max(0.0, min(1.0, (math.log10(frac) - lo_exp) / -lo_exp))


def _pct(frac: float) -> str:
    return "0%" if frac <= 0 else f"{100 * frac:.3g}%"


# ── сортировка: от более актуального к более древнему ──

def _year(x: Any) -> float | None:
    """Год из числа, словаря с mean или числовой строки; иначе None."""
    if isinstance(x, dict):
        x = x.get("mean")
    if x is None or isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _newest_key(x: Any) -> tuple:
    """Свежая дата первой, неизвестная — последней."""
    y = _year(x)
    return (y is None, -(y or 0))


_mean_key = _newest_key


def _age_key(s: Any) -> tuple:
    """Новые образцы / люди — первыми, без даты — в конце; при равенстве — по коду."""
    if not isinstance(s, dict):
        return (True, 0, "")
    return (*_newest_key(s.get("age_estimate_mean")), str(s.get("code") or s.get("name") or ""))


def media_links(item: dict) -> list[str]:
    """Ссылки на картинку и аудио (image_url, audio.ogg_url / mp3_url)."""
    out = []
    if item.get("image_url"):
        out.append(f"🖼 {item['image_url']}")
    audio = item.get("audio")
    if isinstance(audio, dict):
        for k in ("ogg_url", "mp3_url"):
            if audio.get(k):
                out.append(f"🔊 {audio[k]}")
    elif isinstance(audio, str) and audio:
        out.append(f"🔊 {audio}")
    return out


def interval_line(tm: Any) -> str:
    """Короткая строка с интервалами 68% / 99% для time_mrca."""
    if not isinstance(tm, dict):
        return ""
    tm = _fix(tm)
    bits = []
    for k in ("68", "99"):
        lo, hi = tm.get(f"oldest{k}"), tm.get(f"youngest{k}")
        if lo is not None and hi is not None:
            bits.append(f"{k}%: {span(lo, hi)}")
    if not bits:
        lo, hi = tm.get("oldest"), tm.get("youngest")
        if lo is not None and hi is not None:
            bits.append(f"range: {span(lo, hi)}")
    return " · ".join(bits)


def person_line(doc: Doc, p: dict[str, Any]) -> None:
    emoji = p.get("emoji") or "•"
    cc = str(p.get("country_code") or "")
    year = p.get("age_estimate_mean")
    head = [(f"{emoji} ", ""), (str(p.get("name") or "?"), "bold #D4AD72"),
            (f"  {dv(year)}" if year is not None else "", "#B04A4A")]
    tm = p.get("time_mrca")
    mean = tm.get("mean") if isinstance(tm, dict) else None
    who = [x for x in (p.get("category"),) if x]
    gen = [f"Y: {p.get('haplogroup_y') or '—'}", f"MRCA: {p.get('haplogroup_mrca') or '—'}"]
    if p.get("root_y"):
        gen.append(f"root {p['root_y']}")
    if p.get("haplogroup_mt"):
        gen.append(f"mt {p['haplogroup_mt']}")
    ctx_bits = []
    if mean is not None:
        ctx_bits.append(f"MRCA {dv(mean)}")
    if p.get("time_haplogroup") is not None:
        ctx_bits.append(f"own clade {dv(p['time_haplogroup'])}")
    if p.get("shared_modern") is not None:
        ctx_bits.append(f"{_num(p['shared_modern'])} modern")
    if p.get("refinable"):
        ctx_bits.append("refinable")
    with card(doc, *head, rail="#5A5361"):
        if who or cc:
            put(doc, (str(who[0]) if who else "", "#49AE6B"),
                (f"  {flag(cc)}" if cc else "", "#AEA5B8"), indent=1)
        put(doc, (" · ".join(gen), "#8A8294"), indent=1)
        if ctx_bits:
            put(doc, (" · ".join(ctx_bits), "#8A8294"), indent=1)
        lo_a, hi_a = p.get("age_estimate_lower"), p.get("age_estimate_upper")
        if lo_a is not None and hi_a is not None and lo_a != hi_a:
            # FTDNA lower/upper часто «наоборот»; span() сортирует newer→older (yrs BP).
            put(doc, (f"📏 age span {span(lo_a, hi_a)}  ·  yrs BP, newer–older",
                      "#8A8294"), indent=1)
        for ln in media_links(p):
            put(doc, (ln, "underline #71697A"), indent=1)
        txt = excerpt(p.get("story_individual"), 300)
        if txt:
            put(doc, (txt, "#5A5361 italic"), indent=1)
        if p.get("story_key"):
            put(doc, (f"#{p['story_key']}", "#433F47"), indent=1)


def tmrca_timeline(doc: Doc, anc: list[dict], node_name: str, width: int = 46) -> None:
    """Таймлайн TMRCA предков. Строки: от узла (новый) к корню; ось: слева новое."""
    rows = []
    for a in anc:
        tm = _fix(a.get("tmrca"))
        if isinstance(tm, dict) and tm.get("mean") is not None:
            rows.append((a, tm))
    if not rows:
        doc.line("—", "", 1)
        return
    rows.reverse()  # от узла (новый) к корню (древний)

    def lo_of(tm):
        return tm.get("oldest99", tm.get("oldest", tm["mean"]))

    def hi_of(tm):
        return tm.get("youngest99", tm.get("youngest", tm["mean"]))

    lo = min(_tt(lo_of(tm)) for _, tm in rows)
    hi = max(_tt(hi_of(tm)) for _, tm in rows)
    axis_span = (hi - lo) or 1

    def pos(x: float) -> int:
        return max(0, min(width - 1, round((hi - _tt(x)) / axis_span * (width - 1))))

    style = {"─": "#8A8294", "━": "#D4AD72", "◆": "bold #B04A4A", "├": "#8A8294", "┤": "#8A8294"}
    for a, tm in rows:
        o, y = lo_of(tm), hi_of(tm)         # o — древнее (справа), y — новее (слева)
        cells = [" "] * width
        for i in range(pos(y), pos(o) + 1):
            cells[i] = "─"
        if tm.get("oldest68") is not None and tm.get("youngest68") is not None:
            for i in range(pos(tm["youngest68"]), pos(tm["oldest68"]) + 1):
                cells[i] = "━"
        cells[pos(y)], cells[pos(o)] = "├", "┤"
        cells[pos(tm["mean"])] = "◆"
        me = a.get("name") == node_name
        name = _fit_cells(str(a.get("name") or "?"), 12) + " "
        # put_row: иначе "2,700 BCE" переносится по пробелу и ломает ось.
        put_row(doc, (name, "bold #D4AD72" if me else "#FBF6F0"),
                *[(c, style.get(c, "")) for c in cells],
                (f"  {dv(tm['mean'])}", "#B04A4A"),
                (f"  {span(o, y)}", "#8A8294"))


def geo_funnel(doc: Doc, anc: list[dict], top_n: int = 4) -> None:
    rows = [a for a in anc if isinstance(a.get("geochart_countries"), dict) and a["geochart_countries"]]
    if not rows:
        doc.line("— no country breakdown on ancestors for this card", "#8A8294", 1)
        return
    rows = rows[::-1]  # от узла (новый) к корню (древний)
    name_w = 14
    for a in rows:
        g = a["geochart_countries"]
        total = sum(g.values()) or 1
        top = sorted(g.items(), key=lambda kv: (-kv[1], str(kv[0])))[:top_n]
        bits = []
        for c, v in top:
            cc = country_code(c)
            lab = flag(cc) if cc else _fit_cells(str(c), 8)
            bits.append(f"{lab} {100 * v / total:.0f}%")
        put_row(doc,
                (_fit_cells(str(a.get("name") or "?"), name_w), "#FBF6F0"),
                ("  ", ""),
                ("   ".join(bits), "#AEA5B8"))
    if rows:
        n_last = len(rows[-1]["geochart_countries"])
        if n_last >= 40:
            put(doc, (f"… root row spans {n_last} countries (near global)", "#8A8294"))


def ascii_map(doc: Doc, mm: Any, width: int = 84, height: int = 22) -> None:
    mm = _dict(mm)
    feats = _dict(mm.get("geojson")).get("features") or []
    afeats = _dict(mm.get("geojson_ancient")).get("features") or []
    segs, nodes, ancs = [], {}, []
    for f in feats:
        if not isinstance(f, dict):
            continue
        g, pr = _dict(f.get("geometry")), _dict(f.get("properties"))
        c = g.get("coordinates")
        if g.get("type") == "LineString" and c:
            segs.append((c, pr.get("stroke") or "#D4AD72"))
            if pr.get("startNode"):
                nodes.setdefault(pr["startNode"], c[0])
            if pr.get("endNode"):
                nodes.setdefault(pr["endNode"], c[-1])
        elif g.get("type") == "Point" and c and pr.get("name"):
            nodes[pr["name"]] = c
    for f in afeats:
        if isinstance(f, dict):
            c = _dict(f.get("geometry")).get("coordinates")
            if c:
                ancs.append(c)
    pts = [pt for c, _ in segs for pt in c] + ancs
    if not pts:
        doc.line("—", "", 1)
        return
    lons, lats = [p_[0] for p_ in pts], [p_[1] for p_ in pts]
    lo_x, hi_x = min(lons) - 4, max(lons) + 4
    lo_y, hi_y = min(lats) - 3, max(lats) + 3
    sx, sy = (width - 1) / (hi_x - lo_x), (height - 1) / (hi_y - lo_y)
    X = lambda lon: max(0, min(width - 1, round((lon - lo_x) * sx)))
    Y = lambda lat: max(0, min(height - 1, round((hi_y - lat) * sy)))
    grid = [[(" ", "") for _ in range(width)] for _ in range(height)]

    # градусная сетка
    m = math.ceil(lo_y / 10) * 10
    while m <= hi_y:
        for x in range(0, width, 3):
            grid[Y(m)][x] = ("·", "#433F47")
        m += 10
    m = math.ceil(lo_x / 10) * 10
    while m <= hi_x:
        for y in range(0, height, 2):
            grid[y][X(m)] = ("·", "#433F47")
        m += 10

    for c, color in segs:
        for (x0, y0), (x1, y1) in zip(c, c[1:]):
            a, b, a2, b2 = X(x0), Y(y0), X(x1), Y(y1)
            n = max(abs(a2 - a), abs(b2 - b), 1)
            for i in range(n + 1):
                grid[round(b + (b2 - b) * i / n)][round(a + (a2 - a) * i / n)] = ("•", color)
    for c in ancs:
        grid[Y(c[1])][X(c[0])] = ("✚", "bold #B04A4A")

    taken = set()
    for name, (lon, lat) in nodes.items():
        x, y = X(lon), Y(lat)
        grid[y][x] = ("●", "bold #FBF6F0")
        taken.add((y, x))
    for name, (lon, lat) in sorted(nodes.items(), key=lambda kv: -kv[1][1]):
        x, y = X(lon), Y(lat)
        done = False
        for dy in (0, -1, 1, -2, 2):
            yy = y + dy
            if not 0 <= yy < height:
                continue
            for xs in (x + 2, x - len(name) - 1):
                if xs < 0 or xs + len(name) > width:
                    continue
                if all((yy, cx) not in taken for cx in range(xs - 1, xs + len(name) + 1)):
                    for i, ch in enumerate(name):
                        grid[yy][xs + i] = (ch, "bold #D4AD72")
                        taken.add((yy, xs + i))
                    done = True
                    break
            if done:
                break

    ylab = {}
    m = math.ceil(lo_y / 10) * 10
    while m <= hi_y:
        ylab[Y(m)] = f"{abs(m):>3}°{'N' if m >= 0 else 'S'} "
        m += 10
    for r in range(height):
        doc.t.append(ylab.get(r, " " * 6), "#8A8294")
        run, st = "", None
        for ch, sty in grid[r]:
            if sty != st:
                if run:
                    doc.t.append(run, st or "")
                run, st = "", sty
            run += ch
        if run:
            doc.t.append(run, st or "")
        doc.t.append("\n")
    axis = [" "] * width
    m = math.ceil(lo_x / 20) * 20
    while m <= hi_x:
        lab = f"{abs(m)}°{'E' if m >= 0 else 'W'}"
        xs = max(0, min(width - len(lab), X(m) - len(lab) // 2))
        for i, ch in enumerate(lab):
            axis[xs + i] = ch
        m += 20
    doc.t.append("      " + "".join(axis) + "\n", "#8A8294")
    put(doc, ("●", "bold #FBF6F0"), (" node   ", "#AEA5B8"), ("•", "#D4AD72"),
        (" path   ", "#AEA5B8"), ("✚", "bold #B04A4A"), (" ancient sample", "#AEA5B8"))


def mrca_header(doc: Doc, s: dict, count: int | None = None, indent: int = 1) -> None:
    """Сводка clade внутри рамки-хозяина: CLADE · N / MRCA / modern / rarity / интервалы."""
    tm = s.get("time_mrca")
    mean = tm.get("mean") if isinstance(tm, dict) else None
    head: list[Any] = [("CLADE  ", "bold #FBF6F0"),
                       (str(s.get("haplogroup_mrca") or "— no MRCA"), "bold #D4AD72")]
    if count is not None:
        head.append((f" · {count}", "bold #FBF6F0"))
    put(doc, *head, indent=indent)
    sub: list[Any] = []
    if mean is not None:
        sub.append((f"MRCA {dv(mean)}", "#B04A4A"))
    if s.get("shared_modern") is not None:
        sub.append((f"{_num(s['shared_modern'])} modern in clade", "#AEA5B8"))
    if s.get("rarity_in_x") is not None:
        sub.append((f"rarity 1:{_num(s['rarity_in_x'], 1)}", "#AEA5B8"))
    if sub:
        parts: list[Any] = []
        for i, seg in enumerate(sub):
            if i:
                parts.append((" · ", "#5A5361"))
            parts.append(seg)
        put(doc, *parts, indent=indent + 1)
    iv = interval_line(tm)
    if iv:
        put(doc, (iv, "#8A8294"), indent=indent + 1)
        conf_bar(doc, tm, width=40, indent=indent + 1, legend=False)


def _aad_text(aad: dict) -> str:
    mn, mx, mean = aad.get("min"), aad.get("max"), aad.get("mean")
    if mn is not None and mx is not None:
        rng = f"{mn}–{mx}"
    elif mx is not None:
        rng = f"≤{mx}"
    elif mn is not None:
        rng = f"≥{mn}"
    else:
        rng = ""
    if mean is not None:
        rng = f"{rng} (mean {mean})".strip()
    return f"☠ {aad.get('description') or ''} {rng} {aad.get('unit') or ''}".strip()


def sample_block(doc: Doc, s: dict) -> None:
    """Дочерняя карточка образца (шкала времени — только в рамке-хозяине секции)."""
    code = s.get("code") or s.get("name") or "Unnamed sample"
    full_name = s.get("name") if s.get("name") and s.get("name") != code else None
    country = s.get("country_name") or s.get("geography") or "location unknown"
    age = s.get("age_estimate_mean")
    sex = {"M": "♂", "F": "♀"}.get(str(s.get("sex") or "").upper(), "?")
    uncertain = bool(s.get("uncertain_placement"))
    gen = [f"Y {s.get('haplogroup_y') or '—'}"]
    if s.get("root_y"):
        gen.append(f"root {s['root_y']}")
    if s.get("haplogroup_mt"):
        gen.append(f"mt {s['haplogroup_mt']}")
    if s.get("refinable"):
        gen.append("refinable")
    if s.get("preview"):
        gen.append("👁 preview")
    culture = s.get("culture") or (str(s["culture_key"]).replace("_", " ") if s.get("culture_key") else None)
    period = s.get("time_period") or (
        str(s["time_period_key"]).replace("_", " ") if s.get("time_period_key") else None)
    context = [str(x) for x in (culture, period) if x]
    extra = []
    place = ", ".join(str(x) for x in (s.get("site"), s.get("geography")) if x)
    lat, lon = s.get("latitude"), s.get("longitude")
    if lat is not None and lon is not None:
        place += f"  ({_num(lat, 2)}°, {_num(lon, 2)}°)"
    if place:
        extra.append(f"📍 {place}")
    lo_a, hi_a = s.get("age_estimate_lower"), s.get("age_estimate_upper")
    if lo_a is not None and hi_a is not None:
        # FTDNA lower/upper; показываем отсортированный span newer→older (yrs BP).
        extra.append(f"📏 age span {span(lo_a, hi_a)} · yrs BP, newer–older")
    if s.get("time_haplogroup") is not None:
        extra.append(f"🌿 own clade formed ~ {dv(s['time_haplogroup'])}")
    if s.get("kinship"):
        extra.append(f"👪 {s['kinship']}")
    if s.get("historical_event"):
        extra.append(f"⚔ {str(s['historical_event']).strip('~')}")
    if s.get("traits"):
        extra.append(f"🦴 {s['traits']}")
    aad = s.get("age_at_death")
    if isinstance(aad, dict) and (aad.get("description") or any(
            aad.get(k) is not None for k in ("min", "max", "mean"))):
        extra.append(_aad_text(aad))
    if s.get("studies"):
        extra.append("📚 " + "; ".join(s["studies"]))

    age_h = (f"{dv(age)}", "bold #B04A4A") if age is not None else ("—", "#8A8294")
    with card(doc, age_h, "  ", (f"{sex} {code}", "bold #D4AD72"),
              rail="#B04A4A" if uncertain else "#5A5361"):
        who: list[Any] = []
        if full_name:
            who.append((str(full_name), "#FBF6F0"))
        who.append((str(country), "#AEA5B8"))
        if uncertain:
            who.append(("⚠ uncertain", "#B04A4A"))
        parts: list[Any] = []
        for i, seg in enumerate(who):
            if i:
                parts.append((" · ", "#5A5361"))
            parts.append(seg)
        put(doc, *parts, indent=1)
        put(doc, (" · ".join(gen), "#AEA5B8"), indent=1)
        if context:
            put(doc, (" · ".join(context), "#AEA5B8"), indent=1)
        for e in extra:
            put(doc, (e, "#8A8294"), indent=1)
        for ln in media_links(s):
            put(doc, (ln, "underline #71697A"), indent=1)
        txt = excerpt(s.get("story_individual"), 400)
        if txt:
            put(doc, (txt, "#5A5361 italic"), indent=1)


def _group_by_mrca(items: list) -> list[tuple[Any, list[dict]]]:
    """Группы по общему предку: ближайший (самый поздний) MRCA — первым, внутри — новые первыми.
    Дата группы — первая известная среди её членов; при равенстве — по имени MRCA."""
    groups: dict[Any, list[dict]] = {}
    for it in items:
        if isinstance(it, dict):
            groups.setdefault(it.get("haplogroup_mrca"), []).append(it)
    for g in groups.values():
        g.sort(key=_age_key)

    def key(kv):
        for it in kv[1]:
            y = _year(it.get("time_mrca"))
            if y is not None:
                return (False, -y, str(kv[0]))
        return (True, 0, str(kv[0]))

    return sorted(groups.items(), key=key)


def placement_block(doc: Doc, node: dict) -> None:
    """Сколько древних образцов размещено уверенно / неуверенно."""
    doc.title("PLACEMENT · placement confidence")
    placed, unc = node["placed_samples"], node["uncertain_samples"]
    doc.kv("Placed", placed)
    doc.kv("Uncertain", unc)
    try:
        tot = int(placed) + int(unc)
        if tot:
            put_bar(doc, "confidence", int(placed) / tot, f"{100 * int(placed) / tot:.0f}% placed",
                    style="#53A94B")
    except (TypeError, ValueError):
        pass


def badges_block(doc: Doc, badges: list) -> None:
    """Описание гаплогруппы: бейджи с именем, SNP и текстом."""
    doc.title(f"ABOUT · haplogroup description · {len(badges)}")
    if not badges:
        doc.line("—", "", 1)
    for b in badges:
        if not isinstance(b, dict):
            doc.line(f"• {b}", "", 1)
            continue
        color = b.get("color") or "#D4AD72"
        names = " / ".join(str(x) for x in (b.get("shorthand"), b.get("longhand")) if x)
        with card(doc, ("⬢ ", f"bold {color}"), (str(b.get("name") or "?"), f"bold {color}"),
                  (f"  {names} · {b.get('type') or ''}"
                   + (f" · SNP {b['snp']}" if b.get("snp") else ""), "#AEA5B8"),
                  rail=color):
            if b.get("image_file"):
                put(doc, (f"🖼 {b['image_file']}", "#71697A"), indent=1)
            desc = str(b.get("description") or "")
            if desc:
                put(doc, (desc, "#C3BCCC"), indent=1)


# ───────────────────────── tabs ─────────────────────────

def _sources_status_block(ctx, doc: Doc, node: dict) -> None:
    """Сразу в Overview: какие базы включены и есть ли матч имени у текущего узла."""
    from ....core.overlays import resolve_overlay_dir

    name = str(node.get("name") or "")
    doc.title("SOURCES")

    # FTDNA Haplotree — каркас F3.
    put(doc, ("● ", "#53A94B"), ("FTDNA Haplotree", "bold #FBF6F0"),
        ("  ·  topology (always on for F3)", "#8A8294"), indent=1)

    # Discover card.
    if node.get("placeholder"):
        put(doc, ("○ ", "#8A8294"), ("FTDNA Discover", "bold #AEA5B8"),
            ("  ·  not on this node (skeleton)", "#8A8294"), indent=1)
    else:
        put(doc, ("● ", "#53A94B"), ("FTDNA Discover", "bold #FBF6F0"),
            ("  ·  card loaded", "#8A8294"), indent=1)

    ov = ctx.overlays()
    store = getattr(ctx, "store", None)
    yf_dir = tt_dir = None
    if store is not None:
        try:
            yf_dir = resolve_overlay_dir(store, "yfull_ytree")
            tt_dir = resolve_overlay_dir(store, "theytree")
        except Exception:  # noqa: BLE001
            yf_dir = tt_dir = None

    if ov and (ov.meta.get("yfull") or yf_dir):
        yf = ov.meta.get("yfull") or {}
        hit = name in ov.yfull_ages
        put(doc, ("● ", "#53A94B"), ("YFull YTree", "bold #FBF6F0"),
            (f"  ·  v{yf.get('version', '?')} · {yf.get('nodes', 0):,} nodes", "#8A8294"),
            (f"  ·  this node {'match' if hit else 'no name match'}",
             "#53A94B" if hit else "#8BAB4A"), indent=1)
    elif yf_dir:
        put(doc, ("◐ ", "#D4AD72"), ("YFull YTree", "bold #D4AD72"),
            ("  ·  folder set, index not ready — reopen Overview", "#8A8294"), indent=1)
    else:
        put(doc, ("○ ", "#8A8294"), ("YFull YTree", "bold #AEA5B8"),
            ("  ·  off · F2 → download / set path", "#8A8294"), indent=1)

    if ov and (ov.meta.get("theytree") or tt_dir):
        tt = ov.meta.get("theytree") or {}
        hit = name in ov.theytree_ages
        put(doc, ("● ", "#53A94B"), ("TheYtree", "bold #FBF6F0"),
            (f"  ·  {tt.get('version', '?')} · {tt.get('nodes', 0):,} nodes", "#8A8294"),
            (f"  ·  this node {'match' if hit else 'no name match'}",
             "#53A94B" if hit else "#8BAB4A"), indent=1)
    elif tt_dir:
        put(doc, ("◐ ", "#D4AD72"), ("TheYtree", "bold #D4AD72"),
            ("  ·  folder set, index not ready — reopen Overview", "#8A8294"), indent=1)
    else:
        put(doc, ("○ ", "#8A8294"), ("TheYtree", "bold #AEA5B8"),
            ("  ·  off · F2 → download / set path", "#8A8294"), indent=1)

    if ov:
        put(doc, (f"overlay SNP index  {ov.meta.get('snp_index', 0):,} keys · ages below when name matches",
                  "#71697A"), indent=1)


def _overview_lineage(ctx, doc: Doc, name: str) -> None:
    """Компактный путь с кликами — детали / funnel на вкладке Ancestors."""
    chain = ctx.lineage(name)
    doc.title(f"LINEAGE · {len(chain)}  ·  Ancestors tab for funnel")
    if not chain:
        doc.line("—", "#8A8294", 1)
        return
    doc.t.append("  ")
    for i, n in enumerate(chain):
        if i:
            doc.t.append(" › ", style="#8A8294")
        node = ctx.nodes.get(n) or {}
        if n == name:
            style = "bold #53A94B" if n in ctx.ours else "bold #D4AD72"
        elif n in ctx.ours:
            style = "#53A94B"
        elif node.get("placeholder"):
            style = "#8A8294"
        else:
            style = "#D4AD72"
        doc.link(n, style)
    doc.t.append("\n")


@tab("Overview")
def overview(ctx, doc: Doc, node: dict) -> None:
    fit(ctx)
    parent = node.get("parent")
    banner(doc, node["name"], f"← {parent}" if parent else "root")
    ask_cta(doc)
    _sources_status_block(ctx, doc, node)

    pnode = ctx.nodes.get(parent) if parent else None
    if parent and pnode and pnode.get("placeholder"):
        psuffix = "  [haplotree]" if pnode.get("haplotree") else "  [MISSING RESOURCE]"
    else:
        psuffix = ""
    doc.title("HAPLOGROUP")
    doc.kv("Name", node["name"])
    if parent:
        doc.t.append("  Parent: ", style="#AEA5B8")
        if parent in ctx.nodes:
            doc.link(parent, "#D4AD72")
            doc.t.append(psuffix + "\n", style="#8A8294")
        else:
            doc.t.append(f"{parent}{psuffix}\n")
    else:
        doc.kv("Parent", "— (root)")
    doc.kv("Status", "LOADED")
    if node.get("root") is not None:
        doc.kv("Root", node["root"])
    if node.get("hg_id") is not None:
        doc.kv("Haplogroup ID", node["hg_id"])
    placements = node.get("placements")
    if placements is None:
        doc.kv("Placements", "—")
    else:
        doc.kv("Placements", f"{placements:,}" if placements else "0 · none reported")

    badges_block(doc, node["badges"])

    date_block(doc, "FORMED", node.get("formed"))
    if parent and pnode and not pnode.get("placeholder"):
        doc.kv("Parent formed", pnode["date"])
    date_block(doc, "TMRCA", node.get("tmrca"))
    _overlay_ages_block(ctx, doc, node)

    bigy_tm = _dict(_dict(node.get("upgrades")).get("bigy")).get("tmrca")
    if isinstance(bigy_tm, dict) and bigy_tm:
        doc.title("TMRCA · BIG Y")
        tmrca_explained(doc, bigy_tm)

    # Краткая сводка — детали на вкладке Geography.
    doc.title("GEOGRAPHY · summary  ·  Geography tab for maps")
    labels = []
    for n in node["countries"]:
        cc = country_code(n)
        labels.append(f"{flag(cc)} {n}" if cc else n)
    doc.line(" · ".join(labels) if labels else "—", "", 1)

    ctx.branch_block(doc, node)

    doc.title("GENETIC DATA")
    here, up = ctx.split_mrca(node["ancient"], node)
    fam_here, fam_up = ctx.split_mrca(node["famous"], node)
    total_a = max(1, len(here) + len(up))
    put_bar(doc, "Ancient · in clade", len(here) / total_a, f"{len(here)}", style="#53A94B")
    put_bar(doc, "Ancient · from ancestors", len(up) / total_a, f"{len(up)}", style="#53A94B")
    put(doc, (f"uncertain placements: {node['uncertain_samples']}", "#B04A4A"), indent=2)
    doc.kv("Famous", f"{len(fam_here)} in clade · {len(fam_up)} from ancestors")
    doc.kv("Variants", len(node["variants"]))
    doc.kv("Markers", len(node["markers"]))

    ctx.children_block(doc, node["name"])
    _overlay_children_block(ctx, doc, node)
    _overview_lineage(ctx, doc, node["name"])
    doc.title("RESOURCE")
    doc.kv("Source", "FamilyTreeDNA Discover")
    tree = _dict(node.get("tree"))
    version = node.get("tree_version") or (
        ".".join(str(tree[k]) for k in ("version_major", "version_minor", "version_micro")
                 if tree.get(k) is not None) or None)
    if version:
        doc.kv("Tree version", version)
    tree_start = node.get("tree_start") or tree.get("start")
    if tree_start:
        doc.kv("Tree start", tree_start)
    doc.kv("File", node.get("source_file"))
    ctx.discover_fetch_block(doc, node["name"])


def _overlay_children_block(ctx, doc: Doc, node: dict) -> None:
    """Осторожная сверка детей: только показ, без вливания в FTDNA-топологию."""
    ov = ctx.overlays()
    if not ov:
        return
    name = str(node.get("name") or "")
    ftdna_kids = list(ctx.children.get(name) or [])
    diff = ov.children_diff(name, ftdna_kids)
    if not diff or not (diff.get("has_yfull") or diff.get("has_theytree")):
        return

    extra_yf = list(diff.get("extra_yfull") or ())
    extra_tt = list(diff.get("extra_theytree") or ())
    shared_yf = list(diff.get("shared_yfull") or ())
    shared_tt = list(diff.get("shared_theytree") or ())
    if not (extra_yf or extra_tt or shared_yf or shared_tt):
        # Узел есть в другой базе, но детей нет / пусто — короткий статус.
        bits = []
        if diff.get("has_yfull"):
            bits.append(f"YFull children {len(diff.get('yfull') or ())}")
        if diff.get("has_theytree"):
            bits.append(f"TheYtree children {len(diff.get('theytree') or ())}")
        doc.title("CHILDREN · OVERLAY · name match only · not merged into tree")
        put(doc, (" · ".join(bits) + " · no extras vs FTDNA", "#8A8294"), indent=1)
        return

    doc.title("CHILDREN · OVERLAY · exact name match · read-only (not added to FTDNA tree)")
    put(doc, ("FTDNA remains the topology. Extra names below are hints from other databases.",
              "#71697A"), indent=1)

    def _row(label: str, names: list[str], *, style: str = "#C3BCCC", limit: int = 24) -> None:
        if not names:
            return
        put(doc, (f"{label} · {len(names)}", "bold #D4AD72"), indent=1)
        for n in names[:limit]:
            if n in ctx.nodes:
                # Уже есть в нашем индексе (часто под другим родителем) — можно прыгнуть.
                doc.t.append("    └─ ")
                doc.link(n, "#D4AD72")
                other_parent = ctx.nodes[n].get("parent")
                note = ""
                if other_parent and other_parent != name:
                    note = f"  ·  in FTDNA under {other_parent}"
                elif n in ftdna_kids:
                    note = "  ·  already a FTDNA child"
                doc.t.append(note + "\n", style="#8A8294")
            else:
                put(doc, ("└─ ", "#8A8294"), (n, style),
                    ("  ·  not in FTDNA haplotree", "#8A8294"), indent=2)
        if len(names) > limit:
            put(doc, (f"… +{len(names) - limit} more", "#8A8294"), indent=2)

    if shared_yf or shared_tt:
        put(doc, (f"shared with YFull {len(shared_yf)} · shared with TheYtree {len(shared_tt)}",
                  "#AEA5B8"), indent=1)
    _row("extra in YFull only", extra_yf, style="#C3BCCC")
    _row("extra in TheYtree only", extra_tt, style="#C3BCCC")
    # Пересечение «лишних» из обеих баз — особенно интересно.
    both = sorted(set(extra_yf) & set(extra_tt))
    _row("extra in BOTH YFull & TheYtree", both, style="#53A94B")


def _overlay_ages_block(ctx, doc: Doc, node: dict) -> None:
    """Ages / crosswalk из YFull + TheYtree — дополнение к Discover, не замена."""
    ov = ctx.overlays()
    if not ov:
        return
    name = str(node.get("name") or "")
    ages = ov.age(name)
    xw = ov.crosswalk(name)
    from ....core.overlays import format_age_line
    doc.title("AGES · OVERLAY · YFull / TheYtree (ybp; Discover above stays primary)")
    if ages:
        for a in ages:
            put(doc, (format_age_line(a), "#C3BCCC"), indent=1)
    else:
        put(doc, ("no age/crosswalk for this FTDNA name in YFull/TheYtree "
                  "(sources can still be ON — see SOURCES)", "#8A8294"), indent=1)
        return
    bits: list[str] = []
    if xw.get("yfull"):
        n_snps = len(xw.get("yfull_snps") or ())
        bits.append(f"YFull name match · {n_snps} defining SNP token(s)")
    if xw.get("theytree"):
        parent = xw.get("theytree_parent") or "—"
        t2t = " · T2T branch" if xw.get("theytree_t2t") else ""
        bits.append(f"TheYtree name match · parent {parent}{t2t}")
    if bits:
        doc.title("CROSSWALK")
        for b in bits:
            put(doc, (b, "#AEA5B8"), indent=1)
        ysnps = xw.get("yfull_snps") or ()
        if ysnps:
            show = ", ".join(ysnps[:12])
            more = f" · +{len(ysnps) - 12} more" if len(ysnps) > 12 else ""
            put(doc, ("YFull SNPs  ", "#AEA5B8"), (show + more, "#D4AD72"), indent=1)
    meta = ov.meta or {}
    yf = meta.get("yfull") or {}
    tt = meta.get("theytree") or {}
    put(doc, (f"index: YFull {yf.get('version', '—')} ({yf.get('nodes', 0):,} nodes) · "
              f"TheYtree {tt.get('version', '—')} ({tt.get('nodes', 0):,} nodes) · "
              f"{meta.get('snp_index', 0):,} SNP keys", "#71697A"), indent=1)


@tab("SNPs")
def snps(ctx, doc: Doc, node: dict) -> None:
    fit(ctx)
    variants, markers = node["variants"], node["markers"]
    ov = ctx.overlays()
    doc.title(f"SEARCH MARKERS · {len(markers)}")
    if markers:
        doc.line(", ".join(markers), "", 1)
    else:
        doc.line("— none · optional progress.csv names (not Discover variants)",
                 "#8A8294", 1)
    doc.title(f"GENETIC VARIANTS · {len(variants)}"
              + (" · overlay aliases / coords / QC" if ov else ""))
    for v in variants:
        if not isinstance(v, dict):
            doc.line(f"• {v}", "", 1)
            continue
        name = str(v.get("name") or "Unnamed SNP")
        anc, der = v.get("anc") or "?", v.get("der") or "?"
        put(doc, ("🧬 ", ""), (name, "bold #D4AD72"))
        if v.get("position") is not None:
            put(doc, ("position  ", "#AEA5B8"), (f"{v['position']:,}", "#B04A4A"), indent=3)
        if v.get("anc") is not None or v.get("der") is not None:
            put(doc, ("mutation  ", "#AEA5B8"), (anc, "bold #53A94B"), (" ──▶ ", "#8A8294"),
                (der, "bold #8BAB4A"), indent=3)
        # Discover aliases — в основной карточке, не только в dump.
        raw_aliases = v.get("aliases")
        disc_aliases: list[str] = []
        if isinstance(raw_aliases, list):
            disc_aliases = [str(a) for a in raw_aliases if a]
        elif isinstance(raw_aliases, str) and raw_aliases.strip():
            disc_aliases = [raw_aliases.strip()]
        if disc_aliases:
            put(doc, ("aliases   ", "#AEA5B8"),
                (", ".join(disc_aliases[:12])
                 + (f" · +{len(disc_aliases) - 12}" if len(disc_aliases) > 12 else ""),
                 "#C3BCCC"), indent=3)
        if ov:
            hit = ov.snp(name)
            if hit:
                extra_al = [a for a in (hit.aliases or [])
                            if a and a not in disc_aliases and a != name]
                if extra_al:
                    put(doc, ("overlay = ", "#AEA5B8"),
                        (", ".join(extra_al[:12])
                         + (f" · +{len(extra_al) - 12}" if len(extra_al) > 12 else ""),
                         "#D4AD72"), indent=3)
                for lab, coord in (("hg19", hit.hg19), ("hg38", hit.hg38), ("T2T ", hit.t2t)):
                    if not coord:
                        continue
                    pos, ref, alt = coord
                    put(doc, (f"{lab:<8} ", "#AEA5B8"),
                        (f"{pos:,}  {ref}→{alt}", "#C3BCCC"), indent=3)
                flags = []
                if hit.bad_region:
                    flags.append("bad region")
                if hit.combbed:
                    flags.append("combbed")
                if hit.is_ancestor:
                    flags.append("ancestor-flag")
                if flags:
                    put(doc, ("QC        ", "#AEA5B8"),
                        (", ".join(flags), "#B04A4A" if hit.bad_region else "#8BAB4A"), indent=3)
                if hit.haplogroups:
                    put(doc, ("in trees  ", "#AEA5B8"),
                        (", ".join(hit.haplogroups[:6]), "#AEA5B8"), indent=3)
        dump(doc, v, 2, skip=("name", "position", "anc", "der", "aliases"))

    # Defining SNPs с YFull, которых нет в Discover variants (дополнение).
    if ov:
        xw = ov.crosswalk(str(node.get("name") or ""))
        ysnps = list(xw.get("yfull_snps") or ())
        have = {str(v.get("name") or "").upper() for v in variants if isinstance(v, dict)}
        extra = [s for s in ysnps if s.upper() not in have]
        if extra:
            doc.title(f"YFULL DEFINING · not in Discover card · {len(extra)}")
            for s in extra[:40]:
                hit = ov.snp(s)
                label = s
                if hit and hit.aliases:
                    label += "  =  " + ", ".join(hit.aliases[:4])
                flags = []
                if hit and hit.bad_region:
                    flags.append("bad")
                if hit and hit.combbed:
                    flags.append("combbed")
                tail = f"  ·  {', '.join(flags)}" if flags else ""
                put(doc, ("• ", "#8A8294"), (label, "#C3BCCC"), (tail, "#B04A4A"), indent=1)
            if len(extra) > 40:
                put(doc, (f"… +{len(extra) - 40} more", "#8A8294"), indent=1)


def toggle_geo(ctx) -> None:
    """Ancestors: top-4 ↔ top-10 в geographic funnel."""
    ctx.geo_all = not getattr(ctx, "geo_all", False)


def toggle_geo_detail(ctx) -> None:
    """Geography: показать регионы и Unknown Origin в DETAIL."""
    ctx.geo_detail = not getattr(ctx, "geo_detail", False)


def toggle_geo_world(ctx) -> None:
    """Geography: WORLD top-12 ↔ all."""
    ctx.geo_world_all = not getattr(ctx, "geo_world_all", False)


# UK-составляющие и коды без ISO-страны — в DETAIL по умолчанию скрыты как регионы.
_GEO_UK_PARTS = frozenset({"ENG", "SCT", "WLS", "NIR"})
_GEO_NAME_ALIAS = {
    "russian federation": "russia",
    "russia": "russia",
    "england": "united kingdom",
    "scotland": "united kingdom",
    "wales": "united kingdom",
    "northern ireland": "united kingdom",
    "united kingdom": "united kingdom",
    "syrian arab republic": "syria",
    "syria": "syria",
    "south korea": "south korea",
    "korea, republic of": "south korea",
    "moldova, republic of": "moldova",
    "moldova": "moldova",
}


def _country_count_share(item: dict[str, Any]) -> tuple[float, float]:
    """(count, share 0..1). share=0 если total неизвестен."""
    try:
        count = float(item.get("count") or 0)
    except (TypeError, ValueError):
        count = 0.0
    try:
        total = float(item["total"]) if item.get("total") not in (None, "", 0, "0") else 0.0
        share = (count / total) if total else 0.0
    except (TypeError, ValueError, ZeroDivisionError):
        share = 0.0
    return count, share


def _geo_row_share(item: dict[str, Any]) -> tuple[float, float]:
    count, share = _country_count_share(item)
    if not share and item.get("frequency") is not None:
        try:
            share = float(item["frequency"])
        except (TypeError, ValueError):
            pass
    return count, share


def _geo_is_unknown(item: dict[str, Any]) -> bool:
    code = str(item.get("code") or "").strip().upper()
    name = str(item.get("name") or "").strip().lower()
    return code == "X" or name == "unknown origin"


def _geo_is_region(item: dict[str, Any]) -> bool:
    """Субрегион / субъект: код с дефисом, UK-часть, или «Name (Subdivision)»."""
    if _geo_is_unknown(item):
        return False
    code = str(item.get("code") or "").strip().upper()
    name = str(item.get("name") or "")
    if "-" in code:
        return True
    if code in _GEO_UK_PARTS:
        return True
    return " (" in name


def _geo_canon(name: str) -> str:
    key = str(name or "").strip().lower()
    return _GEO_NAME_ALIAS.get(key, key)


def put_country_dual(doc: Doc, label: str, count: float, share: float,
                     peak_count: float, peak_share: float, *,
                     share_ok: bool = True) -> None:
    """Две полоски: абсолютное число в кладе и доля от тестов страны."""
    put(doc, (label, "bold #D4AD72"), indent=1)
    c_frac = (count / peak_count) if peak_count else 0.0
    put_bar(doc, "count", c_frac, _num(count), width=22, style="#D4AD72",
            label_w=8, indent=2)
    if share_ok:
        s_frac = (share / peak_share) if peak_share else 0.0
        share_tail = f"{share * 100:.1f}%" if share else "—"
        put_bar(doc, "share", s_frac, share_tail, width=22, style="#53A94B",
                label_w=8, indent=2)
    else:
        put(doc, ("share", "#8A8294"), ("  —  (no country total)", "#8A8294"), indent=2)


def _put_geo_dual_list(doc: Doc, items: list[dict[str, Any]], *,
                       with_code: bool = False) -> None:
    parsed: list[tuple[dict[str, Any], float, float]] = []
    for item in items:
        parsed.append((item, *_geo_row_share(item)))
    parsed.sort(key=lambda r: (-r[1], -r[2], str(r[0].get("name") or "")))
    peak_count = max((r[1] for r in parsed), default=0) or 1
    peak_share = max((r[2] for r in parsed), default=0) or 1
    for item, count, share in parsed:
        code = str(item.get("code") or "").strip().upper()
        raw_name = str(item.get("name") or "Unknown")
        if with_code:
            cc = code or country_code(raw_name) or ""
            label = f"{flag(cc or None)} {raw_name}"
            if code:
                label += f" ({code})"
        else:
            cc = country_code(raw_name)
            label = f"{flag(cc)} {raw_name}"
        total = item.get("total")
        share_ok = total not in (None, "", 0, "0")
        if share_ok:
            try:
                label += f"  ·  {_num(count)} of {_num(total)}"
            except (TypeError, ValueError):
                pass
        put_country_dual(doc, label, count, share, peak_count, peak_share,
                         share_ok=share_ok)


def _geo_diff_lines(cd: list[dict[str, Any]], grow: list[dict[str, Any]]) -> list[str]:
    """Короткие строки расхождения countries ↔ geochart (агрегация UK/Russia и т.п.)."""
    c_names = {str(c.get("name") or "") for c in cd if not _geo_is_unknown(c)}
    g_names = {str(g.get("name") or "") for g in grow}
    g_canon = {_geo_canon(n): n for n in g_names}
    c_canon = {_geo_canon(n): n for n in c_names}
    only_c = sorted(n for n in c_names if _geo_canon(n) not in g_canon)
    only_g = sorted(n for n in g_names if _geo_canon(n) not in c_canon)
    lines: list[str] = []
    # Свёртки: несколько countries → одна geochart-страна.
    buckets: dict[str, list[str]] = {}
    for n in c_names:
        key = _geo_canon(n)
        if key in g_canon and key != n.lower():
            buckets.setdefault(key, []).append(n)
    for key, parts in sorted(buckets.items()):
        gname = g_canon[key]
        parts = sorted(parts)
        if len(parts) >= 2:
            lines.append(f"merged → {gname}: " + ", ".join(parts))
        elif parts[0] != gname:
            lines.append(f"as {gname}: {parts[0]}")
    if only_c:
        show = only_c[:8]
        extra = f" (+{len(only_c) - 8})" if len(only_c) > 8 else ""
        lines.append("countries only: " + ", ".join(show) + extra)
    if only_g:
        show = only_g[:8]
        extra = f" (+{len(only_g) - 8})" if len(only_g) > 8 else ""
        lines.append("geochart only: " + ", ".join(show) + extra)
    return lines


@tab("Geography", keys={"g": toggle_geo_detail, "w": toggle_geo_world})
def geography(ctx, doc: Doc, node: dict) -> None:
    fit(ctx)
    detail = getattr(ctx, "geo_detail", False)
    world_all = getattr(ctx, "geo_world_all", False)

    cd = [c for c in node["countries_data"] if isinstance(c, dict)]
    gc_raw = node.get("geochart_countries")
    grow = [g for g in gc_raw if isinstance(g, dict)] if isinstance(gc_raw, list) else []

    unknown_n = 0.0
    for c in cd:
        if _geo_is_unknown(c):
            unknown_n, _ = _country_count_share(c)
            break
    sum_all = sum(_country_count_share(c)[0] for c in cd)
    sum_known = sum_all - unknown_n
    if grow:
        sum_known = sum(_geo_row_share(g)[0] for g in grow)
    n_regions = sum(1 for c in cd if _geo_is_region(c))
    n_plain = sum(1 for c in cd if not _geo_is_unknown(c) and not _geo_is_region(c))

    doc.title("SUMMARY")
    put(doc, ("map countries  ", "#AEA5B8"), (f"{len(grow)}", "#FBF6F0"),
        ("   country rows  ", "#AEA5B8"), (f"{n_plain}", "#FBF6F0"),
        ("   regions  ", "#AEA5B8"), (f"{n_regions}", "#FBF6F0"),
        ("   raw  ", "#AEA5B8"), (f"{len(cd)}", "#FBF6F0"))
    put(doc, ("Σ known tests  ", "#AEA5B8"), (_num(sum_known), "#D4AD72"),
        ("   Σ all (w/ unknown)  ", "#AEA5B8"), (_num(sum_all), "#FBF6F0"),
        ("   unknown  ", "#AEA5B8"), (_num(unknown_n), "#8A8294"))
    put(doc, ("sources: geochart (map) + countries (raw labels/codes)", "#8A8294"))

    doc.title(f"MAP VIEW · geochart · {len(grow)} · ranked by count · UK/Russia already merged")
    if grow:
        _put_geo_dual_list(doc, grow, with_code=False)
        put(doc, ("no Unknown Origin · country totals for share bars", "#8A8294"))
    else:
        doc.line("— no geochart_countries", "#8A8294", 1)
        if isinstance(gc_raw, dict) and gc_raw:
            dump(doc, gc_raw, 1)

    if detail:
        shown = [c for c in cd if not _geo_is_unknown(c)] + [c for c in cd if _geo_is_unknown(c)]
        mode = f"all {len(shown)} rows · regions + Unknown Origin"
        hint = "G — countries only"
    else:
        shown = [c for c in cd if not _geo_is_unknown(c) and not _geo_is_region(c)]
        mode = f"{len(shown)} countries · regions/unknown hidden"
        hint = "G — show regions + Unknown Origin"
    doc.title(f"DETAIL · countries · {mode} · {hint}")
    if shown:
        _put_geo_dual_list(doc, shown, with_code=True)
    else:
        doc.line("—", "", 1)
    if not detail and (n_regions or unknown_n):
        bits = []
        if n_regions:
            bits.append(f"{n_regions} region row(s)")
        if unknown_n:
            bits.append(f"Unknown Origin {_num(unknown_n)}")
        put(doc, (f"hidden: {', '.join(bits)} — press G", "#8A8294"))

    diff = _geo_diff_lines(cd, grow)
    if diff:
        doc.title("DIFF · geochart vs countries")
        for line in diff:
            put(doc, (line, "#AEA5B8"))

    world = node.get("google_countries")
    if isinstance(world, dict) and world:
        ranked = sorted(world.items(), key=lambda kv: (-kv[1], str(kv[0])))
        shown_w = ranked if world_all else ranked[:12]
        doc.title(
            f"WORLD DATABASE · {'all' if world_all else 'top-12'} of {len(ranked)}"
            f" by FTDNA test count · W — {'top-12' if world_all else 'show all'}"
        )
        mine = {str(c.get("name")) for c in cd} | {str(g.get("name")) for g in grow}
        mine_canon = {_geo_canon(n) for n in mine}
        for name, v in shown_w:
            hit = name in mine or _geo_canon(name) in mine_canon
            star = "★" if hit else " "
            put_bar(doc, f"{star} {flag(country_code(name))} {name}",
                    v / ranked[0][1] if ranked[0][1] else 0, _num(v),
                    style="#53A94B" if hit else "#8A8294")
        if len(ranked) > len(shown_w):
            rest = ranked[len(shown_w):]
            put(doc, (f"… {len(rest)} more countries, {_num(sum(v for _, v in rest))} tests",
                      "#8A8294"))


_YSTR = {
    "matches": ("👥", "Y-STR matches", "people with similar STR panels"),
    "countries": ("🌍", "Countries", "where matches live"),
    "surnames": ("📛", "Surnames", "distinct surnames among matches"),
    "haplogroups": ("🧬", "Haplogroups", "clades matches fall into"),
}
# Big Y на Matches: выборка + счётчики науки/структуры.
# TMRCA → Overview; globetrekker_paths → Migration.
_BIGY_GROUPS = (
    ("Sample", (
        ("matches", "👥", "Big Y matches", "full Y-sequence tests"),
        ("countries", "🌍", "Countries", "test geography"),
    )),
    ("Science", (
        ("academic_samples", "📚", "Academic samples", "published research kits"),
        ("academic_studies", "📄", "Academic studies", "papers citing this clade"),
        ("ancient_samples", "🦴", "Ancient samples", "aDNA linked via Big Y"),
        ("ancient_studies", "📜", "Ancient studies", "aDNA publications"),
    )),
    ("Structure", (
        ("subclades", "🌿", "Subclades", "downstream Big Y branches"),
        ("variants", "🧬", "Variants", "SNPs seen in Big Y tests"),
        ("max_additional_depth", "↕", "Max added depth", "extra levels under this node"),
    )),
)
_BIGY_MOVED = {
    "tmrca",                 # подробный блок на Overview
    "globetrekker_paths",    # Migration tab
}


def _stat(doc: Doc, icon: str, label: str, value: Any, hint: str = "",
          indent: int = 1) -> None:
    if value is None:
        v = "—"
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        v = _num(value)
    else:
        v = str(value)
    quiet = value in (0, None)
    put(doc, (f"{icon} ", ""), (f"{label:<30}", "#AEA5B8"),
        (f"{v:>10}", "#8A8294" if quiet else "bold #D4AD72"),
        (f"   {hint}" if hint else "", "#71697A"), indent=indent)


def tmrca_explained(doc: Doc, tm: dict) -> None:
    n, swapped = norm_tmrca(tm)
    put(doc, ("TMRCA", "bold #FBF6F0"),
        ("  — when the MRCA of all Big Y tests in this clade lived", "#8A8294"), indent=2)
    mean, med = n.get("mean"), n.get("median")
    if mean is not None:
        put(doc, (f"{'mean':<14}", "#AEA5B8"), (dv(mean), "bold #B04A4A"), ("  estimate center", "#71697A"), indent=3)
    if med is not None:
        put(doc, (f"{'median':<14}", "#AEA5B8"), (dv(med), "bold #49AE6B"),
            ("  equally likely earlier or later", "#71697A"), indent=3)
    width99 = None
    for k, lab in (("68", "68% interval"), ("99", "99% interval")):
        lo, hi = n.get(f"oldest{k}"), n.get(f"youngest{k}")
        if lo is not None and hi is not None:
            if k == "99":
                width99 = hi - lo
            put(doc, (f"{lab:<14}", "#AEA5B8"), (span(lo, hi), "#D4AD72"),
                (f"  width {_num(hi - lo)} years", "#71697A"), indent=3)
    conf_bar(doc, n, median=med, indent=2)
    notes = []
    if mean is not None and med is not None and abs(mean - med) > 150:
        notes.append("mean and median disagree — skewed distribution; prefer the intervals")
    if width99 is not None and width99 > 1500:
        notes.append("99% interval is very wide — dating is approximate")
    if swapped:
        notes.append("oldest/youngest were swapped in the data — bounds reordered by year")
    for t_ in notes:
        put(doc, ("⚠ " + t_, "#B04A4A"), indent=3)


@tab("Matches")
def matches(ctx, doc: Doc, node: dict) -> None:
    fit(ctx)
    up = node["upgrades"]
    doc.title("MODERN MATCHES")
    if not up:
        doc.line("—")
        return

    # Magazine: счётчики — плоские секции, не card (card = сущности).
    ystr = _dict(up.get("ystr"))
    if ystr:
        doc.title("Y-STR · short tandem repeat (STR) test")
        shown = False
        for k, (icon, label, hint) in _YSTR.items():
            if k in ystr:
                _stat(doc, icon, label, ystr[k], hint)
                shown = True
        for k, v in ystr.items():
            if k not in _YSTR and k != "badges":
                _stat(doc, "•", str(k), v)
                shown = True
        for b in ystr.get("badges") or []:
            put(doc, ("🏅 ", ""), (str(b), "bold #B04A4A"))
            shown = True
        if not shown:
            put(doc, ("no data", "#8A8294"))

    bigy = _dict(up.get("bigy"))
    if bigy:
        doc.title("Big Y · full Y-chromosome sequencing")
        known = set(_BIGY_MOVED)
        shown = False
        for title, rows in _BIGY_GROUPS:
            present = [r for r in rows if r[0] in bigy]
            if not present:
                continue
            doc.sub(title)
            for k, icon, label, hint in present:
                _stat(doc, icon, label, bigy[k], hint)
                known.add(k)
                shown = True
        extra = {k: v for k, v in bigy.items() if k not in known}
        if extra:
            doc.sub("Other")
            for k, v in extra.items():
                _stat(doc, "•", str(k), v)
            shown = True
        if not shown:
            put(doc, ("no data", "#8A8294"))
        elif "tmrca" in bigy:
            put(doc, ("TMRCA · see Overview", "#71697A"))

    rest = {k: v for k, v in up.items() if k not in ("ystr", "bigy")}
    if rest:
        dump(doc, rest, 1)


@tab("Ancient DNA")
def ancient(ctx, doc: Doc, node: dict) -> None:
    fit(ctx)
    placement_block(doc, node)
    here, up = ctx.split_mrca(node["ancient"], node)

    for title, items in (("IN THIS CLADE", here), ("FROM ANCESTORS UPSTREAM", up)):
        dicts = [s for s in items if isinstance(s, dict)]
        ages = [s.get("age_estimate_mean") for s in dicts if s.get("age_estimate_mean") is not None]
        groups = _group_by_mrca(dicts)

        # рамка-хозяин: заголовок секции + ось времени + сводки clade
        with card(doc, (f"ANCIENT · {title} · {len(items)}", "bold #FBF6F0"), rail="#D4AD72"):
            if items:
                conf_legend(doc, indent=1)
            if len(ages) > 1:
                # лог-шкала: образцы от -121000 до +1189 иначе слипаются в пару ячеек
                spark, lo, hi = sparkline(ages, transform=_tt)
                put(doc, ("newest ◂", "#8A8294"), (spark[::-1], "#D4AD72"),
                    ("▸ older", "#8A8294"), indent=1)
                a, b = dv(hi), dv(lo)
                put(doc, (a, "#8A8294"), " " * max(1, 40 - len(a) - len(b)),
                    (b, "#8A8294"), indent=1)
                put(doc, ("log scale · sample ages", "#71697A"), indent=1)
            elif len(ages) == 1:
                put(doc, (f"single sample · {dv(ages[0])}", "#8A8294"), indent=1)

            for _, grp in groups:
                mrca_header(doc, grp[0], len(grp), indent=1)

            for s in items:
                if not isinstance(s, dict):
                    put(doc, (f"• {s}", "#8A8294"), indent=1)
            if not items:
                put(doc, ("—", "#8A8294"), indent=1)

        # дочерние карточки образцов — вне хозяина, группы уже в host
        for _, grp in groups:
            for s in grp:
                sample_block(doc, s)


@tab("Famous")
def famous(ctx, doc: Doc, node: dict) -> None:
    fit(ctx)
    fam_here, fam_up = ctx.split_mrca(node["famous"], node)
    for title, group in (("IN CLADE", fam_here), ("FROM ANCESTORS", fam_up)):
        groups = _group_by_mrca([p for p in group if isinstance(p, dict)])
        with card(doc, (f"HISTORICAL · {title} · {len(group)}", "bold #FBF6F0"), rail="#D4AD72"):
            if group:
                conf_legend(doc, indent=1)
                cats: dict[str, int] = {}
                for p_ in group:
                    if isinstance(p_, dict):
                        c = str(p_.get("category") or "—")
                        cats[c] = cats.get(c, 0) + 1
                if cats:
                    put(doc, ("  ".join(
                        f"{k} ×{v}" for k, v in sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))),
                              "#49AE6B"), indent=1)
                for _, grp in groups:
                    mrca_header(doc, grp[0], len(grp), indent=1)
                for p_ in group:
                    if not isinstance(p_, dict):
                        put(doc, (f"• {p_}", "#8A8294"), indent=1)
            else:
                put(doc, ("—", "#8A8294"), indent=1)

        for _, grp in groups:
            for p_ in grp:
                person_line(doc, p_)


def _anc_int(value: Any) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


@tab("Ancestors", keys={"g": toggle_geo})
def ancestors(ctx, doc: Doc, node: dict) -> None:
    """Father-line tip→root: ровные колонки (без лесенки), TMRCA, geo funnel."""
    fit(ctx)
    geo_all = getattr(ctx, "geo_all", False)
    anc = [a for a in node["ancestors"] if isinstance(a, dict)]
    # Discover обычно отдаёт root→…→parent; показываем tip→root.
    ordered = list(reversed(anc))

    doc.title("WHAT YOU'RE LOOKING AT")
    put(doc, ("Father-line from this clade up to the Y-tree root.", "#AEA5B8"))
    put(doc, ("Down = older. Same columns every row (no staircase).", "#AEA5B8"))
    ask_cta(doc)

    doc.title(f"YOUR LINE · tip → root · {len(ordered)} steps")
    if not ordered:
        doc.line("— no ancestors on this card", "#8A8294", 1)
    else:
        mods = [_anc_int(pick(a, "modern", "modern_count")) or 0 for a in ordered]
        peak = math.log10(max(max(mods), 1) + 1) or 1
        # Колонки: уже при узкой панели; put_row — без переноса, иначе колонки плывут.
        w = _WIDTH[0]
        if w < 52:
            clade_w, age_w, live_w, adna_w, bar_w = 14, 11, 7, 5, 8
        elif w < 68:
            clade_w, age_w, live_w, adna_w, bar_w = 16, 12, 8, 6, 10
        else:
            clade_w, age_w, live_w, adna_w, bar_w = 20, 14, 9, 7, 10
        put_row(doc,
                ("  ", "#8A8294"),
                (_fit_cells("clade", clade_w), "#8A8294"),
                (" ", ""),
                (_fit_cells("age", age_w), "#8A8294"),
                (_rcells("living", live_w), "#8A8294"),
                (_rcells("aDNA", adna_w), "#8A8294"),
                (" ", ""),
                (_fit_cells("kits", bar_w), "#8A8294"))
        put_row(doc, ("─" * min(72, max(36, clade_w + age_w + live_w + adna_w + bar_w + 8)),
                      "#433F47"))

        n = len(ordered)
        for i, (a, modern) in enumerate(zip(ordered, mods)):
            tm = pick(a, "tmrca")
            if isinstance(tm, dict):
                tm = tm.get("mean")
            tm_s = dv(tm) if tm not in (None, False) else "—"
            ancient_n = _anc_int(pick(a, "ancient", "ancient_count"))
            anc_s = f"{ancient_n:,}" if ancient_n is not None else "—"
            short = pick(a, "short_name", "shortName", "short") or ""
            name = str(a.get("name") or "?")
            clade = f"{name} · {short}" if short else name
            is_me = a.get("name") == node["name"]
            is_root = i == n - 1
            if is_me:
                rail, tag = "●", " ← you"
            elif is_root:
                rail, tag = "└", " ← root"
            else:
                rail, tag = "│", ""
            f, e = bar(math.log10(modern + 1) / peak, bar_w)
            put_row(doc,
                    (f"{rail} ", "#8A8294"),
                    (_fit_cells(clade, clade_w), "bold #D4AD72" if is_me else "#FBF6F0"),
                    (" ", ""),
                    (_fit_cells(tm_s, age_w), "#B04A4A"),
                    (_rcells(f"{modern:,}", live_w), "#AEA5B8"),
                    (_rcells(anc_s, adna_w), "#8A8294"),
                    (" ", ""),
                    (f, "#53A94B"), (e, "#433F47"),
                    (tag, "#D4AD72"))
        put(doc, ("living = FTDNA kits under this node", "#71697A"))
        put(doc, ("aDNA   = published ancient samples in/under this node", "#71697A"))
        put(doc, ("bar    = living vs the largest row (log scale)", "#71697A"))

    doc.title("WHEN DID THIS ANCESTOR LIVE? · TMRCA")
    put(doc, ("Shared father of everyone in the clade.", "#AEA5B8"))
    put(doc, ("Left = newer · right = older (log).", "#AEA5B8"))
    tmrca_timeline(doc, anc, node["name"])
    put(doc, ("├─┤ 99%    ━ 68%    ◆ mean", "#71697A"))

    tip = "top-10 · G — top-4" if geo_all else "top-4 · G — top-10"
    doc.title(f"WHERE DID THE LINE SPREAD? · geographic funnel · {tip}")
    put(doc, ("Older ancestors → geography usually widens.", "#AEA5B8"))
    geo_funnel(doc, anc, top_n=10 if geo_all else 4)


def _project_key(p: Any) -> tuple:
    """Приоритет (меньше — важнее) → доля совпадения → размер → имя."""
    if not isinstance(p, dict):
        return (999, 0, 0, "")
    try:
        prio = int(p.get("Priority"))
    except (TypeError, ValueError):
        prio = 99
    try:
        frac = float(p.get("ProportionMatch") or 0)
    except (TypeError, ValueError):
        frac = 0.0
    try:
        members = float(p.get("TotalPublicMembers") or 0)
    except (TypeError, ValueError):
        members = 0.0
    return (prio, -frac, -members, str(p.get("Name") or ""))


@tab("Projects")
def projects(ctx, doc: Doc, node: dict) -> None:
    fit(ctx)
    items = sorted(node["projects"], key=_project_key)
    doc.title(f"FTDNA PROJECTS · {len(items)} · log scale (1e-5 … 100%)")
    if not items:
        doc.line("—", "", 1)
    for p in items:
        if not isinstance(p, dict):
            doc.line(f"• {p}", "", 1)
            continue
        hap, tot = p.get("HaplogroupPublicMembers") or 0, p.get("TotalPublicMembers") or 0
        scope = {"H": "haplogroup", "G": "geographic", "L": "lineage"}.get(p.get("Scope"), p.get("Scope"))
        try:
            frac = float(p.get("ProportionMatch") or 0)
        except (TypeError, ValueError):
            frac = 0.0
        with card(doc, ("▸ ", "bold #B04A4A"), (str(p.get("Name") or "?"), "bold #D4AD72"),
                  (f"  [{scope}]", "#49AE6B"),
                  rail="#53A94B" if hap else "#5A5361"):
            title = p.get("Title")
            if title and title != p.get("Name"):
                put(doc, (str(title), "#FBF6F0"), indent=1)
            put_bar(doc, f"{hap} of {_num(tot)}", _log_frac(frac), _pct(frac),
                    style="#53A94B" if hap else "#8A8294", indent=1)
            if p.get("Subtitle"):
                put(doc, (str(p["Subtitle"]), "#AEA5B8"), indent=1)
            meta = []
            if p.get("GroupID") is not None:
                meta.append(f"#{p['GroupID']}")
            if p.get("ShortName"):
                meta.append(str(p["ShortName"]))
            if p.get("Priority") is not None:
                meta.append(f"priority {p['Priority']}")
            if p.get("BannerImage") is not None:
                meta.append(f"banner {p['BannerImage']}")
            if p.get("FamilyCrestImage") is not None:
                meta.append(f"crest {p['FamilyCrestImage']}")
            if meta:
                put(doc, (" · ".join(meta), "#71697A"), indent=1)
            if p.get("URL"):
                put(doc, (str(p["URL"]), "underline #8A8294"), indent=1)


def _path_lines(seg: list[str], color_for, newest_first: bool, width: int | None = None) -> list[list[Any]]:
    """Цепочку узлов разбиваем на строки, чтобы длинные пути не заворачивались."""
    if width is None:
        width = max(20, _WIDTH[0] - 8)   # минус рамка карточки и отступ
    lines: list[list[Any]] = []
    cur: list[Any] = []
    w = 0
    for i, hg in enumerate(seg):
        arrow = "◉ " if i == 0 else ("◀ " if newest_first else "▶ ")
        piece = len(arrow) + len(hg) + 2
        if cur and w + piece > width:
            lines.append(cur)
            cur, w = [], 0
        cur += [(arrow, "#8A8294"), (hg, "#AEA5B8" if hg == "…" else f"bold {color_for(hg)}"), "  "]
        w += piece
    if cur:
        lines.append(cur)
    return lines


def render_migration(doc: Doc, node: dict, *, compact: bool = False) -> None:
    """Маршруты Globetrekker / endpoints / легенда. compact — для правой панели fullscreen."""

    def nm(x: Any) -> str:
        if isinstance(x, dict) and x.get("name"):
            return str(x["name"])
        if x is None or str(x) in ("", "None"):
            return "…"  # открытый конец пути (endNode: null)
        return str(x)

    steps: list = []
    find_steps(node.get("migrationmap"), steps)
    mm = _dict(node.get("migrationmap"))
    colors = {}
    for item in mm.get("haplogroup_legend") or []:
        if isinstance(item, dict) and item.get("name"):
            colors[str(item["name"])] = item.get("color")

    def color_for(hg: str) -> str:
        return colors.get(hg.split("-")[0]) or "#D4AD72"

    feats = [f for f in (_dict(mm.get("geojson")).get("features") or [])
             if isinstance(f, dict) and _dict(f.get("geometry")).get("type") == "LineString"]

    order = "newest nodes first" if PATH_NEWEST_FIRST else "from root"
    doc.title(f"MIGRATION PATH · {len(steps)} steps · {order}")
    if feats:
        open_end = sum(1 for f in feats if not _dict(f.get("properties")).get("endNode"))
        secondary = sum(1 for f in feats if _dict(f.get("properties")).get("secondaryArrow"))
        bits = [f"{len(feats)} segments"]
        if open_end:
            bits.append(f"{open_end} open-ended")
        if secondary:
            bits.append(f"{secondary} secondary arrows")
        put(doc, (" · ".join(bits), "#8A8294"))
    if steps:
        segs: list[list[str]] = []
        for s in steps:
            s = _dict(s)
            a, b = nm(s.get("startNode")), nm(s.get("endNode"))
            if segs and segs[-1][-1] == a and a != "…":
                segs[-1].append(b)
            else:
                segs.append([a, b])
        if PATH_NEWEST_FIRST:
            segs = [list(reversed(sg)) for sg in reversed(segs)]
        for n_, seg in enumerate(segs, 1):
            with card(doc, (f"ROUTE {n_}/{len(segs)}", "bold #FBF6F0"),
                      (f"  · {len(seg) - 1} steps", "#AEA5B8"), rail="#5A5361"):
                for ln in _path_lines(seg, color_for, PATH_NEWEST_FIRST):
                    put(doc, *ln, indent=1)
    else:
        doc.line("—", "", 1)

    desc = mm.get("descendants")
    if isinstance(desc, list) and desc:
        doc.title(f"ENDPOINTS · {len(desc)} · newest first")
        for d in sorted((d for d in desc if isinstance(d, dict)),
                        key=lambda d: (*_newest_key(d.get("tmrca")), str(d.get("name")))):
            kids = d.get("children") or []
            put(doc, ("🏁 ", ""), (str(d.get("name")), "bold #D4AD72"),
                (f"   {dv(_year(d.get('tmrca')))}", "#B04A4A"),
                (f"   children: {len(kids)}", "#AEA5B8"))
    elif isinstance(desc, list):
        put(doc, ("ENDPOINTS · none — FTDNA left descendants[] empty on this card",
                  "#8A8294"))

    if compact:
        return

    if mm.get("time"):
        # migrationmap.haplogroup — корень маршрута Globetrekker, не обязательно этот узел.
        route_root = str(mm.get("haplogroup") or "")
        here = str(node.get("name") or "")
        doc.title("PATH ORIGIN · Globetrekker route root (not always this node)")
        if route_root:
            note = "  · this node" if route_root == here else "  · ancestor of this card"
            put(doc, ("Starts at  ", "#AEA5B8"), (route_root, "bold #D4AD72"),
                (note, "#8A8294"))
        doc.kv("Mean", dv(_dict(mm["time"]).get("mean")))
        conf_bar(doc, mm["time"])
        flags = [k for k in ("isAdam", "isRoot") if mm.get(k)]
        if flags:
            doc.kv("Flags", ", ".join(flags))

    # ── служебное: легенда цветов и сырые данные Globetrekker ──
    doc.title("SERVICE · legend and Globetrekker")
    legend = mm.get("legend") or mm.get("haplogroup_legend")
    if legend:
        put(doc, ("Legend · newest first", "bold #FBF6F0"))
        if isinstance(legend, list):
            dict_items = sorted((i for i in legend if isinstance(i, dict)),
                                key=lambda i: (*_newest_key(i.get("tmrca")), str(i.get("name"))))
            for item in dict_items:
                t = _year(item.get("tmrca"))
                put(doc, ("■■ ", item.get("color") or ""), (str(item.get("name")), "bold"),
                    (f"   {dv(t)}" if t is not None else "", "#AEA5B8"), indent=2)
            for item in legend:
                if not isinstance(item, dict):
                    doc.line(f"• {item}", "", 1)
        else:
            dump(doc, legend, 1)
    put(doc, ("Globetrekker", "bold #FBF6F0"))
    gt = node.get("globetrekker") or {}
    if isinstance(gt, dict) and gt and set(gt.keys()) <= {"start"}:
        put(doc, ("start only  ", "#AEA5B8"), (str(gt.get("start")), "#D4AD72"), indent=1)
        put(doc, ("no path payload in this card — routes above / Big Y path count",
                  "#71697A"), indent=1)
    elif gt:
        dump(doc, gt, 1)
    else:
        doc.line("—", "", 1)
    paths = _dict(node["upgrades"].get("bigy")).get("globetrekker_paths")
    if paths is not None:
        doc.kv("Big Y Globetrekker paths", paths)


def toggle_world(ctx) -> None:
    ctx.map_world = not getattr(ctx, "map_world", False)


@tab("Migration Map", keys={"w": toggle_world})
def migration_map(ctx, doc: Doc, node: dict) -> None:
    """Карта + Contact log + маршруты миграции (бывшие вкладки Map и Migration)."""
    fit(ctx)
    world = getattr(ctx, "map_world", False)
    doc.title("THEATER · " + ("WORLD" if world else "FIT") + " · W — view · M — fullscreen")
    try:
        from .worldmap import defcon_map
    except ImportError:
        ascii_map(doc, node["migrationmap"])  # запасной вариант без береговых линий
        render_migration(doc, node)
        return
    # ширину берём из панели инспектора (ctx.panel_width), иначе строки заворачиваются
    cols = max(34, min(140, int(getattr(ctx, "panel_width", 60)) - 6))
    defcon_map(doc, _dict(node["migrationmap"]), node["ancient"], node["name"], dv,
               world=world, cols=cols, with_log=True)
    render_migration(doc, node)


def toggle_raw(ctx) -> None:
    ctx.raw_full = not ctx.raw_full


_TOK = re.compile(
    r'("(?:\\.|[^"\\])*")(\s*:)?'          # строка / ключ
    r"|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"  # число
    r"|\b(true|false|null)\b"               # литералы
)


def _highlight(doc: Doc, line: str, no: int, width: int) -> None:
    doc.t.append(f"{no:>{width}} ", "#5A5361")
    pos = 0
    for m in _TOK.finditer(line):
        if m.start() > pos:
            doc.t.append(line[pos:m.start()], "#AEA5B8")
        if m.group(1):
            if m.group(2):
                doc.t.append(m.group(1), "#D4AD72")
                doc.t.append(m.group(2), "#AEA5B8")
            else:
                doc.t.append(m.group(1), "#53A94B")
        elif m.group(3):
            doc.t.append(m.group(3), "#B04A4A")
        else:
            doc.t.append(m.group(4), "#49AE6B")
        pos = m.end()
    doc.t.append(line[pos:] + "\n", "#AEA5B8")


@tab("Raw JSON", keys={"x": toggle_raw})
def raw(ctx, doc: Doc, node: dict) -> None:
    doc.title("RAW JSON")
    data, err = load_resource(ctx.folder / (node.get("source_file") or ""))
    if err:
        doc.line(f"Cannot read {node.get('source_file')}: {err}", "#8BAB4A", 1)
        return
    lines = json.dumps(data, ensure_ascii=False, indent=2).splitlines()
    limit = len(lines) if ctx.raw_full else RAW_LINES
    w = len(str(limit))
    for i, ln in enumerate(lines[:limit], 1):
        _highlight(doc, ln, i, w)
    if len(lines) > limit:
        doc.line(f"… +{len(lines) - limit:,} more lines — press X to show all", "bold #AEA5B8")
    elif ctx.raw_full and len(lines) > RAW_LINES:
        doc.line("press X to collapse", "#8A8294")