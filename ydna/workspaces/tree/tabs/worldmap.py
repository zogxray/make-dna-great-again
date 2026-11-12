"""Карта в стиле DEFCON для вкладки Map.

Береговая линия и границы: Natural Earth 110m (public domain) через world-atlas,
упрощены и вшиты в файл (_DATA). Рисуем на брайлевском холсте: 2x4 точки на символ.
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
    "eNplXQmSJDkK/JDCTOjWW8b2/99YgTtIlW3d01OVGRmHDnAch/zvP0n/1brTJznVlb4iSdI3kjR96X//S//lc8Au6Wszff28WHDA"
    "SF89h6dvp5y+lc5nZSR9ST90ztpy1VNUffMc/8n5WNOfsn3S3jz/2RnPL+fswk/WZod3PUiqfmTpZfzEteivxe51+2v5fH5VvcNz"
    "OtxeOf9I6vqDH1b2OaT182JO5+p6S+cSOU6iB7dzN3LeH3q7I95q9gTtvFXPu3z5WzOVOe2R/rzU9CV7gqqXOqft8bxF77Lw7e3D"
    "waH+ZEoqem+p+Rll5vNSS0WHXgej6vToX308G75zV+f1ff6v83B+0YfP9hS8Wzt3OUPX9rni+b/N8PlVR2vb8d8ZbBuuZZOij9Xj"
    "HnRwelsJg6cHzzTtX1n2LFtPxKfk5A57Q0dZV5f9q392ixtq56WxZzpz8un9Nz6Onkf/Pb/ropo6F2dck97gtPeGLgnRV0U/ej5W"
    "kk1d8bfPgOls24MWm25bTXrqZgtFl/uZq/O6Pj9u2IZUb/TM/fnPtsPi5/H2+WM74SyDgkVmn7YLZr0pnaGlY2efEbHBKrZ09OM6"
    "sHofYm93zIPoB/WXrafMdgeiU6qPM21Apx1v/+rIb5yu2AfwXGIPZ7uNexX/buyuht2mq+Orvpc/u5XyHrFsn0psWh60uICxjZdv"
    "XUzVxkpoOhLbBj1jynSp282fcbYz6UexFKadTDDcy6ZW7I71pakrV2fgvKQ/26RPfX1zceq17fBtm1jXRbWrNJuQYkOKBbBs2eHX"
    "8zEbsmnT2fUyZgj0WrXYhtXnmxxTW9Vixs6mwR7dHqXZZTmIDQOImRKzivabDl+xddJ9bCeGUxelGimdZFs+58Wia013TLLF8G1a"
    "sjOknOGJhSpqoL6JffHB4n169zZDhdcRzIyYcbbx9908jpFpXTDM2ZZgoxHWIUh+BrvZ4q8LLBVs24id3uwGM1dcxb3AhHCEdFtm"
    "m1Z7BP2gPmSxyeFYYscneT7zVeyOjVHtCauwcH9+Nho4vCTuv4yRneNaPX9Y3j72gF7PfAS2m3kuNTcZl67PA5ftK96WWfE3G7el"
    "XrTQp1W38Bg7GmO1FsnNfuE9YoQK9khzs2tmy25N58/WU6xc+6fGiTOvrwd3jn/zmxbB5OXYwzZYM8FYZ14UCwl+8INLF5oSdXZY"
    "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"
)

BG = MAP["bg"]
COL = {k: MAP[k] for k in ("coast", "border", "ring", "grid", "route")}
_BITS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))
_PRIO = ("route", "coast", "border", "ring", "grid")
_cache: list | None = None
# Framing → (cells, color) for grid+coast+border. Filters/blink reuse the same box.
_BASE_CACHE: dict[tuple, tuple[dict, dict]] = {}
_BASE_CACHE_MAX = 8


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


def _paint_base(cv: "_Canvas", *, lon_c: float, lat_c: float, scale: float,
                gstep: int) -> None:
    """Grid + Natural Earth coast/borders. Cached by map framing."""
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
                fg = cv.color.get((cx, r), COL["route"]) if top == "route" else COL[top]
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
    """True for empty/grid sea — no coast, border, or sample marks."""
    if not (0 <= cx < cv.cols and 0 <= cy < cv.rows):
        return False
    if (cx, cy) in cv.over or (cx, cy) in cv.taken:
        return False
    layers = cv.cells.get((cx, cy))
    if not layers:
        return True
    return set(layers).issubset({"grid"})


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


def scatter_map(doc: Any, points: list[dict], title: str, *, world: bool = True,
                cols: int = 96, max_rows: int = 28, selected: str | None = None,
                selected_blink: bool = True,
                culture_labels: bool = False,
                anchors: list[dict] | None = None,
                map_focus: str | None = None) -> None:
    """Точки на брайлевской карте. Подписи культур/Seshat — сводка в океане.

    culture_labels: ocean legend of cultures (count · name), not over land/points.
    anchors: Seshat names in the same ocean summary (not labeled on the map).
    selected_blink: when False, hide ◆ / triangle highlight (pulse off-phase).
    map_focus: 'c:Culture' or 's:SeshatLabel' — DEFCON rings around that cluster.
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

    selected_cell = None
    # culture → (count, best_rank, color)
    cult_sum: dict[str, tuple[int, float, str]] = {}
    focus_cult = map_focus[2:] if map_focus and map_focus.startswith("c:") else None
    focus_sesh = map_focus[2:] if map_focus and map_focus.startswith("s:") else None
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
        for cult, (n, _rank, color) in ranked[:budget]:
            mark = "◆" if cult and cult == sel_cult else "·"
            key = f"c:{cult}"
            legend.append((
                f"{mark}{n:>3} {cult}"[:20],
                f"bold {color}",
                f"screen.map_focus({key!r})",
            ))
    if culture_labels and anchor_pts:
        if legend:
            legend.append(("§ Seshat", "#8A8294", None))
        seshat_budget = max(2, min(5, rows // 4))
        ranked_a = sorted(
            anchor_pts,
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
    if map_focus:
        tag = map_focus[2:] if map_focus[:2] in ("c:", "s:") else map_focus
        tail = f" ◉ {tag} · {len(pts)} pts "
    else:
        tail = f" {len(pts)} pts "
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
                fg = cv.color.get((cx, r), COL["route"]) if top == "route" else COL[top]
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