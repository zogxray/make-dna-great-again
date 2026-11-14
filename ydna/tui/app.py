"""Оболочка: переключение режимов (F1–F10) и выход. Вся работа — в ydna.workspaces."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual import events
from textual.app import App
from textual.binding import Binding

from ..core.clip import read_clipboard, write_clipboard
from ..core.store import APP_TITLE, Store, data_dir
from ..theme import YDNA_DARK, YDNA_LIGHT
from ..workspaces.base import (CLICK_META, MODE_LABELS, ConfirmScreen, alt_keys,
                              busy_progress, hide_busy, show_busy)

if TYPE_CHECKING:
    from ..workspaces.genome import GenomeWorkspace
    from ..workspaces.tree import TreeWorkspace

# String keys — avoid importing ydna.core.sources (pgs/ylink) before first paint.
_KEY_DISCOVER = "ftdna_discover"
_KEY_HAPLOTREE = "ftdna_haplotree"
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
        # Store does not open DuckDB here — first frame shows BusyModal, then ensure().
        self.store = Store()
        self._folder_arg = folder
        self.folder = folder or DEFAULT_DISCOVER
        self.genomes = genomes or []
        self.tree_ws: TreeWorkspace | None = None
        self.genome_ws: GenomeWorkspace | None = None
        self._quitting = False
        self._booted = False

    def on_mount(self) -> None:
        self.register_theme(YDNA_DARK)
        self.register_theme(YDNA_LIGHT)
        self.theme = "ydna-dark"
        show_busy(self, "Starting", "Opening data store…")
        self.call_after_refresh(self._startup)

    def _startup(self) -> None:
        """After first paint: DuckDB connect/seed, then mount F1."""
        if self._booted:
            return
        self._booted = True
        try:
            self.store.ensure()
            if self._folder_arg is None:
                self.folder = self.store.path(_KEY_DISCOVER) or DEFAULT_DISCOVER
            # Import each mode only when first entered (F1 genome loads immediately).
            factories = {
                "tree": self._make_tree,
                "genome": self._make_genome,
                "data": self._make_data,
                "pgs": self._make_pgs,
                "reference": self._make_reference,
                "chat": self._make_chat,
                "jobs": self._make_jobs,
                "samples": self._make_samples,
                "help": self._make_help,
                "gwas": self._make_gwas,
            }
            for key, _ in MODE_LABELS:
                self.add_mode(key, factories[key])
            # Drop boot modal so mode stack is clean; F1 paints its own BusyModal.
            hide_busy(self)
            # F1 Genome — онбординг: файл → F2 данные → F3 дерево. Tree не грузим до первого захода.
            self.switch_mode("genome")
        except Exception as exc:
            hide_busy(self)
            from ..core.single import db_busy_message, is_db_busy_error
            if is_db_busy_error(exc) or (
                    isinstance(exc, RuntimeError)
                    and "data store" in str(exc).lower()):
                self.notify(db_busy_message(), severity="error", timeout=12)
                self.exit(1)
                return
            raise

    def _make_tree(self) -> Any:
        from ..workspaces.tree import TreeWorkspace
        self.tree_ws = TreeWorkspace(self.store, self.folder)
        return self.tree_ws

    def _make_genome(self) -> Any:
        from ..workspaces.genome import GenomeWorkspace
        self.genome_ws = GenomeWorkspace(self.store, self.genomes)
        return self.genome_ws

    def _make_data(self) -> Any:
        from ..workspaces.data import DataWorkspace
        return DataWorkspace(self.store)

    def _make_pgs(self) -> Any:
        from ..workspaces.pgs import PgsWorkspace
        return PgsWorkspace(self.store)

    def _make_reference(self) -> Any:
        from ..workspaces.reference import ReferenceWorkspace
        return ReferenceWorkspace(self.store)

    def _make_chat(self) -> Any:
        from ..workspaces.chat import ChatWorkspace
        return ChatWorkspace(self.store)

    def _make_jobs(self) -> Any:
        from ..workspaces.jobs import JobsWorkspace
        return JobsWorkspace()

    def _make_samples(self) -> Any:
        from ..workspaces.samples import SamplesWorkspace
        return SamplesWorkspace(self.store)

    def _make_help(self) -> Any:
        from ..workspaces.help import HelpWorkspace
        return HelpWorkspace()

    def _make_gwas(self) -> Any:
        from ..workspaces.gwas import GwasWorkspace
        return GwasWorkspace(self.store)

    def source_changed(self, key: str) -> None:
        """Режим Data сменил путь к источнику или докачал его."""
        if key == _KEY_DISCOVER and (path := self.store.path(key)):
            self.folder = path
            if self.tree_ws:
                self.tree_ws.set_folder(path)
        if key == _KEY_HAPLOTREE and self.tree_ws:
            self.tree_ws.mark_stale()
        if key in (_KEY_DISCOVER, _KEY_HAPLOTREE) and self.genome_ws:
            self.genome_ws.relink()
        if key in ("yfull_ytree", "theytree"):
            from ..core import overlays as _overlays
            _overlays._cache = None
            _overlays._cache_sig = None
            if self.tree_ws and getattr(self.tree_ws, "ctx", None):
                self.tree_ws.ctx.drop_overlays()
        if key in ("aadr", "aadr_ho") and (ws := self.screen) and getattr(ws, "mode_key", None) == "reference":
            ws.reload()
        if key == "gwas_catalog" and (ws := self.screen) and getattr(ws, "mode_key", None) == "gwas":
            ws.reload(force=True)

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

        from ..core.jobs import JOBS
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
        from ..core import db
        from ..core.jobs import JOBS
        from ..core.single import release

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
        # F9 (and other flows) push ModalScreen on the mode stack. Without popping,
        # F-keys look dead / switch_mode suspends the modal and the UI feels stuck.
        while len(self._screen_stack) > 1:
            try:
                self.screen.dismiss()
            except Exception:  # noqa: BLE001
                self.pop_screen()
                break
        self.switch_mode(key)

    def open_chat_prefill(self, prompt: str, *, title: str | None = None) -> None:
        """F3 Ask GPT → F7: новый тред с готовым промптом в поле ввода."""
        if self._quitting:
            return
        self.chat_prefill = prompt
        self.chat_prefill_title = title or "Ask · F3"
        self.switch_mode("chat")
