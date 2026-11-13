"""F5 GWAS: match the active F1 genome against the local GWAS Catalog (F2)."""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ...core import chat, genome_db, gwas_curated, gwas_match
from ...core.gwas_match import (
    Assoc, MatchResult, P_CUTS, catalog_date, sort_assocs_by_dose,
)
from ...core.jobs import JOBS
from ...core.sources import GwasCatalog
from ...core.store import Store
from ...core.ylink import active_genome
from ..base import KeyBar, ModeBar, Workspace, alt_keys, hide_busy, show_busy
from .ask import (
    ask_subject, ask_title, build_ask_prompt, load_cached, save_cached,
)
from .view import (
    DETAIL_TABS, TRAIT_TABS, ask_tab_text, assoc_legend, assoc_prompt,
    detail_tab_bar, detail_text, empty_state, p_cut_label, stats_text,
    theme_header, trait_prompt, trait_tab_bar,
)

JOB = "gwas_match"
JOB_ASK = "gwas-ask"


class GwasWorkspace(Workspace):
    mode_key = "gwas"
    DEFAULT_CSS = """
    #stats { height: 4; padding: 0 1; border: round $primary; }
    #lists { height: 1fr; }
    #traits-box { width: 36; border: round $primary; }
    #assoc-box { width: 1fr; min-width: 40; border: round $primary; }
    #det-box { width: 72; border: round $primary; }
    #stats:focus-within, #traits-box:focus-within, #assoc-box:focus-within, #det-box:focus-within {
        border: round $accent;
    }
    #traits, #assocs { height: 1fr; border: none; background: transparent; }
    #trait-tabs { height: 1; padding: 0 1; }
    #trait-search { height: 1; margin: 0 1; border: none; background: transparent; }
    #assoc-legend { height: 1; padding: 0 1; color: $text-muted; }
    #det-tabs { height: 1; padding: 0 1; }
    #det-scroll { height: 1fr; scrollbar-size-vertical: 1; }
    #detail { padding: 0 1; }
    #help { height: 1; padding: 0 1; color: $text-muted; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """
    BINDINGS = [
        Binding("slash", "focus_search", "Search"),
        Binding("escape", "clear_search", "Clear search", show=False),
        Binding(alt_keys("p"), "cycle_p", "p-value cut"),
        Binding(alt_keys("r"), "rescan", "Rescan"),
        Binding(alt_keys("a"), "ask_chat", "Ask chat"),
        Binding(alt_keys("l"), "toggle_list", "CUR/ALL"),
        Binding(alt_keys("c"), "copy_rsid", "Copy rsID", show=False),
        Binding(alt_keys("b"), "open_pubmed", "PubMed", show=False),
    ]

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.genome: Path | None = None
        self.folder: Path | None = None
        self.result: MatchResult | None = None
        self.problem = ""
        self.p_max: float | None = 5e-8
        self.search_q = ""
        self.trait: str | None = None
        self.list_tab = "curated"  # curated (CUR) | browse (ALL)
        self._load_gen = 0
        self._first_paint_done = False
        self._assoc_by_id: dict[str, Assoc] = {}
        self._trait_index: dict[str, int] = {}
        self._trait_haystacks: dict[str, str] = {}
        self._assocs_by_trait: dict[str, list[Assoc]] = {}
        self._box: list[MatchResult] = []
        self._pending_gen: int | None = None
        self._seen_job: int | None = None
        self.detail_tab: str = "hit"
        self.ask_cache: dict[str, dict] = {}
        self.ask_job_key: str | None = None
        self._ask_seen: set[int] = set()
        self._search_timer = None

    def compose(self) -> ComposeResult:
        yield ModeBar(self.mode_key)
        yield Static(id="stats")
        with Horizontal(id="lists"):
            with Vertical(id="traits-box"):
                yield Static(id="trait-tabs")
                yield Input(placeholder="/ trait · rsID · gene · GCST", id="trait-search")
                yield OptionList(id="traits")
            with Vertical(id="assoc-box"):
                yield Static(id="assoc-legend")
                yield OptionList(id="assocs")
            with Vertical(id="det-box"):
                yield Static(id="det-tabs")
                with VerticalScroll(id="det-scroll"):
                    yield Static(id="detail")
        yield KeyBar([
            ("/", "Traits", "screen.focus_search"),
            ("L", "CUR/ALL", "screen.toggle_list"),
            ("P", "p-cut", "screen.cycle_p"),
            ("R", "Rescan", "screen.rescan"),
            ("A", "Ask", "screen.ask_chat"),
            ("C", "Copy rs", "screen.copy_rsid"),
            ("B", "PubMed", "screen.open_pubmed"),
            ("Tab", "Panel", "app.focus_next"),
            ("F1–F10", "Modes", None),
            ("Q", "Quit", "app.quit"),
        ], id="help")
        yield Static(id="status")

    def on_mount(self) -> None:
        self.query_one("#stats").border_title = "MATCH"
        self.query_one("#traits-box").border_title = "TRAITS"
        self.query_one("#assoc-box").border_title = "ASSOCIATIONS"
        self.query_one("#det-box").border_title = "DETAIL"
        self.query_one("#trait-tabs", Static).update(trait_tab_bar(self.list_tab))
        self.query_one("#assoc-legend", Static).update(assoc_legend())
        self.query_one("#det-tabs", Static).update(detail_tab_bar(self.detail_tab))
        self.set_interval(0.35, self._tick)
        show_busy(self.app, "GWAS match", "Loading…")
        self.call_after_refresh(self._first_paint)

    def _first_paint(self) -> None:
        if self._first_paint_done or not self.is_mounted:
            hide_busy(self.app)
            return
        self._first_paint_done = True
        try:
            self.reload()
            self.query_one("#traits", OptionList).focus()
        finally:
            if self._pending_gen is not None:
                return
            hide_busy(self.app)

    def on_screen_resume(self) -> None:
        if not self._first_paint_done:
            return
        g = self._active()
        folder = self.store.path("gwas_catalog")
        if g != self.genome or folder != self.folder:
            self.reload()

    def _active(self) -> Path | None:
        ws = getattr(self.app, "genome_ws", None)
        if ws and ws.active and ws.active.exists():
            return ws.active
        return active_genome()

    def reload(self, *, force: bool = False) -> None:
        self._load_gen += 1
        gen = self._load_gen
        self.genome = self._active()
        self.folder = self.store.path("gwas_catalog")
        self.result = None
        self.problem = ""
        self.trait = None
        self._assoc_by_id.clear()
        self._trait_haystacks = {}
        self._assocs_by_trait = {}
        self._box = []
        self._pending_gen = None
        self._cancel_search_timer()
        self._paint_shell()
        self.fill_traits()
        self.fill_assocs()
        self.refresh_detail()
        self.update_status()

        if self.genome is None:
            self.problem = "genome"
            self._paint_shell()
            hide_busy(self.app)
            return
        folder = self.folder
        if folder is None or GwasCatalog().associations_tsv(folder) is None:
            self.problem = "catalog"
            self._paint_shell()
            hide_busy(self.app)
            return

        if (job := JOBS.get(JOB)) and job.active:
            self.app.notify("GWAS match already running", severity="warning")
            self.update_status()
            return

        show_busy(self.app, "GWAS match", f"Matching {self.genome.name}…")
        store = self.store
        genome_path = self.genome
        p_max = self.p_max
        box: list[MatchResult] = []
        self._box = box
        self._pending_gen = gen

        def work(job) -> str:
            # Always ensure() — F1 may hold a meta-only Genome without call arrays.
            g = genome_db.get(genome_path, store=store)
            result = gwas_match.match_genome(
                folder, g, p_max=p_max, job=job, use_cache=not force)
            box.append(result)
            return (f"{result.matched_rs:,} SNP · {result.traits_hit:,} traits · "
                    f"{len(result.assocs):,} rows")

        JOBS.start(JOB, f"GWAS match · {genome_path.name}", work)
        self._seen_job = id(JOBS.get(JOB))
        self.update_status()

    def _tick(self) -> None:
        ask = JOBS.get(JOB_ASK)
        if ask:
            if ask.active:
                if self.ask_job_key and self.detail_tab == "ask":
                    self.refresh_detail()
                self.update_status()
            elif id(ask) not in self._ask_seen:
                self._ask_seen.add(id(ask))
                if self.ask_job_key:
                    self._finish_ask_job(ask)
        job = JOBS.get(JOB)
        if self._pending_gen is None:
            if job and job.running:
                self.update_status()
            return
        if job is None or job.active:
            if job and job.running:
                self.update_status()
            return
        gen = self._pending_gen
        self._pending_gen = None
        hide_busy(self.app)
        if gen != self._load_gen:
            return
        if job.status == "failed":
            self.problem = job.message or "match failed"
            self.result = None
        elif job.status == "cancelled":
            self.problem = "cancelled"
            self.result = None
        elif self._box:
            self.problem = ""
            self.result = self._box[-1]
            self.trait = None  # fill_traits picks first visible (CUR/ALL)
            self._trait_haystacks, self._assocs_by_trait = (
                gwas_curated.build_trait_index(self.result))
        else:
            self.problem = "no result"
            self.result = None
            self._trait_haystacks = {}
            self._assocs_by_trait = {}
        self._paint_shell()
        self.fill_traits()
        self.fill_assocs()
        self.refresh_detail()
        self.update_status()

    def _paint_shell(self) -> None:
        date = catalog_date(self.folder) if self.folder else ""
        if self.problem in ("genome", "catalog"):
            self.query_one("#stats", Static).update(empty_state(self.problem))
        elif self.problem:
            self.query_one("#stats", Static).update(
                stats_text(None, genome_name=None, catalog_date=date, problem=self.problem))
        else:
            self.query_one("#stats", Static).update(
                stats_text(
                    self.result,
                    genome_name=self.genome.name if self.genome else None,
                    catalog_date=date or (self.result.catalog_date if self.result else ""),
                ))

    def _traits(self) -> list[tuple[str | None, str, int]]:
        if not self.result:
            return []
        return gwas_curated.filter_traits(
            self.result,
            curated=self.list_tab == "curated",
            query=self.search_q,
            haystacks=self._trait_haystacks,
        )

    def _assocs(self) -> list[Assoc]:
        if not self.result or not self.trait:
            return []
        rows = self._assocs_by_trait.get(self.trait)
        if rows is None:
            rows = gwas_match.assocs_for_trait(self.result, self.trait)
        q = self.search_q.strip().lower()
        if q:
            rows = [
                a for a in rows
                if q in a.rsid.lower() or q in (a.gene or "").lower()
                or q in (a.study or "").lower() or q in a.trait.lower()
                or q in (a.mapped_trait or "").lower()
            ]
        return sort_assocs_by_dose(rows)

    def _paint_trait_tabs(self) -> None:
        self.query_one("#trait-tabs", Static).update(trait_tab_bar(self.list_tab))

    def fill_traits(self) -> None:
        ol = self.query_one("#traits", OptionList)
        keep = self.trait
        ol.clear_options()
        self._trait_index.clear()
        self._paint_trait_tabs()
        rows = self._traits()
        if not rows:
            self.trait = None
            label = "CUR" if self.list_tab == "curated" else "ALL"
            self.query_one("#traits-box").border_title = f"TRAITS · {label} · 0"
            return
        opts: list[Option] = []
        group: str | None = None
        curated = self.list_tab == "curated"
        theme_n: dict[str, int] = {}
        if curated:
            for theme, _, _ in rows:
                if theme:
                    theme_n[theme] = theme_n.get(theme, 0) + 1
        for theme, trait, n in rows:
            if curated and theme and theme != group:
                group = theme
                opts.append(Option(theme_header(theme, theme_n.get(theme, 0)), disabled=True))
            self._trait_index[trait] = len(opts)
            opts.append(Option(trait_prompt(trait, n), id=f"trait:{trait}"))
        ol.add_options(opts)
        if keep in self._trait_index:
            ol.highlighted = self._trait_index[keep]
            self.trait = keep
        else:
            first = rows[0][1]
            ol.highlighted = self._trait_index[first]
            self.trait = first
        label = "CUR" if curated else "ALL"
        self.query_one("#traits-box").border_title = f"TRAITS · {label} · {len(rows)}"

    def fill_assocs(self) -> None:
        ol = self.query_one("#assocs", OptionList)
        ol.clear_options()
        self._assoc_by_id.clear()
        rows = self._assocs()
        opts = []
        for i, a in enumerate(rows):
            oid = f"a:{i}"
            self._assoc_by_id[oid] = a
            opts.append(Option(assoc_prompt(a), id=oid))
        ol.add_options(opts)
        if rows:
            ol.highlighted = 0
        title = "ASSOCIATIONS"
        if self.trait:
            title += f" · {len(rows)}"
        self.query_one("#assoc-box").border_title = title

    def _current_assoc(self) -> Assoc | None:
        ol = self.query_one("#assocs", OptionList)
        if ol.highlighted is None:
            return None
        opt = ol.get_option_at_index(ol.highlighted)
        return self._assoc_by_id.get(opt.id or "")

    def _ask_key(self, a: Assoc | None = None) -> str | None:
        if self.result is None:
            return None
        return ask_subject(a if a is not None else self._current_assoc(),
                           self.trait, self.result)

    def _ask_entry(self, key: str | None) -> dict | None:
        if not key:
            return None
        if key in self.ask_cache:
            return self.ask_cache[key]
        hit = load_cached(key)
        if hit:
            self.ask_cache[key] = hit
        return hit

    def refresh_detail(self) -> None:
        self.query_one("#det-tabs", Static).update(detail_tab_bar(self.detail_tab))
        a = self._current_assoc()
        if self.detail_tab == "ask":
            key = self._ask_key(a)
            busy = bool(key and self.ask_job_key == key
                        and (job := JOBS.get(JOB_ASK)) and job.active)
            draft = ""
            model_hint = ""
            if busy and (job := JOBS.get(JOB_ASK)):
                draft = job.draft or ""
                model_hint = job.stage or chat.model_label(self.store)
            body = ask_tab_text(
                a, self.result,
                trait=self.trait,
                genome_name=self.genome.name if self.genome else None,
                cache=None if busy else self._ask_entry(key),
                busy=busy,
                draft=draft,
                model_hint=model_hint,
                width=max(28, self.query_one("#detail").size.width or 34),
            )
        else:
            body = detail_text(a, self.result)
        self.query_one("#detail", Static).update(body)

    def action_detail_tab(self, key: str) -> None:
        if key not in DETAIL_TABS or key == self.detail_tab:
            return
        self.detail_tab = key
        self.refresh_detail()

    def action_ask_chat(self, refresh: bool = False) -> None:
        """ASK tab: scientific brief via shared GWAS · F5 chat; show inline."""
        if self.result is None:
            self.app.notify("No match yet — wait for GWAS scan or fix F1/F2",
                            severity="warning")
            return
        err = chat.can_send(self.store)
        if err:
            self.app.notify(err, severity="warning")
            return
        a = self._current_assoc()
        key = self._ask_key(a)
        if not key:
            return
        if self.detail_tab != "ask":
            self.detail_tab = "ask"
        if not refresh and self._ask_entry(key):
            self.refresh_detail()
            return
        if (old := JOBS.get(JOB_ASK)) and old.active:
            old.cancel()
            # Next tick / retry if still winding down.
            import time
            for _ in range(40):
                j = JOBS.get(JOB_ASK)
                if not j or not j.active:
                    break
                time.sleep(0.05)
            if (j := JOBS.get(JOB_ASK)) and j.active:
                self.app.notify("Ask still stopping · try again", severity="warning")
                self.refresh_detail()
                return

        prompt = build_ask_prompt(
            self.result,
            trait=self.trait,
            assoc=a,
            genome_name=self.genome.name if self.genome else None,
        )
        title = ask_title(a, self.trait)
        store = self.store
        subject = key
        label = self.trait or (a.rsid if a else "trait")

        def work(job) -> str:
            t = chat.ensure_gwas_thread()
            headed = f"[F5 · {label}]\n\n{prompt}"
            t.messages.append(chat.Message("user", headed))
            chat.save(t)
            chat.send(
                store, t, job, continue_chat=False, tier="reasoning", use_saved=False,
            )
            text = next((m.text for m in reversed(t.messages) if m.role == "model"), "")
            return (text or job.draft or "").strip() or "(empty reply)"

        self.ask_job_key = subject
        try:
            JOBS.start(JOB_ASK, title, work)
        except RuntimeError as exc:
            self.ask_job_key = None
            self.app.notify(str(exc), severity="warning")
            self.refresh_detail()
            return
        self.refresh_detail()
        self.update_status()

    def _finish_ask_job(self, job) -> None:
        key = self.ask_job_key
        self.ask_job_key = None
        if not key:
            self.refresh_detail()
            return
        if job.status == "done":
            text = (job.message or job.draft or "").strip()
            if text in ("done",):
                text = (job.draft or "").strip()
            model = chat.model_label(self.store)
            ts = chat.now()
            entry = {"text": text or "(empty reply)", "model": model, "ts": ts}
            self.ask_cache[key] = entry
            try:
                save_cached(key, text=entry["text"], model=model, ts=ts)
            except OSError:
                pass
            self.app.notify(f"GWAS note · {key.split('|')[0]}", timeout=5)
        elif job.status == "failed":
            self.app.notify(f"GWAS ask failed: {job.message}", severity="error",
                            timeout=10)
        else:
            self.app.notify(f"GWAS ask: {job.message or job.status}",
                            severity="warning", timeout=8)
        self.detail_tab = "ask"
        self.refresh_detail()
        self.update_status()

    def update_status(self) -> None:
        if self.problem == "genome":
            line = "GENOME: — (F1)"
        elif self.problem == "catalog":
            line = "CATALOG: — (F2 · GWAS Catalog)"
        elif self.problem:
            line = f"ERROR: {self.problem[:80]}"
        elif self.result:
            line = (f"{self.genome.name if self.genome else '—'}    "
                    f"MATCHED {self.result.matched_rs:,}/{self.result.genome_rs:,} rs    "
                    f"{p_cut_label(self.p_max)}    "
                    f"{self.trait or '—'}")
        else:
            line = "Matching…"
        job = JOBS.get(JOB)
        if job and job.running:
            line += f"    ⚙ {job.stage}"
        ask = JOBS.get(JOB_ASK)
        if ask and ask.running:
            line += f"    ⚙ ask {ask.stage}"
        self.query_one("#status", Static).update(line)

    @on(OptionList.OptionHighlighted, "#traits")
    def _trait_hi(self) -> None:
        ol = self.query_one("#traits", OptionList)
        if ol.highlighted is None:
            return
        opt = ol.get_option_at_index(ol.highlighted)
        oid = opt.id or ""
        if not oid.startswith("trait:"):
            return
        self.trait = oid[len("trait:"):]
        self.fill_assocs()
        self.refresh_detail()
        self.update_status()

    def action_list_tab(self, key: str) -> None:
        ids = {k for k, _ in TRAIT_TABS}
        if key not in ids or key == self.list_tab:
            return
        self.list_tab = key
        self.fill_traits()
        self.fill_assocs()
        self.refresh_detail()
        self.update_status()

    def action_toggle_list(self) -> None:
        ids = [k for k, _ in TRAIT_TABS]
        i = ids.index(self.list_tab) if self.list_tab in ids else -1
        self.action_list_tab(ids[(i + 1) % len(ids)])

    @on(OptionList.OptionHighlighted, "#assocs")
    def _assoc_hi(self) -> None:
        self.refresh_detail()

    def _cancel_search_timer(self) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
            self._search_timer = None

    def _apply_search(self) -> None:
        self._search_timer = None
        if not self.is_mounted:
            return
        self.fill_traits()
        self.fill_assocs()
        self.refresh_detail()

    @on(Input.Changed, "#trait-search")
    def _search(self, event: Input.Changed) -> None:
        self.search_q = event.value
        # Debounce: rebuilding OptionLists on every keystroke is the lag.
        self._cancel_search_timer()
        self._search_timer = self.set_timer(0.15, self._apply_search)

    @on(Input.Submitted, "#trait-search")
    def _search_submit(self) -> None:
        self._cancel_search_timer()
        self._apply_search()
        self.query_one("#traits", OptionList).focus()

    def action_focus_search(self) -> None:
        inp = self.query_one("#trait-search", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)

    def action_clear_search(self) -> None:
        inp = self.query_one("#trait-search", Input)
        if inp.has_focus or self.search_q:
            self._cancel_search_timer()
            inp.value = ""
            self.search_q = ""
            self._apply_search()
            self.query_one("#traits", OptionList).focus()

    def action_cycle_p(self) -> None:
        cuts = list(P_CUTS)
        try:
            i = cuts.index(self.p_max)
        except ValueError:
            i = 0
        self.p_max = cuts[(i + 1) % len(cuts)]
        self.app.notify(f"p-value cut: {p_cut_label(self.p_max)}")
        self.reload(force=True)

    def action_rescan(self) -> None:
        self.reload(force=True)

    def action_copy_rsid(self) -> None:
        a = self._current_assoc()
        if not a:
            self.app.notify("No association selected", severity="warning")
            return
        self.app.copy_to_clipboard(a.rsid)
        self.app.notify(f"Copied {a.rsid}")

    def action_open_pubmed(self) -> None:
        a = self._current_assoc()
        if not a or not a.pubmed:
            self.app.notify("No PubMed id on this row", severity="warning")
            return
        import webbrowser
        webbrowser.open(f"https://pubmed.ncbi.nlm.nih.gov/{a.pubmed}/")
