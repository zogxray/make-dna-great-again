"""F8 Jobs screen for textual-wgpu — JOBS monitor (app logic, lib widgets)."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from textual_wgpu.on import on
from textual_wgpu.reactive import reactive
from textual_wgpu.screen import Screen
from textual_wgpu.widgets import (
    Digits,
    Horizontal,
    KeyBar,
    ModeBar,
    Option,
    OptionList,
    Panel,
    RichText,
    Rule,
    Sparkline,
    StatusBar,
    Vertical,
)

from ..core.jobs import JOBS, Job
from ..core.sources import human_size
from ..workspaces.base import MODE_LABELS
from ..workspaces.jobs.view import FILTERS, detail_text, list_prompt, status_of


def _hex_rgba(hex_color: str) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 6:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
    return (174, 165, 184, 255)


def _text_lines(text: Text) -> list[list[tuple[str, tuple[int, int, int, int]]]]:
    """Split a Rich Text into paint runs per line."""
    console = Console(width=120, height=64, force_terminal=True)
    default = (195, 188, 204, 255)
    out: list[list[tuple[str, tuple[int, int, int, int]]]] = []
    for line in text.split("\n"):
        plain = line.plain
        if not plain:
            out.append([("", default)])
            continue
        runs: list[tuple[str, tuple[int, int, int, int]]] = []
        i = 0
        n = len(plain)
        while i < n:
            style = line.get_style_at_offset(console, i)
            fill = default
            if style and style.color is not None:
                try:
                    c = style.color.get_truecolor()
                    if c is not None:
                        fill = (int(c.red), int(c.green), int(c.blue), 255)
                except Exception:  # noqa: BLE001
                    pass
            j = i + 1
            while j < n:
                st2 = line.get_style_at_offset(console, j)
                fill2 = fill
                if st2 and st2.color is not None:
                    try:
                        c2 = st2.color.get_truecolor()
                        if c2 is not None:
                            fill2 = (int(c2.red), int(c2.green), int(c2.blue), 255)
                    except Exception:  # noqa: BLE001
                        pass
                if fill2 != fill:
                    break
                j += 1
            runs.append((plain[i:j], fill))
            i = j
        out.append(runs or [("", default)])
    return out


class JobsScreen(Screen):
    """Same JOBS monitor as Textual F8, hosted on wgpu widgets."""

    DEFAULT_CSS = """
    #main { height: 1fr; }
    #list-box { width: 48%; min-width: 36; border: round $primary; }
    #det-box { width: 1fr; border: round $primary; }
    #list-box:focus-within, #det-box:focus-within { border: round $accent; }
    #jobs { height: 1fr; border: none; background: transparent; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """

    BINDINGS = [
        ("p", "pause"),
        ("c", "cancel"),
        ("r", "retry"),
        ("x", "clear_done"),
        ("a", "pause_all"),
        ("/", "cycle_filter"),
        ("slash", "cycle_filter"),
        ("f", "cycle_filter"),
    ]

    filter = reactive("all")

    def __init__(self) -> None:
        super().__init__()
        self._keys: list[str] = []
        self._selected: str | None = None
        self._list_sig: tuple | None = None
        self._labels = list(MODE_LABELS)
        self._tick_handle = None

    def compose(self):
        from .app_api import make_mode_bar

        yield make_mode_bar(self, "jobs", self._labels)
        ol = OptionList(id="jobs", row_lines=2)
        list_panel = Panel(ol, title="JOBS", id="list-box")
        metrics = Horizontal(
            Digits("0", id="jobs-count"),
            Sparkline([], id="jobs-spark"),
            id="jobs-metrics",
        )
        metrics.layout_hint_rows = 3
        detail = RichText(id="details")
        det_body = Vertical(metrics, Rule(id="jobs-rule"), detail)
        det_panel = Panel(det_body, title="DETAIL", id="det-box")
        yield Horizontal(list_panel, det_panel, id="main")
        from .app_api import focus_next

        yield KeyBar([
            ("↑↓", "Select", None),
            ("P", "Pause/Resume", lambda: self.pause()),
            ("C", "Cancel", lambda: self.cancel()),
            ("R", "Retry", lambda: self.retry()),
            ("X", "Clear done", lambda: self.clear_done()),
            ("A", "Pause all", lambda: self.pause_all()),
            ("/|F", "Filter", lambda: self.cycle_filter()),
            ("Tab", "Panel", lambda: focus_next(self)),
            ("F1–F10", "Modes", None),
            ("Q", "Quit", lambda: self.app.ask_quit() if self.app else None),
        ])
        yield StatusBar(id="status")

    def on_screen_mount(self) -> None:
        self.reload()
        if self.app is not None and hasattr(self.app, "set_interval"):
            self._tick_handle = self.app.set_interval(0.5, self._tick)

    def _notify(self, msg: str) -> None:
        if self.app is not None and hasattr(self.app, "notify"):
            self.app.notify(msg)

    @on(OptionList.OptionHighlighted, "#jobs")
    def _on_jobs_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        opt = event.option
        if opt is None or opt.disabled or not opt.id or opt.id == "__empty__":
            self._selected = None
        else:
            self._selected = opt.id
        self.refresh_detail()
        self.refresh()

    def watch_filter(self, _old: str, _new: str) -> None:
        if self._built:
            self.reload()

    def _tick(self) -> None:
        # Only refresh when this mode is visible.
        if self.app is None or getattr(self.app, "mode_key", None) != "jobs":
            return
        live = any(j.active or j.status == "queued" for j in JOBS.items.values())
        sig = self._jobs_sig(live=live)
        if sig == self._list_sig:
            return
        self.reload(keep=True)

    def _jobs_sig(self, *, live: bool) -> tuple:
        rows: list[tuple] = []
        for key, j in JOBS.items.items():
            st = status_of(j)
            if live and (j.active or st == "queued"):
                rows.append((
                    key, st, j.stage or "",
                    int(round((j.fraction or 0.0) * 100)),
                    int(round(j.rate or 0.0)),
                    (j.message or "")[:48],
                ))
            else:
                rows.append((key, st))
        rows.sort()
        return (self.filter, tuple(rows))

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
        if not self._keys or not ol.options:
            return None
        if 0 <= ol.highlighted < len(self._keys):
            return JOBS.get(self._keys[ol.highlighted])
        return None

    def reload(self, *, keep: bool = False) -> None:
        prev = self._selected if keep else None
        if keep and self._selected is None:
            job = self._job()
            prev = job.key if job else None

        jobs = self._filtered()
        ol = self.query_one("#jobs", OptionList)
        self._keys = [j.key for j in jobs]
        options: list[Option] = []
        if not jobs:
            empty = Text()
            empty.append("No tasks", style="bold #AEA5B8")
            empty.append(f"  ·  filter={self.filter}\n", style="#8A8294")
            empty.append(
                "Start a download in F2 or a score in F4 — it will appear here.",
                style="#8A8294",
            )
            options.append(Option(
                "No tasks",
                id="__empty__",
                lines=_text_lines(empty),
                disabled=True,
            ))
        else:
            from textual_wgpu.fonts import cell_width
            col_w = 52
            if ol.rect.w > 0:
                col_w = max(36, (ol.rect.w - 16) // cell_width(ol.FONT_SIZE))
            for j in jobs:
                prompt = list_prompt(j, width=col_w)
                options.append(Option(
                    j.title,
                    id=j.key,
                    lines=_text_lines(prompt),
                    data=j,
                ))
        ol.set_options(options, keep_id=prev)
        if self._keys:
            if prev and prev in self._keys:
                self._selected = prev
            elif 0 <= ol.highlighted < len(self._keys):
                self._selected = self._keys[ol.highlighted]
            else:
                self._selected = self._keys[0]
        else:
            self._selected = None

        n_run = len(JOBS.running())
        n_pause = sum(1 for j in JOBS.items.values() if j.status == "paused")
        n_q = sum(1 for j in JOBS.items.values() if j.status == "queued")
        bits = [f"{n_run} running"]
        if n_pause:
            bits.append(f"{n_pause} paused")
        if n_q:
            bits.append(f"{n_q} queued")
        box = self.query_one("#list-box", Panel)
        box.subtitle = f"{len(jobs)} shown · filter {self.filter} · {' · '.join(bits)}"
        try:
            self.query_one("#jobs-count", Digits).update(str(len(jobs)))
            sp_data = [round((j.fraction or 0.0) * 100.0, 1) for j in jobs] or [0.0]
            self.query_one("#jobs-spark", Sparkline).data = sp_data
        except KeyError:
            pass
        self._list_sig = self._jobs_sig(
            live=any(j.active or j.status == "queued" for j in JOBS.items.values()),
        )
        self.refresh_detail()
        self.update_status()
        if self.app is not None:
            self.app.request_draw()

    def refresh_detail(self) -> None:
        job = self._job()
        self.query_one("#details", RichText).update(
            detail_text(job, filter_name=self.filter, n_total=len(JOBS.items)),
        )
        det = self.query_one("#det-box", Panel)
        det.title = f"DETAIL · {job.key}" if job else "DETAIL"

    def update_status(self) -> None:
        by: dict[str, int] = {}
        for j in JOBS.items.values():
            by[status_of(j)] = by.get(status_of(j), 0) + 1
        parts = [
            f"{by.get('running', 0)} running",
            f"{by.get('paused', 0)} paused",
            f"{by.get('queued', 0)} queued",
            f"{by.get('failed', 0)} failed",
            f"{by.get('done', 0)} done",
        ]
        net = 0.0
        for j in JOBS.running():
            if j.unit == "B" and j.rate:
                net += j.rate
        extra = f" · ↓ {human_size(net)}/s" if net else ""
        self.query_one("#status", StatusBar).update(
            f"JOBS: {' · '.join(parts)}{extra} · start tasks from F2/F3/F4/F5/F7",
        )

    def cycle_filter(self) -> None:
        i = FILTERS.index(self.filter) if self.filter in FILTERS else 0
        self.filter = FILTERS[(i + 1) % len(FILTERS)]  # watch_filter → reload
        self._notify(f"Filter: {self.filter}")

    def cancel(self) -> None:
        job = self._job()
        if not job:
            return
        st = status_of(job)
        if st == "queued":
            if JOBS.cancel_queued(job.key):
                self._notify(f"Removed from queue: {job.title}")
            else:
                self._notify(f"{job.title}: not queued")
            self.reload(keep=True)
            return
        if not job.active:
            self._notify(f"{job.title}: not running")
            return
        job.cancel()
        self._notify(f"Cancelling: {job.title}")
        self.reload(keep=True)

    def pause(self) -> None:
        job = self._job()
        if not job:
            self._notify("No task selected")
            return
        st = status_of(job)
        if st == "running":
            if job.pause():
                self._notify(f"Paused: {job.title}")
            else:
                self._notify(f"{job.title}: could not pause")
        elif st == "paused":
            if job.resume():
                self._notify(f"Resumed: {job.title}")
            else:
                self._notify(f"{job.title}: could not resume")
        elif st == "queued":
            self._notify("Queued tasks wait for the slot — cancel with C")
        else:
            self._notify(f"{job.title}: {st} — nothing to pause")
        self.reload(keep=True)

    def pause_all(self) -> None:
        n = 0
        for j in list(JOBS.items.values()):
            if j.status == "running" and j.pause():
                n += 1
        if n:
            self._notify(f"Paused {n} task(s)")
        else:
            self._notify("Nothing running to pause")
        self.reload(keep=True)

    def retry(self) -> None:
        job = self._job()
        if not job:
            return
        if job.active or job.status == "queued":
            self._notify("Cancel the active/queued task first")
            return
        self._notify(f"Retry from the source screen (F2/F3/…) · {job.key}")

    def clear_done(self) -> None:
        drop = [
            k for k, j in JOBS.items.items()
            if j.status in ("done", "failed", "cancelled")
        ]
        if not drop:
            self._notify("Nothing finished to clear")
            return
        for k in drop:
            JOBS.items.pop(k, None)
        if self._selected in drop:
            self._selected = None
        self.reload()
        self._notify(f"Cleared {len(drop)} finished task(s)")
