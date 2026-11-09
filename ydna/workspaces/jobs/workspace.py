"""Режим Jobs (F7): монитор фоновых задач — список + detail. Запуск задач пока из других F."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ...core.jobs import JOBS, Job
from rich.text import Text

from ..base import KeyBar, ModeBar, Workspace, alt_keys
from ...core.sources import human_size
from .view import FILTERS, detail_text, list_prompt, status_of


class JobsWorkspace(Workspace):
    mode_key = "jobs"
    DEFAULT_CSS = """
    #main { height: 1fr; }
    #list-box { width: 48%; min-width: 36; border: round $primary; }
    #det-box { width: 1fr; border: round $primary; }
    #list-box:focus-within, #det-box:focus-within { border: round $accent; }
    #jobs { height: 1fr; border: none; background: transparent; }
    #det-scroll { height: 1fr; scrollbar-size-vertical: 1; }
    #details { padding: 0 1; }
    #help { height: 1; padding: 0 1; color: $text-muted; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """
    BINDINGS = [
        Binding(alt_keys("p"), "pause", "Pause / resume"),
        Binding(alt_keys("c"), "cancel", "Cancel"),
        Binding(alt_keys("r"), "retry", "Retry"),
        Binding(alt_keys("x"), "clear_done", "Clear finished"),
        Binding(alt_keys("a"), "pause_all", "Pause all", show=False),
        Binding("slash", "cycle_filter", "Filter", show=False),
        Binding(alt_keys("f"), "cycle_filter", "Filter", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.filter = "all"
        self._keys: list[str] = []  # порядок в OptionList
        self._selected: str | None = None

    def compose(self) -> ComposeResult:
        yield ModeBar(self.mode_key)
        with Horizontal(id="main"):
            with Vertical(id="list-box"):
                yield OptionList(id="jobs")
            with Vertical(id="det-box"):
                with VerticalScroll(id="det-scroll"):
                    yield Static(id="details")
        yield KeyBar([
            ("↑↓", "Select", None),
            ("P", "Pause/Resume", "screen.pause"),
            ("C", "Cancel", "screen.cancel"),
            ("R", "Retry", "screen.retry"),
            ("X", "Clear done", "screen.clear_done"),
            ("A", "Pause all", "screen.pause_all"),
            ("/|F", "Filter", "screen.cycle_filter"),
            ("Tab", "Panel", "app.focus_next"),
            ("F1–F8", "Modes", None),
            ("Q", "Quit", "app.quit"),
        ], id="help")
        yield Static(id="status")

    def on_mount(self) -> None:
        self.query_one("#list-box").border_title = "JOBS"
        self.query_one("#det-box").border_title = "DETAIL"
        self.reload()
        self.query_one("#jobs", OptionList).focus()
        self.set_interval(0.5, self._tick)

    def on_screen_resume(self) -> None:
        self.reload()

    def _tick(self) -> None:
        live = any(j.active or j.status == "queued" for j in JOBS.items.values())
        if live or self._keys:
            self.reload(keep=True)

    def _filtered(self) -> list[Job]:
        items = list(JOBS.items.values())
        order = {"running": 0, "paused": 1, "queued": 2, "failed": 3, "cancelled": 4, "done": 5}

        def sort_key(j: Job) -> tuple:
            return (order.get(status_of(j), 9), -(j.started or 0), j.key)

        items.sort(key=sort_key)
        if self.filter == "all":
            return items
        return [j for j in items if status_of(j) == self.filter]

    def _job(self) -> Job | None:
        if self._selected:
            return JOBS.get(self._selected)
        ol = self.query_one("#jobs", OptionList)
        if ol.highlighted is None or not self._keys:
            return None
        if 0 <= ol.highlighted < len(self._keys):
            return JOBS.get(self._keys[ol.highlighted])
        return None

    def reload(self, *, keep: bool = False) -> None:
        """Обновить список из JOBS (не путать с Widget.refresh CSS)."""
        prev = self._selected if keep else None
        if keep and self._selected is None:
            job = self._job()
            prev = job.key if job else None

        jobs = self._filtered()
        ol = self.query_one("#jobs", OptionList)
        self._keys = [j.key for j in jobs]
        ol.clear_options()
        if not jobs:
            ol.add_option(Option(_empty_prompt(self.filter), id="__empty__", disabled=True))
        else:
            for j in jobs:
                ol.add_option(Option(list_prompt(j), id=j.key))

        # восстановить выделение
        idx = 0
        if prev and prev in self._keys:
            idx = self._keys.index(prev)
        if self._keys:
            ol.highlighted = idx
            self._selected = self._keys[idx]
        else:
            ol.highlighted = None
            self._selected = None

        n_run = len(JOBS.running())
        n_pause = sum(1 for j in JOBS.items.values() if j.status == "paused")
        n_q = sum(1 for j in JOBS.items.values() if j.status == "queued")
        bits = [f"{n_run} running"]
        if n_pause:
            bits.append(f"{n_pause} paused")
        if n_q:
            bits.append(f"{n_q} queued")
        self.query_one("#list-box").border_subtitle = (
            f"{len(jobs)} shown · filter {self.filter} · {' · '.join(bits)}"
        )
        self.refresh_detail()
        self.update_status()

    def refresh_detail(self) -> None:
        job = self._job()
        self.query_one("#details", Static).update(
            detail_text(job, filter_name=self.filter, n_total=len(JOBS.items)))
        title = f"DETAIL · {job.key}" if job else "DETAIL"
        self.query_one("#det-box").border_title = title

    def update_status(self) -> None:
        by: dict[str, int] = {}
        for j in JOBS.items.values():
            by[status_of(j)] = by.get(status_of(j), 0) + 1
        parts = [f"{by.get('running', 0)} running",
                 f"{by.get('paused', 0)} paused",
                 f"{by.get('queued', 0)} queued",
                 f"{by.get('failed', 0)} failed",
                 f"{by.get('done', 0)} done"]
        net = 0.0
        for j in JOBS.running():
            if j.unit == "B" and j.rate:
                net += j.rate
        extra = f" · ↓ {human_size(net)}/s" if net else ""
        self.query_one("#status", Static).update(
            f"JOBS: {' · '.join(parts)}{extra} · start tasks from F2/F3/F4/F6")

    @on(OptionList.OptionHighlighted, "#jobs")
    def _highlighted(self) -> None:
        ol = self.query_one("#jobs", OptionList)
        if ol.highlighted is not None and 0 <= ol.highlighted < len(self._keys):
            self._selected = self._keys[ol.highlighted]
        self.refresh_detail()

    def action_cycle_filter(self) -> None:
        i = FILTERS.index(self.filter) if self.filter in FILTERS else 0
        self.filter = FILTERS[(i + 1) % len(FILTERS)]
        self.reload()
        self.app.notify(f"Filter: {self.filter}")

    def action_cancel(self) -> None:
        job = self._job()
        if not job:
            return
        st = status_of(job)
        if st == "queued":
            if JOBS.cancel_queued(job.key):
                self.app.notify(f"Removed from queue: {job.title}")
            else:
                self.app.notify(f"{job.title}: not queued")
            self.reload(keep=True)
            return
        if not job.active:
            self.app.notify(f"{job.title}: not running")
            return
        job.cancel()
        self.app.notify(f"Cancelling: {job.title}")
        self.reload(keep=True)

    def action_pause(self) -> None:
        job = self._job()
        if not job:
            self.app.notify("No task selected")
            return
        st = status_of(job)
        if st == "running":
            if job.pause():
                self.app.notify(f"Paused: {job.title}")
            else:
                self.app.notify(f"{job.title}: could not pause", severity="warning")
        elif st == "paused":
            if job.resume():
                self.app.notify(f"Resumed: {job.title}")
            else:
                self.app.notify(f"{job.title}: could not resume", severity="warning")
        elif st == "queued":
            self.app.notify("Queued tasks wait for the slot — cancel with C", severity="information")
        else:
            self.app.notify(f"{job.title}: {st} — nothing to pause")
        self.reload(keep=True)

    def action_pause_all(self) -> None:
        n = 0
        for j in list(JOBS.items.values()):
            if j.status == "running" and j.pause():
                n += 1
        if n:
            self.app.notify(f"Paused {n} task(s)")
        else:
            self.app.notify("Nothing running to pause")
        self.reload(keep=True)

    def action_retry(self) -> None:
        job = self._job()
        if not job:
            return
        if job.active or job.status == "queued":
            self.app.notify("Cancel the active/queued task first", severity="warning")
            return
        self.app.notify(
            f"Retry from the source screen (F2/F3/…) · {job.key}",
            severity="information",
        )

    def action_clear_done(self) -> None:
        drop = [k for k, j in JOBS.items.items()
                if j.status in ("done", "failed", "cancelled")]
        if not drop:
            self.app.notify("Nothing finished to clear")
            return
        for k in drop:
            JOBS.items.pop(k, None)
        if self._selected in drop:
            self._selected = None
        self.reload()
        self.app.notify(f"Cleared {len(drop)} finished task(s)")


def _empty_prompt(filt: str) -> Text:
    t = Text()
    t.append("No tasks", style="bold #AEA5B8")
    t.append(f"  ·  filter={filt}\n", style="#8A8294")
    t.append("Start a download in F2 or a score in F4 — it will appear here.",
             style="#8A8294")
    return t
