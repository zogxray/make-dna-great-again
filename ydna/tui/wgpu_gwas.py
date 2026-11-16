"""F5 GWAS screen for textual-wgpu — traits · associations · detail/ASK."""

from __future__ import annotations

import ast
import re
import time
import webbrowser
from pathlib import Path

from rich.text import Text

from textual_wgpu.screen import Screen
from textual_wgpu.widgets import (
    Horizontal,
    Input,
    KeyBar,
    ModeBar,
    Option,
    OptionList,
    Panel,
    RichText,
    StatusBar,
    Vertical,
)

from ..core import chat, genome_db, gwas_curated, gwas_match
from ..core.gwas_match import Assoc, MatchResult, P_CUTS, catalog_date, sort_assocs_by_dose
from ..core.jobs import JOBS
from ..core.sources import GwasCatalog
from ..core.store import Store
from ..core.ylink import active_genome
from ..workspaces.base import MODE_LABELS
from ..workspaces.gwas.ask import (
    ask_subject,
    ask_title,
    build_ask_prompt,
    load_cached,
    save_cached,
)
from ..workspaces.gwas.view import (
    DETAIL_TABS,
    TRAIT_TABS,
    ask_tab_text,
    assoc_legend,
    assoc_prompt,
    detail_tab_bar,
    detail_text,
    empty_state,
    p_cut_label,
    stats_text,
    theme_header,
    trait_prompt,
    trait_tab_bar,
)
from .app_api import focus_next
from .wgpu_jobs import _text_lines

JOB = "gwas_match"
JOB_ASK = "gwas-ask"
_ACTION_RE = re.compile(r"^screen\.(\w+)(?:\((.*)\))?$", re.DOTALL)


class GwasScreen(Screen):
    """Same F5 GWAS logic as Textual, hosted on wgpu widgets."""

    DEFAULT_CSS = """
    #lists { height: 1fr; }
    #traits-box { width: 36; border: round $primary; }
    #assoc-box { width: 1fr; min-width: 40; border: round $primary; }
    #det-box { width: 72; border: round $primary; }
    #traits-box:focus-within, #assoc-box:focus-within, #det-box:focus-within { border: round $accent; }
    #traits, #assocs { height: 1fr; border: none; background: transparent; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """

    BINDINGS = [
        ("slash", "focus_search"),
        ("/", "focus_search"),
        ("p", "cycle_p"),
        ("r", "rescan"),
        ("a", "ask_chat"),
        ("l", "toggle_list"),
        ("c", "copy_rsid"),
        ("b", "open_pubmed"),
        ("t", "toggle_detail"),
        ("h", "detail_hit"),
    ]

    def __init__(self, store: Store | None = None) -> None:
        super().__init__()
        self.store = store or Store()
        self.genome: Path | None = None
        self.folder: Path | None = None
        self.result: MatchResult | None = None
        self.problem = ""
        self.p_max: float | None = 5e-8
        self.search_q = ""
        self.trait: str | None = None
        self.list_tab = "curated"
        self._load_gen = 0
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
        self._search_gen = 0
        self._labels = list(MODE_LABELS)
        self._tick_handle = None
        self._traits_box: Panel | None = None
        self._assoc_box: Panel | None = None

    # ── compose ──────────────────────────────────────────────────────
    def compose(self):
        yield ModeBar(
            "gwas",
            self._labels,
            on_select=lambda i: self.app.set_mode(i) if self.app else None,
        )

        stats = RichText(id="stats")
        stats_box = Panel(stats, title="MATCH", id="stats-box")
        stats_box.layout_hint_h = 90

        trait_tabs = RichText(id="trait-tabs")
        trait_tabs.layout_hint_h = 28
        trait_tabs.on_action = self._run_action
        search = Input(
            "",
            id="trait-search",
            placeholder="/ trait · rsID · gene · GCST",
            on_change=self._search_changed,
            on_submit=self._search_submit,
        )
        traits = OptionList(
            id="traits",
            on_highlight=self._trait_hi,
            on_activate=self._on_trait_activate,
            row_lines=1,
        )
        traits_inner = Vertical(trait_tabs, search, traits, id="traits-inner")
        self._traits_box = Panel(traits_inner, title="TRAITS", id="traits-box")

        legend = RichText(id="assoc-legend")
        legend.layout_hint_h = 28
        assocs = OptionList(
            id="assocs",
            on_highlight=self._assoc_hi,
            on_activate=self._on_assoc_activate,
            row_lines=1,
        )
        assoc_inner = Vertical(legend, assocs, id="assoc-inner")
        self._assoc_box = Panel(assoc_inner, title="ASSOCIATIONS", id="assoc-box")

        det_tabs = RichText(id="det-tabs")
        det_tabs.layout_hint_h = 28
        det_tabs.on_action = self._run_action
        detail = RichText(id="detail")
        detail.on_action = self._run_action
        det_inner = Vertical(det_tabs, detail, id="det-inner")
        det_box = Panel(det_inner, title="DETAIL", id="det-box")

        lists = Horizontal(self._traits_box, self._assoc_box, det_box, id="lists")
        yield Vertical(stats_box, lists, id="main")

        yield KeyBar([
            ("/", "Search", lambda: self.focus_search()),
            ("Esc", "Clear", lambda: self.clear_search()),
            ("L", "CUR/ALL", lambda: self.toggle_list()),
            ("P", "p-cut", lambda: self.cycle_p()),
            ("R", "Rescan", lambda: self.rescan()),
            ("A", "Ask", lambda: self.ask_chat()),
            ("T", "HIT/ASK", lambda: self.toggle_detail()),
            ("C", "Copy rs", lambda: self.copy_rsid()),
            ("B", "PubMed", lambda: self.open_pubmed()),
            ("Tab", "Panel", lambda: focus_next(self)),
            ("F1–F10", "Modes", None),
            ("Q", "Quit", lambda: self.app.ask_quit() if self.app else None),
        ])
        yield StatusBar(id="status")

    def on_screen_mount(self) -> None:
        try:
            self.query_one("#assoc-legend", RichText).update(assoc_legend())
            tabs = self.query_one("#trait-tabs", RichText)
            tabs.on_action = self._run_action
            tabs.update(trait_tab_bar(self.list_tab))
            det = self.query_one("#det-tabs", RichText)
            det.on_action = self._run_action
            det.update(detail_tab_bar(self.detail_tab))
            self.query_one("#detail", RichText).on_action = self._run_action
        except KeyError:
            pass
        if self.app is not None:
            self.app.show_busy("GWAS match", "Loading…")
        try:
            self.reload()
            if self.app is not None:
                self.app.set_focus(self.query_one("#traits", OptionList))
            if self.app is not None and hasattr(self.app, "set_interval"):
                self._tick_handle = self.app.set_interval(0.35, self._tick)
        except Exception as exc:  # noqa: BLE001
            self._notify(f"GWAS open failed: {exc}")
            if self.app is not None:
                self.app.hide_busy()

    def on_screen_resume(self) -> None:
        g = self._active()
        folder = self._path("gwas_catalog")
        if g != self.genome or folder != self.folder:
            self.reload()

    # ── helpers ──────────────────────────────────────────────────────
    def _notify(self, msg: str) -> None:
        if self.app is not None and hasattr(self.app, "notify"):
            self.app.notify(msg)

    def _path(self, key: str) -> Path | None:
        try:
            return self.store.path(key)
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
        except Exception:  # noqa: BLE001
            return None

    # ── data ─────────────────────────────────────────────────────────
    def reload(self, *, force: bool = False) -> None:
        self._load_gen += 1
        gen = self._load_gen
        self.genome = self._active()
        self.folder = self._path("gwas_catalog")
        self.result = None
        self.problem = ""
        self.trait = None
        self._assoc_by_id.clear()
        self._trait_haystacks = {}
        self._assocs_by_trait = {}
        self._box = []
        self._pending_gen = None
        self._paint_shell()
        self.fill_traits()
        self.fill_assocs()
        self.refresh_detail()
        self.update_status()

        if self.genome is None:
            self.problem = "genome"
            self._paint_shell()
            if self.app is not None:
                self.app.hide_busy()
            return
        folder = self.folder
        try:
            catalog_ok = folder is not None and GwasCatalog().associations_tsv(folder) is not None
        except Exception:  # noqa: BLE001
            catalog_ok = False
        if not catalog_ok:
            self.problem = "catalog"
            self._paint_shell()
            if self.app is not None:
                self.app.hide_busy()
            return

        if (job := JOBS.get(JOB)) and job.active:
            self._notify("GWAS match already running")
            self.update_status()
            return

        if self.app is not None:
            self.app.show_busy("GWAS match", f"Matching {self.genome.name}…")
        store = self.store
        genome_path = self.genome
        p_max = self.p_max
        box: list[MatchResult] = []
        self._box = box
        self._pending_gen = gen

        def work(job) -> str:
            g = genome_db.get(genome_path, store=store)
            result = gwas_match.match_genome(
                folder, g, p_max=p_max, job=job, use_cache=not force,
            )
            box.append(result)
            return (
                f"{result.matched_rs:,} SNP · {result.traits_hit:,} traits · "
                f"{len(result.assocs):,} rows"
            )

        JOBS.start(JOB, f"GWAS match · {genome_path.name}", work)
        self._seen_job = id(JOBS.get(JOB))
        self.update_status()

    def _tick(self) -> None:
        if self.app is None or getattr(self.app, "mode_key", None) != "gwas":
            return
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
        if self.app is not None:
            self.app.hide_busy()
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
            self.trait = None
            try:
                self._trait_haystacks, self._assocs_by_trait = (
                    gwas_curated.build_trait_index(self.result)
                )
            except Exception:  # noqa: BLE001
                self._trait_haystacks, self._assocs_by_trait = {}, {}
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
        if self.app is not None:
            self.app.request_draw()

    def _paint_shell(self) -> None:
        try:
            date = catalog_date(self.folder) if self.folder else ""
        except Exception:  # noqa: BLE001
            date = ""
        try:
            stats = self.query_one("#stats", RichText)
        except KeyError:
            return
        if self.problem in ("genome", "catalog"):
            stats.update(empty_state(self.problem))
        elif self.problem:
            stats.update(stats_text(
                None, genome_name=None, catalog_date=date, problem=self.problem,
            ))
        else:
            stats.update(stats_text(
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

    def fill_traits(self) -> None:
        try:
            ol = self.query_one("#traits", OptionList)
        except KeyError:
            return
        keep = self.trait
        self._trait_index.clear()
        try:
            tabs = self.query_one("#trait-tabs", RichText)
            tabs.on_action = self._run_action
            tabs.update(trait_tab_bar(self.list_tab))
        except KeyError:
            pass
        rows = self._traits()
        if not rows:
            self.trait = None
            label = "CUR" if self.list_tab == "curated" else "ALL"
            if self._traits_box is not None:
                self._traits_box.title = f"TRAITS · {label} · 0"
            ol.set_options([])
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
                opts.append(Option(
                    theme or "",
                    id=f"grp:{theme}",
                    disabled=True,
                    lines=_text_lines(theme_header(theme, theme_n.get(theme, 0))),
                ))
            self._trait_index[trait] = len(opts)
            opts.append(Option(
                trait,
                id=f"trait:{trait}",
                lines=_text_lines(trait_prompt(trait, n)),
            ))
        keep_id = f"trait:{keep}" if keep in self._trait_index else None
        ol.set_options(opts, keep_id=keep_id)
        if keep in self._trait_index:
            self.trait = keep
        else:
            first = rows[0][1]
            self.trait = first
            for i, o in enumerate(ol.options):
                if o.id == f"trait:{first}":
                    ol.highlighted = i
                    break
        label = "CUR" if curated else "ALL"
        if self._traits_box is not None:
            self._traits_box.title = f"TRAITS · {label} · {len(rows)}"

    def fill_assocs(self) -> None:
        try:
            ol = self.query_one("#assocs", OptionList)
        except KeyError:
            return
        self._assoc_by_id.clear()
        rows = self._assocs()
        opts = []
        for i, a in enumerate(rows):
            oid = f"a:{i}"
            self._assoc_by_id[oid] = a
            opts.append(Option(a.rsid, id=oid, lines=_text_lines(assoc_prompt(a))))
        ol.set_options(opts)
        title = "ASSOCIATIONS"
        if self.trait:
            title += f" · {len(rows)}"
        if self._assoc_box is not None:
            self._assoc_box.title = title

    def _current_assoc(self) -> Assoc | None:
        try:
            ol = self.query_one("#assocs", OptionList)
        except KeyError:
            return None
        opt = ol.highlighted_option
        if opt is None or not opt.id:
            return None
        return self._assoc_by_id.get(opt.id)

    def _ask_key(self, a: Assoc | None = None) -> str | None:
        if self.result is None:
            return None
        return ask_subject(
            a if a is not None else self._current_assoc(),
            self.trait,
            self.result,
        )

    def _ask_entry(self, key: str | None) -> dict | None:
        if not key:
            return None
        if key in self.ask_cache:
            return self.ask_cache[key]
        try:
            hit = load_cached(key)
        except Exception:  # noqa: BLE001
            hit = None
        if hit:
            self.ask_cache[key] = hit
        return hit

    def refresh_detail(self) -> None:
        try:
            tabs = self.query_one("#det-tabs", RichText)
            tabs.on_action = self._run_action
            tabs.update(detail_tab_bar(self.detail_tab))
            detail = self.query_one("#detail", RichText)
            detail.on_action = self._run_action
        except KeyError:
            return
        a = self._current_assoc()
        if self.detail_tab == "ask":
            key = self._ask_key(a)
            busy = bool(
                key and self.ask_job_key == key
                and (job := JOBS.get(JOB_ASK)) and job.active
            )
            draft = ""
            model_hint = ""
            if busy and (job := JOBS.get(JOB_ASK)):
                draft = job.draft or ""
                try:
                    model_hint = job.stage or chat.model_label(self.store)
                except Exception:  # noqa: BLE001
                    model_hint = job.stage or ""
            body = ask_tab_text(
                a, self.result,
                trait=self.trait,
                genome_name=self.genome.name if self.genome else None,
                cache=None if busy else self._ask_entry(key),
                busy=busy,
                draft=draft,
                model_hint=model_hint,
                width=max(28, 48),
            )
        else:
            body = detail_text(a, self.result)
        detail.update(body)

    def update_status(self) -> None:
        try:
            bar = self.query_one("#status", StatusBar)
        except KeyError:
            return
        if self.problem == "genome":
            line = "GENOME: — (F1)"
        elif self.problem == "catalog":
            line = "CATALOG: — (F2 · GWAS Catalog)"
        elif self.problem:
            line = f"ERROR: {self.problem[:80]}"
        elif self.result:
            line = (
                f"{self.genome.name if self.genome else '—'}    "
                f"MATCHED {self.result.matched_rs:,}/{self.result.genome_rs:,} rs    "
                f"{p_cut_label(self.p_max)}    "
                f"{self.trait or '—'}"
            )
        else:
            line = "Matching…"
        job = JOBS.get(JOB)
        if job and job.running:
            line += f"    ⚙ {job.stage}"
        ask = JOBS.get(JOB_ASK)
        if ask and ask.running:
            line += f"    ⚙ ask {ask.stage}"
        bar.update(line)
        if self.app is not None:
            self.app.request_draw()

    # ── events ───────────────────────────────────────────────────────
    def _trait_hi(self, opt: Option | None) -> None:
        if opt is None or opt.disabled or not opt.id or not opt.id.startswith("trait:"):
            return
        self.trait = opt.id[len("trait:"):]
        self.fill_assocs()
        self.refresh_detail()
        self.update_status()

    def _assoc_hi(self, opt: Option | None) -> None:
        if opt is None:
            return
        self.refresh_detail()
        if self.app is not None:
            self.app.request_draw()

    def _on_trait_activate(self, opt: Option | None) -> None:
        """Enter/click on trait → associations (Textual moves focus after select)."""
        if opt is None or opt.disabled or not opt.id or not opt.id.startswith("trait:"):
            return
        self._trait_hi(opt)
        try:
            ol = self.query_one("#assocs", OptionList)
        except KeyError:
            return
        if self.app is not None:
            self.app.set_focus(ol)

    def _on_assoc_activate(self, opt: Option | None) -> None:
        """Enter/click on association → detail HIT (same as highlight + show)."""
        if opt is None or opt.disabled:
            return
        if self.detail_tab != "hit":
            self.detail_tab = "hit"
        self.refresh_detail()
        try:
            detail = self.query_one("#detail", RichText)
        except KeyError:
            return
        if self.app is not None:
            self.app.set_focus(detail)

    def _run_action(self, action: str) -> None:
        """Dispatch Rich ``ydna_click`` actions from shared view helpers."""
        m = _ACTION_RE.match((action or "").strip())
        if not m:
            return
        name, args_src = m.group(1), (m.group(2) or "").strip()
        fn = getattr(self, name, None)
        if not callable(fn):
            return
        try:
            args = ast.literal_eval(f"({args_src},)") if args_src else ()
        except Exception:  # noqa: BLE001
            return
        if not isinstance(args, tuple):
            args = (args,)
        fn(*args)

    def _search_changed(self, value: str) -> None:
        self.search_q = value
        self._search_gen += 1
        gen = self._search_gen
        app = self.app
        if app is not None and hasattr(app, "set_timer"):
            app.set_timer(0.15, lambda g=gen: self._apply_search_if(g))
        else:
            self._apply_search()

    def _apply_search_if(self, gen: int) -> None:
        if gen != self._search_gen:
            return
        self._apply_search()

    def _apply_search(self) -> None:
        self.fill_traits()
        self.fill_assocs()
        self.refresh_detail()
        if self.app is not None:
            self.app.request_draw()

    def _search_submit(self) -> None:
        self._apply_search()
        try:
            ol = self.query_one("#traits", OptionList)
        except KeyError:
            return
        if self.app is not None:
            self.app.set_focus(ol)

    def focus_search(self) -> None:
        try:
            inp = self.query_one("#trait-search", Input)
        except KeyError:
            return
        inp.focus()
        inp.action_select_all()

    def clear_search(self) -> bool:
        """Esc handler: clear search filter. Returns True if something was cleared."""
        try:
            inp = self.query_one("#trait-search", Input)
        except KeyError:
            return False
        focused = self.app is not None and self.app._focus is inp
        if not focused and not self.search_q:
            return False
        if inp.value:
            inp.load_text("")
        self.search_q = ""
        self._apply_search()
        if self.app is not None:
            self.app.set_focus(self.query_one("#traits", OptionList))
        return True

    # ── actions ──────────────────────────────────────────────────────
    def set_list_tab(self, key: str) -> None:
        ids = {k for k, _ in TRAIT_TABS}
        if key not in ids or key == self.list_tab:
            return
        self.list_tab = key
        self.fill_traits()
        self.fill_assocs()
        self.refresh_detail()
        self.update_status()

    def list_tab(self, key: str) -> None:
        """Click alias — shared view emits ``screen.list_tab(...)``."""
        self.set_list_tab(key)

    def toggle_list(self) -> None:
        ids = [k for k, _ in TRAIT_TABS]
        i = ids.index(self.list_tab) if self.list_tab in ids else -1
        self.set_list_tab(ids[(i + 1) % len(ids)])

    def set_detail_tab(self, key: str) -> None:
        if key not in DETAIL_TABS or key == self.detail_tab:
            return
        self.detail_tab = key
        self.refresh_detail()
        if self.app is not None:
            self.app.request_draw()

    def detail_tab(self, key: str) -> None:
        """Click alias — shared view emits ``screen.detail_tab(...)``."""
        self.set_detail_tab(key)

    def detail_hit(self) -> None:
        self.set_detail_tab("hit")

    def toggle_detail(self) -> None:
        self.set_detail_tab("ask" if self.detail_tab == "hit" else "hit")

    def ask_chat(self, refresh: bool = False) -> None:
        if self.result is None:
            self._notify("No match yet — wait for GWAS scan or fix F1/F2")
            return
        try:
            err = chat.can_send(self.store)
        except Exception as exc:  # noqa: BLE001
            self._notify(str(exc))
            return
        if err:
            self._notify(err)
            return
        a = self._current_assoc()
        key = self._ask_key(a)
        if not key:
            return
        if self.detail_tab != "ask":
            self.detail_tab = "ask"
        if not refresh and self._ask_entry(key):
            self.refresh_detail()
            if self.app is not None:
                self.app.request_draw()
            return
        if (old := JOBS.get(JOB_ASK)) and old.active:
            old.cancel()
            for _ in range(40):
                j = JOBS.get(JOB_ASK)
                if not j or not j.active:
                    break
                time.sleep(0.05)
            if (j := JOBS.get(JOB_ASK)) and j.active:
                self._notify("Ask still stopping · try again")
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
            text = next(
                (m.text for m in reversed(t.messages) if m.role == "model"),
                "",
            )
            return (text or job.draft or "").strip() or "(empty reply)"

        self.ask_job_key = subject
        try:
            JOBS.start(JOB_ASK, title, work)
        except RuntimeError as exc:
            self.ask_job_key = None
            self._notify(str(exc))
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
            try:
                model = chat.model_label(self.store)
                ts = chat.now()
            except Exception:  # noqa: BLE001
                model, ts = "", ""
            entry = {"text": text or "(empty reply)", "model": model, "ts": ts}
            self.ask_cache[key] = entry
            try:
                save_cached(key, text=entry["text"], model=model, ts=ts)
            except OSError:
                pass
            self._notify(f"GWAS note · {key.split('|')[0]}")
        elif job.status == "failed":
            self._notify(f"GWAS ask failed: {job.message}")
        else:
            self._notify(f"GWAS ask: {job.message or job.status}")
        self.detail_tab = "ask"
        self.refresh_detail()
        self.update_status()

    def cycle_p(self) -> None:
        cuts = list(P_CUTS)
        try:
            i = cuts.index(self.p_max)
        except ValueError:
            i = 0
        self.p_max = cuts[(i + 1) % len(cuts)]
        self._notify(f"p-value cut: {p_cut_label(self.p_max)}")
        self.reload(force=True)

    def rescan(self) -> None:
        self.reload(force=True)

    def copy_rsid(self) -> None:
        from .clipboard import copy_status, copy_text

        a = self._current_assoc()
        if not a:
            self._notify("No association selected")
            return
        ok = copy_text(self.app, a.rsid)
        self._notify(copy_status(ok, success=f"Copied {a.rsid}", label=a.rsid))

    def open_pubmed(self) -> None:
        a = self._current_assoc()
        if not a or not a.pubmed:
            self._notify("No PubMed id on this row")
            return
        webbrowser.open(f"https://pubmed.ncbi.nlm.nih.gov/{a.pubmed}/")
