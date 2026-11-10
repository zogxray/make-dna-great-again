"""Слой данных F3: скелет = FTDNA haplotree, Discover JSON — обогащение. Без UI."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from ...core.ylink import Haplotree, progress_markers

COUNTRY_CODES = {
    "argentina": "AR", "armenia": "AM", "austria": "AT", "azerbaijan": "AZ",
    "belarus": "BY", "belgium": "BE", "bosnia and herzegovina": "BA",
    "bulgaria": "BG", "china": "CN", "croatia": "HR", "czech republic": "CZ",
    "czechia": "CZ", "denmark": "DK", "estonia": "EE", "finland": "FI",
    "france": "FR", "georgia": "GE", "germany": "DE", "greece": "GR",
    "hungary": "HU", "india": "IN", "iran": "IR", "ireland": "IE",
    "italy": "IT", "latvia": "LV", "lithuania": "LT", "netherlands": "NL",
    "norway": "NO", "pakistan": "PK", "poland": "PL", "portugal": "PT",
    "romania": "RO", "russia": "RU", "serbia": "RS", "slovakia": "SK",
    "slovenia": "SI", "spain": "ES", "sweden": "SE", "switzerland": "CH",
    "turkey": "TR", "türkiye": "TR", "ukraine": "UA", "united kingdom": "GB",
    "england": "GB", "scotland": "GB", "wales": "GB", "united states": "US",
    "usa": "US", "canada": "CA", "mexico": "MX", "kazakhstan": "KZ",
    "mongolia": "MN", "japan": "JP", "south korea": "KR", "korea": "KR",
    "cyprus": "CY", "israel": "IL", "lebanon": "LB", "syria": "SY",
    "iraq": "IQ", "afghanistan": "AF", "nepal": "NP", "bangladesh": "BD",
    "morocco": "MA", "algeria": "DZ", "tunisia": "TN", "egypt": "EG",
    "libya": "LY", "ethiopia": "ET", "sudan": "SD", "south africa": "ZA",
    "kenya": "KE", "nigeria": "NG", "iceland": "IS", "luxembourg": "LU",
    "montenegro": "ME", "north macedonia": "MK", "moldova": "MD",
    "bosnia": "BA", "kosovo": "XK", "albania": "AL",
    "saudi arabia": "SA", "united arab emirates": "AE", "uae": "AE",
    "qatar": "QA", "kuwait": "KW", "bahrain": "BH", "oman": "OM",
    "yemen": "YE", "jordan": "JO", "palestine": "PS", "australia": "AU",
    "new zealand": "NZ", "brazil": "BR", "chile": "CL", "colombia": "CO",
    "peru": "PE", "venezuela": "VE", "uruguay": "UY", "paraguay": "PY",
    "bolivia": "BO", "ecuador": "EC", "cuba": "CU", "jamaica": "JM",
    "philippines": "PH", "indonesia": "ID", "malaysia": "MY",
    "singapore": "SG", "thailand": "TH", "vietnam": "VN", "taiwan": "TW",
    "hong kong": "HK", "macau": "MO", "sri lanka": "LK", "myanmar": "MM",
    "cambodia": "KH", "laos": "LA", "malta": "MT", "andorra": "AD",
    "liechtenstein": "LI", "monaco": "MC", "san marino": "SM",
    "vatican": "VA", "vatican city": "VA", "greenland": "GL",
    "faroe islands": "FO", "gibraltar": "GI", "isle of man": "IM",
    "jersey": "JE", "guernsey": "GG", "northern ireland": "GB",
    "puerto rico": "PR", "dominican republic": "DO", "panama": "PA",
    "costa rica": "CR", "guatemala": "GT", "honduras": "HN",
    "el salvador": "SV", "nicaragua": "NI", "belize": "BZ",
    "trinidad and tobago": "TT", "barbados": "BB", "bahamas": "BS",
    "ghana": "GH", "uganda": "UG", "tanzania": "TZ", "cameroon": "CM",
    "senegal": "SN", "ivory coast": "CI", "côte d'ivoire": "CI",
    "zimbabwe": "ZW", "botswana": "BW", "namibia": "NA",
    "madagascar": "MG", "mauritius": "MU", "rwanda": "RW",
}

UK_PARTS = {"ENG", "SCT", "WLS", "NIR"}
TAG_RE = re.compile(r"<[^>]+>")


# ───────────────────────────── данные ─────────────────────────────

def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def strip_html(s: Any) -> str:
    s = re.sub(r"</p>|<br\s*/?>", "\n", str(s), flags=re.I)
    return html.unescape(TAG_RE.sub("", s)).strip()


def clean(value: Any) -> Any:
    """Убирает HTML-теги из строк; остальное возвращает как есть."""
    if isinstance(value, str) and "<" in value and ">" in value:
        return strip_html(value)
    return value


def pick(d: dict, *keys: str) -> Any:
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


def as_int(value: Any) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def year_label(value: Any) -> str:
    year = as_int(value)
    if year is None:
        return "date unknown"
    if year < 0:
        return f"{abs(year):,} BCE"
    if year == 0:
        return "c. 1 BCE"
    return f"{year:,} CE"


def date_info(resource: dict[str, Any]) -> dict[str, Any]:
    formed = (resource.get("time") or {}).get("formed") or {}
    if not isinstance(formed, dict):
        formed = {}
    mean = as_int(formed.get("mean"))
    oldest = as_int(formed.get("oldest"))
    youngest = as_int(formed.get("youngest"))
    range_label = None
    if oldest is not None and youngest is not None:
        low, high = sorted((oldest, youngest))
        range_label = f"{year_label(low)} – {year_label(high)}"
    return {"mean": mean, "label": year_label(mean), "range_label": range_label}


def country_code(country: str) -> str | None:
    code = COUNTRY_CODES.get(country.strip().lower())
    return code.upper() if code and len(code) == 2 and code.isalpha() else None


def get_geography(resource: dict[str, Any]) -> tuple[list[str], list[str], int, int]:
    """Страны берутся только из countries[]; коды как есть (ENG, IT, X).

    Образцы древней ДНК в список стран не подмешиваются: они лишь считаются."""
    names: set[str] = set()
    codes: set[str] = set()
    for item in resource.get("countries") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        code = str(item.get("code") or "").split("-")[0].upper()
        if name:
            names.add(name)
        if code:
            codes.add(code)

    placed = uncertain = 0
    for s in _list(resource.get("ancient")):
        if isinstance(s, dict) and (s.get("country_name") or s.get("latitude") is not None):
            if s.get("uncertain_placement") is True:
                uncertain += 1
            else:
                placed += 1
    return sorted(names), sorted(codes), placed, uncertain


def load_resource(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except Exception as exc:
        return None, str(exc)
    if not isinstance(value, dict):
        return None, "top-level JSON value is not an object"
    return value, None


def year_class(year: int | None) -> str:
    if year is None:
        return "unknown"
    if year < -4000:
        return "deep"
    if year < -2000:
        return "purple"
    if year < 0:
        return "blue"
    if year <= 1000:
        return "cyan"
    return "green"


def date_class(node: dict[str, Any]) -> str:
    return year_class(node.get("date_mean"))


STAGES = [
    ("Building haplotree skeleton", 25),
    ("Linking parents", 5),
    ("Reading Discover JSON", 40),
    ("Sorting branches", 5),
    ("Counting descendants", 10),
    ("Reading SNP markers", 5),
    ("Building search index", 10),
]
CACHE_VERSION = 10

# Общие пустые контейнеры для скелета: pickle хранит одну копию; Discover подменяет целиком.
_EL: list = []
_ED: dict = {}


def quantile(sorted_vals: list[int], p: float) -> int:
    if not sorted_vals:
        return 0
    return sorted_vals[min(len(sorted_vals) - 1, int(p * len(sorted_vals)))]


def haplotree_stub(name: str, parent: str | None) -> dict[str, Any]:
    """Скелет узла с haplotree (без Discover-карточки)."""
    return {
        "name": name, "parent": parent, "root": None, "hg_id": None,
        "formed": _ED, "tmrca": _ED,
        "date": "from haplotree", "date_mean": None, "date_range": None,
        "countries": _EL, "flags": _EL, "placed_samples": 0, "uncertain_samples": 0,
        # markers — свой список: progress.csv делает append до замены в finalize.
        "markers": [], "variants": _EL, "ancient": _EL, "countries_data": _EL,
        "geochart_countries": _EL, "google_countries": _ED,
        "ancestors": _EL, "upgrades": _ED, "famous": _EL,
        "projects": _EL, "badges": _EL, "globetrekker": _ED, "migrationmap": _ED,
        "placements": None, "declared_children": _EL, "tree_version": None,
        "tree_start": None, "tree": _ED,
        "source_file": None, "placeholder": True, "haplotree": True,
        "rich": "normal", "crown": False, "deep": False,
        "children_count": 0, "descendants_count": 0, "height": 0,
        "rank": None, "branch_date": None,
    }


def _snp_names_by_node(tree: Haplotree) -> list[list[str]]:
    """Имена SNP haplotree по узлу — только для поискового индекса скелета."""
    out: list[list[str]] = [[] for _ in range(len(tree))]
    for vname, (ni, _pos, _anc, _der) in tree.variants.items():
        out[ni].append(vname)
    return out


def _apply_discover(node: dict[str, Any], data: dict[str, Any], source_file: str) -> None:
    """Обогащает скелет (или создаёт узел) полями Discover. Топология parent не трогается, если уже есть."""
    hg = data.get("haplogroup") or {}
    if not isinstance(hg, dict):
        hg = {}
    parent = hg.get("parent_name")
    countries, flags, placed, uncertain = get_geography(data)
    dates = date_info(data)
    desc = _dict(data.get("descendants"))
    if not node.get("parent") and parent:
        node["parent"] = str(parent)
    if hg.get("root") is not None:
        node["root"] = hg.get("root")
    if hg.get("id") is not None:
        node["hg_id"] = hg.get("id")
    node["formed"] = _dict((data.get("time") or {}).get("formed"))
    node["tmrca"] = _dict((data.get("time") or {}).get("tmrca"))
    node["date"] = dates["label"]
    node["date_mean"] = dates["mean"]
    node["date_range"] = dates["range_label"]
    node["countries"] = countries
    node["flags"] = flags
    node["placed_samples"] = placed
    node["uncertain_samples"] = uncertain
    node["variants"] = _list(data.get("variants"))
    node["ancient"] = _list(data.get("ancient"))
    node["countries_data"] = _list(data.get("countries"))
    node["geochart_countries"] = _list(data.get("geochart_countries"))
    # Мировая база FTDNA (country → test count); не география клады — вкладка Geography · W.
    raw_world = data.get("google_countries")
    if isinstance(raw_world, dict):
        world: dict[str, int] = {}
        for k, v in raw_world.items():
            try:
                world[str(k)] = int(v)
            except (TypeError, ValueError):
                continue
        node["google_countries"] = world
    else:
        node["google_countries"] = {}
    node["ancestors"] = _list(data.get("ancestors"))
    node["upgrades"] = _dict(data.get("upgrades"))
    node["famous"] = _list(data.get("famous"))
    node["projects"] = _list(data.get("projects"))
    node["badges"] = _list(data.get("badges"))
    node["globetrekker"] = _dict(data.get("globetrekker"))
    node["migrationmap"] = _dict(data.get("migrationmap"))
    node["placements"] = desc.get("placements")
    node["declared_children"] = [
        c.get("name") if isinstance(c, dict) else str(c) for c in _list(desc.get("children"))
    ]
    # Discover JSON: tree.version_major/minor/micro + start (не tree.version).
    tree = _dict(data.get("tree"))
    node["tree"] = tree
    parts = [tree.get(k) for k in ("version_major", "version_minor", "version_micro")]
    if any(p is not None for p in parts):
        node["tree_version"] = ".".join(str(p) for p in parts if p is not None)
    elif tree.get("version") is not None:
        node["tree_version"] = str(tree.get("version"))
    else:
        node["tree_version"] = None
    node["tree_start"] = tree.get("start")
    node["source_file"] = source_file
    node["placeholder"] = False


def _link_topology(nodes: dict[str, Any]) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """Строит children/roots; возвращает также имена отсутствующих родителей (для issues)."""
    children: dict[str, list[str]] = defaultdict(list)
    roots: list[str] = []
    missing: list[str] = []
    for name, node in nodes.items():
        parent = node.get("parent")
        if not parent or parent == name:
            roots.append(name)
        elif parent in nodes:
            children[parent].append(name)
        else:
            missing.append(parent)
            roots.append(name)
    return dict(children), roots, sorted(set(missing))


def _finalize(nodes: dict[str, Any], children: dict[str, list[str]], roots: list[str],
              issues: dict[str, list], discover_files: int, published: str,
              progress_csv: Path | None, progress=None,
              snp_extra: dict[str, list[str]] | None = None) -> dict[str, Any]:
    def report(stage: str, done: int = 0, total: int = 1) -> None:
        if progress:
            progress(stage, done, total)

    report(STAGES[3][0], 0, 1)

    def sort_key(name: str) -> tuple:
        year = nodes[name].get("date_mean")
        return (year is None, year if year is not None else 0, name.lower())

    for values in children.values():
        values.sort(key=sort_key)

    order: list[str] = []
    stack = list(roots)
    seen: set[str] = set()
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        order.append(n)
        stack.extend(children.get(n, []))
    issues["cycles"] = sorted(set(nodes) - seen)
    report(STAGES[3][0], 1, 1)

    report(STAGES[4][0], 0, 1)
    descendants: dict[str, int] = {}
    height: dict[str, int] = {}
    earliest: dict[str, int | None] = {}
    for n in reversed(order):
        kids = [k for k in children.get(n, []) if k in descendants]
        descendants[n] = sum(1 + descendants[k] for k in kids)
        height[n] = 1 + max(height[k] for k in kids) if kids else 0
        dates = [nodes[n]["date_mean"]] if nodes[n].get("date_mean") is not None else []
        dates += [earliest[k] for k in kids if earliest[k] is not None]
        earliest[n] = min(dates) if dates else None

    for name, node in nodes.items():
        node["branch_date"] = earliest.get(name)
        node["children_count"] = len(children.get(name, []))
        node["descendants_count"] = descendants.get(name, 0)
        node["height"] = height.get(name, 0)
        node["rank"] = None

    descs_all = sorted(nodes[n]["descendants_count"] for n in nodes)
    heights_all = sorted(nodes[n]["height"] for n in nodes)
    rich_thr = max(5, quantile(descs_all, 0.95))
    hub_thr = max(25, quantile(descs_all, 0.99), rich_thr)
    deep_thr = max(4, quantile(heights_all, 0.95))
    for node in nodes.values():
        d = node["descendants_count"]
        if d >= hub_thr:
            node["rich"] = "hub"
        elif d >= rich_thr:
            node["rich"] = "rich"
        elif d <= 1:
            node["rich"] = "dead"
        else:
            node["rich"] = "normal"
        node["deep"] = node["height"] >= deep_thr
    for name, node in nodes.items():
        node["crown"] = node["rich"] in ("rich", "hub") and not any(
            nodes[k]["rich"] in ("rich", "hub") for k in children.get(name, []))

    branching = sorted((n for n in nodes if nodes[n]["descendants_count"] > 0),
                       key=lambda n: -nodes[n]["descendants_count"])
    prev, pos = None, 0
    for i, n in enumerate(branching, 1):
        d = nodes[n]["descendants_count"]
        if d != prev:
            pos, prev = i, d
        nodes[n]["rank"] = pos

    def root_sort_key(name: str) -> tuple:
        year = earliest.get(name)
        return (year is None, year if year is not None else 0, name.lower())

    roots.sort(key=root_sort_key)
    report(STAGES[4][0], 1, 1)

    report(STAGES[5][0], 0, 1)
    if progress_csv and progress_csv.exists():
        try:
            for marker, returned in progress_markers(progress_csv).items():
                for name in returned:
                    if name in nodes:
                        nodes[name]["markers"].append(marker)
        except Exception as exc:
            issues["csv"].append(f"progress.csv could not be read: {exc}")
    for node in nodes.values():
        node["markers"] = sorted(set(node["markers"]))
    report(STAGES[5][0], 1, 1)

    haystack: dict[str, str] = {}
    all_names = list(nodes)
    step = max(1, len(all_names) // 100)
    extra = snp_extra or {}
    for i, n in enumerate(all_names, 1):
        text = search_text(nodes[n])
        if n in extra:
            text = f"{text} {' '.join(extra[n])}".lower()
        haystack[n] = text
        if i % step == 0 or i == len(all_names):
            report(STAGES[6][0], i, len(all_names))

    discover_nodes = sum(1 for n in nodes.values() if not n.get("placeholder"))
    skeleton = sum(1 for n in nodes.values() if n.get("placeholder"))
    versions = sorted({str(n["tree_version"]) for n in nodes.values()
                       if not n.get("placeholder") and n.get("tree_version")})
    stats = {
        "files": discover_files,
        "nodes": len(nodes),
        "real_nodes": discover_nodes,  # Discover-enriched; старое имя для совместимости UI
        "discover_nodes": discover_nodes,
        "placeholder_nodes": skeleton,
        "skeleton_nodes": skeleton,
        "roots": len(roots),
        "issues": sum(len(issues[k]) for k in ("unreadable", "no_name", "duplicates", "cycles", "csv")),
        "tree_version": ", ".join(versions) or (published[:10] if published else ""),
        "published": published,
        "max_desc": descs_all[-1] if descs_all else 0,
        "rich_thr": rich_thr, "hub_thr": hub_thr, "deep_thr": deep_thr,
        "branching": len(branching),
        "rich_nodes": sum(1 for n in nodes.values() if n["rich"] in ("rich", "hub")),
        "crowns": sum(1 for n in nodes.values() if n["crown"]),
    }
    return {"nodes": nodes, "children": children, "roots": roots,
            "stats": stats, "issues": issues, "haystack": haystack}


def build_tree(tree: Haplotree, discover: Path | None = None, progress=None) -> dict[str, Any]:
    """Скелет = весь haplotree; Discover JSON подключается только где скачан."""
    def report(stage: str, done: int = 0, total: int = 1) -> None:
        if progress:
            progress(stage, done, total)

    issues: dict[str, list] = {"unreadable": [], "no_name": [], "duplicates": [],
                               "missing_parents": [], "cycles": [], "csv": []}

    report(STAGES[0][0], 0, 1)
    snp_by_node = _snp_names_by_node(tree)
    snp_extra: dict[str, list[str]] = {}
    nodes: dict[str, dict[str, Any]] = {}
    total = len(tree)
    step = max(1, total // 200)
    for i, name in enumerate(tree.names):
        p = tree.parent[i]
        parent = tree.names[p] if p >= 0 else None
        nodes[name] = haplotree_stub(name, parent)
        if snp_by_node[i]:
            snp_extra[name] = snp_by_node[i]
        if i == 0 or (i + 1) % step == 0 or i + 1 == total:
            report(STAGES[0][0], i + 1, total)

    report(STAGES[1][0], 0, 1)
    children, roots, missing = _link_topology(nodes)
    for m in missing:
        issues["missing_parents"].append((m, 0))
    report(STAGES[1][0], 1, 1)

    files: list[Path] = []
    if discover and discover.is_dir():
        files = sorted(discover.glob("*.json"))
    report(STAGES[2][0], 0, max(1, len(files)))
    if files:
        nfiles = len(files)
        step = max(1, nfiles // 200)
        for i, path in enumerate(files, 1):
            if i == 1 or i % step == 0 or i == nfiles:
                report(STAGES[2][0], i, nfiles)
            data, err = load_resource(path)
            if err:
                issues["unreadable"].append((path.name, err))
                continue
            hg = data.get("haplogroup") or {}
            if not isinstance(hg, dict) or not hg.get("name"):
                issues["no_name"].append(path.name)
                continue
            name = str(hg["name"])
            if name in nodes:
                if not nodes[name].get("placeholder") and nodes[name].get("source_file"):
                    old = nodes[name]
                    score = lambda item: sum(bool(item.get(key))
                                             for key in ("formed", "countries", "ancient"))
                    challenger = {
                        "formed": _dict((data.get("time") or {}).get("formed")),
                        "countries": _list(data.get("countries")),
                        "ancient": _list(data.get("ancient")),
                    }
                    if score(challenger) <= score(old):
                        issues["duplicates"].append((name, old["source_file"], path.name))
                        continue
                    issues["duplicates"].append((name, path.name, old["source_file"]))
                _apply_discover(nodes[name], data, path.name)
                continue
            # Discover-only (нет в текущем haplotree — переименован/удалён).
            parent = hg.get("parent_name")
            node = haplotree_stub(name, str(parent) if parent else None)
            node["haplotree"] = False
            _apply_discover(node, data, path.name)
            nodes[name] = node
        # Discover-only узлы могли сослаться на отсутствующих родителей.
        children, roots, missing = _link_topology(nodes)
        for m in missing:
            if m not in nodes:
                stub = haplotree_stub(m, None)
                stub["haplotree"] = False
                stub["date"] = "resource not downloaded"
                nodes[m] = stub
        children, roots, missing2 = _link_topology(nodes)
        issues["missing_parents"] = [(m, len(children.get(m, []))) for m in missing2]
    else:
        report(STAGES[2][0], 1, 1)

    progress_csv = None
    if discover and discover.is_dir():
        candidate = discover.parent.parent / "progress.csv"
        if candidate.exists():
            progress_csv = candidate

    # SNP-имена скелета уже перекрыты Discover.variants у enriched-узлов — не дублируем.
    for name in list(snp_extra):
        if name in nodes and not nodes[name].get("placeholder"):
            snp_extra.pop(name, None)
    return _finalize(nodes, children, roots, issues, len(files),
                     tree.published, progress_csv, progress, snp_extra)


def source_signature(haplotree: Path, discover: Path | None) -> str:
    """Отпечаток источников. Discover — по count/size/newest (без списка имён на каждый вызов)."""
    sig: list[Any] = [CACHE_VERSION]
    st = haplotree.stat()
    sig.append(("haplotree", str(haplotree.resolve()), st.st_mtime_ns, st.st_size))
    if discover and discover.is_dir():
        n = size = newest = 0
        with os.scandir(discover) as it:
            for e in it:
                if e.name.endswith(".json") and e.is_file():
                    st = e.stat()
                    n += 1
                    size += st.st_size
                    if st.st_mtime_ns > newest:
                        newest = st.st_mtime_ns
        sig.append(("discover", str(discover.resolve()), n, size, newest))
        csv_path = discover.parent.parent / "progress.csv"
        if csv_path.exists():
            st = csv_path.stat()
            sig.append(("progress.csv", st.st_mtime_ns, st.st_size))
    return hashlib.sha1(repr(sig).encode()).hexdigest()


_tree_mem: dict[str, dict[str, Any]] = {}


def hydrate_node(node: dict[str, Any], folder: Path | None) -> bool:
    """Тяжёлые Discover-поля с диска (после slim load из DuckDB)."""
    if node.get("_hydrated") or node.get("placeholder"):
        return False
    src = node.get("source_file")
    if not src or not folder:
        return False
    data, err = load_resource(Path(folder) / src)
    if err or not data:
        return False
    _apply_discover(node, data, src)
    node["_hydrated"] = True
    return True


def load_or_build(haplotree: Path, discover: Path | None = None,
                  progress=None) -> tuple[dict[str, Any], bool]:
    """Дерево F3: кэш — RAM + DuckDB tree.* (slim). Pickle больше не пишем."""
    if not haplotree.is_file():
        raise FileNotFoundError(f"Haplotree file not found: {haplotree.resolve()}")
    from ...core import tree_db
    from ...core.ylink import load_haplotree

    sig = source_signature(haplotree, discover)
    if sig in _tree_mem:
        if progress:
            progress("Loading cache", 1, 1)
        return _tree_mem[sig], True

    rid = tree_db.find_release(sig)
    if rid is not None:
        if progress:
            progress("Loading cache", 0, 1)
        try:
            data = tree_db.load_release(rid)
            if progress:
                progress("Loading cache", 1, 1)
            _tree_mem.clear()
            _tree_mem[sig] = data
            return data, True
        except Exception:
            pass

    if progress:
        progress("Loading haplotree", 0, 1)
    tree = load_haplotree(haplotree)
    if progress:
        progress("Loading haplotree", 1, 1)
    data = build_tree(tree, discover, progress)
    try:
        tree_db.save_release(sig, data, haplotree=haplotree, discover=discover)
    except Exception:
        pass
    _tree_mem.clear()
    _tree_mem[sig] = data
    # в RAM после build — полные узлы; следующие сессии — slim+hydrate
    return data, False


def graft_path(nodes: dict[str, Any], tree: Haplotree, path: list[str]) -> int:
    """Вставляет предков с haplotree, которых Discover не скачал. Возвращает число новых узлов."""
    added = 0
    for name in path:
        i = tree.index.get(name)
        if i is None:
            continue
        p = tree.parent[i]
        parent = tree.names[p] if p >= 0 else None
        if name not in nodes:
            nodes[name] = haplotree_stub(name, parent)
            added += 1
            continue
        node = nodes[name]
        if node.get("placeholder") and not node.get("parent") and parent:
            node["parent"] = parent
            node["haplotree"] = True
            if node.get("date") == "resource not downloaded":
                node["date"] = "from haplotree"
    return added


def relink_children(nodes: dict[str, Any]) -> tuple[dict[str, list[str]], list[str]]:
    children: dict[str, list[str]] = defaultdict(list)
    roots: list[str] = []
    for name, node in nodes.items():
        parent = node.get("parent")
        if not parent or parent == name or parent not in nodes:
            roots.append(name)
        else:
            children[parent].append(name)

    def sort_key(name: str) -> tuple:
        year = nodes[name].get("date_mean")
        return (year is None, year if year is not None else 0, name.lower())

    for values in children.values():
        values.sort(key=sort_key)
    roots.sort(key=sort_key)
    for name, kids in children.items():
        if name in nodes:
            nodes[name]["children_count"] = len(kids)
    return dict(children), roots


def node_codes(node: dict[str, Any]) -> set[str]:
    codes = set(node.get("flags") or [])
    for item in node.get("countries_data") or []:
        if isinstance(item, dict):
            codes.add(str(item.get("code") or "").split("-")[0].upper())
    if codes & UK_PARTS:
        codes.add("GB")
    return codes


def search_text(node: dict[str, Any]) -> str:
    parts = [node["name"], *node.get("markers", [])]
    for v in node.get("variants", []):
        if isinstance(v, dict):
            parts.append(str(v.get("name") or ""))
            parts.extend(str(a) for a in _list(v.get("aliases")))
    return " ".join(parts).lower()


def parse_year(text: str) -> int | None:
    """'-500', '500 BCE', '1,200', '1200 CE' → год (BCE отрицательный)."""
    text = text.strip()
    if not text:
        return None
    m = re.fullmatch(r"(-?)\s*([\d,\s]+)\s*(BCE|CE|BC|AD)?", text, re.I)
    if not m:
        raise ValueError(text)
    value = int(re.sub(r"[,\s]", "", m.group(2)))
    bce = m.group(1) == "-" or (m.group(3) or "").upper() in ("BCE", "BC")
    return -value if bce else value


def parse_count(text: str) -> int | None:
    text = text.strip()
    if not text:
        return None
    try:
        return max(0, int(re.sub(r"[,\s]", "", text)))
    except ValueError:
        raise ValueError(text)


def find_steps(v: Any, out: list) -> None:
    """Собирает все шаги миграции (объекты со startNode и endNode) из вложенной структуры."""
    if isinstance(v, dict):
        if "startNode" in v and "endNode" in v:
            out.append(v)
        for x in v.values():
            find_steps(x, out)
    elif isinstance(v, list):
        for x in v:
            find_steps(x, out)

