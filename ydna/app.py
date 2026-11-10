"""Оболочка: переключение режимов (F1–F9) и выход. Вся работа — в ydna.workspaces."""

from __future__ import annotations

from pathlib import Path

from textual import events
from textual.app import App
from textual.binding import Binding

from .core.clip import read_clipboard, write_clipboard
from .core.sources import FtdnaDiscover, FtdnaHaplotree
from .core.store import APP_TITLE, Store, data_dir
from .theme import YDNA_DARK, YDNA_LIGHT
from .workspaces import (ChatWorkspace, DataWorkspace, GenomeWorkspace,
                         HelpWorkspace, JobsWorkspace, PgsWorkspace,
                         ReferenceWorkspace, SamplesWorkspace, TreeWorkspace)
from .workspaces.base import (CLICK_META, MODE_LABELS, ConfirmScreen, alt_keys,
                              busy_progress, show_busy)

DEFAULT_DISCOVER = data_dir() / "ftdna" / "discover"


class YDnaApp(App):
    TITLE = APP_TITLE
    # Своя тема (гармония HarmonyCompanion на brand dark+gold); не дефолтный textual-dark.
    theme = "ydna-dark"
    BINDINGS = [
        Binding(alt_keys("q"), "quit", "Quit"),
        # priority: F-keys work even when a ModalScreen (path/query/confirm) is on top
        *[Binding(f"f{i + 1}", f"mode('{key}')", title, show=False, priority=True)
          for i, (key, title) in enumerate(MODE_LABELS)],
    ]

    def __init__(self, folder: Path | None = None, genomes: list[Path] | None = None) -> None:
        super().__init__()
        self.store = Store()
        self.folder = folder or self.store.path(FtdnaDiscover.key) or DEFAULT_DISCOVER
        self.genomes = genomes or []
        self.tree_ws: TreeWorkspace | None = None
        self.genome_ws: GenomeWorkspace | None = None
        self._quitting = False

    def on_mount(self) -> None:
        self.register_theme(YDNA_DARK)
        self.register_theme(YDNA_LIGHT)
        self.theme = "ydna-dark"
        factories = {
            "tree": self._make_tree,
            "genome": self._make_genome,
            "data": lambda: DataWorkspace(self.store),
            "pgs": lambda: PgsWorkspace(self.store),
            "reference": lambda: ReferenceWorkspace(self.store),
            "chat": lambda: ChatWorkspace(self.store),
            "jobs": lambda: JobsWorkspace(),
            "samples": lambda: SamplesWorkspace(self.store),
            "help": lambda: HelpWorkspace(),
        }
        for key, _ in MODE_LABELS:
            self.add_mode(key, factories[key])
        # F1 Genome — онбординг: файл → F2 данные → F3 дерево. Tree не грузим до первого захода.
        self.switch_mode("genome")

    def _make_tree(self) -> TreeWorkspace:
        self.tree_ws = TreeWorkspace(self.store, self.folder)
        return self.tree_ws

    def _make_genome(self) -> GenomeWorkspace:
        self.genome_ws = GenomeWorkspace(self.store, self.genomes)
        return self.genome_ws

    def source_changed(self, key: str) -> None:
        """Режим Data сменил путь к источнику или докачал его."""
        if key == FtdnaDiscover.key and (path := self.store.path(key)):
            self.folder = path
            if self.tree_ws:
                self.tree_ws.set_folder(path)
        if key == FtdnaHaplotree.key and self.tree_ws:
            self.tree_ws.mark_stale()
        if key in (FtdnaDiscover.key, FtdnaHaplotree.key) and self.genome_ws:
            self.genome_ws.relink()
        if key in ("yfull_ytree", "theytree"):
            from .core import overlays as _overlays
            _overlays._cache = None
            _overlays._cache_sig = None
            if self.tree_ws and getattr(self.tree_ws, "ctx", None):
                self.tree_ws.ctx.drop_overlays()
        if key == "aadr" and (ws := self.screen) and getattr(ws, "mode_key", None) == "reference":
            ws.reload()

    async def on_click(self, event: events.Click) -> None:
        style = event.style
        if style is None:
            return
        if action := style.meta.get(CLICK_META):
            await self.run_action(action)

    @property
    def clipboard(self) -> str:
        """Ctrl+V: системный буфер, иначе внутренний clipboard Textual. Без strip — NOTES многострочные."""
        text = read_clipboard()
        return text if text else self._clipboard

    def copy_to_clipboard(self, text: str) -> None:
        """Пишет и в ОС (wl-copy/xclip/…), и во внутренний буфер + OSC 52."""
        self._clip_system_ok = write_clipboard(text)
        super().copy_to_clipboard(text)

    def action_help_quit(self) -> None:
        """Ctrl+C: сначала копируем screen-selection (Markdown-чат), иначе подсказка про Q."""
        try:
            sel = self.screen.get_selected_text()
        except Exception:
            sel = None
        if sel:
            self.copy_to_clipboard(sel)
            self.notify(f"Copied selection · {len(sel):,} chars")
            return
        super().action_help_quit()

    def action_quit(self) -> None:
        """Q: confirm → BusyModal shutdown → exit только после cleanup."""
        if self._quitting:
            return

        def done(ok: bool | None) -> None:
            if ok:
                self._begin_shutdown()

        self.push_screen(
            ConfirmScreen(
                f"Quit {APP_TITLE}?",
                "Background downloads will stop. Paths and config are already saved.",
                ok="Quit",
                danger=True,
            ),
            done,
        )

    def _begin_shutdown(self) -> None:
        """Показать прогресс в GUI, остановить работу, закрыть DB — потом exit()."""
        if self._quitting:
            return
        self._quitting = True
        show_busy(self, "Shutting down", "Finishing before exit — please wait")
        busy_progress(self, "Cancelling background tasks…", frac=0.05)

        from .core.jobs import JOBS
        n = JOBS.cancel_all()
        busy_progress(
            self,
            f"Cancelled {n} task(s) · stopping workers…" if n else "Stopping workers…",
            frac=0.15,
        )
        try:
            self.workers.cancel_all()
        except Exception:  # noqa: BLE001
            pass

        self.run_worker(
            self._shutdown_worker,
            thread=True,
            exclusive=True,
            group="shutdown",
        )

    def _shutdown_worker(self) -> None:
        from .core import db
        from .core.jobs import JOBS
        from .core.single import release

        def prog(stage: str, frac: float) -> None:
            self.call_from_thread(
                busy_progress, self, stage, frac=frac,
                title="Shutting down",
                subtitle="Finishing before exit — please wait",
            )

        prog("Waiting for jobs to stop…", 0.35)
        ok = JOBS.wait_idle(timeout=6.0)
        if not ok:
            prog("Jobs still winding down (timeout)…", 0.55)
        else:
            prog("Jobs stopped", 0.55)

        prog("Closing database…", 0.75)
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass

        prog("Releasing process lock…", 0.9)
        try:
            release()
        except Exception:  # noqa: BLE001
            pass

        prog("Ready to exit", 1.0)
        self.call_from_thread(self._finish_shutdown)

    def _finish_shutdown(self) -> None:
        self.exit()

    def action_mode(self, key: str) -> None:
        if self._quitting:
            return
        # F8 (and other flows) push ModalScreen on the mode stack. Without popping,
        # F-keys look dead / switch_mode suspends the modal and the UI feels stuck.
        while len(self._screen_stack) > 1:
            try:
                self.screen.dismiss()
            except Exception:  # noqa: BLE001
                self.pop_screen()
                break
        self.switch_mode(key)

    def open_chat_prefill(self, prompt: str, *, title: str | None = None) -> None:
        """F3 Ask GPT → F6: новый тред с готовым промптом в поле ввода."""
        if self._quitting:
            return
        self.chat_prefill = prompt
        self.chat_prefill_title = title or "Ask · F3"
        self.switch_mode("chat")
