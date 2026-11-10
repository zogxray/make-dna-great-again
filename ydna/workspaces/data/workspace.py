"""Режим Data: источники данных — путь, состояние, скачивание и проверка в фоне."""

from __future__ import annotations

import os
import time
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ...core.jobs import JOBS, Job
from ...core import sources_db
from ...core.sources import BY_KEY, SOURCE_GROUPS, SOURCES, Source, Status, human_size
from ...core.store import Store
from ...core.ylink import (FETCH_STAGES, active_genome, fetch_cost, fetch_plan,
                           stage_fetch_names)
from ..base import KeyBar, ModeBar, Workspace, alt_keys, click, fit, short_path
from .screens import PathScreen, TokenScreen

from ...theme.styles import DIM, JOB_STYLE, STATE_ICON_STYLE

PROMPT_W = 36                # #src-box 42 минус рамка, отступы и значок

STATE_ICON = {k: (glyph, STATE_ICON_STYLE[k]) for k, glyph in
              (("ok", "✔"), ("partial", "◐"), ("missing", "✖"), ("unset", "○"))}
STATE_LABEL = {"ok": "READY", "partial": "INCOMPLETE", "missing": "NOT FOUND", "unset": "PATH NOT SET"}
LINKED = {"ftdna_discover", "ftdna_haplotree"}

# Facts written only by Source.enrich (Discover ↔ Haplotree coverage / fetch plan).
# Basic check(enrich=False) must not wipe these — otherwise F2 re-runs ~8s enrich every open.
_ENRICH_FACT_KEYS = {
    "Haplotree coverage",
    "Not in haplotree",
    "Fetch stages",
    "Your Y markers",
    "Not in Discover",
    "Downloaded from Discover",
}


def _is_enrich_fact(key: str) -> bool:
    return key in _ENRICH_FACT_KEYS or key.startswith("  ")


def _has_enrich_facts(st: Status) -> bool:
    return any(_is_enrich_fact(k) for k, _ in st.facts)


def _preserve_enrich(prev: Status | None, st: Status) -> Status:
    """Keep last coverage block when a light check replaces the Status."""
    if prev is None or st.coverage is not None:
        return st
    if prev.coverage is None and not _has_enrich_facts(prev):
        return st
    st.coverage = prev.coverage
    enrich = [(k, v) for k, v in prev.facts if _is_enrich_fact(k)]
    basic = [(k, v) for k, v in st.facts if not _is_enrich_fact(k)]
    insert_at = min(2, len(basic))
    st.facts = basic[:insert_at] + enrich + basic[insert_at:]
    # Enriched summary (percents / stages left) lives on prev until next real enrich.
    st.summary = prev.summary
    return st


def _grouped_sources() -> list[Source]:
    """SOURCES в порядке групп F2 (на случай рассинхрона с плоским списком)."""
    out: list[Source] = []
    seen: set[str] = set()
    for _, keys in SOURCE_GROUPS:
        for key in keys:
            if key in BY_KEY and key not in seen:
                out.append(BY_KEY[key])
                seen.add(key)
    for s in SOURCES:
        if s.key not in seen:
            out.append(s)
    return out


def duration(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m {sec % 60:02d}s"
    return f"{sec // 3600}h {sec % 3600 // 60:02d}m"


def amount(job: Job, n: float) -> str:
    return human_size(n) if job.unit == "B" else f"{n:,.0f}"


def job_line(job: Job) -> str:
    if job.fraction is not None:
        s = f"{job.fraction:.0%}"
    elif job.done:
        s = amount(job, job.done)
    else:
        s = "…"
    if job.rate:
        s += f" · {human_size(job.rate)}/s" if job.unit == "B" else f" · {job.rate * 60:.0f}/min"
    if job.eta is not None:
        s += f" · {duration(job.eta)} left"
    return s


def job_icon(job: Job) -> str:
    return "⇣" if job.stage.startswith(("downloading", "retry")) else "⚙"


def bar(frac: float, width: int) -> str:
    full = int(frac * width)
    return "█" * full + ("▌" if frac * width - full >= 0.5 else "")


class DataWorkspace(Workspace):
    mode_key = "data"
    DEFAULT_CSS = """
    #main { height: 1fr; }
    #src-box { width: 42; border: round $primary; }
    #det-box { width: 1fr; border: round $primary; }
    #act-box { width: 36; min-width: 32; border: round $primary; }
    #src-box:focus-within, #det-box:focus-within, #act-box:focus-within { border: round $accent; }
    #sources { height: 1fr; border: none; background: transparent; }
    #det-scroll, #act-scroll { height: 1fr; scrollbar-size-vertical: 1; }
    #details, #actions { padding: 0 1; }
    #help { height: 1; padding: 0 1; color: $text-muted; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """
    BINDINGS = [
        Binding(alt_keys("p"), "set_path", "Set path"),
        Binding(alt_keys("d"), "download", "Download / fetch stage 1"),
        Binding(alt_keys("b"), "browse_openhumans", "Browse OH catalog", show=False),
        Binding("1", "fetch_stage('our')", "Fetch 1 Our", show=False),
        Binding("2", "fetch_stage('children')", "Fetch 2 Children", show=False),
        Binding("3", "fetch_stage('genome')", "Fetch 3 This genome", show=False),
        Binding("4", "fetch_stage('all_genomes')", "Fetch 4 All genomes", show=False),
        Binding("5", "fetch_stage('tree')", "Fetch 5 Whole tree", show=False),
        Binding(alt_keys("a"), "fetch_stage('tree')", "Fetch whole tree", show=False),
        Binding(alt_keys("v"), "verify", "Verify"),
        Binding(alt_keys("r"), "recheck", "Recheck"),
        Binding(alt_keys("c"), "cancel_job", "Cancel"),
        Binding("delete", "clear_path", "Forget path", show=False),
    ]

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.status: dict[str, Status] = {}
        self.checking: set[str] = set()
        self.finished: set[int] = {id(j) for j in JOBS.items.values() if not j.running}
        self.armed = 0.0
        self.genome = self._active()
        self._refresh_gen = 0
        self._enriching: set[str] = set()
        # LINKED keys that must recompute coverage (genome/path change). Empty = reuse DB facts.
        self._enrich_dirty: set[str] = set()

    def _active(self) -> Path | None:
        """Активный файл F1: из GenomeWorkspace, иначе genome.files."""
        ws = getattr(self.app, "genome_ws", None)
        if ws and ws.active and ws.active.exists():
            return ws.active
        return active_genome()

    # — компоновка —
    def compose(self) -> ComposeResult:
        yield ModeBar(self.mode_key)
        with Horizontal(id="main"):
            with Vertical(id="src-box"):
                yield OptionList(id="sources")
            with Vertical(id="det-box"):
                with VerticalScroll(id="det-scroll"):
                    yield Static(id="details")
            with Vertical(id="act-box"):
                with VerticalScroll(id="act-scroll"):
                    yield Static(id="actions")
        yield KeyBar(self._key_items(), id="help")
        yield Static(id="status")

    def _key_items(self) -> list[tuple[str, str, str | None]]:
        s = None
        try:
            s = self.source
        except Exception:
            pass
        if s and s.key == "openhumans":
            return [("↑↓", "Source", None), ("P", "Samples folder", "screen.set_path"),
                    ("B", "Open F8 catalog", "screen.browse_openhumans"),
                    ("V", "Update new", "screen.verify"),
                    ("R", "Recheck", "screen.recheck"), ("C", "Cancel task", "screen.cancel_job"),
                    ("Del", "Forget path", "screen.clear_path"), ("Tab", "Panel", "app.focus_next"),
                    ("F1–F9", "Modes", None), ("Q", "Quit", "app.quit")]
        return [("↑↓", "Source", None), ("P", "Set path", "screen.set_path"),
                ("D/1–5", "Fetch stage", "screen.download"),
                ("V", "Verify MD5", "screen.verify"),
                ("R", "Recheck", "screen.recheck"), ("C", "Cancel task", "screen.cancel_job"),
                ("Del", "Forget path", "screen.clear_path"), ("Tab", "Panel", "app.focus_next"),
                ("F1–F9", "Modes", None), ("Q", "Quit", "app.quit")]

    def _menu_options(self) -> list[Option]:
        """Опции левого меню: disabled-заголовки групп + источники."""
        options: list[Option] = []
        for title, keys in SOURCE_GROUPS:
            head = Text()
            head.append(f"▸ {title}", style="bold #D4AD72")
            options.append(Option(head, id=f"grp:{title}", disabled=True))
            for key in keys:
                s = BY_KEY.get(key)
                if s:
                    options.append(Option(self._prompt(s), id=s.key))
        # Источники вне групп (на всякий случай).
        grouped = {k for _, keys in SOURCE_GROUPS for k in keys}
        for s in SOURCES:
            if s.key not in grouped:
                options.append(Option(self._prompt(s), id=s.key))
        return options

    def _first_source_index(self, ol: OptionList) -> int:
        for i, opt in enumerate(ol.options):
            if not opt.disabled and opt.id and not str(opt.id).startswith("grp:"):
                return i
        return 0

    def on_mount(self) -> None:
        self.genome = self._active()
        self.query_one("#src-box").border_title = "SOURCES"
        self.query_one("#det-box").border_title = "DETAILS"
        self.query_one("#act-box").border_title = "ACTIONS"
        self.query_one("#act-box").border_subtitle = "click or key"
        # Instant paint from DuckDB; heavy disk checks run in the background.
        try:
            self.store.warm_paths()
            self._hydrate_status_from_db()
        except Exception:  # noqa: BLE001
            pass
        ol = self.query_one("#sources", OptionList)
        ol.add_options(self._menu_options())
        ol.highlighted = self._first_source_index(ol)
        ol.focus()
        self.refresh_all()
        for s in _grouped_sources():
            self.check(s, enrich=False)
        self.set_interval(0.5, self._tick)

    @property
    def source(self) -> Source:
        ol = self.query_one("#sources", OptionList)
        opt = ol.highlighted_option
        if opt and opt.id and not str(opt.id).startswith("grp:") and opt.id in BY_KEY:
            return BY_KEY[opt.id]
        # Подсветка на заголовке группы — ближайший источник ниже/выше.
        idx = ol.highlighted if ol.highlighted is not None else 0
        for i in list(range(idx, len(ol.options))) + list(range(idx - 1, -1, -1)):
            o = ol.options[i]
            if not o.disabled and o.id and o.id in BY_KEY:
                return BY_KEY[o.id]
        return SOURCES[0]

    def _hydrate_status_from_db(self) -> None:
        """Show last-known F2 rows immediately (no disk scan)."""
        for row in sources_db.list_sources():
            key = str(row["key"])
            state = str(row.get("state") or "unset")
            if state == "unset" and not row.get("path"):
                continue
            facts = list(row.get("facts") or [])
            self.status[key] = Status(
                state,  # type: ignore[arg-type]
                str(row.get("summary") or state),
                facts,
            )

    # — проверка состояния —
    def check(self, src: Source, *, enrich: bool = False) -> None:
        self.checking.add(src.key)
        path = self.store.path(src.key)

        def work() -> None:
            st = src.check(path, self.store, enrich=enrich)
            self.app.call_from_thread(self._checked, src.key, st)
        self.run_worker(work, thread=True, group=f"check-{src.key}", exclusive=True)

    def _checked(self, key: str, st: Status) -> None:
        self.checking.discard(key)
        # Light checks must not erase Discover/Haplotree coverage from hydrate / last enrich.
        if key in LINKED:
            st = _preserve_enrich(self.status.get(key), st)
        self.status[key] = st
        src = BY_KEY.get(key)
        if src:
            try:
                sources_db.upsert_status(src, st, self.store.path(key))
            except Exception:
                pass
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        """Coalesce many check completions into one UI pass (keeps F2 responsive)."""
        self._refresh_gen += 1
        gen = self._refresh_gen
        self.set_timer(0.12, lambda g=gen: self._refresh_if_current(g))

    def _refresh_if_current(self, gen: int) -> None:
        if gen != self._refresh_gen or not self.is_mounted:
            return
        self.refresh_all()

    def _tick(self) -> None:
        changed = False
        # слот + архив ·h* / очередь ·q* для этого источника
        for s in SOURCES:
            related = [j for j in JOBS.items.values()
                       if j.key == s.key or (j.slot or "") == s.key]
            for job in related:
                if job.active or job.status == "queued" or id(job) in self.finished:
                    continue
                self.finished.add(id(job))
                changed = True
                sev = {"done": "information", "failed": "error"}.get(job.status, "warning")
                self.app.notify(f"{job.title}: {job.message}", severity=sev, timeout=8)
                self.check_related(s)
                if job.status == "done" or s.can_fetch:
                    self.app.source_changed(s.key)
        live = any(j.active or j.status == "queued" for j in JOBS.items.values())
        if changed or live:
            self.refresh_all()

    def check_related(self, src: Source) -> None:
        """Покрытие Discover зависит от Haplotree и наоборот."""
        self.check(src, enrich=False)
        if src.key in LINKED:
            for s in SOURCES:
                if s.key in LINKED and s is not src:
                    self.check(s, enrich=False)

    def on_screen_resume(self) -> None:
        genome = self._active()
        if genome != self.genome:
            self.genome = genome
            # Coverage depends on active F1 genome — recompute when it changes.
            self._enrich_dirty |= LINKED
            self.check(BY_KEY["ftdna_discover"], enrich=False)
            self.check(BY_KEY["ftdna_haplotree"], enrich=False)
        self.refresh_all()

    def _ensure_enrich(self, src: Source) -> None:
        """Coverage facts for Discover/Haplotree — only when the row is focused."""
        if src.key not in LINKED or src.key in self._enriching:
            return
        st = self.status.get(src.key)
        if not st or st.state != "ok":
            return
        if st.coverage is not None:
            return
        # Reuse last session / hydrate facts unless genome/path marked dirty (or R).
        if _has_enrich_facts(st) and src.key not in self._enrich_dirty:
            return
        self._enriching.add(src.key)
        path = self.store.path(src.key)

        def work() -> None:
            try:
                fresh = src.check(path, self.store, enrich=True)
            except Exception as exc:  # noqa: BLE001
                self.app.call_from_thread(self._enrich_done, src.key, None, exc)
                return
            self.app.call_from_thread(self._enrich_done, src.key, fresh, None)

        self.run_worker(work, thread=True, group=f"enrich-{src.key}", exclusive=True)

    def _enrich_done(self, key: str, st: Status | None, exc: Exception | None) -> None:
        self._enriching.discard(key)
        self._enrich_dirty.discard(key)
        if exc is not None or st is None:
            return
        self.status[key] = st
        src = BY_KEY.get(key)
        if src:
            try:
                sources_db.upsert_status(src, st, self.store.path(key))
            except Exception:  # noqa: BLE001
                pass
        self._schedule_refresh()

    # — отрисовка —
    def _genome_label(self) -> tuple[str, str]:
        """(короткое имя или —, стиль)."""
        if self.genome and self.genome.exists():
            return self.genome.name, "bold #FBF6F0"
        return "— none (open a file in F1)", "#B04A4A"

    def _prompt(self, s: Source) -> Text:
        st = self.status.get(s.key)
        state = st.state if st else "unset"
        icon, style = ("…", "#AEA5B8") if s.key in self.checking and not st else STATE_ICON[state]
        t = Text()
        t.append(f"{icon} ", style=style)
        t.append(fit(s.title, PROMPT_W) + "\n", style="bold")
        job = JOBS.get(s.key)
        n_q = len(JOBS.queued_for(s.key))
        if job and job.active:
            mark = "Ⅱ " if job.status == "paused" else f"{job_icon(job)} "
            extra = f" · +{n_q} queued" if n_q else ""
            t.append("  " + fit(f"{mark}{job_line(job)}{extra}", PROMPT_W) + "\n", style="#D4AD72")
        elif n_q:
            t.append("  " + fit(f"○ {n_q} queued", PROMPT_W) + "\n", style="#D4AD72")
        else:
            t.append("  " + fit(st.summary if st else "checking…", PROMPT_W) + "\n", style=style)
        if s.kind == "secret":
            key = self.store.secret(s.key) or (os.environ.get("GEMINI_API_KEY") if s.key == "gemini" else None)
            t.append("  " + fit(f"key · …{key[-4:]}" if key else "key not set", PROMPT_W),
                     style="#71697A")
        else:
            path = self.store.path(s.key)
            t.append("  " + fit(short_path(path) if path else "path not set", PROMPT_W), style="#71697A")
        return t

    def refresh_all(self) -> None:
        ol = self.query_one("#sources", OptionList)
        for i, opt in enumerate(ol.options):
            if opt.disabled or not opt.id or str(opt.id).startswith("grp:"):
                continue
            s = BY_KEY.get(opt.id)
            if s:
                ol.replace_option_prompt_at_index(i, self._prompt(s))
        self.query_one("#help", KeyBar).set_items(self._key_items())
        self.refresh_details()
        self.refresh_actions()
        self.update_status()

    def refresh_details(self) -> None:
        s = self.source
        self._ensure_enrich(s)
        st = self.status.get(s.key)
        path = self.store.path(s.key)
        t = Text()
        t.append(f"{s.title}\n", style="bold #FBF6F0")
        if st:
            icon, style = STATE_ICON[st.state]
            t.append(f"{icon} {STATE_LABEL[st.state]}", style=f"bold {style}")
            t.append(f"  {st.summary}\n", style=style)
            if s.key in LINKED and s.key in self._enriching:
                t.append("  computing Discover ↔ Haplotree coverage…\n", style="#AEA5B8")
        else:
            t.append("checking…\n", style="#AEA5B8")
        t.append(f"\n{s.about}\n", style="#C3BCCC")

        # Активный геном F1: нужен для «твоих» недокачанных haplogroups и покрытия Y.
        gname, gstyle = self._genome_label()
        t.append("\nACTIVE GENOME (F1)\n", style="bold #D4AD72")
        t.append("  File: ", style="#AEA5B8")
        t.append(f"{gname}\n", style=gstyle)
        if self.genome and self.genome.exists():
            t.append("  Folder: ", style="#AEA5B8")
            t.append(f"{self.genome.parent}\n", style="#C3BCCC")
            if s.can_fetch:
                t.append("  Used for: ", style="#AEA5B8")
                t.append("which Discover haplogroups to fetch for your Y markers (D)\n", style="#C3BCCC")
        else:
            t.append("  Set an active genotype in F1 — then D can fetch haplogroups for your Y line.\n",
                     style="#8A8294")

        t.append("\nLOCATION\n", style="bold #D4AD72")
        if s.kind == "secret":
            key = self.store.secret(s.key) or (os.environ.get("GEMINI_API_KEY") if s.key == "gemini" else None)
            t.append("  Key: ", style="#AEA5B8")
            t.append(f"set · …{key[-4:]}\n" if key else "not set\n", style="" if key else "#8A8294")
        else:
            t.append(f"  {s.kind.capitalize()}: ", style="#AEA5B8")
            t.append(f"{path}\n" if path else "not set\n", style="" if path else "#8A8294")
        t.append("  Source: ", style="#AEA5B8")
        t.append(f"{s.url}\n")
        t.append("  Size: ", style="#AEA5B8")
        t.append(f"{s.size_hint}\n")

        if st and st.facts:
            t.append("\nCONTENT\n", style="bold #D4AD72")
            for k, v in st.facts:
                t.append(f"  {k}: ", style="#AEA5B8")
                t.append(f"{v}\n")

        if st and st.files:
            t.append("\nFILES\n", style="bold #D4AD72")
            w = min(58, max(len(r.name) for r in st.files))
            for r in st.files:
                t.append(f"  {'✔' if r.ok else '✖'} ", style="#53A94B" if r.ok else "#8BAB4A")
                t.append(fit(r.name, w).ljust(w), style="" if r.ok else "#B04A4A")
                t.append(f"  {human_size(r.size):>9}", style="#C3BCCC")
                if r.note:
                    t.append(f"  {r.note}", style="#8A8294" if r.ok else "#B04A4A")
                t.append("\n")

        job = JOBS.get(s.key)
        queued = JOBS.queued_for(s.key)
        if job or queued:
            t.append("\nTASK\n", style="bold #D4AD72")
        if job:
            style = JOB_STYLE.get(job.status, "#AEA5B8")
            t.append(f"  {job.status.upper()}", style=f"bold {style}")
            started = time.strftime("%H:%M:%S", time.localtime(job.started))
            took = duration((job.finished or time.time()) - job.started)
            t.append(f"  started {started} · {took}\n", style="#8A8294")
            if job.active:
                t.append(f"  {job.stage}\n")
                if job.fraction is not None:
                    t.append("  " + bar(job.fraction, 40).ljust(40), style="#D4AD72")
                    t.append(f" {job.fraction:6.1%}\n")
                size = amount(job, job.done) + (f" of {amount(job, job.total)}" if job.total else "")
                t.append(f"  {size} · {job_line(job)}\n", style="#AEA5B8")
            else:
                t.append(f"  {job.message}\n", style=style)
        for qj in queued:
            t.append(f"  QUEUED  {qj.title}\n", style="#8A8294")
        self.query_one("#details", Static).update(t)

    def refresh_actions(self) -> None:
        """Правая панель: только действия текущего источника."""
        s = self.source
        st = self.status.get(s.key)
        path = self.store.path(s.key)
        job = JOBS.get(s.key)
        t = Text()
        t.append(f"{s.title}\n", style="bold #FBF6F0")
        t.append("Click a line or press the key.\n\n", style="#8A8294")

        def action(key: str, text: str, name: str) -> None:
            t.append(f"[{key}] ", style=click(f"screen.{name}", "bold #D4AD72"))
            t.append(text + "\n\n", style=click(f"screen.{name}"))

        if s.kind == "secret":
            action("P", "paste the API key", "set_path")
        else:
            action("P", f"point to an existing {s.kind}", "set_path")
        cov = st.coverage if st else None
        if s.can_fetch and cov:
            plan = fetch_plan(self.store)
            if not self.genome:
                t.append("[1–4]  no active genome in F1 — open a genotype file there first\n\n",
                         style="#8A8294")
            elif isinstance(plan, str):
                t.append(f"[1–5]  {plan}\n\n", style="#8A8294")
            else:
                tip = plan.tip or "—"
                t.append(f"Discover fetch stages · tip {tip}\n", style="bold #D4AD72")
                t.append("Busy slot → queue (F7). Pause/cancel in F7.\n\n", style="#8A8294")
                keys = ("1", "2", "3", "4", "5")
                stages = ("our", "children", "genome", "all_genomes", "tree")
                labels = {
                    "our": "Our path (tip→root)",
                    "children": "Children below Our",
                    "genome": f"This genome · {plan.genome_label}",
                    "all_genomes": f"All F1 genomes · {plan.n_genomes} file(s)",
                    "tree": "Whole haplotree",
                }
                for key, stage in zip(keys, stages):
                    n = plan.count(stage)
                    label = labels[stage]
                    if n:
                        note = f"fetch {n:,} · {fetch_cost(n)}"
                        if stage == "tree":
                            note += " · press 5/A twice"
                        action(key, f"{label}: {note}", f"fetch_stage('{stage}')")
                    else:
                        t.append(f"[{key}]  {label}: done\n\n", style="#8A8294")
                t.append("[D]  same as [1] Our path\n\n", style="#8A8294")
        elif s.key == "openhumans":
            action("B", "open F8 Samples catalog (D on a row downloads)", "browse_openhumans")
        elif s.can_download:
            if getattr(s, "needs_genome", False) and not self.genome:
                t.append("[D]  needs an active genome in F1 (coverage uses your SNPs)\n\n",
                         style="#8A8294")
            elif s.key == "pgs_models":
                action("D", "build/update catalog (API + weights + coverage vs F1)", "download")
            else:
                action("D", "download (asks where; existing complete files are skipped)", "download")
        else:
            t.append(f"[D]  {s.download_note}\n\n", style="#8A8294")
        if s.can_verify:
            if s.key == "openhumans":
                action("V", "API new items → download missing → DuckDB", "verify")
            else:
                action("V", "verify MD5 checksums of the files", "verify")
        action("R", "recheck", "recheck")
        if job and job.active:
            action("C", "cancel the running/paused task", "cancel_job")
        elif JOBS.queued_for(s.key):
            action("C", "cancel active slot task (queued stay — manage in F7)", "cancel_job")
        if s.kind == "secret" and self.store.secret(s.key):
            action("Del", "forget the API key", "clear_path")
        elif path:
            action("Del", "forget the path (files are untouched)", "clear_path")
        self.query_one("#actions", Static).update(t)

    def update_status(self) -> None:
        live = [j for j in JOBS.items.values() if j.active]
        n_q = sum(1 for j in JOBS.items.values() if j.status == "queued")
        ready = sum(1 for s in SOURCES if self.status.get(s.key) and self.status[s.key].state == "ok")
        gname, _ = self._genome_label()
        genome = f"    GENOME: {gname if self.genome else '— (F1)'}"
        bits = []
        for j in live:
            mark = "Ⅱ" if j.status == "paused" else job_icon(j)
            bits.append(f"{mark} {j.title}: {job_line(j)}")
        if n_q:
            bits.append(f"○ {n_q} queued")
        jobs = ("    " + "    ".join(bits)) if bits else ""
        self.query_one("#status", Static).update(
            f"READY: {ready}/{len(SOURCES)} sources{genome}{jobs}")

    @on(OptionList.OptionHighlighted, "#sources")
    def _highlighted(self) -> None:
        self.refresh_details()
        self.refresh_actions()
        self.query_one("#det-scroll").scroll_home(animate=False)
        self.query_one("#act-scroll").scroll_home(animate=False)

    @on(OptionList.OptionSelected, "#sources")
    def _selected(self) -> None:
        self.query_one("#act-scroll").focus()

    # — действия —
    def _apply_path(self, s: Source, path: Path) -> None:
        self.store.set_path(s.key, path)
        self.status.pop(s.key, None)
        if s.key in LINKED:
            self._enrich_dirty |= LINKED
        self.check_related(s)
        self.refresh_all()
        self.app.source_changed(s.key)

    def action_set_path(self) -> None:
        s = self.source
        if s.kind == "secret":
            def done_key(value: str | None) -> None:
                if value:
                    self.store.set_secret(s.key, value)
                    self.status.pop(s.key, None)
                    self.check(s)
                    self.refresh_all()
                    self.app.notify(f"{s.title}: key saved")
            self.app.push_screen(TokenScreen(f"[b]{s.title}[/b] · API key",
                                             bool(self.store.secret(s.key))), done_key)
            return

        def done(path: Path | None) -> None:
            if path:
                self._apply_path(s, path)
        self.app.push_screen(PathScreen(f"[b]{s.title}[/b] · path to the existing {s.kind}",
                                        self.store.path(s.key) or s.suggest(self.store),
                                        s.kind, must_exist=True), done)

    def _fetch(self, s: Source, names: list[str], what: str) -> None:
        path = self.store.path(s.key)
        if not path:
            return
        job = JOBS.enqueue(s.key, f"Fetch {what}", lambda j: s.fetch(path, names, j))
        if job.status == "queued":
            self.app.notify(f"Queued: {job.title}", severity="information")
        self.refresh_all()

    def action_browse_openhumans(self) -> None:
        """Catalog lives in F8 — jump there (no modal / intermediate screen)."""
        if "openhumans" not in BY_KEY:
            return
        if self.store.path("openhumans") is None:
            folder = BY_KEY["openhumans"].suggest(self.store)
            folder.mkdir(parents=True, exist_ok=True)
            self.store.set_path("openhumans", folder)
        self.app.action_mode("samples")

    def action_download(self) -> None:
        s = self.source
        if s.key == "openhumans":
            self.action_browse_openhumans()
            return
        if s.can_fetch:
            self.action_fetch_stage("our")
            return
        if not s.can_download:
            self.app.notify(s.download_note, severity="warning", timeout=8)
            return
        if getattr(s, "needs_genome", False) and not self.genome:
            self.app.notify("Load a genotype file in F1 first — catalog coverage uses it",
                            severity="warning")
            return
        job = JOBS.get(s.key)
        if job and job.active:
            self.app.notify(f"{s.title}: a task is already running (pause/cancel in F7)",
                            severity="warning")
            return

        def done(path: Path | None) -> None:
            if not path:
                return
            self.store.set_path(s.key, path)
            title = f"Build {s.title}" if s.key == "pgs_models" else f"Download {s.title}"
            JOBS.start(s.key, title, lambda j: s.download(path, j))
            self.check(s)
            self.refresh_all()
        prompt = (f"[b]Build {s.title}[/b] ({s.size_hint}) · work folder:"
                  if s.key == "pgs_models" else
                  f"[b]Download {s.title}[/b] ({s.size_hint}) · save to this {s.kind}:")
        self.app.push_screen(PathScreen(prompt, s.suggest(self.store), s.kind, must_exist=False), done)

    def action_fetch_stage(self, stage: str = "our") -> None:
        """Этапы 1–5 Discover: Our → Children → This genome → All genomes → Tree.
        Если слот занят — задача встаёт в очередь (F7)."""
        s = self.source
        if not s.can_fetch:
            return
        labels = dict(FETCH_STAGES)
        title = labels.get(stage, stage)
        if stage in ("our", "children", "genome") and not self.genome:
            self.app.notify("Load a genotype file in F1 first", severity="warning")
            return
        if stage == "tree":
            plan = fetch_plan(self.store)
            n = plan.count("tree") if not isinstance(plan, str) else 0
            if n <= 0:
                self.app.notify("Whole tree: nothing to fetch")
                return
            if time.monotonic() - self.armed > 5:
                self.armed = time.monotonic()
                self.app.notify(
                    f"Press 5 or A again within 5 s to fetch {n:,} haplogroups "
                    f"({fetch_cost(n)}). Cancel anytime from F2/F7.",
                    severity="warning", timeout=5,
                )
                return
            self.armed = 0.0
        names = stage_fetch_names(self.store, stage)
        if not names:
            self.app.notify(f"{title}: nothing to fetch")
            return
        tip = ""
        plan = fetch_plan(self.store)
        if not isinstance(plan, str) and plan.tip:
            tip = f" · tip {plan.tip}"
        self._fetch(s, names, f"{title} · {len(names):,}{tip}")
        # план устарел после старта — сброс кэша на следующем check
        from ...core import ylink as _ylink
        _ylink._plan_cache = None
        _ylink._cov_cache = None

    def action_verify(self) -> None:
        s = self.source
        path = self.store.path(s.key)
        if not s.can_verify:
            self.app.notify("No published checksums for this source", severity="warning")
            return
        if not path and s.key == "openhumans":
            path = s.suggest(self.store)
            path.mkdir(parents=True, exist_ok=True)
            self.store.set_path(s.key, path)
        if not path:
            self.app.notify("Set the path first (P)", severity="warning")
        elif (job := JOBS.get(s.key)) and job.active:
            self.app.notify(f"{s.title}: a task is already running", severity="warning")
        else:
            title = f"Sync {s.title}" if s.key == "openhumans" else f"Verify {s.title}"
            JOBS.start(s.key, title, lambda j: s.verify(path, j))
            self.refresh_all()

    def action_cancel_job(self) -> None:
        job = JOBS.get(self.source.key)
        if job and job.active:
            job.cancel()
            self.app.notify(f"Cancelling: {job.title}")
        else:
            self.app.notify("Nothing is running for this source")

    def action_recheck(self) -> None:
        s = self.source
        self.status.pop(s.key, None)
        # Explicit R: include expensive coverage for Discover / Haplotree.
        self.check(s, enrich=s.key in LINKED)
        self.refresh_all()

    def action_clear_path(self) -> None:
        s = self.source
        if s.kind == "secret":
            if not self.store.secret(s.key):
                return
            self.store.set_secret(s.key, None)
            self.status.pop(s.key, None)
            self.check(s)
            self.refresh_all()
            self.app.notify(f"{s.title}: key forgotten")
            return
        if not self.store.path(s.key):
            return
        self.store.set_path(s.key, None)
        self.status.pop(s.key, None)
        self.check_related(s)
        self.refresh_all()
        self.app.source_changed(s.key)
        self.app.notify(f"{s.title}: path forgotten (files are untouched)")
