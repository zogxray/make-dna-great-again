"""F2 Data screen for textual-wgpu — sources · details · actions."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from rich.text import Text

from textual_wgpu.on import on
from textual_wgpu.screen import Screen
from textual_wgpu.widgets import (
    Horizontal,
    KeyBar,
    ModeBar,
    Option,
    OptionList,
    Panel,
    RichText,
    StatusBar,
)

from ..core import sources_db
from ..core.jobs import JOBS, Job
from ..core.sources import BY_KEY, SOURCE_GROUPS, SOURCES, Source, Status, human_size
from ..core.store import Store
from ..core.ylink import (
    FETCH_STAGES,
    active_genome,
    fetch_cost,
    fetch_plan,
    stage_fetch_names,
)
from ..theme.styles import JOB_STYLE, STATE_ICON_STYLE
from ..workspaces.base import MODE_LABELS, fit, short_path
from ..workspaces.data.workspace import (
    LINKED,
    STATE_LABEL,
    _grouped_sources,
    _has_enrich_facts,
    _preserve_enrich,
    bar,
    duration,
    job_icon,
    job_line,
)
from .wgpu_jobs import _text_lines

PROMPT_W = 36
STATE_ICON = {
    k: (glyph, STATE_ICON_STYLE[k])
    for k, glyph in (("ok", "✔"), ("partial", "◐"), ("missing", "✖"), ("unset", "○"))
}


class DataScreen(Screen):
    """Same F2 Data logic as Textual, hosted on wgpu widgets."""

    DEFAULT_CSS = """
    #main { height: 1fr; }
    #src-box { width: 42; border: round $primary; }
    #det-box { width: 1fr; border: round $primary; }
    #act-box { width: 36; min-width: 32; border: round $primary; }
    #src-box:focus-within, #det-box:focus-within, #act-box:focus-within { border: round $accent; }
    #sources { height: 1fr; border: none; background: transparent; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """

    BINDINGS = [
        ("p", "set_path"),
        ("d", "download"),
        ("b", "browse_openhumans"),
        ("1", "fetch_1"),
        ("2", "fetch_2"),
        ("3", "fetch_3"),
        ("4", "fetch_4"),
        ("5", "fetch_5"),
        ("a", "fetch_5"),
        ("v", "verify"),
        ("r", "recheck"),
        ("c", "cancel_job"),
        ("delete", "clear_path"),
    ]

    def __init__(self, store: Store | None = None) -> None:
        super().__init__()
        self.store = store or Store()
        self.status: dict[str, Status] = {}
        self.checking: set[str] = set()
        self.finished: set[int] = {id(j) for j in JOBS.items.values() if not j.running}
        self.armed = 0.0
        self.genome: Path | None = None
        self._refresh_gen = 0
        self._enriching: set[str] = set()
        self._enrich_dirty: set[str] = set()
        self._labels = list(MODE_LABELS)
        self._tick_handle = None
        self._keybar: KeyBar | None = None

    # ── compose ──────────────────────────────────────────────────────
    def compose(self):
        from .app_api import make_mode_bar

        yield make_mode_bar(self, "data", self._labels)
        sources = OptionList(id="sources", row_lines=3)
        src_box = Panel(sources, title="SOURCES", id="src-box")

        details = RichText(id="details")
        det_box = Panel(details, title="DETAILS", id="det-box")

        actions = RichText(id="actions")
        act_box = Panel(actions, title="ACTIONS", subtitle="key or KeyBar", id="act-box")

        yield Horizontal(src_box, det_box, act_box, id="main")
        self._keybar = KeyBar(self._key_items())
        yield self._keybar
        yield StatusBar(id="status")

    def on_screen_mount(self) -> None:
        self.genome = self._active()
        if self.app is not None:
            self.app.show_busy("Data", "Loading sources…")
        try:
            try:
                self.store.warm_paths()
                self._hydrate_status_from_db()
            except Exception as exc:  # noqa: BLE001
                self._notify(f"Data store: {exc}")
            ol = self.query_one("#sources", OptionList)
            ol.set_options(self._menu_options())
            idx = self._first_source_index(ol)
            if idx is not None:
                ol.highlighted = idx
                ol._ensure_visible()
            if self.app is not None:
                self.app.set_focus(ol)
            self._sync_keybar()
            self.refresh_details()
            self.refresh_actions()
            self.update_status()
            for s in _grouped_sources():
                self.check(s, enrich=False)
            if self.app is not None and hasattr(self.app, "set_interval"):
                self._tick_handle = self.app.set_interval(0.5, self._tick)
        except Exception as exc:  # noqa: BLE001
            self._notify(f"Data open failed: {exc}")
        finally:
            if self.app is not None:
                self.app.hide_busy()

    def on_screen_resume(self) -> None:
        genome = self._active()
        if genome != self.genome:
            self.genome = genome
            self._enrich_dirty |= LINKED
            if "ftdna_discover" in BY_KEY:
                self.check(BY_KEY["ftdna_discover"], enrich=False)
            if "ftdna_haplotree" in BY_KEY:
                self.check(BY_KEY["ftdna_haplotree"], enrich=False)
        self.refresh_all()

    # ── helpers ──────────────────────────────────────────────────────
    def _notify(self, msg: str) -> None:
        if self.app is not None and hasattr(self.app, "notify"):
            self.app.notify(msg)

    def _path(self, key: str) -> Path | None:
        try:
            return self.store.path(key)
        except Exception:  # noqa: BLE001 — DB lock
            return None

    def _secret(self, key: str) -> str | None:
        try:
            return self.store.secret(key)
        except Exception:  # noqa: BLE001
            return None

    def _active(self) -> Path | None:
        app = self.app
        if app is not None:
            screens = getattr(app, "_screens", {}) or {}
            gws = screens.get("genome")
            active = getattr(gws, "active", None) if gws is not None else None
            if active is not None and Path(active).exists():
                return Path(active)
        try:
            return active_genome()
        except Exception:  # noqa: BLE001 — DB lock / missing store
            return None

    def _goto_mode(self, key: str) -> None:
        if self.app is None:
            return
        for i, (k, _) in enumerate(self._labels):
            if k == key:
                self.app.set_mode(i)
                return

    def _source_changed(self, key: str) -> None:
        app = self.app
        if app is None:
            return
        if key in ("ftdna_discover", "ftdna_haplotree"):
            gws = getattr(app, "_screens", {}).get("genome")
            if gws is not None and hasattr(gws, "relink"):
                try:
                    gws.relink()
                except Exception:  # noqa: BLE001
                    pass

    def _ask_path(
        self,
        title: str,
        initial: Path | str | None,
        done: Callable[[Path], None],
        *,
        must_exist: bool,
        kind: str | None = None,
    ) -> None:
        """PathScreen analogue via ModeApp.ask_open_file (dir tree + path field)."""
        s = self.source
        path_kind = kind or (s.kind if s.kind in ("file", "folder") else "folder")
        seed = Path(initial) if initial else Path.cwd()

        from .app_api import require_app

        if not require_app(self, what=f"path · {title}"):
            return

        def _cb(path: Path | None) -> None:
            if path is None:
                return
            done(path)

        self.app.ask_open_file(
            seed,
            _cb,
            title=title,
            subtitle=(
                "Tree: click fills the field · U / ↑ parent · "
                "Enter in field: open folder · OK confirm · Esc cancel"
            ),
            kind=path_kind,
            must_exist=must_exist,
            ok_label="OK",
            suffixes=None if path_kind == "folder" else None,
        )

    def _ask_secret(self, title: str, done: Callable[[str], None]) -> None:
        from .app_api import require_app

        if not require_app(self, what=f"secret · {title}"):
            return

        def _cb(value: str | None) -> None:
            if value is None:
                return
            text = value.strip()
            if not text:
                self._notify("Empty key")
                return
            done(text)

        self.app.ask_prompt(title, "", _cb, password=True)

    # ── source list ──────────────────────────────────────────────────
    def _menu_options(self) -> list[Option]:
        options: list[Option] = []
        for title, keys in SOURCE_GROUPS:
            head = Text()
            head.append(f"▸ {title}", style="bold #729fcf")
            options.append(
                Option(title, id=f"grp:{title}", disabled=True, lines=_text_lines(head)),
            )
            for key in keys:
                s = BY_KEY.get(key)
                if s:
                    options.append(self._option_for(s))
        grouped = {k for _, keys in SOURCE_GROUPS for k in keys}
        for s in SOURCES:
            if s.key not in grouped:
                options.append(self._option_for(s))
        return options

    def _option_for(self, s: Source) -> Option:
        return Option(s.title, id=s.key, lines=_text_lines(self._prompt(s)))

    def _first_source_index(self, ol: OptionList) -> int | None:
        for i, opt in enumerate(ol.options):
            if not opt.disabled and opt.id and not str(opt.id).startswith("grp:"):
                return i
        return None

    @property
    def source(self) -> Source:
        ol = self.query_one("#sources", OptionList)
        opt = ol.highlighted_option
        if opt and opt.id and not str(opt.id).startswith("grp:") and opt.id in BY_KEY:
            return BY_KEY[opt.id]
        idx = ol.highlighted if ol.highlighted is not None else 0
        for i in list(range(idx, len(ol.options))) + list(range(idx - 1, -1, -1)):
            o = ol.options[i]
            if not o.disabled and o.id and o.id in BY_KEY:
                return BY_KEY[o.id]
        return SOURCES[0]

    @on(OptionList.OptionHighlighted, "#sources")
    def _on_sources_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        opt = event.option
        if opt is None or opt.disabled:
            return
        self.refresh_details()
        self.refresh_actions()
        self._sync_keybar()
        self.refresh()

    def _hydrate_status_from_db(self) -> None:
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

    # ── checks / enrich ──────────────────────────────────────────────
    def check(self, src: Source, *, enrich: bool = False) -> None:
        self.checking.add(src.key)
        path = self._path(src.key)
        app = self.app

        def work() -> None:
            try:
                st = src.check(path, self.store, enrich=enrich)
            except Exception as exc:  # noqa: BLE001
                st = Status("unset", f"check failed: {exc}", [])
            if app is not None:
                app.call_from_thread(self._checked, src.key, st)
            else:
                self._checked(src.key, st)

        if app is not None:
            app.run_worker(work, name=f"check-{src.key}", exclusive=True, group=f"check-{src.key}")
        else:
            work()

    def _checked(self, key: str, st: Status) -> None:
        self.checking.discard(key)
        if key in LINKED:
            st = _preserve_enrich(self.status.get(key), st)
        self.status[key] = st
        src = BY_KEY.get(key)
        if src:
            try:
                sources_db.upsert_status(src, st, self._path(key))
            except Exception:  # noqa: BLE001
                pass
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        self._refresh_gen += 1
        gen = self._refresh_gen
        app = self.app
        if app is not None and hasattr(app, "set_timer"):
            app.set_timer(0.12, lambda g=gen: self._refresh_if_current(g))
        else:
            self._refresh_if_current(gen)

    def _refresh_if_current(self, gen: int) -> None:
        if gen != self._refresh_gen:
            return
        if self.app is not None and getattr(self.app, "mode_key", None) != "data":
            return
        self.refresh_all()

    def check_related(self, src: Source) -> None:
        self.check(src, enrich=False)
        if src.key in LINKED:
            for s in SOURCES:
                if s.key in LINKED and s is not src:
                    self.check(s, enrich=False)

    def _ensure_enrich(self, src: Source) -> None:
        if src.key not in LINKED or src.key in self._enriching:
            return
        st = self.status.get(src.key)
        if not st or st.state != "ok":
            return
        if st.coverage is not None:
            return
        if _has_enrich_facts(st) and src.key not in self._enrich_dirty:
            return
        self._enriching.add(src.key)
        path = self._path(src.key)
        app = self.app

        def work() -> None:
            try:
                fresh = src.check(path, self.store, enrich=True)
            except Exception as exc:  # noqa: BLE001
                if app is not None:
                    app.call_from_thread(self._enrich_done, src.key, None, exc)
                return
            if app is not None:
                app.call_from_thread(self._enrich_done, src.key, fresh, None)
            else:
                self._enrich_done(src.key, fresh, None)

        if app is not None:
            app.run_worker(work, name=f"enrich-{src.key}", exclusive=True, group=f"enrich-{src.key}")
        else:
            work()

    def _enrich_done(self, key: str, st: Status | None, exc: Exception | None) -> None:
        self._enriching.discard(key)
        self._enrich_dirty.discard(key)
        if exc is not None or st is None:
            return
        self.status[key] = st
        src = BY_KEY.get(key)
        if src:
            try:
                sources_db.upsert_status(src, st, self._path(key))
            except Exception:  # noqa: BLE001
                pass
        self._schedule_refresh()

    def _tick(self) -> None:
        if self.app is None or getattr(self.app, "mode_key", None) != "data":
            return
        changed = False
        for s in SOURCES:
            related = [
                j for j in JOBS.items.values()
                if j.key == s.key or (j.slot or "") == s.key
            ]
            for job in related:
                if job.active or job.status == "queued" or id(job) in self.finished:
                    continue
                self.finished.add(id(job))
                changed = True
                self._notify(f"{job.title}: {job.message}")
                self.check_related(s)
                if job.status == "done" or s.can_fetch:
                    self._source_changed(s.key)
        live = any(j.active or j.status == "queued" for j in JOBS.items.values())
        if changed or live:
            self.refresh_all()

    # ── paint ────────────────────────────────────────────────────────
    def _genome_label(self) -> tuple[str, str]:
        if self.genome and self.genome.exists():
            return self.genome.name, "bold #FBF6F0"
        return "— none (open a file in F1)", "#B04A4A"

    def _prompt(self, s: Source) -> Text:
        st = self.status.get(s.key)
        state = st.state if st else "unset"
        icon, style = (
            ("…", "#AEA5B8") if s.key in self.checking and not st else STATE_ICON[state]
        )
        t = Text()
        t.append(f"{icon} ", style=style)
        t.append(fit(s.title, PROMPT_W) + "\n", style="bold")
        job = JOBS.get(s.key)
        n_q = len(JOBS.queued_for(s.key))
        if job and job.active:
            mark = "Ⅱ " if job.status == "paused" else f"{job_icon(job)} "
            extra = f" · +{n_q} queued" if n_q else ""
            t.append(
                "  " + fit(f"{mark}{job_line(job)}{extra}", PROMPT_W) + "\n",
                style="#729fcf",
            )
        elif n_q:
            t.append("  " + fit(f"○ {n_q} queued", PROMPT_W) + "\n", style="#729fcf")
        else:
            t.append(
                "  " + fit(st.summary if st else "checking…", PROMPT_W) + "\n",
                style=style,
            )
        if s.kind == "secret":
            if s.key == "gemini":
                try:
                    from ..core import llm

                    key = llm.studio_api_key(self.store)
                    project = llm.vertex_project(self.store)
                    adc = llm.vertex_adc_path(self.store)
                    bits = [
                        f"key · …{key[-4:]}" if key else "key —",
                        f"proj · {project}" if project else "proj —",
                        "adc · ok" if adc and adc.is_file() else "adc —",
                    ]
                    t.append("  " + fit(" · ".join(bits), PROMPT_W), style="#71697A")
                except Exception:  # noqa: BLE001
                    t.append("  store busy", style="#71697A")
            else:
                key = self._secret(s.key)
                t.append(
                    "  " + fit(f"key · …{key[-4:]}" if key else "key not set", PROMPT_W),
                    style="#71697A",
                )
        else:
            path = self._path(s.key)
            t.append(
                "  " + fit(short_path(path) if path else "path not set", PROMPT_W),
                style="#71697A",
            )
        return t

    def refresh_all(self) -> None:
        try:
            ol = self.query_one("#sources", OptionList)
        except KeyError:
            return
        keep = None
        opt = ol.highlighted_option
        if opt and opt.id and not opt.disabled:
            keep = opt.id
        # Rebuild prompts in place when possible.
        for i, o in enumerate(ol.options):
            if o.disabled or not o.id or str(o.id).startswith("grp:"):
                continue
            s = BY_KEY.get(o.id)
            if s:
                o.lines = _text_lines(self._prompt(s))
                o.label = s.title
        if keep:
            for i, o in enumerate(ol.options):
                if o.id == keep:
                    ol.highlighted = i
                    break
        self._sync_keybar()
        self.refresh_details()
        self.refresh_actions()
        self.update_status()
        if self.app is not None:
            self.app.request_draw()

    def refresh_details(self) -> None:
        s = self.source
        self._ensure_enrich(s)
        st = self.status.get(s.key)
        path = self._path(s.key)
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

        gname, gstyle = self._genome_label()
        t.append("\nACTIVE GENOME (F1)\n", style="bold #729fcf")
        t.append("  File: ", style="#AEA5B8")
        t.append(f"{gname}\n", style=gstyle)
        if self.genome and self.genome.exists():
            t.append("  Folder: ", style="#AEA5B8")
            t.append(f"{self.genome.parent}\n", style="#C3BCCC")
            if s.can_fetch:
                t.append("  Used for: ", style="#AEA5B8")
                t.append(
                    "which Discover haplogroups to fetch for your Y markers (D)\n",
                    style="#C3BCCC",
                )
        else:
            t.append(
                "  Set an active genotype in F1 — then D can fetch haplogroups for your Y line.\n",
                style="#8A8294",
            )

        t.append("\nLOCATION\n", style="bold #729fcf")
        if s.kind == "secret":
            if s.key == "gemini":
                try:
                    from ..core import llm

                    key = llm.studio_api_key(self.store)
                    project = llm.vertex_project(self.store)
                    adc = llm.vertex_adc_path(self.store)
                    t.append("  AI Studio key: ", style="#AEA5B8")
                    t.append(
                        f"set · …{key[-4:]}\n" if key else "not set (P)\n",
                        style="" if key else "#8A8294",
                    )
                    t.append("  Cloud project: ", style="#AEA5B8")
                    t.append(
                        f"{project}\n" if project else "not set (P)\n",
                        style="" if project else "#8A8294",
                    )
                    t.append("  ADC file: ", style="#AEA5B8")
                    if adc and adc.is_file():
                        t.append(f"{adc}\n")
                    else:
                        t.append(f"not found · {llm.adc_hint(self.store)}\n", style="#8A8294")
                except Exception as exc:  # noqa: BLE001
                    t.append(f"  store busy: {exc}\n", style="#B04A4A")
            else:
                key = self._secret(s.key)
                t.append("  Key: ", style="#AEA5B8")
                t.append(
                    f"set · …{key[-4:]}\n" if key else "not set\n",
                    style="" if key else "#8A8294",
                )
        else:
            t.append(f"  {s.kind.capitalize()}: ", style="#AEA5B8")
            t.append(f"{path}\n" if path else "not set\n", style="" if path else "#8A8294")
        t.append("  Source: ", style="#AEA5B8")
        t.append(f"{s.url}\n")
        t.append("  Size: ", style="#AEA5B8")
        t.append(f"{s.size_hint}\n")

        if st and st.facts:
            t.append("\nCONTENT\n", style="bold #729fcf")
            for k, v in st.facts:
                t.append(f"  {k}: ", style="#AEA5B8")
                t.append(f"{v}\n")

        if st and st.files:
            t.append("\nFILES\n", style="bold #729fcf")
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
            t.append("\nTASK\n", style="bold #729fcf")
        if job:
            style = JOB_STYLE.get(job.status, "#AEA5B8")
            t.append(f"  {job.status.upper()}", style=f"bold {style}")
            started = time.strftime("%H:%M:%S", time.localtime(job.started))
            took = duration((job.finished or time.time()) - job.started)
            t.append(f"  started {started} · {took}\n", style="#8A8294")
            if job.active:
                t.append(f"  {job.stage}\n")
                if job.fraction is not None:
                    t.append("  " + bar(job.fraction, 40).ljust(40), style="#729fcf")
                    t.append(f" {job.fraction:6.1%}\n")
                size = (
                    _amount(job, job.done)
                    + (f" of {_amount(job, job.total)}" if job.total else "")
                )
                t.append(f"  {size} · {job_line(job)}\n", style="#AEA5B8")
            else:
                t.append(f"  {job.message}\n", style=style)
        for qj in queued:
            t.append(f"  QUEUED  {qj.title}\n", style="#8A8294")
        self.query_one("#details", RichText).update(t)

    def refresh_actions(self) -> None:
        s = self.source
        st = self.status.get(s.key)
        path = self._path(s.key)
        job = JOBS.get(s.key)
        t = Text()
        t.append(f"{s.title}\n", style="bold #FBF6F0")
        t.append("Press the key (or KeyBar).\n\n", style="#8A8294")

        def action(key: str, text: str) -> None:
            t.append(f"[{key}] ", style="bold #729fcf")
            t.append(text + "\n\n")

        if s.kind == "secret":
            if s.key == "gemini":
                action("P", "set AI Studio key / Cloud project / ADC file")
            else:
                action("P", "paste the API key")
        else:
            action("P", f"point to an existing {s.kind}")

        cov = st.coverage if st else None
        if s.can_fetch and cov:
            plan = fetch_plan(self.store)
            if not self.genome:
                t.append(
                    "[1–4]  no active genome in F1 — open a genotype file there first\n\n",
                    style="#8A8294",
                )
            elif isinstance(plan, str):
                t.append(f"[1–5]  {plan}\n\n", style="#8A8294")
            else:
                tip = plan.tip or "—"
                t.append(f"Discover fetch stages · tip {tip}\n", style="bold #729fcf")
                t.append("Busy slot → queue (F8).\n\n", style="#8A8294")
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
                        action(key, f"{label}: {note}")
                    else:
                        t.append(f"[{key}]  {label}: done\n\n", style="#8A8294")
                t.append("[D]  same as [1] Our path\n\n", style="#8A8294")
        elif s.key == "openhumans":
            action("B", "open F9 Samples catalog (D on a row downloads)")
        elif s.can_download:
            if getattr(s, "needs_genome", False) and not self.genome:
                t.append(
                    "[D]  needs an active genome in F1 (coverage uses your SNPs)\n\n",
                    style="#8A8294",
                )
            elif s.key == "pgs_models":
                action("D", "build/update catalog (API + weights + coverage vs F1)")
            else:
                action("D", "download (asks where; existing complete files are skipped)")
        else:
            t.append(f"[D]  {s.download_note}\n\n", style="#8A8294")

        if s.can_verify:
            if s.key == "openhumans":
                action("V", "API new items → download missing → DuckDB")
            else:
                action("V", "verify MD5 checksums of the files")
        action("R", "recheck")
        if job and job.active:
            action("C", "cancel the running/paused task")
        elif JOBS.queued_for(s.key):
            action("C", "cancel active slot task (queued stay — manage in F8)")
        if s.kind == "secret":
            has = bool(self._secret(s.key))
            if s.key == "gemini" and not has:
                try:
                    from ..core import llm

                    has = bool(
                        llm.vertex_project(self.store)
                        or self.store.setting(llm.SETTING_GCP_ADC)
                    )
                except Exception:  # noqa: BLE001
                    has = False
            if has:
                if s.key == "gemini":
                    action("Del", "forget key, project, and ADC path")
                else:
                    action("Del", "forget the API key")
        elif path:
            action("Del", "forget the path (files are untouched)")
        self.query_one("#actions", RichText).update(t)

    def update_status(self) -> None:
        live = [j for j in JOBS.items.values() if j.active]
        n_q = sum(1 for j in JOBS.items.values() if j.status == "queued")
        ready = sum(
            1 for s in SOURCES if self.status.get(s.key) and self.status[s.key].state == "ok"
        )
        gname, _ = self._genome_label()
        genome = f"    GENOME: {gname if self.genome else '— (F1)'}"
        bits = []
        for j in live:
            mark = "Ⅱ" if j.status == "paused" else job_icon(j)
            bits.append(f"{mark} {j.title}: {job_line(j)}")
        if n_q:
            bits.append(f"○ {n_q} queued")
        jobs = ("    " + "    ".join(bits)) if bits else ""
        self.query_one("#status", StatusBar).update(
            f"READY: {ready}/{len(SOURCES)} sources{genome}{jobs}",
        )

    def _key_items(self) -> list[tuple[str, str, Callable[[], None] | None]]:
        from .app_api import focus_next

        try:
            s = self.source
        except Exception:  # noqa: BLE001
            s = None
        if s and s.key == "openhumans":
            return [
                ("↑↓", "Source", None),
                ("P", "Samples folder", lambda: self.set_path()),
                ("B", "Open F9 catalog", lambda: self.browse_openhumans()),
                ("V", "Update new", lambda: self.verify()),
                ("R", "Recheck", lambda: self.recheck()),
                ("C", "Cancel", lambda: self.cancel_job()),
                ("Del", "Forget path", lambda: self.clear_path()),
                ("Tab", "Panel", lambda: focus_next(self)),
                ("F1–F10", "Modes", None),
                ("Q", "Quit", lambda: self.app.ask_quit() if self.app else None),
            ]
        return [
            ("↑↓", "Source", None),
            ("P", "Set path", lambda: self.set_path()),
            ("D/1–5", "Fetch/DL", lambda: self.download()),
            ("V", "Verify", lambda: self.verify()),
            ("R", "Recheck", lambda: self.recheck()),
            ("C", "Cancel", lambda: self.cancel_job()),
            ("Del", "Forget", lambda: self.clear_path()),
            ("Tab", "Panel", lambda: focus_next(self)),
            ("F1–F10", "Modes", None),
            ("Q", "Quit", lambda: self.app.ask_quit() if self.app else None),
        ]

    def _sync_keybar(self) -> None:
        if self._keybar is not None:
            self._keybar.set_items(self._key_items())

    # ── actions ──────────────────────────────────────────────────────
    def _apply_path(self, s: Source, path: Path) -> None:
        self.store.set_path(s.key, path)
        self.status.pop(s.key, None)
        if s.key in LINKED:
            self._enrich_dirty |= LINKED
        self.check_related(s)
        self.refresh_all()
        self._source_changed(s.key)

    def set_path(self) -> None:
        s = self.source
        if s.kind == "secret" and s.key == "gemini":
            self._set_gemini()
            return
        if s.kind == "secret":
            def done_key(value: str) -> None:
                self.store.set_secret(s.key, value)
                self.status.pop(s.key, None)
                self.check(s)
                self.refresh_all()
                self._notify(f"{s.title}: key saved")

            self._ask_secret(f"{s.title} · API key", done_key)
            return

        def done(path: Path) -> None:
            self._apply_path(s, path)
            self._notify(f"{s.title}: path set")

        initial = self._path(s.key) or s.suggest(self.store)
        self._ask_path(
            f"{s.title} · path to existing {s.kind}",
            initial,
            done,
            must_exist=True,
        )

    def _set_gemini(self) -> None:
        """GeminiConfigScreen analogue — one form: key + project + ADC."""
        from ..core import llm

        from .app_api import require_app

        if not require_app(self, what="Gemini config"):
            return
        s = self.source
        has_key = bool(llm.studio_api_key(self.store))
        project = (llm.vertex_project(self.store) or "").strip()
        adc_path = llm.vertex_adc_path(self.store) or llm.default_adc_path()
        adc = str(adc_path) if adc_path else ""
        key_label = (
            "AI Studio API key (blank = keep current)"
            if has_key else "AI Studio API key"
        )

        def done(values: dict[str, str] | None) -> None:
            if not values:
                return
            key = (values.get("key") or "").strip()
            proj = (values.get("project") or "").strip()
            adc_v = (values.get("adc") or "").strip()
            if (not key and not proj and not adc_v
                    and not has_key and not project and not adc):
                self._notify("Enter a key, Cloud project, and/or ADC path")
                return
            if adc_v:
                path = Path(adc_v).expanduser()
                if not path.is_file():
                    self._notify(f"ADC file not found: {path}")
                    return
            notes: list[str] = []
            if key:
                self.store.set_secret(s.key, key)
                notes.append("key")
            llm.set_vertex_project(self.store, proj or None)
            notes.append("project")
            llm.set_vertex_adc(self.store, adc_v or None)
            notes.append("ADC")
            self.status.pop(s.key, None)
            self.check(s)
            self.refresh_all()
            self._notify(f"{s.title}: saved ({', '.join(notes)})")

        self.app.ask_form(
            "Google Gemini · F2 credentials",
            [
                ("key", key_label, ""),
                ("project", "Vertex AI · Cloud project ID", project),
                ("adc", "Vertex AI · ADC JSON path", adc),
            ],
            done,
            hint="OK saves · blank key keeps old · ADC is a file path · Esc cancel",
            show_reset=False,
            ok_label="OK",
            password_fields=("key",),
            box_w=720,
        )

    def _fetch(self, s: Source, names: list[str], what: str) -> None:
        path = self._path(s.key)
        if not path:
            self._notify("Set the path first (P)")
            return
        job = JOBS.enqueue(s.key, f"Fetch {what}", lambda j: s.fetch(path, names, j))
        if job.status == "queued":
            self._notify(f"Queued: {job.title}")
        self.refresh_all()

    def browse_openhumans(self) -> None:
        if "openhumans" not in BY_KEY:
            return
        if self._path("openhumans") is None:
            folder = BY_KEY["openhumans"].suggest(self.store)
            folder.mkdir(parents=True, exist_ok=True)
            try:
                self.store.set_path("openhumans", folder)
            except Exception as exc:  # noqa: BLE001
                self._notify(f"Cannot set path: {exc}")
                return
        self._goto_mode("samples")

    def download(self) -> None:
        s = self.source
        if s.key == "openhumans":
            self.browse_openhumans()
            return
        if s.can_fetch:
            self.fetch_stage("our")
            return
        if not s.can_download:
            self._notify(s.download_note or "Download not available")
            return
        if getattr(s, "needs_genome", False) and not self.genome:
            self._notify("Load a genotype file in F1 first — catalog coverage uses it")
            return
        job = JOBS.get(s.key)
        if job and job.active:
            self._notify(f"{s.title}: a task is already running (pause/cancel in F8)")
            return

        def done(path: Path) -> None:
            self.store.set_path(s.key, path)
            title = f"Build {s.title}" if s.key == "pgs_models" else f"Download {s.title}"
            JOBS.start(s.key, title, lambda j: s.download(path, j))
            self.check(s)
            self.refresh_all()

        prompt = (
            f"Build {s.title} ({s.size_hint}) · work folder"
            if s.key == "pgs_models"
            else f"Download {s.title} ({s.size_hint}) · save to this {s.kind}"
        )
        self._ask_path(prompt, s.suggest(self.store), done, must_exist=False)

    def fetch_1(self) -> None:
        self.fetch_stage("our")

    def fetch_2(self) -> None:
        self.fetch_stage("children")

    def fetch_3(self) -> None:
        self.fetch_stage("genome")

    def fetch_4(self) -> None:
        self.fetch_stage("all_genomes")

    def fetch_5(self) -> None:
        self.fetch_stage("tree")

    def fetch_stage(self, stage: str = "our") -> None:
        s = self.source
        if not s.can_fetch:
            return
        labels = dict(FETCH_STAGES)
        title = labels.get(stage, stage)
        if stage in ("our", "children", "genome") and not self.genome:
            self._notify("Load a genotype file in F1 first")
            return
        if stage == "tree":
            plan = fetch_plan(self.store)
            n = plan.count("tree") if not isinstance(plan, str) else 0
            if n <= 0:
                self._notify("Whole tree: nothing to fetch")
                return
            if time.monotonic() - self.armed > 5:
                self.armed = time.monotonic()
                self._notify(
                    f"Press 5 or A again within 5 s to fetch {n:,} haplogroups "
                    f"({fetch_cost(n)}). Cancel anytime from F2/F8.",
                )
                return
            self.armed = 0.0
        names = stage_fetch_names(self.store, stage)
        if not names:
            self._notify(f"{title}: nothing to fetch")
            return
        tip = ""
        plan = fetch_plan(self.store)
        if not isinstance(plan, str) and plan.tip:
            tip = f" · tip {plan.tip}"
        self._fetch(s, names, f"{title} · {len(names):,}{tip}")
        from ..core import ylink as _ylink

        _ylink._plan_cache = None
        _ylink._cov_cache = None

    def verify(self) -> None:
        s = self.source
        path = self._path(s.key)
        if not s.can_verify:
            self._notify("No published checksums for this source")
            return
        if not path and s.key == "openhumans":
            path = s.suggest(self.store)
            path.mkdir(parents=True, exist_ok=True)
            try:
                self.store.set_path(s.key, path)
            except Exception as exc:  # noqa: BLE001
                self._notify(f"Cannot set path: {exc}")
                return
        if not path:
            self._notify("Set the path first (P)")
        elif (job := JOBS.get(s.key)) and job.active:
            self._notify(f"{s.title}: a task is already running")
        else:
            title = f"Sync {s.title}" if s.key == "openhumans" else f"Verify {s.title}"
            JOBS.start(s.key, title, lambda j: s.verify(path, j))
            self.refresh_all()

    def cancel_job(self) -> None:
        job = JOBS.get(self.source.key)
        if job and job.active:
            job.cancel()
            self._notify(f"Cancelling: {job.title}")
        else:
            self._notify("Nothing is running for this source")

    def recheck(self) -> None:
        s = self.source
        self.status.pop(s.key, None)
        self.check(s, enrich=s.key in LINKED)
        self.refresh_all()

    def clear_path(self) -> None:
        s = self.source
        if s.kind == "secret":
            if s.key == "gemini":
                from ..core import llm

                had = bool(
                    self.store.secret(s.key)
                    or llm.vertex_project(self.store)
                    or self.store.setting(llm.SETTING_GCP_ADC)
                )
                if not had:
                    return
                self.store.set_secret(s.key, None)
                llm.set_vertex_project(self.store, None)
                llm.set_vertex_adc(self.store, None)
                self.status.pop(s.key, None)
                self.check(s)
                self.refresh_all()
                self._notify(f"{s.title}: key, project, and ADC path forgotten")
                return
            if not self.store.secret(s.key):
                return
            self.store.set_secret(s.key, None)
            self.status.pop(s.key, None)
            self.check(s)
            self.refresh_all()
            self._notify(f"{s.title}: key forgotten")
            return
        if not self._path(s.key):
            return
        try:
            self.store.set_path(s.key, None)
        except Exception as exc:  # noqa: BLE001
            self._notify(f"Cannot clear path: {exc}")
            return
        self.status.pop(s.key, None)
        self.check_related(s)
        self.refresh_all()
        self._source_changed(s.key)
        self._notify(f"{s.title}: path forgotten (files are untouched)")


def _amount(job: Job, n: float) -> str:
    return human_size(n) if job.unit == "B" else f"{n:,.0f}"
