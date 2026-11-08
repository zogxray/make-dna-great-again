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
from ...core.ylink import active_genome, fetch_cost
from ..base import KeyBar, ModeBar, Workspace, alt_keys, click, fit, short_path
from .screens import PathScreen, TokenScreen

from ...theme.styles import DIM, JOB_STYLE, STATE_ICON_STYLE

PROMPT_W = 40                # #src-box 48 минус рамка, отступы и значок

STATE_ICON = {k: (glyph, STATE_ICON_STYLE[k]) for k, glyph in
              (("ok", "✔"), ("partial", "◐"), ("missing", "✖"), ("unset", "○"))}
STATE_LABEL = {"ok": "READY", "partial": "INCOMPLETE", "missing": "NOT FOUND", "unset": "PATH NOT SET"}
LINKED = {"ftdna_discover", "ftdna_haplotree"}


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
    #src-box { width: 48; border: round $primary; }
    #det-box { width: 1fr; border: round $primary; }
    #src-box:focus-within, #det-box:focus-within { border: round $accent; }
    #sources { height: 1fr; border: none; background: transparent; }
    #det-scroll { height: 1fr; scrollbar-size-vertical: 1; }
    #details { padding: 0 1; }
    #help { height: 1; padding: 0 1; color: $text-muted; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """
    BINDINGS = [
        Binding(alt_keys("p"), "set_path", "Set path"),
        Binding(alt_keys("d"), "download", "Download"),
        Binding(alt_keys("a"), "fetch_all", "Fetch whole tree", show=False),
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
        yield KeyBar([("↑↓", "Source", None), ("P", "Set path", "screen.set_path"),
                      ("D", "Download", "screen.download"), ("V", "Verify MD5", "screen.verify"),
                      ("R", "Recheck", "screen.recheck"), ("C", "Cancel task", "screen.cancel_job"),
                      ("Del", "Forget path", "screen.clear_path"), ("Tab", "Panel", "app.focus_next"),
                      ("F1–F7", "Modes", None), ("Q", "Quit", "app.quit")], id="help")
        yield Static(id="status")

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
        ol = self.query_one("#sources", OptionList)
        ol.add_options(self._menu_options())
        ol.highlighted = self._first_source_index(ol)
        ol.focus()
        for s in _grouped_sources():
            self.check(s)
        self.set_interval(0.5, self._tick)
        self.refresh_all()

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

    # — проверка состояния —
    def check(self, src: Source) -> None:
        self.checking.add(src.key)
        path = self.store.path(src.key)

        def work() -> None:
            st = src.check(path, self.store)
            self.app.call_from_thread(self._checked, src.key, st)
        self.run_worker(work, thread=True, group=f"check-{src.key}", exclusive=True)

    def _checked(self, key: str, st: Status) -> None:
        self.checking.discard(key)
        self.status[key] = st
        src = BY_KEY.get(key)
        if src:
            try:
                sources_db.upsert_status(src, st, self.store.path(key))
            except Exception:
                pass
        self.refresh_all()

    def _tick(self) -> None:
        changed = False
        for s in SOURCES:
            job = JOBS.get(s.key)
            if not job or job.running or id(job) in self.finished:
                continue
            self.finished.add(id(job))
            changed = True
            sev = {"done": "information", "failed": "error"}.get(job.status, "warning")
            self.app.notify(f"{job.title}: {job.message}", severity=sev, timeout=8)
            self.check_related(s)
            if job.status == "done" or s.can_fetch:
                self.app.source_changed(s.key)
        if changed or JOBS.running():
            self.refresh_all()

    def check_related(self, src: Source) -> None:
        """Покрытие Discover зависит от Haplotree и наоборот."""
        self.check(src)
        if src.key in LINKED:
            for s in SOURCES:
                if s.key in LINKED and s is not src:
                    self.check(s)

    def on_screen_resume(self) -> None:
        genome = self._active()
        if genome != self.genome:
            self.genome = genome
            self.check(BY_KEY["ftdna_discover"])
            self.check(BY_KEY["ftdna_haplotree"])
        self.refresh_all()

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
        if job and job.running:
            t.append("  " + fit(f"{job_icon(job)} {job_line(job)}", PROMPT_W) + "\n", style="#D4AD72")
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
        self.refresh_details()
        self.update_status()

    def refresh_details(self) -> None:
        s = self.source
        st = self.status.get(s.key)
        path = self.store.path(s.key)
        t = Text()
        t.append(f"{s.title}\n", style="bold #FBF6F0")
        if st:
            icon, style = STATE_ICON[st.state]
            t.append(f"{icon} {STATE_LABEL[st.state]}", style=f"bold {style}")
            t.append(f"  {st.summary}\n", style=style)
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
        if job:
            t.append("\nTASK\n", style="bold #D4AD72")
            t.append(f"  {job.status.upper()}", style=f"bold {JOB_STYLE[job.status]}")
            started = time.strftime("%H:%M:%S", time.localtime(job.started))
            took = duration((job.finished or time.time()) - job.started)
            t.append(f"  started {started} · {took}\n", style="#8A8294")
            if job.running:
                t.append(f"  {job.stage}\n")
                if job.fraction is not None:
                    t.append("  " + bar(job.fraction, 40).ljust(40), style="#D4AD72")
                    t.append(f" {job.fraction:6.1%}\n")
                size = amount(job, job.done) + (f" of {amount(job, job.total)}" if job.total else "")
                t.append(f"  {size} · {job_line(job)}\n", style="#AEA5B8")
            else:
                t.append(f"  {job.message}\n", style=JOB_STYLE[job.status])

        t.append("\nACTIONS  (click or press the key)\n", style="bold #D4AD72")

        def action(key: str, text: str, name: str) -> None:
            t.append(f"  [{key}] ", style=click(f"screen.{name}", "bold #D4AD72"))
            t.append(text + "\n", style=click(f"screen.{name}"))

        if s.kind == "secret":
            action("P", "paste the API key", "set_path")
        else:
            action("P", f"point to an existing {s.kind}", "set_path")
        cov = st.coverage if st else None
        if s.can_fetch and cov:
            if not self.genome:
                t.append("  D   no active genome in F1 — open a genotype file there first\n", style="#8A8294")
            elif not cov.found:
                t.append(f"  D   no Y markers of {self.genome.name} are linked to the haplotree\n",
                         style="#8A8294")
            elif cov.missing_mine:
                action("D", f"fetch {len(cov.missing_mine):,} missing haplogroups for {self.genome.name} "
                       f"and their ancestors ({fetch_cost(len(cov.missing_mine))})", "download")
            else:
                t.append(f"  D   everything linked to {self.genome.name} is downloaded\n", style="#8A8294")
            action("A", f"fetch the whole haplotree: {cov.n_missing_all:,} missing "
                   f"({fetch_cost(cov.n_missing_all)}; press A twice)", "fetch_all")
        elif s.can_download:
            action("D", "download (asks where; existing complete files are skipped)", "download")
        else:
            t.append(f"  D   {s.download_note}\n", style="#8A8294")
        if s.can_verify:
            action("V", "verify MD5 checksums of the files", "verify")
        action("R", "recheck", "recheck")
        if job and job.running:
            action("C", "cancel the running task", "cancel_job")
        if s.kind == "secret" and self.store.secret(s.key):
            action("Del", "forget the API key", "clear_path")
        elif path:
            action("Del", "forget the path (files are untouched)", "clear_path")
        self.query_one("#details", Static).update(t)

    def update_status(self) -> None:
        running = JOBS.running()
        ready = sum(1 for s in SOURCES if self.status.get(s.key) and self.status[s.key].state == "ok")
        gname, _ = self._genome_label()
        genome = f"    GENOME: {gname if self.genome else '— (F1)'}"
        jobs = "    " + "    ".join(f"{job_icon(j)} {j.title}: {job_line(j)}" for j in running) if running else ""
        self.query_one("#status", Static).update(
            f"READY: {ready}/{len(SOURCES)} sources{genome}{jobs}")

    @on(OptionList.OptionHighlighted, "#sources")
    def _highlighted(self) -> None:
        self.refresh_details()
        self.query_one("#det-scroll").scroll_home(animate=False)

    @on(OptionList.OptionSelected, "#sources")
    def _selected(self) -> None:
        self.query_one("#det-scroll").focus()

    # — действия —
    def _apply_path(self, s: Source, path: Path) -> None:
        self.store.set_path(s.key, path)
        self.status.pop(s.key, None)
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
        JOBS.start(s.key, f"Fetch {what}", lambda j: s.fetch(path, names, j))
        self.refresh_all()

    def action_download(self) -> None:
        s = self.source
        st = self.status.get(s.key)
        if s.can_fetch:
            job = JOBS.get(s.key)
            if job and job.running:
                self.app.notify(f"{s.title}: a task is already running", severity="warning")
            elif not st or not st.coverage:
                self.app.notify("Set the Discover folder and the haplotree path first", severity="warning")
            elif not self.genome:
                self.app.notify("Load a genotype file in F1 first", severity="warning")
            elif not st.coverage.found:
                self.app.notify(f"No Y markers of {self.genome.name} are linked to the haplotree",
                                severity="warning")
            elif not st.coverage.missing_mine:
                self.app.notify(f"Everything linked to {self.genome.name} is already downloaded")
            else:
                self._fetch(s, st.coverage.missing_mine,
                            f"haplogroups for {self.genome.name}")
            return
        if not s.can_download:
            self.app.notify(s.download_note, severity="warning", timeout=8)
            return
        job = JOBS.get(s.key)
        if job and job.running:
            self.app.notify(f"{s.title}: a task is already running", severity="warning")
            return

        def done(path: Path | None) -> None:
            if not path:
                return
            self.store.set_path(s.key, path)
            JOBS.start(s.key, f"Download {s.title}", lambda j: s.download(path, j))
            self.check(s)
            self.refresh_all()
        self.app.push_screen(PathScreen(f"[b]Download {s.title}[/b] ({s.size_hint}) · save to this {s.kind}:",
                                        s.suggest(self.store), s.kind, must_exist=False), done)

    def action_fetch_all(self) -> None:
        s = self.source
        st = self.status.get(s.key)
        if not s.can_fetch or not st or not st.coverage:
            return
        if (job := JOBS.get(s.key)) and job.running:
            self.app.notify(f"{s.title}: a task is already running", severity="warning")
            return
        from ...core.ylink import missing_all_names
        n = st.coverage.n_missing_all
        if time.monotonic() - self.armed > 5:
            self.armed = time.monotonic()
            self.app.notify(f"Press A again within 5 s to fetch {n:,} haplogroups "
                            f"({fetch_cost(n)}). It can be cancelled and resumed any time.",
                            severity="warning", timeout=5)
            return
        self.armed = 0.0
        names = missing_all_names(self.store)
        if not names:
            self.app.notify("Nothing to fetch", severity="warning")
            return
        self._fetch(s, names, "the whole haplotree")

    def action_verify(self) -> None:
        s = self.source
        path = self.store.path(s.key)
        if not s.can_verify:
            self.app.notify("No published checksums for this source", severity="warning")
        elif not path:
            self.app.notify("Set the path first (P)", severity="warning")
        elif (job := JOBS.get(s.key)) and job.running:
            self.app.notify(f"{s.title}: a task is already running", severity="warning")
        else:
            JOBS.start(s.key, f"Verify {s.title}", lambda j: s.verify(path, j))
            self.refresh_all()

    def action_cancel_job(self) -> None:
        job = JOBS.get(self.source.key)
        if job and job.running:
            job.cancel()
            self.app.notify(f"Cancelling: {job.title}")
        else:
            self.app.notify("Nothing is running for this source")

    def action_recheck(self) -> None:
        s = self.source
        self.status.pop(s.key, None)
        self.check(s)
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
