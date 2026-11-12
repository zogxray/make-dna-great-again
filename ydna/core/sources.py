"""Источники данных: описание, проверка состояния по пути и скачивание. Без зависимостей от UI.

Каждый источник — папка или файл. check(path) должна быть дешёвой (читает только заголовки);
download/verify выполняются внутри core.jobs.Job."""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import urllib.request
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import pgs
from .jobs import USER_AGENT, Job, ensure_space, fetch, md5_file, run_tool
from .store import Store, data_dir
from .ylink import (Coverage, FetchPlan, coverage, eta_text, fetch_cost,
                    fetch_plan, fetch_resources)


def _stage_fact(n: int) -> str:
    if not n:
        return "done"
    return f"{n:,} · {fetch_cost(n)}"


def human_size(n: float | None) -> str:
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def mtime_label(p: Path) -> str:
    return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


@dataclass
class FileRow:
    name: str
    size: int | None
    note: str = ""
    ok: bool = True


@dataclass
class Status:
    state: str                    # ok · partial · missing · unset
    summary: str
    facts: list[tuple[str, str]] = field(default_factory=list)
    files: list[FileRow] = field(default_factory=list)
    coverage: Coverage | None = None


def leftovers(folder: Path) -> list[FileRow]:
    return [FileRow(p.name, p.stat().st_size, "partial download", False)
            for p in sorted(folder.glob("*.part"))]


class Source:
    key = ""
    title = ""
    kind = "folder"               # folder · file
    url = ""
    about = ""
    size_hint = ""
    can_download = False
    can_verify = False
    can_fetch = False             # докачка недостающего на месте, без выбора пути
    needs_genome = False          # D требует активный геном F1 (покрытие / Y-link)
    download_note = ""

    def check(self, path: Path | None, store: Store | None = None,
              *, enrich: bool = True) -> Status:
        if path is None:
            return Status("unset", "not configured")
        try:
            st = self._check(path)
            # enrich (Discover↔Haplotree coverage) is heavy — F2 list skips it;
            # details / explicit recheck can turn it back on.
            if enrich and store and st.state == "ok":
                self.enrich(st, store)
            return st
        except OSError as exc:
            return Status("missing", f"cannot read: {exc}")

    def _check(self, path: Path) -> Status:
        raise NotImplementedError

    def enrich(self, st: Status, store: Store) -> None:
        """Сведения, которым нужны другие источники."""

    def suggest(self, store: Store) -> Path:
        return store.path(self.key) or data_dir() / self.key

    def download(self, path: Path, job: Job) -> str:
        raise NotImplementedError

    def verify(self, path: Path, job: Job) -> str:
        raise NotImplementedError


# ───────────────────────── FTDNA Discover ─────────────────────────

class FtdnaDiscover(Source):
    key = "ftdna_discover"
    title = "FTDNA Discover · haplogroups"
    url = "https://discover.familytreedna.com/resources/y-dna/{name}.json"
    about = ("JSON resources of Y haplogroups from FamilyTreeDNA Discover: dates, countries, ancient samples, "
             "migration. F3 builds the tree from the Haplotree and enriches nodes from this folder where present.")
    size_hint = "~2 GB for ~10,000 haplogroups, ~200 KB each"
    can_fetch = True
    download_note = ("The initial set is built by ftdna_downloader.py from the Y markers of your genome; "
                     "set the folder first, then missing haplogroups can be fetched here.")

    def _check(self, path: Path) -> Status:
        if not path.is_dir():
            return Status("missing", "folder not found")
        n = size = 0
        sample = None
        with os.scandir(path) as it:
            for e in it:
                if e.name.endswith(".json") and e.is_file():
                    n += 1
                    size += e.stat().st_size
                    sample = sample or Path(e.path)
        if not n:
            return Status("missing", "no *.json files in the folder")
        facts = [("Resources", f"{n:,} JSON files"), ("Size", human_size(size))]
        try:
            tree = json.loads(sample.read_text(encoding="utf-8")).get("tree") or {}
            ver = ".".join(str(tree[k]) for k in ("version_major", "version_minor", "version_micro") if k in tree)
            if ver:
                facts.append(("Tree version", ver))
        except (OSError, ValueError, AttributeError):
            pass
        root = path.parent.parent
        files = []
        for name in ("progress.csv", "resources.csv", "summary.txt", "errors.log"):
            p = root / name
            if p.exists():
                files.append(FileRow(f"../../{name}", p.stat().st_size, mtime_label(p)))
        summary = root / "summary.txt"
        if summary.exists():
            for line in summary.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("Updated UTC:"):
                    facts.append(("Downloaded", line.split(":", 1)[1].strip()[:19].replace("T", " ") + " UTC"))
        if not (root / "progress.csv").exists():
            facts.append(("SNP markers", "progress.csv not found two levels up; search by marker is limited"))
        try:
            from . import tree_db
            rel = tree_db.release_status()
        except Exception:
            rel = None
        if rel:
            when = rel["built_at"]
            if hasattr(when, "strftime"):
                when = when.strftime("%Y-%m-%d %H:%M")
            facts.append((
                "F3 tree index",
                f"{rel['n_nodes']:,} nodes · {rel['n_discover']:,} Discover"
                + (f" · {when}" if when else ""),
            ))
        else:
            facts.append(("F3 tree index",
                          "not built yet (first start of F3 will build it)"))
        return Status("ok", f"{n:,} resources · {human_size(size)}", facts, files)

    def enrich(self, st: Status, store: Store) -> None:
        cov = coverage(store)
        if isinstance(cov, str):
            st.facts.insert(2, ("Haplotree coverage", cov))
            return
        st.coverage = cov
        plan = fetch_plan(store)
        n = st.summary.split(" ", 1)[0]
        if isinstance(plan, FetchPlan):
            pending = (plan.count("our") + plan.count("children") + plan.count("genome")
                       + plan.count("all_genomes"))
            st.summary = (f"{n} · {cov.percent:.1f}% of haplotree · stages 1–4 left {pending:,}"
                          f" · tip {plan.tip or '—'}")
        else:
            st.summary = f"{n} · {cov.percent:.1f}% of haplotree · {len(cov.missing_mine):,} to fetch"
        rows = [("Haplotree coverage", f"{cov.downloaded:,} of {cov.tree_nodes:,} haplogroups "
                 f"({cov.percent:.2f}%)")]
        if cov.outside:
            rows.append(("Not in haplotree", f"{cov.outside:,} downloaded haplogroups are absent from the "
                         "current haplotree (renamed or removed)"))
        if isinstance(plan, FetchPlan):
            tip = plan.tip or "—"
            rows += [
                ("Fetch stages", f"tip {tip} · genome {plan.genome_label} · {plan.n_genomes} file(s) in F1"),
                ("  1 Our path", _stage_fact(plan.count("our"))),
                ("  2 Children", _stage_fact(plan.count("children"))),
                ("  3 This genome", _stage_fact(plan.count("genome"))
                 + " · exclusive of 1–2"),
                ("  4 All genomes", _stage_fact(plan.count("all_genomes"))
                 + " · other F1 files, exclusive of 1–3"),
                ("  5 Whole tree", _stage_fact(plan.count("tree"))),
            ]
        elif cov.named and not cov.found:
            rows.append(("Your Y markers", f"{cov.marker_source}: none of {cov.named:,} named SNPs is a "
                         "haplotree variant (e.g. 23andMe internal i-IDs); markers come from the active "
                         "file in F1"))
        elif cov.named:
            rows += [("Your Y markers", f"{cov.marker_source} (active in F1): {cov.named:,} named, "
                      f"{cov.found:,} found in haplotree"),
                     ("  can be fetched", f"{len(cov.missing_mine):,}"
                      + (f" ({eta_text(len(cov.missing_mine))})" if cov.missing_mine else ""))]
        else:
            rows.append(("Your Y markers", "no active genome in F1 and no progress.csv"))
        if cov.not_found:
            rows.append(("Not in Discover", f"{cov.not_found:,} haplogroups: no Search hint and no "
                         "resource JSON, or only a Discover alias (skipped when fetching)"))
        st.facts[2:2] = rows

    def fetch(self, path: Path, names: list[str], job: Job) -> str:
        return fetch_resources(path, names, job)

    def suggest(self, store: Store) -> Path:
        return store.path(self.key) or data_dir() / "ftdna" / "discover"


# ───────────────────────── FTDNA Haplotree ─────────────────────────

class FtdnaHaplotree(Source):
    key = "ftdna_haplotree"
    title = "FTDNA Y-DNA Haplotree"
    kind = "file"
    url = "https://www.familytreedna.com/public/y-dna-haplotree/get"
    about = ("The whole public FamilyTreeDNA Y tree in one JSON: every haplogroup with parent, children, "
             "defining SNPs with positions, kit counts by country. F3 builds its phylogenetic tree from this file; "
             "Discover JSON only enriches cards where downloaded.")
    size_hint = "~130 MB, the server is slow (several minutes) and cannot resume"
    can_download = True

    def _check(self, path: Path) -> Status:
        part = path.with_name(path.name + ".part")
        if path.is_dir():
            return Status("missing", "this source is a file, but the path is a folder")
        if not path.exists():
            if part.exists():
                return Status("partial", f"download interrupted at {human_size(part.stat().st_size)}",
                              files=[FileRow(part.name, part.stat().st_size, "partial download", False)])
            return Status("missing", "file not found")
        size = path.stat().st_size
        with path.open("rb") as fh:
            head = fh.read(64)
            fh.seek(max(0, size - 64))
            tail = fh.read()
        facts = [("Size", human_size(size)), ("Modified", mtime_label(path))]
        if not head.startswith(b'{"allNodes"') or not tail.rstrip().endswith(b"}"):
            return Status("partial", "not a complete haplotree JSON", facts,
                          [FileRow(path.name, size, "unexpected content", False)])
        # Do NOT scan the ~130 MB JSON on every F2 open — that blocked the UI
        # behind DuckDB after F1. Node count comes from a warm ylink index or enrich.
        nodes = None
        try:
            from . import ylink
            nodes = ylink.cached_haplotree_size(path)
        except Exception:  # noqa: BLE001
            nodes = None
        if nodes is not None:
            facts.insert(0, ("Haplogroups", f"{nodes:,}"))
            return Status("ok", f"{nodes:,} haplogroups · {human_size(size)}", facts,
                          [FileRow(path.name, size, mtime_label(path))])
        return Status("ok", f"{human_size(size)} · haplotree JSON", facts,
                      [FileRow(path.name, size, mtime_label(path))])

    def enrich(self, st: Status, store: Store) -> None:
        cov = coverage(store)
        if isinstance(cov, str):
            return
        st.summary = f"{cov.tree_nodes:,} haplogroups · " + (
            st.summary.split(" · ", 1)[-1] if " · " in st.summary else st.summary
        )
        # Keep Size/Modified; put / update Haplogroups at the front.
        st.facts = [(k, v) for k, v in st.facts if k != "Haplogroups"]
        st.facts.insert(0, ("Haplogroups", f"{cov.tree_nodes:,}"))
        st.facts.insert(1, ("Downloaded from Discover", f"{cov.downloaded:,} ({cov.percent:.2f}%)"))
        if cov.named:
            st.facts.insert(2, ("Your Y markers", f"{cov.found:,} of {cov.named:,} named are haplotree variants"))

    def suggest(self, store: Store) -> Path:
        if store.path(self.key):
            return store.path(self.key)
        return data_dir() / "ftdna" / "haplotree" / "y-dna-haplotree.json"

    def download(self, path: Path, job: Job) -> str:
        ensure_space(path, 300 << 20)
        old = path.with_name(path.name + ".previous")
        if path.exists():
            path.replace(old)
        try:
            fetch(self.url, path, job, "haplotree JSON")
        except BaseException:
            if old.exists() and not path.exists():
                old.replace(path)
            raise
        st = self.check(path)
        if st.state != "ok":
            raise RuntimeError(st.summary)
        old.unlink(missing_ok=True)
        return st.summary


# ───────────────────────── YFull YTree ─────────────────────────

YFULL_GITHUB = "https://api.github.com/repos/YFullTeam/YTree/contents/ytree?ref=master"
YFULL_PAGE = "https://github.com/YFullTeam/YTree/tree/master/ytree"
YFULL_MANIFEST = "yfull-ytree-manifest.json"
_YFULL_ZIP_RE = re.compile(r"^tree_(\d+(?:\.\d+)*)\.zip$", re.I)
_YFULL_JSON_RE = re.compile(r"^tree_(\d+(?:\.\d+)*)\.json$", re.I)


def _yfull_ver(name: str) -> tuple[int, ...]:
    m = _YFULL_ZIP_RE.match(name) or _YFULL_JSON_RE.match(name)
    if not m:
        return (0,)
    return tuple(int(p) for p in m.group(1).split("."))


def _yfull_ver_label(name: str) -> str:
    m = _YFULL_ZIP_RE.match(name) or _YFULL_JSON_RE.match(name)
    return m.group(1) if m else "?"


class YfullYtree(Source):
    key = "yfull_ytree"
    title = "YFull YTree"
    url = YFULL_PAGE
    about = ("Public YFull Y-chromosome tree releases from YFullTeam/YTree on GitHub: nested JSON with "
             "TMRCA, formed ages and defining SNPs. D downloads the newest tree_*.zip and unpacks "
             "tree_*.json into the folder.")
    size_hint = "~3 MB zip → ~15 MB JSON"
    can_download = True

    def latest_json(self, path: Path) -> Path | None:
        files = [p for p in path.glob("tree_*.json") if p.is_file() and _YFULL_JSON_RE.match(p.name)]
        if not files:
            return None
        files.sort(key=lambda p: _yfull_ver(p.name))
        return files[-1]

    def _check(self, path: Path) -> Status:
        if not path.is_dir():
            return Status("missing", "folder not found")
        rows = leftovers(path)
        tree = self.latest_json(path)
        man = path / YFULL_MANIFEST
        facts: list[tuple[str, str]] = []
        if man.exists():
            try:
                data = json.loads(man.read_text(encoding="utf-8"))
                if data.get("version"):
                    facts.append(("Release", str(data["version"])))
                if data.get("downloaded_at_utc"):
                    facts.append(("Downloaded",
                                  str(data["downloaded_at_utc"])[:19].replace("T", " ") + " UTC"))
                if data.get("zip"):
                    facts.append(("Zip", str(data["zip"])))
            except (ValueError, TypeError, OSError):
                pass
        if not tree:
            zips = sorted(path.glob("tree_*.zip"), key=lambda p: _yfull_ver(p.name))
            if zips:
                rows.append(FileRow(zips[-1].name, zips[-1].stat().st_size, "zip present, unpack missing", False))
                return Status("partial", f"{zips[-1].name} not unpacked", facts, rows)
            return Status("partial" if rows else "missing", "no tree_*.json in the folder", facts, rows)

        size = tree.stat().st_size
        ver = _yfull_ver_label(tree.name)
        if not any(k == "Release" for k, _ in facts):
            facts.insert(0, ("Release", ver))
        facts.append(("Modified", mtime_label(tree)))
        # Дешёвый подсчёт узлов: у каждого есть "tmrca".
        nodes, carry = 0, b""
        needle = b'"tmrca":'
        with tree.open("rb") as fh:
            while chunk := fh.read(8 << 20):
                buf = carry + chunk
                nodes += buf.count(needle)
                carry = buf[-(len(needle) - 1):]
        facts.insert(0, ("Nodes", f"≈ {nodes:,}"))
        rows.insert(0, FileRow(tree.name, size, mtime_label(tree)))
        zip_path = path / f"tree_{ver}.zip"
        if zip_path.exists():
            rows.append(FileRow(zip_path.name, zip_path.stat().st_size, "downloaded archive"))
        if man.exists():
            rows.append(FileRow(man.name, man.stat().st_size, "download meta"))
        return Status("ok", f"v{ver} · ≈ {nodes:,} nodes · {human_size(size)}", facts, rows)

    def suggest(self, store: Store) -> Path:
        return store.path(self.key) or data_dir() / "yfull" / "ytree"

    def _list_remote_zips(self, job: Job) -> list[dict]:
        job.set_stage("listing YFullTeam/YTree releases")
        req = urllib.request.Request(
            YFULL_GITHUB,
            headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            items = json.load(resp)
        if not isinstance(items, list):
            raise RuntimeError("GitHub API did not return a file list for ytree/")
        zips = [it for it in items
                if isinstance(it, dict) and it.get("type") == "file"
                and _YFULL_ZIP_RE.match(str(it.get("name") or ""))]
        if not zips:
            raise RuntimeError("no tree_*.zip files in YFullTeam/YTree/ytree")
        zips.sort(key=lambda it: _yfull_ver(str(it["name"])))
        return zips

    def download(self, path: Path, job: Job) -> str:
        zips = self._list_remote_zips(job)
        latest = zips[-1]
        name = str(latest["name"])
        ver = _yfull_ver_label(name)
        url = latest.get("download_url")
        if not url:
            raise RuntimeError(f"{name}: GitHub did not provide download_url")
        size = latest.get("size")
        size = int(size) if isinstance(size, int) else None

        ensure_space(path, (size or (4 << 20)) + (32 << 20))
        path.mkdir(parents=True, exist_ok=True)
        zip_dest = path / name
        json_name = f"tree_{ver}.json"
        json_dest = path / json_name

        fetch(url, zip_dest, job, f"YFull {name}", size)

        job.set_stage(f"unpacking {name}")
        job.check()
        with zipfile.ZipFile(zip_dest) as zf:
            members = [m for m in zf.namelist() if not m.endswith("/")]
            json_members = [m for m in members if m.rstrip("/").endswith(".json")]
            if not json_members:
                raise RuntimeError(f"{name}: no .json inside the archive")
            # Обычно один файл tree_X.Y.Z.json в корне zip.
            member = next((m for m in json_members if Path(m).name == json_name), json_members[0])
            tmp = json_dest.with_name(json_dest.name + ".part")
            tmp.unlink(missing_ok=True)
            with zf.open(member) as src, tmp.open("wb") as dst:
                while chunk := src.read(1 << 20):
                    job.check()
                    dst.write(chunk)
                    job.progress(tmp.stat().st_size)
            tmp.replace(json_dest)

        # Старые релизы в этой папке больше не нужны.
        for p in path.glob("tree_*.json"):
            if p.name != json_dest.name:
                p.unlink(missing_ok=True)
        for p in path.glob("tree_*.zip"):
            if p.name != zip_dest.name:
                p.unlink(missing_ok=True)

        (path / YFULL_MANIFEST).write_text(json.dumps({
            "source": "YFullTeam/YTree",
            "page": YFULL_PAGE,
            "version": ver,
            "zip": name,
            "json": json_dest.name,
            "github_size_bytes": size,
            "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        }, indent=2), encoding="utf-8")

        st = self.check(path)
        if st.state != "ok":
            raise RuntimeError(st.summary)
        return st.summary


# ───────────────────────── TheYtree ─────────────────────────

THEYTREE_PAGE = "https://www.theytree.com/en/portal/index/dataDownload"
# Сайт dataDownload требует логин; публичный CC-BY релиз той же базы — Zenodo concept.
THEYTREE_ZENODO_CONCEPT = "20571784"
THEYTREE_ZENODO_API = f"https://zenodo.org/api/records/{THEYTREE_ZENODO_CONCEPT}"
THEYTREE_MANIFEST = "theytree-manifest.json"
_THEYTREE_ZIP_RE = re.compile(r"^theytree_com_ysnp_data_v([\d.]+)_(?:\d+)\.zip$", re.I)


class Theytree(Source):
    key = "theytree"
    title = "TheYtree Y-SNP tree"
    url = THEYTREE_PAGE
    about = ("TheYtree / YSNP paternal tree dump: tree structure CSV + SNP-relation CSV "
             "(hg19/hg38/T2T). The portal Data Release page needs a login; F2 pulls the same "
             "public CC-BY package from Zenodo (latest concept version) and unpacks it.")
    size_hint = "~11 MB zip → ~70 MB unpacked"
    can_download = True
    can_verify = True

    def stats_path(self, path: Path) -> Path | None:
        p = path / "metadata" / "data_statistics.json"
        return p if p.is_file() else None

    def tree_csvs(self, path: Path) -> list[Path]:
        tree = path / "tree"
        if not tree.is_dir():
            return []
        return sorted(p for p in tree.glob("theytree_com_tree_*.csv") if p.is_file())

    def _check(self, path: Path) -> Status:
        if not path.is_dir():
            return Status("missing", "folder not found")
        rows = leftovers(path)
        stats_file = self.stats_path(path)
        csvs = self.tree_csvs(path)
        facts: list[tuple[str, str]] = []
        man = path / THEYTREE_MANIFEST
        if man.exists():
            try:
                data = json.loads(man.read_text(encoding="utf-8"))
                if data.get("version"):
                    facts.append(("Release", str(data["version"])))
                if data.get("zenodo_id"):
                    facts.append(("Zenodo", f"record {data['zenodo_id']}"))
                if data.get("downloaded_at_utc"):
                    facts.append(("Downloaded",
                                  str(data["downloaded_at_utc"])[:19].replace("T", " ") + " UTC"))
            except (ValueError, TypeError, OSError):
                pass
        nodes = snps = None
        if stats_file:
            try:
                meta = json.loads(stats_file.read_text(encoding="utf-8"))
                ver = meta.get("version")
                if ver and not any(k == "Release" for k, _ in facts):
                    facts.insert(0, ("Release", str(ver)))
                if meta.get("release_date") or meta.get("data_date"):
                    facts.append(("Data date", str(meta.get("release_date") or meta.get("data_date"))))
                st = meta.get("statistics") or {}
                nodes = st.get("tree_nodes_count")
                snps = st.get("tree_snp_relations_count")
                if nodes is not None:
                    facts.insert(0, ("Nodes", f"{int(nodes):,}"))
                if snps is not None:
                    facts.insert(1 if nodes is not None else 0,
                                 ("SNP relations", f"{int(snps):,}"))
            except (ValueError, TypeError, OSError):
                pass
        for p in csvs:
            rows.append(FileRow(f"tree/{p.name}", p.stat().st_size, mtime_label(p)))
        if stats_file:
            rows.append(FileRow("metadata/data_statistics.json", stats_file.stat().st_size,
                                mtime_label(stats_file)))
        zips = sorted(path.glob("theytree_com_ysnp_data_*.zip"))
        if zips:
            rows.append(FileRow(zips[-1].name, zips[-1].stat().st_size, "downloaded archive"))
        if man.exists():
            rows.append(FileRow(man.name, man.stat().st_size, "download meta"))

        if not csvs:
            if zips:
                return Status("partial", f"{zips[-1].name} not unpacked", facts, rows)
            return Status("partial" if rows else "missing",
                          "no tree/theytree_com_tree_*.csv", facts, rows)
        total = sum(r.size or 0 for r in rows if r.ok and r.size)
        bits = []
        if nodes is not None:
            bits.append(f"{int(nodes):,} nodes")
        bits.append(human_size(total))
        return Status("ok", " · ".join(bits), facts, rows)

    def suggest(self, store: Store) -> Path:
        return store.path(self.key) or data_dir() / "theytree"

    def _latest_zenodo(self, job: Job) -> dict:
        job.set_stage("querying Zenodo for latest TheYtree release")
        req = urllib.request.Request(
            THEYTREE_ZENODO_API,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            rec = json.load(resp)
        if not isinstance(rec, dict) or not rec.get("files"):
            raise RuntimeError("Zenodo did not return files for the TheYtree concept record")
        return rec

    def download(self, path: Path, job: Job) -> str:
        rec = self._latest_zenodo(job)
        files = [f for f in rec["files"] if str(f.get("key") or "").endswith(".zip")]
        if not files:
            raise RuntimeError("no .zip in the latest TheYtree Zenodo record")
        files.sort(key=lambda f: str(f.get("key") or ""))
        remote = files[-1]
        name = str(remote["key"])
        size = int(remote["size"]) if isinstance(remote.get("size"), int) else None
        checksum = str(remote.get("checksum") or "")
        md5 = checksum.split(":", 1)[1] if checksum.startswith("md5:") else None
        url = (remote.get("links") or {}).get("self")
        if not url:
            raise RuntimeError(f"{name}: no Zenodo download link")

        m = _THEYTREE_ZIP_RE.match(name)
        ver = f"v{m.group(1)}" if m else name

        ensure_space(path, (size or (16 << 20)) + (120 << 20))
        path.mkdir(parents=True, exist_ok=True)
        zip_dest = path / name
        fetch(url, zip_dest, job, f"TheYtree {name}", size, md5)

        job.set_stage(f"unpacking {name}")
        # Чистая распаковка: убрать прошлый tree/metadata, затем extract.
        for sub in ("tree", "metadata"):
            old = path / sub
            if old.is_dir():
                shutil.rmtree(old)
        for leftover in path.glob("CHANGELOG*"):
            leftover.unlink(missing_ok=True)
        for leftover in path.glob("LICENSE*"):
            leftover.unlink(missing_ok=True)
        for leftover in path.glob("README*"):
            leftover.unlink(missing_ok=True)

        with zipfile.ZipFile(zip_dest) as zf:
            members = [m for m in zf.namelist() if not m.endswith("/")]
            if not members:
                raise RuntimeError(f"{name}: empty archive")
            total = sum(zf.getinfo(m).file_size for m in members)
            job.set_stage(f"unpacking {name}", total, 0)
            done = 0
            for member in members:
                job.check()
                # Zip-slip guard.
                dest = (path / member).resolve()
                if not str(dest).startswith(str(path.resolve())):
                    raise RuntimeError(f"refusing unsafe zip path: {member}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, dest.open("wb") as dst:
                    while chunk := src.read(1 << 20):
                        job.check()
                        dst.write(chunk)
                        done += len(chunk)
                        job.progress(done)

        for p in path.glob("theytree_com_ysnp_data_*.zip"):
            if p.name != zip_dest.name:
                p.unlink(missing_ok=True)

        (path / THEYTREE_MANIFEST).write_text(json.dumps({
            "source": "TheYtree / YSNP",
            "page": THEYTREE_PAGE,
            "zenodo_concept": THEYTREE_ZENODO_CONCEPT,
            "zenodo_id": rec.get("id"),
            "doi": (rec.get("doi") or (rec.get("metadata") or {}).get("doi")),
            "version": ver,
            "zip": name,
            "checksum": checksum or None,
            "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        }, indent=2), encoding="utf-8")

        st = self.check(path)
        if st.state != "ok":
            raise RuntimeError(st.summary)
        return st.summary

    def verify(self, path: Path, job: Job) -> str:
        man = path / THEYTREE_MANIFEST
        if not man.is_file():
            raise RuntimeError("no theytree-manifest.json (download via D first)")
        data = json.loads(man.read_text(encoding="utf-8"))
        name = data.get("zip")
        checksum = str(data.get("checksum") or "")
        if not name or not checksum.startswith("md5:"):
            raise RuntimeError("manifest has no zip MD5")
        zip_path = path / str(name)
        if not zip_path.is_file():
            raise RuntimeError(f"{name} missing next to the unpacked files")
        got = md5_file(zip_path, job, name)
        expect = checksum.split(":", 1)[1].lower()
        if got != expect:
            raise RuntimeError(f"MD5 mismatch for {name}")
        return f"MD5 OK for {name}"


# ───────────────────────── AADR ─────────────────────────

DATAVERSE = "https://dataverse.harvard.edu"
AADR_DOI = "doi:10.7910/DVN/FFIDCW"
AADR_PANEL = "1240K"
MANIFEST = "aadr-download-manifest.json"


def _geno_header(path: Path) -> tuple[int, int] | None:
    with path.open("rb") as fh:
        parts = fh.read(48).split(b"\x00")[0].split()
    if len(parts) >= 3 and parts[0] in (b"GENO", b"TGENO"):
        return int(parts[1]), int(parts[2])
    return None


def _md5_list(folder: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in folder.glob("*files.md5sum"):
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) >= 2 and re.fullmatch(r"[0-9a-f]{32}", parts[-2]):
                out[parts[-1]] = parts[-2]
    return out


class Aadr(Source):
    key = "aadr"
    title = f"AADR · {AADR_PANEL}"
    url = "https://doi.org/10.7910/DVN/FFIDCW"
    about = ("Allen Ancient DNA Resource: genotypes of ancient and modern individuals (EIGENSTRAT "
             ".geno/.snp/.ind) and the .anno table with dates, locations and Y/mt haplogroups.")
    size_hint = "~7.3 GB (geno 7.1 GB + snp + ind + anno)"
    can_download = True
    can_verify = True

    def trio(self, path: Path) -> tuple[str, dict[str, Path | None]] | None:
        genos = sorted(path.glob("*.geno")) + sorted(path.glob("*.tgeno"))
        if not genos:
            return None
        geno = next((g for g in genos if f"_{AADR_PANEL}." in g.name), genos[0])
        prefix = geno.name[: -len(geno.suffix)]
        stem = prefix.split(".patch")[0].split(".aadr")[0]
        files: dict[str, Path | None] = {"geno": geno}
        for ext in ("snp", "ind"):
            p = path / f"{prefix}.{ext}"
            files[ext] = p if p.exists() else None
        anno = path / f"{prefix}.anno"
        if not anno.exists():
            anno = next(iter(sorted(path.glob(f"{stem}*.anno"))), None)
        files["anno"] = anno
        return prefix, files

    def _check(self, path: Path) -> Status:
        if not path.is_dir():
            return Status("missing", "folder not found")
        found = self.trio(path)
        rows = leftovers(path)
        if not found:
            return Status("partial" if rows else "missing", "no .geno file in the folder", files=rows)
        prefix, files = found
        expected: dict[str, int] = {}
        facts: list[tuple[str, str]] = []
        m = re.match(r"(v[\d.]+(?:p\d+)?)_([^.]+)", prefix)
        if m:
            facts += [("Release", m.group(1)), ("Panel", m.group(2))]
        man = path / MANIFEST
        if man.exists():
            try:
                data = json.loads(man.read_text(encoding="utf-8"))
                expected = {f["name"]: f.get("expected_size_bytes") for f in data.get("files", [])}
                facts.append(("Dataverse version", f"{data.get('version', '?')} · released "
                              f"{str(data.get('release_time', '?'))[:10]}"))
                facts.append(("Downloaded", str(data.get("downloaded_at_utc", "?"))[:19].replace("T", " ") + " UTC"))
            except (ValueError, KeyError, TypeError):
                pass
        hdr = _geno_header(files["geno"])
        if hdr:
            facts.insert(0, ("Individuals", f"{hdr[0]:,}"))
            facts.insert(1, ("SNPs", f"{hdr[1]:,}"))
        missing = []
        md5s = _md5_list(path)
        for role in ("geno", "snp", "ind", "anno"):
            p = files[role]
            if p is None:
                missing.append(role)
                rows.append(FileRow(f"{prefix}.{role}", None, "missing", False))
                continue
            size = p.stat().st_size
            exp = expected.get(p.name)
            ok = exp is None or exp == size
            note = (f"expected {exp:,} bytes" if not ok else
                    ("size matches manifest" if exp else "")) + ("" if p.name in md5s else " · no MD5 in list")
            rows.insert(0 if role == "geno" else len(rows), FileRow(p.name, size, note.strip(" ·"), ok))
        total = sum(r.size or 0 for r in rows if r.ok)
        bad = [r for r in rows if not r.ok]
        if bad:
            what = ", ".join(missing) + " missing" if missing else f"{len(bad)} file(s) need attention"
            return Status("partial", what, facts, rows)
        indiv = f"{hdr[0]:,} individuals · " if hdr else ""
        return Status("ok", f"{indiv}{human_size(total)}", facts, rows)

    def suggest(self, store: Store) -> Path:
        return store.path(self.key) or data_dir() / "aadr"

    def download(self, path: Path, job: Job) -> str:
        job.set_stage("querying Harvard Dataverse")
        req = urllib.request.Request(f"{DATAVERSE}/api/datasets/:persistentId/?persistentId={AADR_DOI}",
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            version = json.load(resp)["data"]["latestVersion"]
        records = [{"name": f["dataFile"]["filename"], "id": f["dataFile"]["id"],
                    "size": f["dataFile"].get("filesize"), "md5": f["dataFile"].get("md5")}
                   for f in version.get("files", []) if f.get("dataFile")]
        tag = f"_{AADR_PANEL}.aadr."

        def pick(ext: str) -> dict | None:
            c = [r for r in records if tag in r["name"] and r["name"].endswith(ext)]
            c.sort(key=lambda r: (".patch." not in r["name"], "PUB" not in r["name"]))
            return c[0] if c else None

        chosen = [pick(e) for e in (".geno", ".snp", ".ind", ".anno")]
        chosen += [r for r in records if r["name"].endswith("files.md5sum")][:1]
        if not all(chosen[:3]):
            raise RuntimeError("cannot find the 1240K geno/snp/ind in the latest release: "
                               + ", ".join(r["name"] for r in records))
        chosen = [r for r in chosen if r]
        need = sum((r["size"] or 0) for r in chosen
                   if not ((path / r["name"]).exists() and (path / r["name"]).stat().st_size == r["size"]))
        ensure_space(path, need + (64 << 20))
        path.mkdir(parents=True, exist_ok=True)
        got = 0
        for i, r in enumerate(chosen, 1):
            res = fetch(f"{DATAVERSE}/api/access/datafile/{r['id']}", path / r["name"], job,
                        f"{i}/{len(chosen)} {r['name']}", r["size"], r["md5"])
            got += res == "downloaded"
        minor = version.get("versionMinorNumber")
        (path / MANIFEST).write_text(json.dumps({
            "dataset": "The Allen Ancient DNA Resource (AADR)",
            "persistent_id": AADR_DOI,
            "dataverse_url": self.url,
            "version": f"{version.get('versionNumber')}.{minor}" if minor else str(version.get("versionNumber")),
            "release_time": version.get("releaseTime"),
            "panel": AADR_PANEL,
            "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
            "files": [{"name": r["name"], "dataverse_file_id": r["id"], "expected_size_bytes": r["size"],
                       "published_md5": r["md5"]} for r in chosen],
        }, indent=2), encoding="utf-8")
        return f"{got} file(s) downloaded, {len(chosen) - got} already present"

    def verify(self, path: Path, job: Job) -> str:
        found = self.trio(path) if path.is_dir() else None
        if not found:
            raise RuntimeError("no AADR files to verify")
        md5s = _md5_list(path)
        if not md5s:
            raise RuntimeError("no *files.md5sum next to the data")
        ok, skipped, bad = [], [], []
        for p in found[1].values():
            if p is None:
                continue
            if p.name not in md5s:
                skipped.append(p.name)
                continue
            (ok if md5_file(p, job, p.name) == md5s[p.name] else bad).append(p.name)
        if bad:
            raise RuntimeError("MD5 mismatch: " + ", ".join(bad))
        tail = f"; no checksum for {', '.join(skipped)}" if skipped else ""
        return f"MD5 OK for {len(ok)} file(s){tail}"


# ───────────────────────── 1000 Genomes ─────────────────────────

KG_URLS = {
    "all_hg38.pgen.zst": "https://www.dropbox.com/s/j72j6uciq5zuzii/all_hg38.pgen.zst?dl=1",
    "all_hg38.pvar.zst": "https://www.dropbox.com/scl/fi/id642dpdd858uy41og8qi/all_hg38_rs_noannot.pvar.zst"
                         "?dl=1&rlkey=sskyiyam1bsqweujjmxqv1h55",
    "all_hg38.psam": "https://www.dropbox.com/s/qhtb5t3py3kyjeq/hg38_orig.psam?dl=1",
}


def _pgen_header(path: Path) -> tuple[int, int] | None:
    with path.open("rb") as fh:
        b = fh.read(11)
    if len(b) == 11 and b[:2] == b"\x6c\x1b" and b[2] in (0x10, 0x11):
        return struct.unpack("<II", b[3:11])
    return None


class ThousandGenomes(Source):
    key = "1000g"
    title = "1000 Genomes · high coverage 3202"
    url = "https://www.cog-genomics.org/plink/2.0/resources"
    about = ("1000 Genomes Project high-coverage panel, 3,202 samples, GRCh38, as a ready PLINK 2 set "
             "(all_hg38.pgen/.pvar.zst/.psam, rsID-annotated PVAR). Reference for allele frequencies and PGS.")
    size_hint = "~4 GB to download, ~13.5 GB after unpacking the PGEN"
    can_download = True

    def _check(self, path: Path) -> Status:
        if not path.is_dir():
            return Status("missing", "folder not found")
        rows = []
        pgen, pgen_zst = path / "all_hg38.pgen", path / "all_hg38.pgen.zst"
        pvar = next((p for p in (path / "all_hg38.pvar.zst", path / "all_hg38.pvar") if p.exists()), None)
        psam = path / "all_hg38.psam"
        facts: list[tuple[str, str]] = [("Build", "GRCh38")]
        hdr = _pgen_header(pgen) if pgen.exists() else None
        if hdr:
            facts.insert(0, ("Variants", f"{hdr[0]:,}"))
            facts.insert(1, ("Samples", f"{hdr[1]:,}"))
        if psam.exists():
            lines = psam.read_text(encoding="utf-8", errors="replace").splitlines()
            head = lines[0].lstrip("#").split("\t") if lines else []
            col = head.index("SuperPop") if "SuperPop" in head else None
            if col is not None:
                pops = Counter(r.split("\t")[col] for r in lines[1:] if r.count("\t") >= col)
                facts.append(("Super-populations", " · ".join(f"{k} {v:,}" for k, v in sorted(pops.items()))))
            if not hdr:
                facts.insert(0, ("Samples", f"{len(lines) - 1:,}"))
        for p, need in ((pgen, "unpacked genotypes (PLINK reads this)"), (pgen_zst, "compressed download"),
                        (pvar, "variants"), (psam, "samples")):
            if p is not None and p.exists():
                rows.append(FileRow(p.name, p.stat().st_size, need))
        missing = []
        if not pgen.exists():
            missing.append("all_hg38.pgen" + (" (unpack .zst)" if pgen_zst.exists() else ""))
        if pvar is None:
            missing.append("all_hg38.pvar.zst")
        if not psam.exists():
            missing.append("all_hg38.psam")
        for m in missing:
            rows.append(FileRow(m, None, "missing", False))
        rows += leftovers(path)
        if missing:
            have = any(p.exists() for p in (pgen, pgen_zst, psam)) or pvar is not None
            return Status("partial" if have else "missing", "missing: " + ", ".join(missing), facts, rows)
        total = sum(r.size or 0 for r in rows if r.ok)
        samples = f"{hdr[1]:,} samples · " if hdr else ""
        if any(not r.ok for r in rows):
            facts.append(("Leftovers", ".part files from an interrupted download can be deleted"))
        return Status("ok", f"{samples}{human_size(total)}", facts, rows)

    def suggest(self, store: Store) -> Path:
        return store.path(self.key) or data_dir() / "kg"

    def download(self, path: Path, job: Job) -> str:
        pgen = path / "all_hg38.pgen"
        need = 0 if pgen.exists() else 10 << 30
        need += sum(4 << 30 if "pgen" in n else 600 << 20 for n in KG_URLS if not (path / n).exists())
        ensure_space(path, need)
        path.mkdir(parents=True, exist_ok=True)
        for i, (name, url) in enumerate(KG_URLS.items(), 1):
            fetch(url, path / name, job, f"{i}/{len(KG_URLS)} {name}")
        if not pgen.exists():
            tmp = path / "all_hg38.pgen.part"
            tmp.unlink(missing_ok=True)
            plink = shutil.which("plink2")
            if plink:
                cmd = [plink, "--zst-decompress", str(path / "all_hg38.pgen.zst"), str(tmp)]
            elif shutil.which("zstd"):
                cmd = ["zstd", "-d", "-f", "-q", "-o", str(tmp), str(path / "all_hg38.pgen.zst")]
            else:
                raise RuntimeError("downloaded; to unpack all_hg38.pgen.zst install plink2 or zstd")
            run_tool(cmd, job, tmp, "unpacking all_hg38.pgen.zst")
            tmp.replace(pgen)
        st = self.check(path)
        return st.summary


# ───────────────────────── rsID identity (GRCh37 ↔ GRCh38) ─────────────────────────

RSID_IDENTITY_URL = (
    "https://huggingface.co/datasets/allelix/allelix-rsid-identity/"
    "resolve/main/rsid_identity.sqlite.gz"
)
RSID_IDENTITY_PAGE = "https://huggingface.co/datasets/allelix/allelix-rsid-identity"
RSID_IDENTITY_GZ = "rsid_identity.sqlite.gz"
RSID_IDENTITY_SQLITE = "rsid_identity.sqlite"
RSID_IDENTITY_DB = "rsid_identity.duckdb"
RSID_IDENTITY_MANIFEST = "rsid-identity-manifest.json"
# ~155 MB gz; sqlite/duckdb each ~0.5–1 GB during convert
RSID_IDENTITY_GZ_SIZE = 154_630_450


def _sql_path(p: Path) -> str:
    return str(p.resolve()).replace("'", "''")


class RsidIdentity(Source):
    key = "rsid_identity"
    title = "rsID identity · GRCh37/38"
    url = RSID_IDENTITY_PAGE
    about = ("Allelix rsID identity cache (dbSNP 157): rsID → positions and alleles on GRCh37 and "
             "GRCh38. F2 downloads the gzipped SQLite from Hugging Face and converts it to DuckDB "
             "for offline build detection and cross-build lookups.")
    size_hint = "~155 MB download → ~0.5–1 GB DuckDB"
    can_download = True
    can_verify = True

    def db_file(self, path: Path) -> Path:
        return path / RSID_IDENTITY_DB

    def _check(self, path: Path) -> Status:
        if not path.is_dir():
            return Status("missing", "folder not found")
        rows = leftovers(path)
        gz = path / RSID_IDENTITY_GZ
        db = self.db_file(path)
        sqlite = path / RSID_IDENTITY_SQLITE
        man = path / RSID_IDENTITY_MANIFEST
        facts: list[tuple[str, str]] = []
        if man.exists():
            try:
                data = json.loads(man.read_text(encoding="utf-8"))
                if data.get("rows") is not None:
                    facts.append(("Rows", f"{int(data['rows']):,}"))
                if data.get("rsids") is not None:
                    facts.append(("rsIDs", f"{int(data['rsids']):,}"))
                if data.get("source"):
                    facts.append(("Upstream", str(data["source"])))
                if data.get("downloaded_at_utc"):
                    facts.append(("Downloaded",
                                  str(data["downloaded_at_utc"])[:19].replace("T", " ") + " UTC"))
            except (ValueError, TypeError, OSError):
                pass
        if gz.exists():
            rows.append(FileRow(gz.name, gz.stat().st_size, "Hugging Face download"))
        if sqlite.exists():
            rows.append(FileRow(sqlite.name, sqlite.stat().st_size, "temp SQLite (convert in progress?)"))
        if db.exists():
            rows.append(FileRow(db.name, db.stat().st_size, mtime_label(db)))
        if man.exists():
            rows.append(FileRow(man.name, man.stat().st_size, "download meta"))

        if not db.exists():
            if gz.exists() or sqlite.exists():
                return Status("partial", f"{RSID_IDENTITY_GZ} not converted to DuckDB", facts, rows)
            return Status("partial" if rows else "missing",
                          f"no {RSID_IDENTITY_DB}", facts, rows)

        # Живой COUNT только если в манифесте нет rows (иначе check остаётся дешёвым).
        if not any(k == "Rows" for k, _ in facts):
            try:
                import duckdb
                with duckdb.connect(str(db), read_only=True) as con:
                    n = con.execute("SELECT COUNT(*) FROM rsid_identity").fetchone()[0]
                facts.insert(0, ("Rows", f"{int(n):,}"))
            except Exception as exc:
                return Status("partial", f"DuckDB unreadable: {exc}", facts, rows)

        total = sum(r.size or 0 for r in rows if r.ok and r.size)
        bits = [f"{v}" for k, v in facts if k == "Rows"]
        bits.append(human_size(total))
        return Status("ok", " · ".join(bits), facts, rows)

    def suggest(self, store: Store) -> Path:
        return store.path(self.key) or data_dir() / "rsid_identity"

    def download(self, path: Path, job: Job) -> str:
        import gzip

        import duckdb

        ensure_space(path, RSID_IDENTITY_GZ_SIZE + (2 << 30))
        path.mkdir(parents=True, exist_ok=True)
        gz = path / RSID_IDENTITY_GZ
        sqlite = path / RSID_IDENTITY_SQLITE
        db = self.db_file(path)
        db_part = path / (RSID_IDENTITY_DB + ".part")

        fetch(RSID_IDENTITY_URL, gz, job, RSID_IDENTITY_GZ)

        job.set_stage(f"decompressing {RSID_IDENTITY_GZ}", gz.stat().st_size, 0)
        sqlite.unlink(missing_ok=True)
        with gz.open("rb") as raw, gzip.GzipFile(fileobj=raw) as zin, sqlite.open("wb") as zout:
            while chunk := zin.read(1 << 20):
                job.check()
                zout.write(chunk)
                job.progress(raw.tell())

        db_part.unlink(missing_ok=True)
        job.set_stage("converting SQLite → DuckDB")
        con = duckdb.connect(str(db_part))
        try:
            con.execute("LOAD sqlite")
            con.execute(f"ATTACH '{_sql_path(sqlite)}' AS src (TYPE SQLITE, READ_ONLY)")
            job.check()
            con.execute("CREATE TABLE rsid_identity AS SELECT * FROM src.rsid_identity")
            job.check()
            con.execute("CREATE INDEX idx_rsid_identity_rsid ON rsid_identity (rsid)")
            con.execute("CREATE INDEX idx_rsid_identity_pos37 ON rsid_identity (chrom, pos37)")
            con.execute("CREATE INDEX idx_rsid_identity_pos38 ON rsid_identity (chrom, pos38)")
            rows = int(con.execute("SELECT COUNT(*) FROM rsid_identity").fetchone()[0])
            rsids = int(con.execute("SELECT COUNT(DISTINCT rsid) FROM rsid_identity").fetchone()[0])
            con.execute("DETACH src")
        finally:
            con.close()

        sqlite.unlink(missing_ok=True)
        db.unlink(missing_ok=True)
        db_part.replace(db)

        (path / RSID_IDENTITY_MANIFEST).write_text(json.dumps({
            "source": "allelix/allelix-rsid-identity",
            "page": RSID_IDENTITY_PAGE,
            "url": RSID_IDENTITY_URL,
            "gz": RSID_IDENTITY_GZ,
            "duckdb": RSID_IDENTITY_DB,
            "rows": rows,
            "rsids": rsids,
            "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        }, indent=2), encoding="utf-8")

        st = self.check(path)
        if st.state != "ok":
            raise RuntimeError(st.summary)
        return st.summary

    def verify(self, path: Path, job: Job) -> str:
        import duckdb

        db = self.db_file(path)
        if not db.is_file():
            raise RuntimeError(f"no {RSID_IDENTITY_DB} (download via D first)")
        job.set_stage("checking DuckDB table")
        with duckdb.connect(str(db), read_only=True) as con:
            cols = [r[0] for r in con.execute("DESCRIBE rsid_identity").fetchall()]
            need = {"rsid", "chrom", "pos37", "pos38"}
            missing = sorted(need - set(cols))
            if missing:
                raise RuntimeError("DuckDB missing columns: " + ", ".join(missing))
            rows = int(con.execute("SELECT COUNT(*) FROM rsid_identity").fetchone()[0])
            both = int(con.execute(
                "SELECT COUNT(*) FROM rsid_identity WHERE pos37 <> pos38"
            ).fetchone()[0])
        if rows < 1_000_000:
            raise RuntimeError(f"too few rows: {rows:,}")
        return f"{rows:,} rows · {both:,} with pos37≠pos38"


def rsid_identity_db(store: Store) -> Path | None:
    """Путь к готовому DuckDB (или None, если F2-источник не настроен / не сконверчен)."""
    folder = store.path("rsid_identity")
    if folder is None:
        return None
    db = folder / RSID_IDENTITY_DB
    return db if db.is_file() else None


# ───────────────────────── GWAS Catalog (EBI bulk release) ─────────────────────────

GWAS_PAGE = "https://www.ebi.ac.uk/gwas/"
GWAS_FTP = "https://ftp.ebi.ac.uk/pub/databases/gwas/releases/latest"
GWAS_STATS = "https://www.ebi.ac.uk/gwas/api/search/stats"
GWAS_MANIFEST = "gwas-download-manifest.json"
# Core curated tables from the published “latest” FTP snapshot.
GWAS_FILES: tuple[tuple[str, str], ...] = (
    ("gwas-catalog-associations_ontology-annotated-full.zip", "associations"),
    ("gwas-catalog-studies.tsv", "studies"),
    ("gwas-efo-trait-mappings.tsv", "EFO mappings"),
)


def _gwas_line_count(path: Path, *, limit: int | None = None) -> int:
    n = 0
    with path.open("rb") as fh:
        for _ in fh:
            n += 1
            if limit is not None and n > limit:
                break
    return n


def _gwas_extract_zip(zip_path: Path, dest_dir: Path, job: Job) -> Path:
    """Unpack the first .tsv from a GWAS zip into dest_dir; return that path."""
    job.set_stage(f"extracting · {zip_path.name}")
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".tsv") and not n.endswith("/")]
        if not names:
            raise RuntimeError(f"{zip_path.name}: no .tsv inside the archive")
        member = names[0]
        out = dest_dir / Path(member).name
        if out.exists() and out.stat().st_size > 0:
            return out
        with zf.open(member) as src, out.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    return out


class GwasCatalog(Source):
    key = "gwas_catalog"
    title = "GWAS Catalog"
    url = GWAS_PAGE
    about = ("NHGRI-EBI GWAS Catalog bulk release: curated SNP–trait associations, "
             "studies, and EFO trait mappings. Downloaded from the official FTP "
             "“latest” snapshot (not full summary statistics). Feeds F5 genome × "
             "catalog match. Not a scoring engine — that is F4 PGS.")
    size_hint = "~150–200 MB (associations zip + studies + EFO tables)"
    can_download = True
    can_verify = True
    download_note = ("D fetches the latest FTP snapshot here (associations, studies, "
                     "EFO mappings) for F5. Or P to an existing GWAS Catalog folder.")

    def associations_tsv(self, path: Path) -> Path | None:
        # FTP zip member names drift (…associations…, …download-associations-alt…).
        preferred = (
            "gwas-catalog-associations_ontology-annotated.tsv",
            "gwas-catalog-associations-full.tsv",
            "gwas-catalog-associations.tsv",
            "gwas-catalog-download-associations-alt-full.tsv",
            "gwas-catalog-download-associations-full.tsv",
        )
        for name in preferred:
            p = path / name
            if p.is_file() and p.stat().st_size > 0:
                return p
        cands = [
            p for p in path.glob("gwas-catalog*association*.tsv")
            if p.is_file() and p.stat().st_size > 0
        ]
        if not cands:
            return None
        # Prefer the largest table (full dump over tiny stubs).
        return max(cands, key=lambda p: p.stat().st_size)

    def _check(self, path: Path) -> Status:
        if not path.is_dir():
            return Status("missing", "folder not found")
        assoc = self.associations_tsv(path)
        studies = path / "gwas-catalog-studies.tsv"
        efo = path / "gwas-efo-trait-mappings.tsv"
        zip_assoc = path / "gwas-catalog-associations_ontology-annotated-full.zip"
        files: list[FileRow] = []
        for p, note in (
            (assoc, "associations"),
            (studies if studies.is_file() else None, "studies"),
            (efo if efo.is_file() else None, "EFO mappings"),
            (zip_assoc if zip_assoc.is_file() else None, "associations zip"),
        ):
            if p is not None:
                files.append(FileRow(p.name, p.stat().st_size, note))
        files.extend(leftovers(path))
        man = path / GWAS_MANIFEST
        release = ""
        if man.is_file():
            try:
                release = str(json.loads(man.read_text(encoding="utf-8")).get("catalog_date") or "")
            except (OSError, ValueError, TypeError):
                release = ""
            files.append(FileRow(GWAS_MANIFEST, man.stat().st_size, "download manifest"))

        have_assoc = assoc is not None
        have_studies = studies.is_file() and studies.stat().st_size > 0
        have_efo = efo.is_file() and efo.stat().st_size > 0
        facts = [
            ("Release date", release or "— (local files only)"),
            ("Associations", human_size(assoc.stat().st_size) if have_assoc else "missing"),
            ("Studies", human_size(studies.stat().st_size) if have_studies else "missing"),
            ("EFO mappings", human_size(efo.stat().st_size) if have_efo else "missing"),
        ]
        if not have_assoc and not have_studies and not have_efo:
            return Status("missing", "no GWAS Catalog tables — press D to download", facts, files)
        if not (have_assoc and have_studies and have_efo):
            missing = [n for n, ok in (("associations", have_assoc),
                                       ("studies", have_studies),
                                       ("EFO", have_efo)) if not ok]
            return Status("partial", f"incomplete · missing {', '.join(missing)}", facts, files)
        label = f"GWAS Catalog · {release}" if release else "GWAS Catalog tables"
        return Status("ok", label, facts, files)

    def suggest(self, store: Store) -> Path:
        return store.path(self.key) or data_dir() / "gwas"

    def _remote_stats(self, job: Job) -> dict:
        job.set_stage("GWAS Catalog · release stats")
        req = urllib.request.Request(
            GWAS_STATS, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
        if not isinstance(data, dict) or not data.get("date"):
            raise RuntimeError("GWAS Catalog stats API returned no release date")
        return data

    def download(self, path: Path, job: Job) -> str:
        stats = self._remote_stats(job)
        path.mkdir(parents=True, exist_ok=True)
        # ~250 MB headroom for zip + extracted tables.
        ensure_space(path, 300 << 20)
        results: dict[str, str] = {}
        for name, label in GWAS_FILES:
            url = f"{GWAS_FTP}/{name}"
            dest = path / name
            results[name] = fetch(url, dest, job, label)
            if name.endswith(".zip"):
                _gwas_extract_zip(dest, path, job)
        (path / GWAS_MANIFEST).write_text(json.dumps({
            "source": "NHGRI-EBI GWAS Catalog",
            "page": GWAS_PAGE,
            "ftp": GWAS_FTP,
            "catalog_date": stats.get("date"),
            "stats": {k: stats.get(k) for k in (
                "date", "studies", "associations", "snps", "sumstats",
                "ensemblbuild", "dbsnpbuild", "genebuild", "efoversion")},
            "files": [name for name, _ in GWAS_FILES],
            "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
            "download_result": results,
        }, indent=2), encoding="utf-8")
        st = self.check(path)
        if st.state != "ok":
            raise RuntimeError(st.summary)
        return (f"{st.summary} · {stats.get('studies')} studies · "
                f"{stats.get('associations')} associations (catalog stats)")

    def verify(self, path: Path, job: Job) -> str:
        job.set_stage("checking GWAS Catalog tables")
        st = self._check(path)
        if st.state != "ok":
            raise RuntimeError(st.summary)
        assoc = self.associations_tsv(path)
        assert assoc is not None
        with assoc.open("r", encoding="utf-8", errors="replace") as fh:
            header = fh.readline()
        if "SNPS" not in header.upper() and "SNP" not in header.upper():
            # Ontology-annotated export still has an SNP / rs id column under varying names.
            if "RSID" not in header.upper() and "VARIANT" not in header.upper():
                raise RuntimeError(f"{assoc.name}: unexpected header (no SNP/rs column)")
        n_assoc = _gwas_line_count(assoc) - 1
        n_studies = _gwas_line_count(path / "gwas-catalog-studies.tsv") - 1
        return (f"OK · {n_assoc:,} association rows · {n_studies:,} study rows · "
                f"{human_size(assoc.stat().st_size)}")


# ───────────────────────── PGS Catalog ─────────────────────────

class PgsModels(Source):
    key = "pgs_models"
    title = "PGS Catalog models"
    url = "https://www.pgscatalog.org"
    about = ("Local catalog of polygenic scores from the PGS Catalog API: themed traits "
             "(intelligence, sleep, brain, appearance, body, fitness, behavior, diet), "
             "clinical phenotypes kept as excluded_disease, plus coverage vs your F1 genome. "
             "Files: catalog/catalog.tsv and data/PGS*.txt.gz. F4 scores them against 1000 Genomes.")
    size_hint = "API metadata cache + ~1–2 MB per weight file; first build can take a while"
    can_download = True
    needs_genome = True
    download_note = ("D builds/updates the catalog here (API metadata + weights + coverage vs F1). "
                     "Or P to an existing catalog folder.")

    def _check(self, path: Path) -> Status:
        if not path.is_dir():
            return Status("missing", "folder not found")
        cat = pgs.catalog_path(path)
        if not cat.exists():
            return Status("missing", "catalog/catalog.tsv not found — press D to build")
        # Полный индекс (любой status), не только ok — для DISEASES / LOW COV вкладок F4.
        index = pgs.read_catalog_index(path)
        models = list(index.values())
        ok = [m for m in models if m.status == "ok"]
        have = [m for m in ok if pgs.weights_path(path, m.pgs_id).exists()]
        by_status = Counter(m.status or "?" for m in models)
        by_theme = Counter(m.theme for m in ok)
        facts = [
            ("Rows in catalog", f"{len(models):,}"),
            ("Status ok", f"{len(ok):,}"),
            ("Weight files (ok)", f"{len(have):,} of {len(ok):,}"),
            ("Statuses", " · ".join(f"{s} {n}" for s, n in by_status.most_common(6))),
            ("Ok by theme", " · ".join(
                f"{pgs.THEME_LABEL.get(t, t)} {n}" for t, n in by_theme.items()) or "—"),
            ("Catalog", mtime_label(cat)),
        ]
        info = pgs.panel_info()
        facts.append(("Reference panel", f"{info['variants']:,} SNPs from {info['models']} models, built "
                      f"{info['built'].replace('T', ' ')}" if info else
                      "not built yet; F4 builds it on the first run (needs 1000 Genomes)"))
        files = [FileRow("catalog/catalog.tsv", cat.stat().st_size, mtime_label(cat))]
        meta = path / "catalog" / "scores_all.json"
        if meta.exists():
            files.append(FileRow("catalog/scores_all.json", meta.stat().st_size, "API metadata cache"))
        missing = len(ok) - len(have)
        if not ok:
            return Status("partial", f"{len(models)} rows · no status=ok yet", facts, files)
        if missing:
            files.append(FileRow(f"{missing} weight file(s)", None, "missing in data/", False))
            return Status("partial", f"{len(ok)} ok · {missing} without weights", facts, files)
        return Status("ok", f"{len(ok)} ok · {len(by_theme)} themes · {len(models)} total", facts, files)

    def suggest(self, store: Store) -> Path:
        return store.path(self.key) or data_dir() / "pgs"

    def download(self, path: Path, job: Job) -> str:
        from . import pgs_catalog
        path.mkdir(parents=True, exist_ok=True)
        return pgs_catalog.build(path, job)


# ───────────────────────── Seshat (Polaris snapshot) ─────────────────────────

SESHAT_PAGE = "https://seshatdatabank.info/data"
SESHAT_GITHUB_API = (
    "https://api.github.com/repos/Seshat-Global-History-Databank/"
    "build_polaris_dataset/contents"
)
SESHAT_POLARIS = "Polaris2026.xlsx"
SESHAT_MANIFEST = "seshat-download-manifest.json"


class Seshat(Source):
    key = "seshat"
    title = "Seshat · Polaris"
    url = SESHAT_PAGE
    about = ("Seshat Global History Databank snapshot (Polaris): polities with coded "
             "social complexity, warfare, and religion variables across deep history. "
             "F2 downloads the curated Excel release from the official GitHub mirror. "
             "License: CC BY-NC (non-commercial). For LORE / culture briefs — not a Y tree.")
    size_hint = "~3 MB Excel (Polaris sheets)"
    can_download = True
    can_verify = True

    def workbook(self, path: Path) -> Path | None:
        p = path / SESHAT_POLARIS
        return p if p.is_file() and p.stat().st_size > 0 else None

    def _sheet_names(self, xlsx: Path) -> list[str]:
        """Имена листов из Office Open XML без openpyxl."""
        try:
            with zipfile.ZipFile(xlsx) as zf:
                xml = zf.read("xl/workbook.xml").decode("utf-8", errors="replace")
        except (OSError, KeyError, zipfile.BadZipFile):
            return []
        return re.findall(r'<sheet[^>]*\bname="([^"]+)"', xml)

    def _check(self, path: Path) -> Status:
        if not path.is_dir():
            return Status("missing", "folder not found")
        rows = leftovers(path)
        book = self.workbook(path)
        facts: list[tuple[str, str]] = []
        man = path / SESHAT_MANIFEST
        if man.exists():
            try:
                data = json.loads(man.read_text(encoding="utf-8"))
                if data.get("release"):
                    facts.append(("Release", str(data["release"])))
                if data.get("github_sha"):
                    facts.append(("GitHub SHA", str(data["github_sha"])[:12]))
                if data.get("downloaded_at_utc"):
                    facts.append(("Downloaded",
                                  str(data["downloaded_at_utc"])[:19].replace("T", " ") + " UTC"))
                if data.get("license"):
                    facts.append(("License", str(data["license"])))
            except (ValueError, TypeError, OSError):
                pass
        if not book:
            if rows:
                return Status("partial", "download incomplete", facts, rows)
            return Status("missing", f"no {SESHAT_POLARIS}", facts, rows)
        size = book.stat().st_size
        sheets = self._sheet_names(book)
        if sheets:
            facts.insert(0, ("Sheets", f"{len(sheets)} · " + ", ".join(sheets[:6])
                             + ("…" if len(sheets) > 6 else "")))
        note = "OOXML workbook" if sheets else "Excel file"
        rows.insert(0, FileRow(book.name, size, note))
        if man.exists():
            rows.append(FileRow(man.name, man.stat().st_size, "download meta"))
        if not sheets:
            return Status("partial", f"{book.name} is not a readable xlsx", facts, rows)
        return Status("ok", f"{len(sheets)} sheets · {human_size(size)}", facts, rows)

    def suggest(self, store: Store) -> Path:
        return store.path(self.key) or data_dir() / "seshat"

    def _remote(self, job: Job) -> dict:
        job.set_stage(f"querying GitHub for {SESHAT_POLARIS}")
        req = urllib.request.Request(
            f"{SESHAT_GITHUB_API}/{SESHAT_POLARIS}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
        if not isinstance(data, dict) or not data.get("download_url"):
            raise RuntimeError(f"GitHub did not return download_url for {SESHAT_POLARIS}")
        return data

    def download(self, path: Path, job: Job) -> str:
        remote = self._remote(job)
        url = str(remote["download_url"])
        size = int(remote["size"]) if isinstance(remote.get("size"), int) else None
        sha = str(remote.get("sha") or "")
        ensure_space(path, (size or (4 << 20)) + (8 << 20))
        path.mkdir(parents=True, exist_ok=True)
        dest = path / SESHAT_POLARIS
        res = fetch(url, dest, job, SESHAT_POLARIS, size)
        (path / SESHAT_MANIFEST).write_text(json.dumps({
            "source": "Seshat Global History Databank",
            "page": SESHAT_PAGE,
            "release": "Polaris-2026",
            "file": SESHAT_POLARIS,
            "github_repo": "Seshat-Global-History-Databank/build_polaris_dataset",
            "github_sha": sha or None,
            "expected_size_bytes": size,
            "license": "CC BY-NC",
            "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
            "download_result": res,
        }, indent=2), encoding="utf-8")
        st = self.check(path)
        if st.state != "ok":
            raise RuntimeError(st.summary)
        return st.summary

    def verify(self, path: Path, job: Job) -> str:
        book = self.workbook(path) if path.is_dir() else None
        if not book:
            raise RuntimeError(f"no {SESHAT_POLARIS} to verify")
        job.set_stage(f"checking {SESHAT_POLARIS}")
        with book.open("rb") as fh:
            magic = fh.read(4)
        if magic[:2] != b"PK":
            raise RuntimeError(f"{SESHAT_POLARIS}: not a ZIP/OOXML workbook")
        sheets = self._sheet_names(book)
        if not sheets:
            raise RuntimeError(f"{SESHAT_POLARIS}: no sheets in workbook.xml")
        man = path / SESHAT_MANIFEST
        if man.exists():
            try:
                data = json.loads(man.read_text(encoding="utf-8"))
                exp = data.get("expected_size_bytes")
                if isinstance(exp, int) and book.stat().st_size != exp:
                    raise RuntimeError(
                        f"size {book.stat().st_size:,} ≠ manifest {exp:,}")
            except (ValueError, TypeError, OSError):
                pass
        return f"OK · {len(sheets)} sheets · {human_size(book.stat().st_size)}"


class Gemini(Source):
    key = "gemini"
    title = "Google Gemini API"
    kind = "secret"
    url = "https://aistudio.google.com/apikey"
    about = ("LLM for F7 Chat, F3 Tree ASK, F5 GWAS ASK, F6 Reference SUMMARY. "
             "Press P: AI Studio API key (optional), Vertex Cloud project, "
             "and path to ADC JSON (application_default_credentials.json). "
             "Channel: F7 · U. Vertex uses the ADC file — not an API key.")
    size_hint = "AI Studio quota · or Vertex grant via ADC"
    download_note = "Press P — key (Studio) / project + ADC file (Vertex)."

    def check(self, path: Path | None, store: Store | None = None,
              *, enrich: bool = True) -> Status:
        del enrich  # secrets have no Discover/Haplotree enrich step
        from . import llm
        from . import chat as chat_mod
        mode = llm.provider_mode(store)
        ok, detail = llm.provider_ready(store)
        key = llm.studio_api_key(store)
        project = llm.vertex_project(store)
        adc = llm.vertex_adc_path(store)
        saved = chat_mod.get_saved_model(store, mode) if store else ""
        model = chat_mod.short_model(saved) if saved else chat_mod.short_model(
            llm.default_model_for(mode)
        )
        if adc and adc.is_file():
            adc_note = str(adc)
        elif adc:
            adc_note = f"missing · {adc}"
        else:
            adc_note = f"not set · default {llm.default_adc_path()}"
        facts = [
            ("Channel", llm.provider_label(mode)),
            ("Status", detail),
            ("AI Studio key", f"…{key[-4:]}" if key else "not set (F2 · P)"),
            ("Cloud project", project or "not set (F2 · P)"),
            ("ADC file", adc_note),
            ("Model", model),
        ]
        if mode == "aistudio":
            facts.append(("Tools", "Google Search"))
        else:
            facts.append(("Auth", "ADC file (no API key)"))
        if key or project or (adc and adc.is_file()):
            return Status("ok" if ok else "partial", detail, facts)
        return Status("unset", "set key / project / ADC path with P", facts)


# ───────────────────────── Open Humans (public samples) ─────────────────────────

class OpenHumans(Source):
    key = "openhumans"
    title = "Open Humans · public"
    url = "https://www.openhumans.org/api/public-data/?source=direct-sharing-354"
    about = ("Public Open Humans datafiles (project direct-sharing-354, ~14k catalog rows). "
             "Most rows are wearables/surveys — filter to chip raw. "
             "● READY / SUPPORTED = 23andMe / AncestryDNA original .txt (F1 parses). "
             "○ NEED / NOT YET = FTDNA CSV, Big Y, VCF, zip — labeled, still downloadable. "
             "Catalog is F9 Samples (also B from F2). D on a row (or Space-marked set) "
             "fetches that genome → DuckDB (READY also → genome ingest). "
             "V: API new items + missing under filter. "
             "Respect OH goodwill / CC0 sharing norms — selective download, not a dump.")
    size_hint = "catalog on disk · samples vary (chip files ~5–30 MB)"
    can_download = True
    can_verify = True
    download_note = "F9 / B → catalog; D on a row downloads that genome (any compat)."

    def suggest(self, store: Store) -> Path:
        from .openhumans import default_samples_dir
        return store.path(self.key) or default_samples_dir()

    def _check(self, path: Path) -> Status:
        from . import openhumans as oh
        from . import openhumans_db as ohdb
        cat = oh.load_catalog()
        rows = leftovers(path) if path.is_dir() else []
        facts: list[tuple[str, str]] = []
        # Do NOT sync 14k rows here — that locked the app DB for minutes.
        # DuckDB sync runs on F9 V / browse update / verify job only.
        try:
            n_db, _ = ohdb.catalog_synced()
            if cat.n and n_db < cat.n:
                facts.append(("DuckDB", f"{n_db:,}/{cat.n:,} · V / F9 to sync new"))
        except Exception as exc:  # noqa: BLE001
            facts.append(("DuckDB", f"unavailable · {exc}"))
        if cat.n:
            counts = cat.counts()
            ready = counts.get(f"compat:{oh.COMPAT_READY}", 0)
            need = counts.get(f"compat:{oh.COMPAT_NEED}", 0)
            peek = counts.get(f"compat:{oh.COMPAT_PEEK}", 0)
            skip = counts.get(f"compat:{oh.COMPAT_SKIP}", 0)
            facts.append(("Catalog", f"{cat.n:,} files"
                          + (f" · claimed {cat.claimed:,}" if cat.claimed else "")))
            facts.append(("Compat",
                          f"● {ready:,} READY · ○ {need:,} NEED · "
                          f"△ {peek:,} PEEK · ✖ {skip:,} SKIP"))
            facts.append(("Chip raw",
                          f"{counts.get('kind:chip', 0):,} · "
                          f"23andMe {counts.get('vendor:23andme', 0):,} · "
                          f"Ancestry {counts.get('vendor:ancestry', 0):,} · "
                          f"FTDNA {counts.get('vendor:ftdna', 0):,}"))
            if cat.catalogued_at:
                facts.append(("Catalogued",
                              cat.catalogued_at[:19].replace("T", " ") + " UTC"))
            facts.append(("Catalog dir", str(oh.catalog_dir())))
        else:
            facts.append(("Catalog", f"missing · expected {oh.catalog_jsonl()}"))
        try:
            st = ohdb.stats()
            facts.append(("DuckDB",
                          f"{st['catalog']:,} rows · {st['local']:,} local · "
                          f"{st['ingested']:,} ingested · {st['error']:,} error"))
        except Exception:  # noqa: BLE001
            pass
        local_n = 0
        if path.is_dir():
            ids = oh.local_ids(path)
            local_n = len(ids)
            facts.append(("Local samples", f"{local_n:,} file(s) with id_ prefix"))
            for p in sorted(path.iterdir())[:12]:
                if p.is_file() and not p.name.endswith(".part"):
                    rows.append(FileRow(p.name, p.stat().st_size, "sample"))
            if local_n > 12:
                rows.append(FileRow(f"… +{local_n - 12} more", None, "samples"))
        if not cat.n:
            return Status("missing", "catalog.jsonl not found (run scripts/catalog_openhumans.py)",
                          facts, rows)
        if not path.is_dir():
            return Status("partial", f"catalog {cat.n:,} · samples folder missing", facts, rows)
        try:
            st = ohdb.stats()
            if st["ingested"] or local_n:
                return Status(
                    "ok",
                    f"catalog {cat.n:,} · {local_n:,} local · "
                    f"{st['ingested']:,} in DuckDB/genome",
                    facts, rows,
                )
        except Exception:  # noqa: BLE001
            if local_n:
                return Status("ok", f"catalog {cat.n:,} · {local_n:,} local", facts, rows)
        return Status("partial", f"catalog {cat.n:,} · no local samples yet (B to browse)",
                      facts, rows)

    def download(self, path: Path, job: Job) -> str:
        """Not used for bulk dump — browser calls download_files on marked rows."""
        raise NotImplementedError("Use B browse → mark → D")

    def verify(self, path: Path, job: Job) -> str:
        """Refresh catalog API for new ids, then download missing chip samples + DuckDB."""
        from . import openhumans_db as ohdb
        from .store import Store
        path.mkdir(parents=True, exist_ok=True)
        return ohdb.update_new_items(path, job, store=Store())


SOURCES: list[Source] = [FtdnaDiscover(), FtdnaHaplotree(), YfullYtree(), Theytree(),
                         Aadr(), ThousandGenomes(), RsidIdentity(), Seshat(),
                         OpenHumans(), GwasCatalog(), PgsModels(), Gemini()]
BY_KEY = {s.key: s for s in SOURCES}

# Группы левого меню F2: (заголовок, ключи источников по порядку).
SOURCE_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("Y-DNA trees", ("ftdna_discover", "ftdna_haplotree", "yfull_ytree", "theytree")),
    ("Reference panels", ("aadr", "1000g", "rsid_identity")),
    ("History & lore", ("seshat",)),
    ("Public samples", ("openhumans",)),
    ("GWAS & polygenic", ("gwas_catalog", "pgs_models")),
    ("AI", ("gemini",)),
]
