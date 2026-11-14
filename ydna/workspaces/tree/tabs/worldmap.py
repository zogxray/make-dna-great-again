"""Карта в стиле DEFCON для вкладки Map.

Береговая линия: Natural Earth 50m land (public domain) через world-atlas,
упрощена и вшита в _DATA (только берега — без современных границ государств).
Рисуем на брайлевском холсте: 2x4 точки на символ. Rebuild: scripts/build_worldmap_coast.py.
"""

from __future__ import annotations

import base64
import json
import math
import zlib
from typing import Any, Callable

from ....theme.styles import MAP
from ...base import click as click_style

_DATA = (
    "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"
)

BG = MAP["bg"]
COL = {k: MAP[k] for k in (
    "coast", "border", "polity", "polity_fill", "habitat", "habitat_fill",
    "ring", "grid", "route")}
_BITS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))
# First in _PRIO wins when layers share a braille cell.
_PRIO = ("route", "kin", "freq", "habitat", "polity", "coast", "border", "ring",
         "habitat_fill", "polity_fill", "grid")
_cache: list | None = None
# Framing → (cells, color) for grid+coast+border. Filters/blink reuse the same box.
_BASE_CACHE: dict[tuple, tuple[dict, dict]] = {}
_BASE_CACHE_MAX = 16


def _polys() -> list[tuple[int, list[tuple[float, float]]]]:
    global _cache
    if _cache is None:
        res = []
        for kind, flat in json.loads(zlib.decompress(base64.b64decode(_DATA))):
            x, y = flat[0], flat[1]
            pts = [(x / 10, y / 10)]
            for i in range(2, len(flat), 2):
                x += flat[i]
                y += flat[i + 1]
                pts.append((x / 10, y / 10))
            res.append((kind, pts))
        _cache = res
    return _cache


def _base_frame_key(cols: int, rows: int, lon_c: float, lat_c: float,
                    scale: float, gstep: int) -> tuple:
    return (cols, rows, round(lon_c, 5), round(lat_c, 5), round(scale, 5), gstep)


def world_base_params(cols: int, max_rows: int) -> tuple[int, float, float, float, int]:
    """Framing for ``world=True`` scatter maps (matches ``scatter_map``).

    Returns ``(rows, lon_c, lat_c, scale, gstep)`` — ``rows`` may be < max_rows.
    """
    cols = max(30, cols)
    max_rows = max(12, max_rows)
    lon0, lon1, lat0, lat1 = -180.0, 180.0, -58.0, 84.0
    lon_c, lat_c = (lon0 + lon1) / 2, (lat0 + lat1) / 2
    W = cols * 2
    scale = min(W / max(1e-6, lon1 - lon0), (max_rows * 4) / max(1e-6, lat1 - lat0))
    rows = max(12, min(max_rows, math.ceil((lat1 - lat0) * scale / 4)))
    gstep = 10 if scale >= 1.2 else 30
    return rows, lon_c, lat_c, scale, gstep


def prewarm_world_base(cols: int = 80, max_rows: int = 24) -> None:
    """Rasterize the default world coast once into ``_BASE_CACHE`` (first-paint win)."""
    cols = max(30, int(cols))
    max_rows = max(12, int(max_rows))
    rows, lon_c, lat_c, scale, gstep = world_base_params(cols, max_rows)
    key = _base_frame_key(cols, rows, lon_c, lat_c, scale, gstep)
    if key in _BASE_CACHE:
        return
    cv = _Canvas(cols, rows)
    _paint_base(cv, lon_c=lon_c, lat_c=lat_c, scale=scale, gstep=gstep)


def _paint_base(cv: "_Canvas", *, lon_c: float, lat_c: float, scale: float,
                gstep: int) -> None:
    """Grid + Natural Earth 50m coasts. Cached by map framing."""
    key = _base_frame_key(cv.cols, cv.rows, lon_c, lat_c, scale, gstep)
    hit = _BASE_CACHE.get(key)
    if hit is not None:
        cells, color = hit
        cv.cells = {k: dict(v) for k, v in cells.items()}
        cv.color = dict(color)
        return

    px = lambda lon, lat: ((lon - lon_c) * scale + cv.W / 2, (lat_c - lat) * scale + cv.H / 2)
    vis_lon0, vis_lon1 = lon_c - cv.W / 2 / scale, lon_c + cv.W / 2 / scale
    vis_lat0, vis_lat1 = lat_c - cv.H / 2 / scale, lat_c + cv.H / 2 / scale
    m = math.ceil(vis_lon0 / gstep) * gstep
    while m <= vis_lon1:
        x = round(px(m, 0)[0])
        for y in range(0, cv.H, 4):
            cv.dot(x, y, "grid")
        m += gstep
    m = math.ceil(vis_lat0 / gstep) * gstep
    while m <= vis_lat1:
        y = round(px(0, m)[1])
        for x in range(0, cv.W, 4):
            cv.dot(x, y, "grid")
        m += gstep
    shifts = [sh for sh in (-360, 0, 360) if -180 + sh <= vis_lon1 and 180 + sh >= vis_lon0]
    for kind, pl in _polys():
        layer = "border" if kind else "coast"
        for sh in shifts:
            for a, b in zip(pl, pl[1:]):
                if abs(a[0] - b[0]) > 100:
                    continue
                (x0, y0), (x1, y1) = px(a[0] + sh, a[1]), px(b[0] + sh, b[1])
                cv.line(x0, y0, x1, y1, layer)

    _BASE_CACHE[key] = (
        {k: dict(v) for k, v in cv.cells.items()},
        dict(cv.color),
    )
    while len(_BASE_CACHE) > _BASE_CACHE_MAX:
        _BASE_CACHE.pop(next(iter(_BASE_CACHE)))


def _d(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _layer_fg(cv: "_Canvas", cx: int, cy: int, top: str) -> str:
    if top in ("route", "kin", "freq", "polity", "polity_fill", "habitat", "habitat_fill"):
        return cv.color.get((cx, cy), COL.get(top, COL["route"]))
    return COL[top]


class _Canvas:
    def __init__(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        self.W, self.H = cols * 2, rows * 4
        self.cells: dict[tuple[int, int], dict[str, int]] = {}
        self.color: dict[tuple[int, int], str] = {}
        self.over: dict[tuple[int, int], tuple[str, str]] = {}
        self.hot: dict[tuple[int, int], str] = {}  # cell → screen.action(…)
        self.taken: set[tuple[int, int]] = set()

    def dot(self, x: int, y: int, layer: str, color: str | None = None) -> None:
        if 0 <= x < self.W and 0 <= y < self.H:
            key = (x >> 1, y >> 2)
            c = self.cells.setdefault(key, {})
            c[layer] = c.get(layer, 0) | _BITS[y & 3][x & 1]
            if color:
                self.color[key] = color

    def line(self, x0: float, y0: float, x1: float, y1: float, layer: str,
             color: str | None = None) -> None:
        x0, y0, x1, y1 = round(x0), round(y0), round(x1), round(y1)
        if (x0 < 0 and x1 < 0) or (x0 >= self.W and x1 >= self.W) \
                or (y0 < 0 and y1 < 0) or (y0 >= self.H and y1 >= self.H):
            return
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        if dx + dy > 4000:
            return
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx - dy
        while True:
            self.dot(x0, y0, layer, color)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy

    def mark(self, cx: int, cy: int, ch: str, style: str,
             action: str | None = None) -> None:
        if 0 <= cx < self.cols and 0 <= cy < self.rows:
            self.over[(cx, cy)] = (ch, f"{style} on {BG}")
            self.taken.add((cx, cy))
            if action:
                self.hot[(cx, cy)] = action

    def label(self, cx: int, cy: int, text: str, style: str) -> bool:
        for dy in (0, -1, 1, -2, 2):
            yy = cy + dy
            if not 0 <= yy < self.rows:
                continue
            for xs in (cx + 2, cx - len(text) - 1):
                if xs < 0 or xs + len(text) > self.cols:
                    continue
                if all((c, yy) not in self.taken for c in range(xs - 1, xs + len(text) + 1)):
                    for i, ch in enumerate(text):
                        self.over[(xs + i, yy)] = (ch, f"{style} on {BG}")
                        self.taken.add((xs + i, yy))
                    return True
        return False


def _hg_legend_colors(mm: dict) -> dict[str, str]:
    """Имя из haplogroup_legend → hex (как в Migration path)."""
    out: dict[str, str] = {}
    for item in mm.get("haplogroup_legend") or mm.get("legend") or []:
        if isinstance(item, dict) and item.get("name") and item.get("color"):
            out[str(item["name"])] = str(item["color"])
    return out


def _color_for(name: str, legend: dict[str, str], fallback: str = "#FBF6F0") -> str:
    """Полное имя клады или корень до '-'; затем fallback."""
    key = str(name or "")
    if key in legend:
        return legend[key]
    root = key.split("-", 1)[0]
    if root in legend:
        return legend[root]
    return fallback


def _extract(mm: dict) -> tuple[list, dict, Any, dict, dict]:
    """segs, nodes, origin, deg, node_stroke (цвет линии у узла)."""
    segs, nodes, origin, deg = [], {}, None, {}
    node_stroke: dict[str, str] = {}
    for f in _d(mm.get("geojson")).get("features") or []:
        if not isinstance(f, dict):
            continue
        g, pr = _d(f.get("geometry")), _d(f.get("properties"))
        c = g.get("coordinates")
        if g.get("type") == "LineString" and c:
            stroke = pr.get("stroke") or COL["route"]
            segs.append((c, stroke))
            if pr.get("startNode"):
                sn = pr["startNode"]
                nodes.setdefault(sn, c[0])
                deg[sn] = deg.get(sn, 0) + 1
                node_stroke.setdefault(sn, stroke)
            if pr.get("endNode"):
                en = pr["endNode"]
                nodes.setdefault(en, c[-1])
                node_stroke.setdefault(en, stroke)
        elif g.get("type") == "Point" and c and pr.get("name"):
            nodes[pr["name"]] = c
            if pr.get("color"):
                node_stroke.setdefault(pr["name"], pr["color"])
            if pr.get("type") == "haplogroup":
                origin = c
    return segs, nodes, origin, deg, node_stroke


def _contacts(ancient: list, mm: dict) -> list[dict]:
    out = []
    for s in ancient or []:
        if isinstance(s, dict) and s.get("latitude") is not None and s.get("longitude") is not None:
            out.append(s)
    if not out:  # запасной вариант — geojson_ancient из migrationmap
        for f in _d(mm.get("geojson_ancient")).get("features") or []:
            c = _d(f.get("geometry")).get("coordinates")
            pr = _d(f.get("properties"))
            if c:
                out.append({**pr, "longitude": c[0], "latitude": c[1]})
    return out


def _fit_col(text: Any, width: int) -> str:
    s = str(text or "")
    if width <= 0:
        return ""
    if len(s) <= width:
        return s + " " * (width - len(s))
    if width == 1:
        return s[:1]
    return s[: width - 1] + "…"


def _short_country(name: str) -> str:
    if name == "Russian Federation":
        return "Russia"
    if name == "United States":
        return "USA"
    if name == "United Kingdom":
        return "UK"
    return name


def _contact_when(cl: list, fmt: Callable[[Any], str]) -> str:
    ages = [x.get("age_estimate_mean") for x in cl if x.get("age_estimate_mean") is not None]
    if not ages:
        return "?"
    if len(ages) > 1 and min(ages) != max(ages):
        return f"{fmt(max(ages))} … {fmt(min(ages))}"
    return fmt(max(ages))


def _contact_place(cl: list) -> str:
    first = cl[0] if cl else {}
    place = first.get("site") or first.get("geography") or first.get("country_name") or "?"
    country = _short_country(str(first.get("country_name") or ""))
    place_s = str(place)
    if country and country != place_s and country not in place_s:
        return f"{place_s} · {country}"
    return place_s


def _contact_hg(cl: list) -> str:
    return str((cl[0] if cl else {}).get("haplogroup_y") or "")


def _contact_col_widths(width: int) -> tuple[int, int, int, int, int]:
    """id, when, place, hg, n — под узкую правую панель / широкую карту."""
    id_w, n_w = 3, 3
    when_w = 12 if width >= 44 else 10
    hg_w = 10 if width >= 48 else 8
    pad = 8  # разделители между колонками
    place_w = max(8, width - id_w - when_w - hg_w - n_w - pad)
    return id_w, when_w, place_w, hg_w, n_w


def _contact_rows(centers: list, fmt: Callable[[Any], str],
                  limit: int) -> list[tuple[int, str, str, str, int]]:
    rows: list[tuple[int, str, str, str, int]] = []
    for i, _cx, _cy, cl in centers[:limit]:
        rows.append((i, _contact_when(cl, fmt), _contact_place(cl), _contact_hg(cl), len(cl)))
    return rows


def contact_log_lines(centers: list, fmt: Callable[[Any], str] = str,
                      limit: int = 40, width: int = 42) -> list[tuple[str, str]]:
    """CONTACT LOG B2: таблица # / when / place · country / hg / n.

    ▲ только на карте; в логе — номер строки (= подпись у маркера).
    """
    out: list[tuple[str, str]] = []
    if not centers:
        return out
    width = max(28, width)
    id_w, when_w, place_w, hg_w, n_w = _contact_col_widths(width)
    out.append((f"CONTACT LOG · newest first · {len(centers)}", "bold #B04A4A"))
    out.append(("# on map = ▲ · row id matches map label", "#8A8294"))
    hdr = (f"{'#':>{id_w}}  {_fit_col('when', when_w)}  {_fit_col('place / country', place_w)}  "
           f"{_fit_col('hg', hg_w)}  {'n':>{n_w}}")
    out.append((hdr, "#8A8294"))
    out.append(("─" * min(width, len(hdr)), "#433F47"))
    for i, when, place, hg, n in _contact_rows(centers, fmt, limit):
        line = (f"{i:>{id_w}}  {_fit_col(when, when_w)}  {_fit_col(place, place_w)}  "
                f"{_fit_col(hg, hg_w)}  {n:>{n_w}}")
        out.append((line, "#AEA5B8"))
    if len(centers) > limit:
        out.append((f"… {len(centers) - limit} more", "#8A8294"))
    return out


def defcon_map(doc: Any, mm: Any, ancient: list, title: str,
               fmt: Callable[[Any], str] = str, world: bool = False, cols: int = 96,
               max_rows: int = 30, with_log: bool = True,
               show_path_legend: bool | None = None) -> list:
    """Рисует карту. Возвращает centers [(i, cx, cy, cluster), ...] newest first.

    show_path_legend: None → как not with_log (легенда под картой на fullscreen).
    False — не рисовать (легенда уже в боковой панели).
    """
    cols = max(30, cols)
    mm = _d(mm)
    segs, nodes, origin, deg, node_stroke = _extract(mm)
    legend = _hg_legend_colors(mm)
    if show_path_legend is None:
        show_path_legend = not with_log
    contacts = _contacts(ancient, mm)

    def node_color(name: str) -> str:
        return _color_for(name, legend, node_stroke.get(name) or "#FBF6F0")
    _rl = [pt[0] for c, _ in segs for pt in c]
    rlo, rhi = (min(_rl), max(_rl)) if _rl else (None, None)

    def unwrap(lon: float) -> float:
        if rlo is None:
            return lon
        cands = (lon, lon - 360, lon + 360)
        for c in cands:
            if rlo - 15 <= c <= rhi + 15:
                return c
        mid = (rlo + rhi) / 2
        return min(cands, key=lambda x: abs(x - mid))

    contacts = [{**s_, "longitude": unwrap(float(s_["longitude"]))} for s_ in contacts]

    # ── кластеры древних образцов (одна точка карты = одно место) ──
    clusters: dict[tuple[int, int], list[dict]] = {}
    for s in contacts:
        clusters.setdefault((round(float(s["longitude"]) * 2), round(float(s["latitude"]) * 2)), []).append(s)

    def age_key(cl: list[dict]) -> tuple:
        # Календарные годы (отриц. = BCE): сначала новые, без даты — в конце.
        a = [x.get("age_estimate_mean") for x in cl if x.get("age_estimate_mean") is not None]
        if not a:
            return (True, 0)
        return (False, -max(a))

    ordered = sorted(clusters.values(), key=age_key)

    # ── охват карты ──
    pts = [p for c, _ in segs for p in c] + [(float(s["longitude"]), float(s["latitude"])) for s in contacts]
    if origin:
        pts.append(origin)
    if world or not pts:
        lon0 = -180.0
        if pts and (min(p[0] for p in pts) < -185 or max(p[0] for p in pts) > 185):
            lon0 = min(p[0] for p in pts) - 10
        lon1, lat0, lat1 = lon0 + 360, -58.0, 84.0
    else:
        lon0, lon1 = min(p[0] for p in pts) - 6, max(p[0] for p in pts) + 6
        lat0, lat1 = min(p[1] for p in pts) - 5, max(p[1] for p in pts) + 5
        if lon1 - lon0 < 50:
            m = (lon0 + lon1) / 2
            lon0, lon1 = m - 25, m + 25
        if lat1 - lat0 < 30:
            m = (lat0 + lat1) / 2
            lat0, lat1 = m - 15, m + 15
    if lon1 - lon0 > 360:
        lon1 = lon0 + 360
    lon_c, lat_c = (lon0 + lon1) / 2, (lat0 + lat1) / 2
    W = cols * 2
    max_rows = max(1, max_rows)
    scale = min(W / (lon1 - lon0), (max_rows * 4) / max(lat1 - lat0, 1e-6))
    # Не больше max_rows (иначе fullscreen даёт вертикальный скролл).
    want = math.ceil((lat1 - lat0) * scale / 4) if scale else max_rows
    rows = max(1, min(max_rows, want))
    cv = _Canvas(cols, rows)
    px = lambda lon, lat: ((lon - lon_c) * scale + cv.W / 2, (lat_c - lat) * scale + cv.H / 2)
    cell = lambda lon, lat: (int(px(lon, lat)[0]) >> 1, int(px(lon, lat)[1]) >> 2)

    vis_lon0, vis_lon1 = lon_c - cv.W / 2 / scale, lon_c + cv.W / 2 / scale
    vis_lat0, vis_lat1 = lat_c - cv.H / 2 / scale, lat_c + cv.H / 2 / scale

    # ── сетка (шаг зависит от масштаба) ──
    gstep = 10 if scale >= 1.2 else 30
    astep = 20 if scale >= 1.2 else 60
    m = math.ceil(vis_lon0 / gstep) * gstep
    while m <= vis_lon1:
        x = round(px(m, 0)[0])
        for y in range(0, cv.H, 4):
            cv.dot(x, y, "grid")
        m += gstep
    m = math.ceil(vis_lat0 / gstep) * gstep
    while m <= vis_lat1:
        y = round(px(0, m)[1])
        for x in range(0, cv.W, 4):
            cv.dot(x, y, "grid")
        m += gstep

    # ── радарные кольца вокруг точки гаплогруппы ──
    if origin:
        ox, oy = px(*origin)
        for r in range(gstep, 70 if gstep == 10 else 100, gstep):
            rad = r * scale
            step = max(2, int(3 * 360 / (2 * math.pi * max(rad, 1))))
            for k in range(0, 360, step):
                a = math.radians(k)
                cv.dot(round(ox + rad * math.cos(a)), round(oy + rad * math.sin(a)), "ring")

    # ── берега и границы ──
    shifts = [sh for sh in (-360, 0, 360) if -180 + sh <= vis_lon1 and 180 + sh >= vis_lon0]
    for kind, pl in _polys():
        layer = "border" if kind else "coast"
        for sh in shifts:
            for a, b in zip(pl, pl[1:]):
                if abs(a[0] - b[0]) > 100:
                    continue
                (x0, y0), (x1, y1) = px(a[0] + sh, a[1]), px(b[0] + sh, b[1])
                cv.line(x0, y0, x1, y1, layer)

    # ── маршрут миграции (линия потолще) ──
    for c, color in segs:
        for a, b in zip(c, c[1:]):
            (x0, y0), (x1, y1) = px(*a), px(*b)
            cv.line(x0, y0, x1, y1, "route", color)
            cv.line(x0, y0 + 1, x1, y1 + 1, "route", color)

    # ── маркеры (цвет = haplogroup_legend / stroke сегмента) ──
    for name, c in nodes.items():
        cx, cy = cell(*c)
        cv.mark(cx, cy, "◆", f"bold {node_color(name)}")
    if origin:
        cx, cy = cell(*origin)
        route_hg = str(mm.get("haplogroup") or title or "")
        oc = node_color(route_hg) if route_hg else MAP.get("origin", "#53A94B")
        cv.mark(cx, cy, "◉", f"bold {oc}")
    centers = []
    for i, cl in enumerate(ordered, 1):
        lon = sum(float(x["longitude"]) for x in cl) / len(cl)
        lat = sum(float(x["latitude"]) for x in cl) / len(cl)
        cx, cy = cell(lon, lat)
        centers.append((i, cx, cy, cl))
        if (cx, cy) not in cv.over:
            cv.mark(cx, cy, "▲", "bold #B04A4A")
    ocell = cell(*origin) if origin else None
    budget = len(nodes) if cols >= 130 else max(8, cols // 4)
    prio = sorted(range(len(nodes)), key=lambda i: (-deg.get(list(nodes)[i], 0), i))
    keys = list(nodes)
    for rank, i in enumerate(prio):
        name = keys[i]
        cx, cy = cell(*nodes[name])
        ok = rank < budget and cv.label(cx, cy, name, f"bold {node_color(name)}")
        if not ok and (cx, cy) != ocell:
            cv.over.pop((cx, cy), None)
    for i, cx, cy, cl in centers[:30]:
        cv.label(cx, cy, str(i), "bold #B04A4A")

    # ── вывод ──
    total_w = cols + 6

    def pad_line(parts: list[tuple[str, str]], used: int = 0) -> None:
        left = total_w
        for text, fg in parts:
            if left <= 0:
                break
            text = text[:left]
            doc.t.append(text, f"{fg} on {BG}")
            left -= len(text)
        doc.t.append(" " * max(0, left), f"white on {BG}")
        doc.t.append("\n")

    head = f" ◢◤ Y-DNA THEATER ▸ {title} "
    tail = f"{scale:.1f} px/° · {len(nodes)} nodes · {len(contacts)} contacts "
    if len(head) + len(tail) + 1 > total_w:
        tail = ""
    pad_line([(head, "bold #5882C7"), (" " * max(1, total_w - len(head) - len(tail)), "#FBF6F0"),
              (tail, "#5A7CB3")])
    pad_line([("═" * total_w, "#483A59")], total_w)

    ylab = {}
    m = math.ceil(vis_lat0 / gstep) * gstep
    while m <= vis_lat1:
        r = int(px(0, m)[1]) >> 2
        if 0 <= r < rows:
            ylab[r] = f"{abs(m):>3}°{'N' if m >= 0 else 'S'} "
        m += gstep
    for r in range(rows):
        doc.t.append(ylab.get(r, " " * 6), f"#5A7CB3 on {BG}")
        run, st = "", None
        for cx in range(cols):
            if (cx, r) in cv.over:
                ch, sty = cv.over[(cx, r)]
            elif (cx, r) in cv.cells:
                layers = cv.cells[(cx, r)]
                top = next(l for l in _PRIO if l in layers)
                fg = _layer_fg(cv, cx, r, top)
                mask = 0
                for v in layers.values():
                    mask |= v
                ch, sty = chr(0x2800 + mask), f"{fg} on {BG}"
            else:
                ch, sty = " ", f"white on {BG}"
            if sty != st:
                if run:
                    doc.t.append(run, st)
                run, st = "", sty
            run += ch
        if run:
            doc.t.append(run, st)
        doc.t.append("\n")

    axis = [" "] * cols
    m = math.ceil(vis_lon0 / astep) * astep
    while m <= vis_lon1:
        n_ = ((int(m) + 180) % 360) - 180
        lab = f"{abs(n_)}°" + ("" if abs(n_) in (0, 180) else ("E" if n_ > 0 else "W"))
        xs = max(0, min(cols - len(lab), (int(px(m, 0)[0]) >> 1) - len(lab) // 2))
        for i, ch in enumerate(lab):
            axis[xs + i] = ch
        m += astep
    pad_line([(" " * 6 + "".join(axis), "#5A7CB3")], total_w)
    pad_line([("═" * total_w, "#483A59")], total_w)
    leg = [(" ◆", "bold #FBF6F0"), (" node  ", "#AEA5B8"), ("━", "#8BAB4A"), (" route  ", "#AEA5B8"),
           ("▲", "bold #B04A4A"), (" contact  ", "#AEA5B8"), ("◉", "bold #53A94B"), (" origin", "#AEA5B8")]
    if total_w >= 78:
        leg += [("   ··", "#3C3247"), (f" ring {gstep}°", "#AEA5B8")]
    pad_line(leg)

    # ── LEGEND одной строкой под картой (цвета = Migration path) ──
    if legend and show_path_legend:
        used_roots = {str(n).split("-", 1)[0] for n in nodes}
        used_roots |= {str(n) for n in nodes if n in legend}
        items: list[tuple[str, str]] = []
        for item in mm.get("haplogroup_legend") or mm.get("legend") or []:
            if not isinstance(item, dict) or not item.get("name") or not item.get("color"):
                continue
            nm = str(item["name"])
            if nm in used_roots or nm in nodes or nm.split("-", 1)[0] in used_roots:
                items.append((nm, str(item["color"])))
        if not items:
            items = list(legend.items())
        raw = [i for i in (mm.get("haplogroup_legend") or []) if isinstance(i, dict)]
        order = {str(i.get("name")): k for k, i in enumerate(raw)}
        items.sort(key=lambda kv: order.get(kv[0], 10_000))
        row: list[tuple[str, str]] = [(" LEGEND ", "bold #D4AD72")]
        used = len(" LEGEND ")
        for nm, col in items:
            piece = f"■■ {nm}  "
            if used + len(piece) > total_w:
                break
            row += [("■■ ", col), (f"{nm}  ", "#AEA5B8")]
            used += len(piece)
        pad_line(row)

    # ── журнал контактов B2 (newest first; на полноэкране — в правой панели) ──
    if with_log and centers:
        log_w = max(36, total_w - 1)
        id_w, when_w, place_w, hg_w, n_w = _contact_col_widths(log_w)
        pad_line([(f" CONTACT LOG · newest first · {len(centers)}", "bold #B04A4A")])
        pad_line([(" # on map = ▲ · row id matches map label", "#8A8294")])
        pad_line([
            (f" {'#':>{id_w}}  ", "#8A8294"),
            (_fit_col("when", when_w) + "  ", "#8A8294"),
            (_fit_col("place / country", place_w) + "  ", "#8A8294"),
            (_fit_col("hg", hg_w) + "  ", "#8A8294"),
            (f"{'n':>{n_w}}", "#8A8294"),
        ])
        rule = "─" * min(log_w, id_w + when_w + place_w + hg_w + n_w + 10)
        pad_line([(f" {rule}", "#433F47")])
        for i, when, place, hg, n in _contact_rows(centers, fmt, 18):
            pad_line([
                (f" {i:>{id_w}}  ", "bold #B04A4A"),
                (_fit_col(when, when_w) + "  ", "#BD5454"),
                (_fit_col(place, place_w) + "  ", "#5882C7"),
                (_fit_col(hg, hg_w) + "  ", "#AEA5B8"),
                (f"{n:>{n_w}}", "#B04A4A"),
            ])
        if len(centers) > 18:
            pad_line([(f" … {len(centers) - 18} more", "#AEA5B8")], 14)

    return centers


_SMALL_GLYPH = {"●": "·", "○": "◦", "▲": "▴", "▼": "▾"}


def _big_triangle(cv: _Canvas, cx: int, cy: int, color: str) -> None:
    sty = f"bold {color}"
    cv.mark(cx, cy - 1, "▲", sty)
    for dx in (-1, 0, 1):
        cv.mark(cx + dx, cy, "▲", sty)


def _cell_is_ocean(cv: "_Canvas", cx: int, cy: int) -> bool:
    """True for empty/grid sea — no coast / polity / habitat / sample marks."""
    if not (0 <= cx < cv.cols and 0 <= cy < cv.rows):
        return False
    if (cx, cy) in cv.over or (cx, cy) in cv.taken:
        return False
    layers = cv.cells.get((cx, cy))
    if not layers:
        return True
    return set(layers).issubset({"grid"})


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain. Returns CCW ring (open; caller may close)."""
    pts = sorted(set(points))
    if len(pts) <= 1:
        return list(pts)
    if len(pts) == 2:
        return [pts[0], pts[1], pts[0]]

    def cross(o, a, b) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _pad_ring(ring: list[tuple[float, float]], frac: float = 0.12
              ) -> list[tuple[float, float]]:
    """Expand ring from centroid so the area reads thicker than the point cloud."""
    if len(ring) < 3:
        return ring
    cx = sum(p[0] for p in ring) / len(ring)
    cy = sum(p[1] for p in ring) / len(ring)
    out: list[tuple[float, float]] = []
    for x, y in ring:
        out.append((cx + (x - cx) * (1.0 + frac), cy + (y - cy) * (1.0 + frac)))
    return out


def culture_habitat_hulls(
    pts: list[tuple[float, float, dict]],
    *,
    min_sites: int = 3,
    limit: int = 10,
    only_cultures: set[str] | frozenset[str] | None = None,
) -> list[dict]:
    """Convex-hull ranges for cultures with enough distinct sample sites.

    Each dict: culture, n, ring (lon/lat), color, rank, lon, lat.
    Skips cultures that wrap the antimeridian (lon span > 180°).
    ``only_cultures``: build just those names (focus path — skip the full scan).
    """
    buckets: dict[str, list[tuple[float, float, dict]]] = {}
    for lon, lat, p in pts:
        cult = (p.get("culture") or "").strip()
        if not cult:
            continue
        if only_cultures is not None and cult not in only_cultures:
            continue
        buckets.setdefault(cult, []).append((float(lon), float(lat), p))

    scored: list[tuple[int, float, str, list[tuple[float, float]], str]] = []
    for cult, rows in buckets.items():
        uniq: dict[tuple[float, float], dict] = {}
        best_rank = -1.0
        best_color = COL["habitat"]
        for lon, lat, p in rows:
            key = (round(lon, 2), round(lat, 2))
            uniq.setdefault(key, p)
            rnk = float(p.get("rank") if p.get("rank") is not None else -1.0)
            if rnk >= best_rank:
                best_rank = rnk
                best_color = p.get("color") or best_color
        if len(uniq) < min_sites:
            continue
        coords = list(uniq.keys())
        xs = [c[0] for c in coords]
        if max(xs) - min(xs) > 180.0:
            continue
        hull = _convex_hull(coords)
        if len(hull) < 3:
            continue
        scored.append((len(rows), best_rank, cult, _pad_ring(hull), str(best_color)))

    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    out: list[dict] = []
    for n, rank, cult, ring, color in scored[: max(1, limit)]:
        cx = sum(p[0] for p in ring) / len(ring)
        cy = sum(p[1] for p in ring) / len(ring)
        out.append({
            "culture": cult,
            "label": cult,
            "n": n,
            "ring": ring,
            "color": color,
            "rank": rank,
            "lon": cx,
            "lat": cy,
            "kind": "habitat",
        })
    return out


def _paint_habitats(cv: "_Canvas", hulls: list[dict], *,
                    lon_c: float, lat_c: float, scale: float,
                    focus_cult: str | None = None,
                    fill: bool = False) -> set[str]:
    """Draw culture convex hulls (outline; fill only when focused). Return drawn."""
    if not hulls:
        return set()
    px = lambda lon, lat: ((lon - lon_c) * scale + cv.W / 2,
                           (lat_c - lat) * scale + cv.H / 2)
    drawn: set[str] = set()
    ordered = sorted(hulls, key=lambda h: -int(h.get("n") or 0))
    for hull in reversed(ordered):
        cult = (hull.get("culture") or "").strip()
        ring = hull.get("ring") or ()
        if not cult or len(ring) < 3:
            continue
        color = hull.get("color") or COL["habitat"]
        focused = bool(focus_cult and cult == focus_cult)
        pts = [px(float(lon), float(lat)) for lon, lat in ring]
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        if fill or focused:
            _fill_ring(cv, pts, "habitat_fill",
                       color if focused else COL["habitat_fill"])
        for a, b in zip(pts, pts[1:]):
            cv.line(a[0], a[1], b[0], b[1], "habitat", color)
        drawn.add(cult)
    return drawn


def _paint_polities(cv: "_Canvas", polities: list[dict], *,
                    lon_c: float, lat_c: float, scale: float,
                    focus_name: str | None = None,
                    fill: bool = False) -> list[tuple[str, str, str | None]]:
    """Draw Cliopatria rings (outline; fill only for focused polity). Return legend."""
    if not polities:
        return []
    px = lambda lon, lat: ((lon - lon_c) * scale + cv.W / 2,
                           (lat_c - lat) * scale + cv.H / 2)
    legend: list[tuple[str, str, str | None]] = []
    for pol in reversed(polities):
        color = pol.get("color") or COL["polity"]
        rings = pol.get("rings") or ()
        name = (pol.get("name") or pol.get("id") or "").strip()
        focused = bool(focus_name and name == focus_name)
        for ring in rings:
            if not ring or len(ring) < 3:
                continue
            pts = [px(float(lon), float(lat)) for lon, lat in ring]
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            # Default: outline only so genotype points stay readable.
            if fill or focused:
                _fill_ring(cv, pts, "polity_fill",
                           color if focused else COL["polity_fill"])
            for a, b in zip(pts, pts[1:]):
                cv.line(a[0], a[1], b[0], b[1], "polity", color)
    budget = max(4, min(8, cv.rows // 3))
    for pol in polities[:budget]:
        lab = (pol.get("label") or pol.get("name") or "¶").strip()
        name = (pol.get("name") or lab).strip()
        key = f"p:{name}"
        mark = "◆" if focus_name and name == focus_name else "¶"
        legend.append((
            f"{mark} {lab}"[:20],
            pol.get("color") or COL["polity"],
            f"screen.map_focus({key!r})",
        ))
    return legend


def _fill_ring(cv: "_Canvas", pts: list[tuple[float, float]],
               layer: str, color: str) -> None:
    """Scanline fill of a closed ring in canvas pixels (even-odd)."""
    if len(pts) < 4:
        return
    ys = [p[1] for p in pts]
    y0, y1 = int(min(ys)), int(max(ys))
    if y1 < 0 or y0 >= cv.H:
        return
    y0 = max(0, y0)
    y1 = min(cv.H - 1, y1)
    # Braille row stride (~4 px) keeps fill light.
    for y in range(y0, y1 + 1, 2):
        xs: list[float] = []
        for (x0, y_a), (x1, y_b) in zip(pts, pts[1:]):
            if (y_a <= y < y_b) or (y_b <= y < y_a):
                if y_b == y_a:
                    continue
                t = (y - y_a) / (y_b - y_a)
                xs.append(x0 + t * (x1 - x0))
        if len(xs) < 2:
            continue
        xs.sort()
        for i in range(0, len(xs) - 1, 2):
            xa, xb = int(xs[i]), int(xs[i + 1])
            if xb < 0 or xa >= cv.W:
                continue
            xa = max(0, xa)
            xb = min(cv.W - 1, xb)
            # Dotted fill — every 2nd pixel on x.
            for x in range(xa, xb + 1, 2):
                cv.dot(x, y, layer, color)


def _geo_ocean_score(lon: float, lat: float) -> int:
    """Higher = better sea for the legend (Atlantic / Pacific / Southern Ocean)."""
    # Normalize lon to [-180, 180]
    while lon > 180:
        lon -= 360
    while lon < -180:
        lon += 360
    score = 0
    # Atlantic basin west of Europe / Africa
    if -70.0 <= lon <= -5.0 and -50.0 <= lat <= 65.0:
        score += 8
    # Pacific
    if lon <= -140.0 or lon >= 150.0:
        score += 6
    # Southern Ocean
    if lat <= -40.0:
        score += 4
    # Mid-ocean bonus: far from typical land masses
    if abs(lon) > 20 and not ( -10 <= lon <= 60 and 30 <= lat <= 70):
        score += 1
    return score


def _radar_rings(cv: "_Canvas", ox: float, oy: float, radii: list[float],
                 color: str | None = None) -> None:
    """DEFCON radar rings in canvas pixels (same trick as Y-theater origin rings)."""
    for rad in radii:
        if rad < 2:
            continue
        step = max(2, int(3 * 360 / (2 * math.pi * max(rad, 1))))
        for k in range(0, 360, step):
            a = math.radians(k)
            cv.dot(round(ox + rad * math.cos(a)),
                   round(oy + rad * math.sin(a)),
                   "ring", color)


def _ocean_legend(cv: "_Canvas",
                  lines: list[tuple[str, str] | tuple[str, str, str | None]], *,
                  lon_c: float, lat_c: float, scale: float) -> None:
    """Paint group/Seshat summary in the corner with the most ocean.

    lines: (text, style) or (text, style, click_action).
    """
    if not lines:
        return
    width = min(22, max(12, max((len(row[0]) for row in lines), default=12) + 1))
    width = min(width, max(8, cv.cols // 3))
    height = min(len(lines), max(1, cv.rows - 2))
    if width > cv.cols or height > cv.rows:
        return

    def cell_ll(cx: int, cy: int) -> tuple[float, float]:
        lon = lon_c + ((cx * 2 + 1) - cv.W / 2) / max(scale, 1e-9)
        lat = lat_c - ((cy * 4 + 2) - cv.H / 2) / max(scale, 1e-9)
        return lon, lat

    def legend_fit(x0: int, y0: int) -> tuple[int, int] | None:
        """Return (ocean, -blocked) for the legend rect, or None if too land-heavy."""
        ocean = blocked = 0
        for dy in range(height):
            for dx in range(width):
                if _cell_is_ocean(cv, x0 + dx, y0 + dy):
                    ocean += 1
                else:
                    blocked += 1
        if blocked > max(2, width // 2):
            return None
        return (ocean, -blocked)

    # Score each corner by ocean in its quadrant (where the sea is),
    # then place the legend flush in the winning corner.
    qx = max(width, cv.cols // 3)
    qy = max(height, cv.rows // 3)
    corners = (
        # name, legend origin, quadrant x0/x1/y0/y1
        (0, 0, 0, qx, 0, qy),                                          # NW
        (cv.cols - width, 0, cv.cols - qx, cv.cols, 0, qy),            # NE
        (0, cv.rows - height, 0, qx, cv.rows - qy, cv.rows),           # SW
        (cv.cols - width, cv.rows - height,
         cv.cols - qx, cv.cols, cv.rows - qy, cv.rows),                # SE
    )
    best_pos: tuple[int, int] | None = None
    best_score: tuple[int, int, int, int] | None = None
    for lx, ly, x0, x1, y0, y1 in corners:
        lx = max(0, min(lx, cv.cols - width))
        ly = max(0, min(ly, cv.rows - height))
        fit = legend_fit(lx, ly)
        if fit is None:
            continue
        # Weight open cells by geography so real sea beats empty map padding.
        mass = ocean_q = 0
        for cy in range(y0, y1):
            for cx in range(x0, x1):
                if _cell_is_ocean(cv, cx, cy):
                    ocean_q += 1
                    lon, lat = cell_ll(cx, cy)
                    mass += 1 + _geo_ocean_score(lon, lat)
        score = (mass, ocean_q, fit[0], fit[1])
        if best_score is None or score > best_score:
            best_score = score
            best_pos = (lx, ly)

    # Fallback: densest ocean pocket anywhere if every corner is land-heavy.
    if best_pos is None:
        for y0 in range(0, cv.rows - height + 1):
            for x0 in range(0, cv.cols - width + 1):
                fit = legend_fit(x0, y0)
                if fit is None:
                    continue
                sc = (fit[0], fit[1], 0, 0)
                if best_score is None or sc > best_score:
                    best_score = sc
                    best_pos = (x0, y0)

    if best_pos is None:
        x0, y0 = 0, 0
    else:
        x0, y0 = best_pos

    for dy in range(height):
        row = lines[dy]
        text, style = row[0], row[1]
        action = row[2] if len(row) > 2 else None
        body = (" " + text)[: width - 1].ljust(width - 1)
        for dx, ch in enumerate(body):
            cx, cy = x0 + dx, y0 + dy
            if 0 <= cx < cv.cols and 0 <= cy < cv.rows:
                sty = f"{style} on {BG}" if ch != " " else f"#5A5361 on {BG}"
                cv.over[(cx, cy)] = (ch if ch != " " else " ", sty)
                cv.taken.add((cx, cy))
                if action and ch != " ":
                    cv.hot[(cx, cy)] = action


def _focus_radii(px_xy: list[tuple[float, float]], *, scale: float,
                 rank: float = -1.0) -> tuple[float, float, list[float]]:
    """Centroid + DEFCON ring radii (canvas px) from cluster spread / rank."""
    ox = sum(x for x, _ in px_xy) / len(px_xy)
    oy = sum(y for _, y in px_xy) / len(px_xy)
    spread = max((math.hypot(x - ox, y - oy) for x, y in px_xy), default=0.0)
    # Floor ~3–5° so a single site still gets a readable radar.
    floor = max(10.0, 3.5 * scale)
    # Higher f3 percentile → slightly wider rings (data, not decoration).
    boost = 1.0 + max(0.0, min(100.0, rank)) / 200.0  # 1.00 … 1.50
    outer = max(spread * 1.2, floor) * boost
    return ox, oy, [outer * 0.35, outer * 0.65, outer]


def _freq_color(pct: float) -> str:
    """Warm scale for UYSD modern frequency discs."""
    if pct >= 60:
        return "#E07A3A"
    if pct >= 35:
        return "#D4AD72"
    if pct >= 15:
        return "#A89070"
    return "#71697A"


def _paint_freq_heat(cv: "_Canvas", discs: list[dict], *,
                     lon_c: float, lat_c: float, scale: float,
                     focus_id: str | None = None) -> list[tuple[str, str, str | None]]:
    """UYSD frequency discs — under genotype points."""
    legend: list[tuple[str, str, str | None]] = []
    px = lambda lon, lat: ((lon - lon_c) * scale + cv.W / 2, (lat_c - lat) * scale + cv.H / 2)
    ranked = sorted(discs, key=lambda d: -float(d.get("pct") or 0))
    for d in ranked:
        lon, lat = d.get("lon"), d.get("lat")
        if lon is None or lat is None:
            continue
        pct = float(d.get("pct") or 0)
        color = _freq_color(pct)
        x, y = px(float(lon), float(lat))
        cv.dot(round(x), round(y), "freq", color)
        if pct >= 40:
            cv.dot(round(x) + 1, round(y), "freq", color)
        lab = (d.get("label") or d.get("id") or "%").strip()
        did = str(d.get("id") or "")
        mark = "◆" if focus_id and (did == focus_id or lab == focus_id) else "%"
        key = f"u:{lab}"
        legend.append((f"{mark}{pct:4.0f}% {lab}"[:20], color,
                       f"screen.map_focus({key!r})"))
    return legend[:10]


def _paint_kin_edges(cv: "_Canvas", edges: list[dict], *,
                     lon_c: float, lat_c: float, scale: float) -> int:
    """ancIBD segments as faint lines between AADR points."""
    px = lambda lon, lat: ((lon - lon_c) * scale + cv.W / 2, (lat_c - lat) * scale + cv.H / 2)
    n = 0
    for e in edges:
        try:
            x0, y0 = px(float(e["lon1"]), float(e["lat1"]))
            x1, y1 = px(float(e["lon2"]), float(e["lat2"]))
        except (KeyError, TypeError, ValueError):
            continue
        cm = float(e.get("cm") or 0)
        color = "#53A94B" if cm >= 50 else "#7A9E6A" if cm >= 20 else "#5A7A58"
        cv.line(x0, y0, x1, y1, "kin", color)
        n += 1
    return n


def scatter_map(doc: Any, points: list[dict], title: str, *, world: bool = True,
                cols: int = 96, max_rows: int = 28, selected: str | None = None,
                selected_blink: bool = True,
                culture_labels: bool = False,
                anchors: list[dict] | None = None,
                polities: list[dict] | None = None,
                show_habitat: bool = False,
                show_polity: bool = False,
                freq_discs: list[dict] | None = None,
                kin_edges: list[dict] | None = None,
                show_freq: bool = False,
                show_kin: bool = False,
                show_amtdb: bool = False,
                map_focus: str | None = None) -> None:
    """Точки на брайлевской карте. Подписи культур/Seshat/Cliopatria — в океане.

    culture_labels: ocean legend of cultures (count · name), not over land/points.
    show_habitat: draw culture convex-hull ranges (≥3 sites); outline only.
      Focus ``c:…`` still draws that one hull when the overlay is off.
    show_polity: draw Cliopatria outlines; focus ``p:…`` draws one when off.
    show_freq: UYSD modern Y frequency discs.
    show_kin: ancIBD kinship edges between visible / selected samples.
    show_amtdb: AmtDB ancient mt anchors (via anchors kind=amtdb).
    anchors: Seshat / AmtDB names in the same ocean summary (not labeled on the map).
    polities: Cliopatria rings for the epoch year (used when show_polity / focus).
    selected_blink: when False, hide ◆ / triangle highlight (pulse off-phase).
    map_focus: 'c:Culture' / 's:Seshat' / 'p:Polity' / 'u:Region' / 'm:AmtDB' / 'k:gid'.
    """
    cols = max(30, cols)
    pts = [(float(p["lon"]), float(p["lat"]), p) for p in points
           if p.get("lat") is not None and p.get("lon") is not None]
    # Anchors only affect legend / optional extent — not drawn as map text.
    anchor_pts = [(float(a["lon"]), float(a["lat"]), a) for a in (anchors or [])
                  if a.get("lat") is not None and a.get("lon") is not None]
    extent = pts  # map framing from genotype points only
    if world or not extent:
        lon0, lon1, lat0, lat1 = -180.0, 180.0, -58.0, 84.0
    else:
        lon0 = min(p[0] for p in extent) - 8
        lon1 = max(p[0] for p in extent) + 8
        lat0 = min(p[1] for p in extent) - 6
        lat1 = max(p[1] for p in extent) + 6
        if lon1 - lon0 < 40:
            m = (lon0 + lon1) / 2
            lon0, lon1 = m - 20, m + 20
        if lat1 - lat0 < 24:
            m = (lat0 + lat1) / 2
            lat0, lat1 = m - 12, m + 12
    lon1 = min(lon1, lon0 + 360)
    lon_c, lat_c = (lon0 + lon1) / 2, (lat0 + lat1) / 2
    W = cols * 2
    scale = min(W / max(1e-6, lon1 - lon0), (max_rows * 4) / max(1e-6, lat1 - lat0))
    rows = max(12, min(max_rows, math.ceil((lat1 - lat0) * scale / 4)))
    cv = _Canvas(cols, rows)
    px = lambda lon, lat: ((lon - lon_c) * scale + cv.W / 2, (lat_c - lat) * scale + cv.H / 2)
    cell = lambda lon, lat: (int(px(lon, lat)[0]) >> 1, int(px(lon, lat)[1]) >> 2)

    vis_lon0, vis_lon1 = lon_c - cv.W / 2 / scale, lon_c + cv.W / 2 / scale
    vis_lat0, vis_lat1 = lat_c - cv.H / 2 / scale, lat_c + cv.H / 2 / scale
    gstep = 10 if scale >= 1.2 else 30
    astep = 20 if scale >= 1.2 else 60
    _paint_base(cv, lon_c=lon_c, lat_c=lat_c, scale=scale, gstep=gstep)

    focus_cult = map_focus[2:] if map_focus and map_focus.startswith("c:") else None
    focus_sesh = map_focus[2:] if map_focus and map_focus.startswith("s:") else None
    focus_pol = map_focus[2:] if map_focus and map_focus.startswith("p:") else None
    focus_uysd = map_focus[2:] if map_focus and map_focus.startswith("u:") else None
    focus_amt = map_focus[2:] if map_focus and map_focus.startswith("m:") else None
    focus_kin = map_focus[2:] if map_focus and map_focus.startswith("k:") else None

    # Habitation hulls under points (overlay on, or single focused culture).
    hulls: list[dict] = []
    habitat_cults: set[str] = set()
    if culture_labels and (show_habitat or focus_cult):
        only = None if show_habitat else (
            frozenset({focus_cult}) if focus_cult else None)
        hulls = culture_habitat_hulls(
            pts, limit=max(6, min(12, rows // 2)), only_cultures=only)
        habitat_cults = _paint_habitats(
            cv, hulls, lon_c=lon_c, lat_c=lat_c, scale=scale,
            focus_cult=focus_cult, fill=False)

    # Cliopatria under points (overlay on, or single focused polity).
    polity_legend: list[tuple[str, str, str | None]] = []
    pol_list = list(polities or [])
    if show_polity or focus_pol:
        draw_pols = pol_list if show_polity else [
            p for p in pol_list
            if (p.get("name") or p.get("id") or "").strip() == focus_pol
        ]
        polity_legend = _paint_polities(
            cv, draw_pols, lon_c=lon_c, lat_c=lat_c, scale=scale,
            focus_name=focus_pol, fill=False)

    freq_legend: list[tuple[str, str, str | None]] = []
    n_kin = 0
    if show_freq and freq_discs:
        freq_legend = _paint_freq_heat(
            cv, freq_discs, lon_c=lon_c, lat_c=lat_c, scale=scale,
            focus_id=focus_uysd)
    if show_kin and kin_edges:
        n_kin = _paint_kin_edges(
            cv, kin_edges, lon_c=lon_c, lat_c=lat_c, scale=scale)

    selected_cell = None
    # culture → (count, best_rank, color)
    cult_sum: dict[str, tuple[int, float, str]] = {}
    focus_xy: list[tuple[float, float]] = []
    focus_rank = -1.0
    focus_color = COL["ring"]

    # Radar first (under points) — same layer order as Y-theater.
    if focus_cult:
        for lon, lat, p in pts:
            if (p.get("culture") or "").strip() != focus_cult:
                continue
            focus_xy.append(px(lon, lat))
            rnk = float(p.get("rank") if p.get("rank") is not None else -1.0)
            if rnk >= focus_rank:
                focus_rank = rnk
                focus_color = p.get("color") or focus_color
    elif focus_sesh:
        for lon, lat, a in anchor_pts:
            lab = (a.get("label") or a.get("id") or "").strip()
            aid = str(a.get("id") or "").strip()
            if lab != focus_sesh and aid != focus_sesh:
                continue
            focus_xy.append(px(lon, lat))
            focus_rank = float(a.get("rank") if a.get("rank") is not None else 50.0)
            focus_color = a.get("color") or "#AEA5B8"
    elif focus_pol:
        for pol in (polities or []):
            name = (pol.get("name") or pol.get("id") or "").strip()
            if name != focus_pol:
                continue
            lon, lat = pol.get("lon"), pol.get("lat")
            if lon is None or lat is None:
                continue
            focus_xy.append(px(float(lon), float(lat)))
            focus_rank = float(pol.get("rank") if pol.get("rank") is not None else 70.0)
            focus_color = pol.get("color") or COL["polity"]
    elif focus_uysd:
        for d in (freq_discs or []):
            lab = (d.get("label") or d.get("id") or "").strip()
            if lab != focus_uysd and str(d.get("id") or "") != focus_uysd:
                continue
            lon, lat = d.get("lon"), d.get("lat")
            if lon is None or lat is None:
                continue
            focus_xy.append(px(float(lon), float(lat)))
            focus_rank = float(d.get("pct") or 40.0)
            focus_color = _freq_color(float(d.get("pct") or 0))
    elif focus_amt:
        for lon, lat, a in anchor_pts:
            lab = (a.get("label") or a.get("id") or "").strip()
            aid = str(a.get("id") or "").strip()
            if lab != focus_amt and aid != focus_amt and not lab.startswith(focus_amt):
                continue
            focus_xy.append(px(lon, lat))
            focus_rank = 55.0
            focus_color = a.get("color") or "#B04A4A"
    elif focus_kin:
        for lon, lat, p in pts:
            if str(p.get("id") or "") != focus_kin:
                continue
            focus_xy.append(px(lon, lat))
            focus_rank = 60.0
            focus_color = "#53A94B"
    focus_hub: tuple[float, float] | None = None
    if focus_xy:
        ox, oy, radii = _focus_radii(focus_xy, scale=scale, rank=focus_rank)
        focus_hub = (ox, oy)
        _radar_rings(cv, ox, oy, radii, focus_color)

    for lon, lat, p in pts:
        cx, cy = cell(lon, lat)
        color = p.get("color") or COL["coast"]
        glyph = p.get("glyph")
        is_sel = bool(selected) and (p.get("id") == selected or p.get("label") == selected)
        if is_sel:
            selected_cell = (cx, cy, p)
        cult = (p.get("culture") or "").strip()
        in_focus = bool(focus_cult and cult == focus_cult)
        if glyph:
            cv.mark(cx, cy, _SMALL_GLYPH.get(glyph, glyph), color)
        else:
            cv.dot(*map(round, px(lon, lat)), "route", color)
        if cult and culture_labels:
            rank = float(p.get("rank") if p.get("rank") is not None else -1.0)
            prev = cult_sum.get(cult)
            if prev is None:
                cult_sum[cult] = (1, rank, str(color))
            else:
                n, pr, pc = prev
                cult_sum[cult] = (n + 1, max(pr, rank), pc if pr >= rank else str(color))
        # Focused culture sites get a steady ◆ (selection still blinks separately).
        if in_focus and not is_sel:
            cv.mark(cx, cy, "◆", f"bold {color}")

    # Selection pulse: ◆ / big ▲ only while blink is on (off-phase = plain point).
    if selected_cell and selected_blink:
        cx, cy, p = selected_cell
        if p.get("glyph"):
            _big_triangle(cv, cx, cy, p.get("color") or "#5882C7")
        else:
            cv.mark(cx, cy, "◆", f"bold {p.get('color') or '#FBF6F0'}")
        # No text label on the map — selection is ◆ only; name lives in the corner.

    # Centroid hub for the active radar.
    if focus_hub is not None:
        ox, oy = focus_hub
        cv.mark(int(ox) >> 1, int(oy) >> 2, "◉", f"bold {focus_color}")

    legend: list[tuple[str, str] | tuple[str, str, str | None]] = []
    if culture_labels and cult_sum:
        ranked = sorted(cult_sum.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))
        sel_cult = focus_cult or ""
        if not sel_cult and selected_cell:
            sel_cult = (selected_cell[2].get("culture")
                        or selected_cell[2].get("label") or "")
        budget = max(6, min(12, rows - 3))
        if show_habitat and habitat_cults:
            legend.append(("⌂ ranges", "#8A8294", None))
        for cult, (n, _rank, color) in ranked[:budget]:
            if cult and cult == sel_cult:
                mark = "◆"
            elif show_habitat and cult in habitat_cults:
                mark = "⌂"
            else:
                mark = "·"
            key = f"c:{cult}"
            legend.append((
                f"{mark}{n:>3} {cult}"[:20],
                f"bold {color}",
                f"screen.map_focus({key!r})",
            ))
    if culture_labels and show_polity and polity_legend:
        if legend:
            legend.append(("¶ Cliopatria", "#8A8294", None))
        legend.extend(polity_legend)
    if culture_labels and anchor_pts:
        seshat_a = [t for t in anchor_pts if t[2].get("kind") != "amtdb"]
        amtdb_a = [t for t in anchor_pts if t[2].get("kind") == "amtdb"]
        if seshat_a:
            if legend:
                legend.append(("§ Seshat", "#8A8294", None))
            seshat_budget = max(2, min(5, rows // 4))
            ranked_a = sorted(
                seshat_a,
                key=lambda t: -float(t[2].get("rank") if t[2].get("rank") is not None else 0),
            )
            for _lon, _lat, a in ranked_a[:seshat_budget]:
                lab = (a.get("label") or a.get("id") or "§").strip()
                key = f"s:{lab}"
                mark = "◆" if focus_sesh and (lab == focus_sesh
                                              or str(a.get("id") or "") == focus_sesh) else "§"
                legend.append((
                    f"{mark} {lab}"[:20],
                    a.get("color") or "#AEA5B8",
                    f"screen.map_focus({key!r})",
                ))
        if (show_amtdb or focus_amt) and amtdb_a:
            if legend:
                legend.append(("m AmtDB", "#8A8294", None))
            for _lon, _lat, a in amtdb_a[:6]:
                lab = (a.get("label") or a.get("id") or "m").strip()
                aid = str(a.get("id") or lab)
                key = f"m:{aid}"
                mark = "◆" if focus_amt and (aid == focus_amt or lab.startswith(focus_amt)) else "m"
                legend.append((
                    f"{mark} {lab}"[:20],
                    a.get("color") or "#B04A4A",
                    f"screen.map_focus({key!r})",
                ))
                # Mark AmtDB sites on the map itself.
                cx, cy = cell(_lon, _lat)
                cv.mark(cx, cy, "◦", a.get("color") or "#B04A4A")
    if show_freq and freq_legend:
        if legend:
            legend.append(("% UYSD", "#8A8294", None))
        legend.extend(freq_legend)
    if show_kin and n_kin:
        legend.append((f"≈ {n_kin} IBD edges", "#53A94B", None))
    if legend:
        _ocean_legend(cv, legend, lon_c=lon_c, lat_c=lat_c, scale=scale)

    total_w = cols + 6

    def pad_line(parts: list[tuple[str, str]]) -> None:
        left = total_w
        for text, fg in parts:
            if left <= 0:
                break
            text = text[:left]
            doc.t.append(text, f"{fg} on {BG}")
            left -= len(text)
        doc.t.append(" " * max(0, left), f"white on {BG}")
        doc.t.append("\n")

    head = f" {title} "
    # Right-side mode chip: what O is showing (default = sites only).
    if show_freq:
        mode = f"O: % UYSD freq"
        if freq_discs:
            mode += f" ({len(freq_discs)})"
    elif show_kin:
        mode = f"O: ≈ ancIBD kin"
        if n_kin:
            mode += f" ({n_kin})"
    elif show_amtdb:
        mode = "O: m AmtDB mt"
    elif show_habitat and show_polity:
        mode = f"O: ⌂ habitat + ¶ empires"
        if hulls or pol_list:
            mode += f" ({len(hulls)}/{len(pol_list)})"
    elif show_habitat:
        mode = f"O: ⌂ habitat ranges"
        if hulls:
            mode += f" ({len(hulls)})"
    elif show_polity:
        mode = f"O: ¶ Cliopatria empires"
        if pol_list:
            mode += f" ({len(pol_list)})"
    else:
        mode = "O: sites only"
        if focus_cult and hulls:
            mode += " · ⌂ focus"
        elif focus_pol:
            mode += " · ¶ focus"
    bits = [f"{len(pts)} pts", mode]
    meta = " · ".join(bits)
    if map_focus:
        tag = map_focus[2:] if map_focus[:2] in ("c:", "s:", "p:") else map_focus
        tail = f" ◉ {tag} · {meta} "
    else:
        tail = f" {meta} "
    pad_line([(head, "bold #5882C7"),
              (" " * max(1, total_w - len(head) - len(tail)), "#FBF6F0"),
              (tail, "#5A7CB3")])
    pad_line([("═" * total_w, "#483A59")])
    ylab = {}
    m = math.ceil(vis_lat0 / gstep) * gstep
    while m <= vis_lat1:
        r = int(px(0, m)[1]) >> 2
        if 0 <= r < rows:
            ylab[r] = f"{abs(m):>3}°{'N' if m >= 0 else 'S'} "
        m += gstep
    for r in range(rows):
        doc.t.append(ylab.get(r, " " * 6), f"#5A7CB3 on {BG}")
        run, st = "", None
        for cx in range(cols):
            if (cx, r) in cv.over:
                ch, sty = cv.over[(cx, r)]
            elif (cx, r) in cv.cells:
                layers = cv.cells[(cx, r)]
                top = next(l for l in _PRIO if l in layers)
                fg = _layer_fg(cv, cx, r, top)
                mask = 0
                for v in layers.values():
                    mask |= v
                ch, sty = chr(0x2800 + mask), f"{fg} on {BG}"
            else:
                ch, sty = " ", f"white on {BG}"
            action = cv.hot.get((cx, r))
            out = click_style(action, sty) if action else sty
            if out != st:
                if run:
                    doc.t.append(run, st)
                run, st = "", out
            run += ch
        if run:
            doc.t.append(run, st)
        doc.t.append("\n")
    axis = [" "] * cols
    m = math.ceil(vis_lon0 / astep) * astep
    while m <= vis_lon1:
        n_ = ((int(m) + 180) % 360) - 180
        lab = f"{abs(n_)}°" + ("" if abs(n_) in (0, 180) else ("E" if n_ > 0 else "W"))
        xs = max(0, min(cols - len(lab), (int(px(m, 0)[0]) >> 1) - len(lab) // 2))
        for i, ch in enumerate(lab):
            axis[xs + i] = ch
        m += astep
    pad_line([(" " * 6 + "".join(axis), "#5A7CB3")])
    pad_line([("═" * total_w, "#483A59")])