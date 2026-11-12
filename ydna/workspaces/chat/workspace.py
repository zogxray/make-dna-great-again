"""Режим Chat (F6): список тредов, переписка с Gemini, заметки."""

from __future__ import annotations

import time
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, ContentSwitcher, OptionList, Static
from textual.widgets.option_list import Option

from ...core import chat
from ...core.jobs import JOBS
from ...core.store import Store
from ...core.ylink import active_genome
from ..base import (ClipTextArea, KeyBar, ModeBar, ReadOnlyClipTextArea,
                    SelectableRichStatic, Workspace, alt_keys, fit, hide_busy,
                    show_busy)
from ..data.workspace import job_line
from .screens import ConfirmScreen, EditScreen, ModelScreen
from .view import chat_prompt, thread_text, thread_view

JOB = "chat"

# Ctrl+Enter в большинстве TTY неотличим от Enter — шлём через Ctrl+S / Alt+Enter + кнопку.
_SEND_KEYS = "ctrl+s,alt+enter,meta+enter,ctrl+enter"


class PromptArea(ClipTextArea):
    """Многострочный ввод: Enter = новая строка; Send = Ctrl+S / Alt+Enter / кнопка."""

    BINDINGS = [
        *ClipTextArea.BINDINGS,
        Binding(_SEND_KEYS, "chat_send", "Send", show=False, priority=True),
    ]

    def action_chat_send(self) -> None:
        screen = self.screen
        send = getattr(screen, "action_send_prompt", None)
        if callable(send):
            send()


class ChatWorkspace(Workspace):
    mode_key = "chat"
    DEFAULT_CSS = """
    #main { height: 1fr; }
    #chats-box { width: 28; border: round $primary; }
    #thread-box { width: 1fr; border: round $primary; }
    #notes-box { width: 42; border: round $primary; }
    #chats-box:focus-within, #thread-box:focus-within, #notes-box:focus-within { border: round $accent; }
    #chats { height: 1fr; border: none; background: transparent; }
    #thread-switch { height: 1fr; }
    #thread-scroll { height: 1fr; scrollbar-size-vertical: 1; }
    #thread-md { padding: 0 1; }
    #thread-sel {
        height: 1fr; border: none; background: transparent; padding: 0 1;
    }
    #prompt-row {
        height: 8; max-height: 8; margin: 0 1 1 1;
    }
    #prompt {
        width: 1fr; height: 1fr; border: round $primary;
    }
    #send-col {
        width: 12; height: 1fr; align: center middle; padding: 0 0 0 1;
    }
    #send-btn { width: 100%; }
    #notes { height: 1fr; border: none; }
    #thread-sel .text-area--selection, #notes .text-area--selection,
    #prompt .text-area--selection {
        background: $accent 45%;
        color: $text;
    }
    #help { height: 1; padding: 0 1; color: $text-muted; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """
    BINDINGS = [
        Binding(alt_keys("a"), "new_chat", "New chat"),
        Binding("delete", "delete_chat", "Delete chat", show=False),
        Binding(alt_keys("n"), "clip_last", "Last → notes"),
        Binding(alt_keys("e"), "edit_last", "Edit last you"),
        Binding(alt_keys("s"), "toggle_select", "Select mode"),
        Binding(alt_keys("m"), "pick_model", "Model"),
        Binding(alt_keys("c"), "cancel", "Cancel"),
        Binding(_SEND_KEYS, "send_prompt", "Send", show=False),
    ]

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.threads: list[chat.Thread] = []
        self.current: chat.Thread | None = None
        self.seen_job: tuple | None = None
        self.genome: Path | None = None
        self.select_mode = False
        self._first_paint_done = False
        # Streaming body paint throttle (status bar owns the live seconds clock).
        self._stream_shown_draft = 0
        self._stream_show_at = 0.0

    def compose(self) -> ComposeResult:
        yield ModeBar(self.mode_key)
        with Horizontal(id="main"):
            with Vertical(id="chats-box"):
                yield OptionList(id="chats")
            with Vertical(id="thread-box"):
                # Select-mode TextArea is mounted lazily on first S — cold F6 stays lighter.
                with ContentSwitcher(initial="thread-scroll", id="thread-switch"):
                    with VerticalScroll(id="thread-scroll"):
                        yield SelectableRichStatic(id="thread-md")
                with Horizontal(id="prompt-row"):
                    yield PromptArea(
                        "",
                        id="prompt",
                        show_line_numbers=False,
                        placeholder="message · Ctrl+S / Alt+Enter / Send · Enter = new line",
                    )
                    with Vertical(id="send-col"):
                        yield Button("Send", id="send-btn", variant="primary")
            with Vertical(id="notes-box"):
                yield ClipTextArea("", id="notes", show_line_numbers=False,
                                  placeholder="drag or Shift+arrows to select · Ctrl+C copy · Ctrl+V paste")
        yield KeyBar([("A", "New chat", "screen.new_chat"),
                      ("Ctrl+S", "Send", "screen.send_prompt"),
                      ("M", "Model", "screen.pick_model"),
                      ("N", "Last → notes", "screen.clip_last"),
                      ("E", "Edit last you", "screen.edit_last"),
                      ("S", "Select mode", "screen.toggle_select"),
                      ("Del", "Delete chat", "screen.delete_chat"),
                      ("C", "Cancel", "screen.cancel"),
                      ("Tab", "Panel", "app.focus_next"),
                      ("F1–F9", "Modes", None), ("Q", "Quit", "app.quit")], id="help")
        yield Static(id="status")

    def _active(self) -> Path | None:
        ws = getattr(self.app, "genome_ws", None)
        if ws and ws.active and ws.active.exists():
            return ws.active
        return active_genome()

    def _focus_prompt(self) -> None:
        self.query_one("#prompt", PromptArea).focus()

    def on_mount(self) -> None:
        self.query_one("#chats-box").border_title = "CHATS"
        self.query_one("#thread-box").border_title = "CHAT"
        self.query_one("#notes-box").border_title = "NOTES"
        # Spinner first frame, then thread list + Markdown bake (cold F6).
        show_busy(self.app, "Chat", "Loading chats…")
        self.call_after_refresh(self._first_paint)

    def _first_paint(self) -> None:
        if self._first_paint_done or not self.is_mounted:
            hide_busy(self.app)
            return
        self._first_paint_done = True
        try:
            try:
                chat.ensure_reference_thread()
            except Exception:
                pass
            self.reload()
            if not self._apply_prefill():
                self._focus_prompt()
            self.set_interval(0.15, self._tick)
        finally:
            hide_busy(self.app)

    def on_screen_resume(self) -> None:
        if self._apply_prefill():
            return
        # Avoid full Markdown rebuild when the open thread is unchanged.
        keep = self.current.id if self.current else None
        updated = self.current.updated if self.current else None
        self.genome = self._active()
        threads = chat.list_threads()
        if not threads:
            self.reload()
            self._focus_prompt()
            return
        self.threads = threads
        stub = next((t for t in threads if t.id == keep), threads[0])
        ol = self.query_one("#chats", OptionList)
        ol.clear_options()
        ol.add_options([Option(chat_prompt(t), id=t.id) for t in self.threads])
        idx = next((i for i, t in enumerate(self.threads) if t.id == stub.id), 0)
        ol.highlighted = idx
        if (keep and stub.id == keep and stub.updated == updated
                and self.current is not None):
            box = self.query_one("#thread-box")
            box.border_subtitle = self._thread_subtitle()
            self.update_status()
            self._focus_prompt()
            return
        self.current = chat.load(stub.id) or stub
        self._show()
        self._focus_prompt()

    def _apply_prefill(self) -> bool:
        """Ask GPT: текст в prompt активного чата. Новый тред — только если чатов нет."""
        text = getattr(self.app, "chat_prefill", None) or ""
        if not text:
            return False
        title = getattr(self.app, "chat_prefill_title", None) or "Ask · F3"
        self.app.chat_prefill = None
        self.app.chat_prefill_title = None
        self._flush_notes()
        threads = chat.list_threads()
        if not threads:
            t = chat.new_thread()
            if title:
                t.title = title[:48]
                chat.save(t)
            self.current = t
            self.reload(keep=t.id)
        else:
            # Активный = текущий после reload, иначе самый свежий.
            keep = self.current.id if self.current else threads[0].id
            self.reload(keep=keep)
            if (self.current and self.current.title == "New chat"
                    and not self.current.messages and title):
                self.current.title = title[:48]
                chat.save(self.current)
                self.reload(keep=self.current.id)
        prompt = self.query_one("#prompt", PromptArea)
        prompt.load_text(text)
        prompt.focus()
        self.app.notify("Ask GPT · review prompt · Ctrl+S / Alt+Enter / Send")
        return True

    def on_screen_suspend(self) -> None:
        self._flush_notes()

    def on_unmount(self) -> None:
        self._flush_notes()

    def reload(self, keep: str | None = None) -> None:
        self.genome = self._active()
        want = keep or (self.current.id if self.current else None)
        self.threads = chat.list_threads()
        if not self.threads:
            self.threads = [chat.new_thread()]
        stub = next((t for t in self.threads if t.id == want), self.threads[0])
        # list_threads без тел — полный load, иначе save(notes) сотрёт messages
        self.current = chat.load(stub.id) or stub
        ol = self.query_one("#chats", OptionList)
        ol.clear_options()
        ol.add_options([Option(chat_prompt(t), id=t.id) for t in self.threads])
        idx = next((i for i, t in enumerate(self.threads) if t.id == self.current.id), 0)
        ol.highlighted = idx
        self._show()

    def _thread_subtitle(self) -> str:
        bits = []
        if self.select_mode:
            bits.append("SELECT · S back to Markdown")
        else:
            bits.append("Markdown · drag+Ctrl+C · S keyboard select")
        if self.genome:
            bits.append(f"active: {self.genome.name}")
        else:
            bits.append("active: — (open a file in F1)")
        return " · ".join(bits)

    def _ensure_select_area(self) -> ReadOnlyClipTextArea:
        """Mount select-mode TextArea on first use (keeps cold F6 free of a 3rd editor)."""
        switch = self.query_one("#thread-switch", ContentSwitcher)
        try:
            return self.query_one("#thread-sel", ReadOnlyClipTextArea)
        except Exception:
            area = ReadOnlyClipTextArea("", id="thread-sel", highlight_cursor_line=False)
            switch.mount(area)
            return area

    def _show(self) -> None:
        t = self.current
        job = JOBS.get(JOB)
        waiting = bool(job and job.active)
        draft = job.draft if waiting and job else ""
        switch = self.query_one("#thread-switch", ContentSwitcher)
        if self.select_mode:
            area = self._ensure_select_area()
            switch.current = "thread-sel"
            plain = thread_text(t, waiting, draft)
            if area.text != plain and not area.selected_text:
                at_end = area.cursor_location[0] >= max(0, area.document.line_count - 2)
                area.load_text(plain)
                if at_end or waiting:
                    area.scroll_end(animate=False)
        else:
            switch.current = "thread-scroll"
            body = self.query_one("#thread-md", SelectableRichStatic)
            width = body.content_size.width or body.size.width or 88
            body.update(thread_view(t, waiting, draft, width=max(40, width - 2)))
            self.query_one("#thread-scroll").scroll_end(animate=False)
        notes = self.query_one("#notes", ClipTextArea)
        if t and notes.text != t.notes:
            notes.load_text(t.notes)
        box = self.query_one("#thread-box")
        box.border_title = fit(t.title, 40) if t else "CHAT"
        box.border_subtitle = self._thread_subtitle()
        self.update_status()

    def _flush_notes(self) -> None:
        if not self.current or not self.is_attached:
            return
        try:
            text = self.query_one("#notes", ClipTextArea).text
        except Exception:
            return
        if text != self.current.notes:
            self.current.notes = text
            chat.save_notes(self.current)

    def update_status(self) -> None:
        key = chat.api_key(self.store)
        if key:
            bits = [chat.model_label(self.store), f"…{key[-4:]}"]
        else:
            bits = ["no key (F2 · Google Gemini · P)"]
        bits.append("SELECT" if self.select_mode else "Markdown")
        if self.genome:
            bits.append(f"active: {self.genome.name}")
        else:
            bits.append("active: — (F1)")
        job = JOBS.get(JOB)
        if job and job.active:
            bits.append(f"⚙ {chat.wait_line(job)}")
        elif job and job.status == "failed":
            bits.append(f"failed: {job.message}")
        n = len(self.current.messages) if self.current else 0
        bits.append(f"{n} messages")
        self.query_one("#status", Static).update("    ".join(bits))

    def action_toggle_select(self) -> None:
        self.select_mode = not self.select_mode
        self._show()
        if self.select_mode:
            self._ensure_select_area().focus()
            self.app.notify("Select mode · drag or Shift+arrows · Ctrl+C · S back")
        else:
            self._focus_prompt()
            self.app.notify("Markdown view")

    def action_pick_model(self) -> None:
        if not chat.api_key(self.store):
            self.app.notify("Set the Gemini API key in F2 (Google Gemini · P)", severity="warning")
            return

        def work() -> None:
            try:
                rows = chat.list_models(self.store, force=True)
            except Exception as exc:
                self.app.call_from_thread(self._models_failed, str(exc)[:200])
                return
            self.app.call_from_thread(self._open_models, rows)

        self.app.notify("Listing Gemini models…")
        self.run_worker(work, thread=True, exclusive=True, group="models")

    def _models_failed(self, msg: str) -> None:
        self.app.notify(msg, severity="error")

    def _open_models(self, rows: list[chat.ChatModel]) -> None:
        current = chat.chosen_model(self.store, rows)

        def done(mid: str | None) -> None:
            if not mid:
                return
            self.store.set_setting(chat.SETTING_MODEL, mid)
            self.app.notify(f"Chat model · {chat.short_model(mid)}")
            self.update_status()

        self.app.push_screen(ModelScreen(rows, current), done)

    def _tick(self) -> None:
        self._flush_notes()
        job = JOBS.get(JOB)
        draft_n = len(job.draft) if job else 0
        # Identity only — draft length is handled below (avoids full rebuild every char).
        sig = (id(job), job.status if job else None, job.finished if job else None)
        if sig != self.seen_job:
            self.seen_job = sig
            if job and not job.active and self.current:
                fresh = chat.load(self.current.id)
                if fresh:
                    self.current = fresh
                    for i, t in enumerate(self.threads):
                        if t.id == fresh.id:
                            self.threads[i] = fresh
                    ol = self.query_one("#chats", OptionList)
                    idx = next((i for i, x in enumerate(self.threads) if x.id == fresh.id), None)
                    if idx is not None:
                        ol.replace_option_prompt_at_index(idx, chat_prompt(fresh))
            self._show()
            self._stream_shown_draft = draft_n
            self._stream_show_at = time.monotonic()
            return

        if not (job and job.active):
            return

        # Live wait_line() seconds → status bar only (no Markdown rebuild).
        self.update_status()

        if draft_n == self._stream_shown_draft:
            return
        now = time.monotonic()
        first_token = self._stream_shown_draft == 0 and draft_n > 0
        grew = draft_n - self._stream_shown_draft
        if not first_token and grew < 80 and now - self._stream_show_at < 0.4:
            return
        self._stream_shown_draft = draft_n
        self._stream_show_at = now
        self._show()

    @on(OptionList.OptionHighlighted, "#chats")
    def _highlighted(self) -> None:
        oid = None
        ol = self.query_one("#chats", OptionList)
        if ol.highlighted is not None:
            opt = ol.get_option_at_index(ol.highlighted)
            oid = str(opt.id) if opt and opt.id else None
        if not oid or (self.current and oid == self.current.id):
            return
        self._flush_notes()
        t = chat.load(oid)
        if t:
            self.current = t
            self._show()

    def _busy(self) -> bool:
        if (job := JOBS.get(JOB)) and job.running:
            self.app.notify("Wait for the current reply, or C to cancel", severity="warning")
            return True
        if not chat.api_key(self.store):
            self.app.notify("Set the Gemini API key in F2 (Google Gemini · P)", severity="warning")
            return True
        return False

    def _kick_send(self) -> None:
        if self.current is None:
            return
        tid = self.current.id
        store = self.store

        def work(job) -> str:
            t = chat.load(tid)
            if t is None:
                raise RuntimeError("chat disappeared")
            try:
                return chat.send(store, t, job)
            except Exception as exc:
                t.messages.append(chat.Message("error", str(exc)[:400]))
                chat.save(t)
                raise

        JOBS.start(JOB, "Gemini", work)
        self.reload(keep=tid)
        self._focus_prompt()

    @on(Button.Pressed, "#send-btn")
    def on_send_btn(self) -> None:
        self.action_send_prompt()

    def action_send_prompt(self) -> None:
        prompt = self.query_one("#prompt", PromptArea)
        text = prompt.text.strip()
        if not text:
            self.app.notify("Type a message first", severity="warning")
            return
        if self._busy():
            self.app.notify("Gemini is still answering · C to cancel", severity="warning")
            return
        prompt.load_text("")
        if self.current is None:
            self.current = chat.new_thread()
            self.threads.insert(0, self.current)
        self._flush_notes()
        self.current.messages.append(chat.Message("user", text))
        if self.current.title == "New chat":
            self.current.title = text.splitlines()[0][:48]
        chat.save(self.current)
        self._kick_send()

    def action_edit_last(self) -> None:
        if not self.current:
            return
        idx = chat.last_user_index(self.current)
        if idx is None:
            self.app.notify("No message to edit")
            return
        self.action_edit_msg(idx)

    def action_edit_msg(self, index: int) -> None:
        t = self.current
        if t is None or index < 0 or index >= len(t.messages):
            return
        if t.messages[index].role != "user":
            return
        if self._busy():
            return
        resend = index == chat.last_user_index(t)

        def done(text: str | None) -> None:
            if not text:
                return
            if self.current and self.current.id == t.id:
                self._flush_notes()
            if resend:
                chat.replace_user(t, index, text)
                self.current = t
                self.app.notify("Resending")
            else:
                self.current = chat.fork_at(t, index, text)
                self.app.notify("New branch")
            self._kick_send()

        self.app.push_screen(EditScreen(t.messages[index].text, resend), done)

    def action_new_chat(self) -> None:
        self._flush_notes()
        t = chat.new_thread()
        self.current = t
        self.reload(keep=t.id)
        self._focus_prompt()

    def action_delete_chat(self) -> None:
        if not self.current or not self.query_one("#chats").has_focus:
            return
        t = self.current
        if chat.is_reference_thread(t.id):
            self.app.notify("Reference · F5 is shared — open it, do not delete",
                            severity="warning")
            return
        title = (t.title or "New chat").strip() or "New chat"
        n = sum(1 for m in t.messages if m.role == "user")

        def done(ok: bool | None) -> None:
            if not ok:
                return
            chat.delete(t.id)
            if self.current and self.current.id == t.id:
                self.current = None
            self.reload()
            self.app.notify(f"Deleted: {title}")

        self.app.push_screen(
            ConfirmScreen(
                f"Delete chat · {title}",
                f"{n} turn{'s' if n != 1 else ''} · this cannot be undone",
                ok="Delete",
                danger=True,
            ),
            done,
        )

    def action_clip_last(self) -> None:
        if not self.current:
            return
        last = next((m.text for m in reversed(self.current.messages) if m.role == "model"), "")
        if not last.strip():
            self.app.notify("No Gemini reply yet")
            return
        notes = self.query_one("#notes", ClipTextArea)
        block = last.rstrip() + "\n"
        if notes.text and not notes.text.endswith("\n"):
            block = "\n\n" + block
        elif notes.text:
            block = "\n" + block
        notes.load_text(notes.text + block)
        self._flush_notes()
        self.app.notify("Last reply copied to NOTES")

    def action_cancel(self) -> None:
        job = JOBS.get(JOB)
        if job and job.active:
            chat.abort_send(job)
            job.cancel()
            self.app.notify("Cancelling Gemini")
        else:
            self.app.notify("Nothing is running")
