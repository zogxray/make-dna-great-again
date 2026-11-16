"""F7 Chat screen for textual-wgpu — threads + Gemini (app logic, lib widgets)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from rich.console import Console
from rich.text import Text

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
    TextArea,
    Vertical,
    VerticalScroll,
)

from ..core import chat
from ..core import llm
from ..core.jobs import JOBS
from ..core.store import Store
from ..core.ylink import active_genome
from ..workspaces.base import MODE_LABELS, fit
from ..workspaces.chat.view import chat_prompt, thread_text, thread_view
from .wgpu_jobs import _text_lines

JOB = "chat"
JOB_INV = "investigate"
JOB_STORY = "story"
_ACTION_RE = re.compile(r"^screen\.(\w+)(?:\((.*)\))?$", re.DOTALL)
_CUSTOM_MODEL = "__custom__"


def _renderable_text(renderable, *, width: int) -> Text:
    console = Console(
        width=max(40, width),
        height=max(128, width * 4),
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        legacy_windows=False,
    )
    with console.capture() as cap:
        console.print(renderable)
    return Text.from_ansi(cap.get())


class ChatScreen(Screen):
    """F7 Chat on wgpu: list · thread (Markdown drag-select / S keyboard) · notes."""

    DEFAULT_CSS = """
    #main { height: 1fr; }
    #chats-box { width: 28; border: round $primary; }
    #thread-box { width: 1fr; height: 1fr; border: round $primary; }
    #prompt-box { border: round $primary; }
    #notes-box { width: 42; border: round $primary; }
    #chats-box:focus-within, #thread-box:focus-within, #prompt-box:focus-within, #notes-box:focus-within { border: round $accent; }
    #chats { height: 1fr; border: none; background: transparent; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """

    BINDINGS = [
        ("a", "new_chat"),
        ("delete", "delete_chat"),
        ("n", "clip_last"),
        ("e", "edit_last"),
        ("s", "toggle_select"),
        ("m", "pick_model"),
        ("u", "pick_provider"),
        ("c", "cancel"),
        ("i", "investigate"),
        ("t", "story"),
        ("ctrl+s", "send_prompt"),
        ("ctrl+enter", "send_prompt"),
        ("alt+enter", "send_prompt"),
        ("meta+enter", "send_prompt"),
    ]

    def __init__(self, store: Store | None = None) -> None:
        super().__init__()
        self.store = store or Store()
        self.threads: list[chat.Thread] = []
        self.current: chat.Thread | None = None
        self.seen_job: tuple | None = None
        self.genome: Path | None = None
        self.select_mode = False
        self._labels = list(MODE_LABELS)
        self._stream_shown_draft = 0
        self._tick_handle = None
        self._models_pick_open = False

    def compose(self):
        from .app_api import make_mode_bar

        yield make_mode_bar(self, "chat", self._labels)
        chats = OptionList(id="chats", on_highlight=self._on_chat_hi, row_lines=3)
        chats_box = Panel(chats, title="CHATS", id="chats-box")

        thread_md = RichText(id="thread-md", can_focus=False)
        thread_md.on_action = self._run_action
        thread_scroll = VerticalScroll(thread_md, id="thread-scroll")
        thread_sel = TextArea(
            "",
            id="thread-sel",
            placeholder="Keyboard select · Shift+arrows / drag · Ctrl+C · S back to Markdown",
            read_only=True,
            clip_strict=True,
        )
        thread_sel.layout_hint_h = 0
        thread_sel.disabled = True
        thread_switch = Vertical(thread_scroll, thread_sel, id="thread-switch")
        thread_panel = Panel(thread_switch, title="CHAT", id="thread-box")
        actions = KeyBar([
            ("Ctrl+S / Ctrl+Enter", "Send", lambda: self.send_prompt()),
            ("I", "Investigate", lambda: self.investigate()),
            ("T", "Story", lambda: self.story()),
        ], id="chat-actions")
        actions.layout_hint_h = 28
        prompt = TextArea(
            "",
            id="prompt",
            placeholder="message · Enter = new line · Ctrl+S / Ctrl+Enter = send",
            clip_strict=True,
        )
        prompt_box = Panel(prompt, title="MESSAGE", id="prompt-box")
        prompt_box.layout_hint_h = 132
        mid = Vertical(thread_panel, actions, prompt_box, id="thread-col")

        notes = TextArea(
            "",
            id="notes",
            placeholder="notes · Shift+arrows / drag · Ctrl+A all · Ctrl+C copy",
            clip_strict=True,
        )
        notes_box = Panel(notes, title="NOTES", id="notes-box")

        yield Horizontal(chats_box, mid, notes_box, id="main")
        from .app_api import focus_next

        yield KeyBar([
            ("A", "New", lambda: self.new_chat()),
            ("U", "Channel", lambda: self.pick_provider()),
            ("M", "Model", lambda: self.pick_model()),
            ("N", "Last→notes", lambda: self.clip_last()),
            ("E", "Edit last", lambda: self.edit_last()),
            ("S", "Select mode", lambda: self.toggle_select()),
            ("Ctrl+C", "Copy", None),
            ("Del", "Delete", lambda: self.delete_chat()),
            ("C", "Cancel", lambda: self.cancel()),
            ("Tab", "Panel", lambda: focus_next(self)),
            ("Ctrl+S/↵", "Send", lambda: self.send_prompt()),
            ("Q", "Quit", lambda: self.app.ask_quit() if self.app else None),
        ])
        yield StatusBar(id="status")

    def on_screen_mount(self) -> None:
        try:
            chat.ensure_shared_threads()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.query_one("#thread-md", RichText).on_action = self._run_action
        except KeyError:
            pass
        self.reload()
        self._focus_prompt()
        self.call_after_refresh(self._scroll_thread_end)
        if self.app is not None and hasattr(self.app, "set_interval"):
            self._tick_handle = self.app.set_interval(0.2, self._tick)

    def on_screen_resume(self) -> None:
        genome = self._active()
        if genome != self.genome:
            self.genome = genome
        # Refresh thread list / body if files changed under us.
        keep = self.current.id if self.current else None
        try:
            self.reload(keep=keep)
        except Exception:  # noqa: BLE001
            self._show()
            self.update_status()
        self._scroll_thread_end()
        self.call_after_refresh(self._scroll_thread_end)

    def _notify(self, msg: str) -> None:
        if self.app is not None and hasattr(self.app, "notify"):
            self.app.notify(msg)

    def _active(self) -> Path | None:
        if self.app is not None:
            gws = getattr(self.app, "_screens", {}).get("genome")
            if gws is not None:
                active = getattr(gws, "active", None)
                if active is not None and Path(active).exists():
                    return Path(active)
            gws2 = getattr(self.app, "genome_ws", None)
            if gws2 is not None:
                active = getattr(gws2, "active", None)
                if active is not None and Path(active).exists():
                    return Path(active)
        try:
            return active_genome()
        except Exception:  # noqa: BLE001
            return None

    def _chats_focused(self) -> bool:
        try:
            ol = self.query_one("#chats", OptionList)
        except KeyError:
            return False
        return self.app is not None and self.app._focus is ol

    def _focus_prompt(self) -> None:
        self._sync_prompt_lock()
        prompt = self.query_one("#prompt", TextArea)
        if self.current and chat.is_shared_thread(self.current.id):
            if self.app is not None:
                self.app.set_focus(self.query_one("#chats", OptionList))
            return
        prompt.focus()

    def _run_action(self, action: str) -> None:
        """Dispatch Rich ``ydna_click`` (``screen.edit_msg(N)``)."""
        m = _ACTION_RE.match((action or "").strip())
        if not m:
            return
        name, args_src = m.group(1), (m.group(2) or "").strip()
        fn = getattr(self, name, None) or getattr(self, f"action_{name}", None)
        if not callable(fn):
            return
        try:
            args = ast.literal_eval(f"({args_src},)") if args_src else ()
        except Exception:  # noqa: BLE001
            return
        if not isinstance(args, tuple):
            args = (args,)
        fn(*args)

    def _on_chat_hi(self, opt: Option | None) -> None:
        if opt is None or not opt.id:
            return
        if self.current and opt.id == self.current.id:
            return
        self._flush_notes()
        t = chat.load(opt.id)
        if t:
            self.current = t
            self._show()
            self._sync_prompt_lock()
            if self.app is not None:
                self.app.request_draw()

    def reload(self, keep: str | None = None) -> None:
        self.genome = self._active()
        want = keep or (self.current.id if self.current else None)
        self.threads = chat.list_threads()
        if not self.threads:
            self.threads = [chat.new_thread()]
        stub = next((t for t in self.threads if t.id == want), self.threads[0])
        self.current = chat.load(stub.id) or stub
        ol = self.query_one("#chats", OptionList)
        options = []
        for t in self.threads:
            options.append(Option(
                t.title or "New chat",
                id=t.id,
                lines=_text_lines(chat_prompt(t)),
                data=t,
            ))
        ol.set_options(options, keep_id=self.current.id if self.current else None)
        self._show()
        self._sync_prompt_lock()
        if self.app is not None:
            self.app.request_draw()

    def _thread_subtitle(self) -> str:
        if self.select_mode:
            bits = ["SELECT · S back to Markdown"]
        else:
            bits = ["Markdown · drag+Ctrl+C · S keyboard select"]
        if self.genome:
            bits.append(f"active: {self.genome.name}")
        else:
            bits.append("active: — (open a file in F1)")
        return " · ".join(bits)

    def _set_select_layout(self) -> None:
        sc = self.query_one("#thread-scroll", VerticalScroll)
        sel = self.query_one("#thread-sel", TextArea)
        if self.select_mode:
            sc.layout_hint_h = 0
            sel.layout_hint_h = None
            sel.disabled = False
            sel.read_only = True
        else:
            sc.layout_hint_h = None
            sel.layout_hint_h = 0
            sel.disabled = True
            sel.read_only = True

    def _scroll_thread_end(self) -> None:
        if self.select_mode:
            return
        try:
            sc = self.query_one("#thread-scroll", VerticalScroll)
        except KeyError:
            return
        sc.scroll_end()
        if self.app is not None:
            self.app.request_draw()

    def _active_chat_job(self):
        for key in (JOB_INV, JOB_STORY, JOB):
            job = JOBS.get(key)
            if job and job.active:
                return job
        return JOBS.get(JOB_INV) or JOBS.get(JOB_STORY) or JOBS.get(JOB)

    def _show(self) -> None:
        t = self.current
        job = self._active_chat_job()
        waiting = bool(job and job.active)
        draft = job.draft if waiting and job else ""
        self._set_select_layout()
        if self.select_mode:
            area = self.query_one("#thread-sel", TextArea)
            plain = thread_text(t, waiting, draft)
            if area.text != plain:
                area.load_text(plain)
        else:
            body = self.query_one("#thread-md", RichText)
            sc = self.query_one("#thread-scroll", VerticalScroll)
            body.on_action = self._run_action
            avail_w = body.rect.w or (sc.rect.w - sc.BAR_W - 4)
            width = max(40, (avail_w - 12) // 8) if avail_w > 24 else 72
            try:
                rich = _renderable_text(
                    thread_view(t, waiting, draft, width=width), width=width,
                )
                body.update(rich, home=False)
            except Exception:  # noqa: BLE001
                body.update(thread_text(t, waiting, draft), home=False)
            self._scroll_thread_end()
        notes = self.query_one("#notes", TextArea)
        if t and notes.text != t.notes:
            notes.load_text(t.notes)
        box = self.query_one("#thread-box", Panel)
        box.title = f"CHAT · {fit(t.title, 34)}" if (t and t.title) else "CHAT"
        box.subtitle = self._thread_subtitle()
        self._sync_prompt_lock()
        self.update_status()

    def _flush_notes(self) -> None:
        if not self.current:
            return
        try:
            text = self.query_one("#notes", TextArea).text
        except Exception:  # noqa: BLE001
            return
        if text != self.current.notes:
            self.current.notes = text
            chat.save_notes(self.current)

    def _sync_prompt_lock(self) -> None:
        prompt = self.query_one("#prompt", TextArea)
        actions = self.query_one("#chat-actions", KeyBar)
        locked = bool(self.current and chat.is_shared_thread(self.current.id))
        prompt.disabled = locked
        if locked:
            label = chat.shared_thread_label(self.current.id if self.current else None)
            prompt.load_text("")
            prompt.placeholder = f"{label} · read-only · ask from the mode tab"
            actions.set_items([
                ("Ctrl+S", "Send", None),
                ("I", "Investigate", None),
                ("T", "Story", None),
            ])
        else:
            prompt.placeholder = "message · Enter = new line · Ctrl+S / Ctrl+Enter = send"
            actions.set_items([
                ("Ctrl+S / Ctrl+Enter", "Send", lambda: self.send_prompt()),
                ("I", "Investigate", lambda: self.investigate()),
                ("T", "Story", lambda: self.story()),
            ])

    def update_status(self) -> None:
        ok, detail = chat.provider_ready(self.store)
        bits = [chat.model_label(self.store), detail if ok else f"⚠ {detail}"]
        bits.append("SELECT" if self.select_mode else "Markdown")
        if self.genome:
            bits.append(f"active: {self.genome.name}")
        else:
            bits.append("active: — (F1)")
        job = self._active_chat_job()
        if job and job.active:
            bits.append(f"⚙ {chat.wait_line(job)}")
        elif job and job.status == "failed":
            bits.append(f"failed: {job.message}")
        n = len(self.current.messages) if self.current else 0
        bits.append(f"{n} messages")
        self.query_one("#status", StatusBar).update("    ".join(bits))

    def _tick(self) -> None:
        if self.app is None or getattr(self.app, "mode_key", None) != "chat":
            return
        self._flush_notes()
        job = self._active_chat_job()
        draft_n = len(job.draft) if job else 0
        stage = job.stage if job else None
        sig = (id(job), job.status if job else None, job.finished if job else None, stage)
        if sig != self.seen_job:
            self.seen_job = sig
            if job and self.current:
                fresh = chat.load(self.current.id)
                if fresh:
                    self.current = fresh
            self._show()
            self._stream_shown_draft = draft_n
            if self.app is not None:
                self.app.request_draw()
            return
        if not (job and job.active):
            return
        self.update_status()
        if draft_n == self._stream_shown_draft:
            if self.app is not None:
                self.app.request_draw()
            return
        if draft_n - self._stream_shown_draft < 80 and self._stream_shown_draft > 0:
            self.update_status()
            if self.app is not None:
                self.app.request_draw()
            return
        self._stream_shown_draft = draft_n
        if job.key in (JOB_INV, JOB_STORY):
            fresh = chat.load(self.current.id) if self.current else None
            if fresh:
                self.current = fresh
        self._show()
        if self.app is not None:
            self.app.request_draw()

    def _busy(self) -> bool:
        for key in (JOB, JOB_INV, JOB_STORY):
            if (job := JOBS.get(key)) and job.running:
                self._notify("Wait for the current reply, or C to cancel")
                return True
        if self.current and chat.is_shared_thread(self.current.id):
            self._notify(
                f"{chat.shared_thread_label(self.current.id)} is read-only · ask from F3/F5/F6",
            )
            return True
        err = chat.can_send(self.store)
        if err:
            self._notify(err)
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

    def _kick_investigate(self, question: str) -> None:
        if self.current is None:
            return
        tid = self.current.id
        store = self.store
        genome = self._active()

        def work(job) -> str:
            from ..cli.lineage_session import lineage_tool_handlers
            from ..core.investigate_brief import run_investigate

            t = chat.load(tid)
            if t is None:
                raise RuntimeError("chat disappeared")
            try:
                handlers = lineage_tool_handlers(store, genome=genome, job=job)
                out = run_investigate(
                    store, t, job, question, genome=genome, handlers=handlers,
                    with_story=True,
                )
                return (out.get("story") or out.get("oracle") or job.draft or "done")[:2000] or "done"
            except Exception as exc:
                t.messages.append(chat.Message("error", str(exc)[:400]))
                chat.save(t)
                raise

        JOBS.start(JOB_INV, "Investigate", work)
        self.reload(keep=tid)
        self._focus_prompt()

    def _kick_story(self, language: str | None = None) -> None:
        if self.current is None:
            return
        tid = self.current.id
        store = self.store

        def work(job) -> str:
            from ..core.story_brief import run_story

            t = chat.load(tid)
            if t is None:
                raise RuntimeError("chat disappeared")
            try:
                return run_story(store, t, job, language=language)[:2000] or "done"
            except Exception as exc:
                t.messages.append(chat.Message("error", str(exc)[:400]))
                chat.save(t)
                raise

        JOBS.start(JOB_STORY, "Story", work)
        self.reload(keep=tid)
        self._focus_prompt()

    def _take_prompt_text(self) -> str | None:
        if self.current and chat.is_shared_thread(self.current.id):
            self._notify(
                f"{chat.shared_thread_label(self.current.id)} is read-only · "
                "ask from F3/F5/F6",
            )
            return None
        prompt = self.query_one("#prompt", TextArea)
        text = prompt.text.strip()
        if not text:
            self._notify("Type a message first")
            return None
        if self._busy():
            return None
        prompt.load_text("")
        if self.current is None or chat.is_shared_thread(self.current.id):
            self.current = chat.new_thread()
            self.threads.insert(0, self.current)
        self._flush_notes()
        return text

    def send_prompt(self) -> None:
        text = self._take_prompt_text()
        if not text or self.current is None:
            return
        self.current.messages.append(chat.Message("user", text))
        if self.current.title == "New chat":
            self.current.title = text.splitlines()[0][:48]
        chat.save(self.current)
        self._kick_send()

    def investigate(self) -> None:
        text = self._take_prompt_text()
        if not text or self.current is None:
            return
        if self.current.title == "New chat":
            self.current.title = f"Investigate · {text.splitlines()[0][:36]}"
        chat.save(self.current)
        self._notify("Investigate · plan → facts → Oracle → Story")
        self._kick_investigate(text)

    def story(self) -> None:
        if self._busy() or self.current is None:
            return
        if chat.is_shared_thread(self.current.id):
            self._notify(f"{chat.shared_thread_label(self.current.id)} is read-only")
            return
        from ..core.story_brief import find_last_oracle
        if not find_last_oracle(self.current):
            self._notify("Run Investigate first (need an Oracle)")
            return
        prompt = self.query_one("#prompt", TextArea)
        lang = prompt.text.strip() or None
        if lang:
            prompt.load_text("")
        self._notify("Story · human Markdown from last Oracle")
        self._kick_story(language=lang)

    def toggle_select(self) -> None:
        self.select_mode = not self.select_mode
        if self.app is not None:
            self.app.clear_selection()
        self._show()
        if self.select_mode:
            sel = self.query_one("#thread-sel", TextArea)
            sel.read_only = True
            if self.app is not None:
                self.app.set_focus(sel)
            self._notify("Keyboard select · Shift+arrows / drag · Ctrl+C · S back")
        else:
            self._focus_prompt()
            self._notify("Markdown · drag+Ctrl+C")
        if self.app is not None:
            self.app.request_draw()

    def new_chat(self) -> None:
        self._flush_notes()
        self.current = chat.new_thread()
        self.reload(keep=self.current.id)
        self._focus_prompt()
        self._notify("New chat")

    def delete_chat(self) -> None:
        if not self.current:
            return
        if not self._chats_focused():
            self._notify("Focus CHATS (Tab) then Del to delete")
            return
        if chat.is_shared_thread(self.current.id):
            self._notify("Pinned mode chats cannot be deleted")
            return
        tid = self.current.id
        title = (self.current.title or "New chat").strip() or "New chat"
        n = sum(1 for m in self.current.messages if m.role == "user")

        def done(ok: bool) -> None:
            if not ok:
                return
            chat.delete(tid)
            if self.current and self.current.id == tid:
                self.current = None
            self.reload()
            self._focus_prompt()
            self._notify(f"Deleted · {title}")

        from .app_api import require_app

        if not require_app(self, what="delete chat"):
            return
        self.app.ask_confirm(
            f"Delete chat · {title}",
            f"{n} turn{'s' if n != 1 else ''} · this cannot be undone",
            done,
            ok="Delete",
            danger=True,
        )

    def clip_last(self) -> None:
        if not self.current:
            return
        for m in reversed(self.current.messages):
            if m.role == "model" and m.text.strip():
                notes = self.query_one("#notes", TextArea)
                base = notes.text.rstrip()
                chunk = m.text.strip()
                notes.load_text((base + "\n\n" + chunk).strip() + "\n" if base else chunk + "\n")
                self._flush_notes()
                self._notify("Last reply → notes")
                return
        self._notify("No model reply to clip")

    def edit_last(self) -> None:
        if not self.current:
            return
        if chat.is_shared_thread(self.current.id):
            self._notify(f"{chat.shared_thread_label(self.current.id)} is read-only")
            return
        idx = chat.last_user_index(self.current)
        if idx is None:
            self._notify("No message to edit")
            return
        self.edit_msg(idx)

    def edit_msg(self, index: int) -> None:
        t = self.current
        if t is None or index < 0 or index >= len(t.messages):
            return
        if chat.is_shared_thread(t.id):
            self._notify(f"{chat.shared_thread_label(t.id)} is read-only")
            return
        if t.messages[index].role != "user":
            return
        if self._busy():
            return
        resend = index == chat.last_user_index(t)
        title = "Edit · resend" if resend else "Edit · new branch"
        hint = (
            "Resend in this chat · drops later replies · Ctrl+S OK · Esc cancel"
            if resend else
            "New branch from here · original kept · Ctrl+S OK · Esc cancel"
        )

        def done(text: str | None) -> None:
            if not text or not text.strip():
                return
            if self.current and self.current.id == t.id:
                self._flush_notes()
            if resend:
                chat.replace_user(t, index, text.strip())
                self.current = t
                self._notify("Resending")
            else:
                self.current = chat.fork_at(t, index, text.strip())
                self._notify("New branch")
            self._kick_send()

        from .app_api import require_app

        if not require_app(self, what="edit message"):
            return
        self.app.ask_text(title, t.messages[index].text, done, hint=hint)

    def cancel(self) -> None:
        cancelled = False
        for key, label in (
            (JOB_INV, "Investigate"), (JOB_STORY, "Story"), (JOB, "Gemini"),
        ):
            job = JOBS.get(key)
            if job and job.active:
                try:
                    chat.abort_send(job)
                except Exception:  # noqa: BLE001
                    pass
                job.cancel()
                self._notify(f"Cancelling {label}")
                cancelled = True
                break
        if not cancelled:
            self._notify("Nothing is running")

    def _vertex_config(self) -> None:
        """GeminiConfigScreen analogue — one form (same as F2)."""
        from .app_api import require_app

        if not require_app(self, what="Gemini config"):
            return
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
                from pathlib import Path
                path = Path(adc_v).expanduser()
                if not path.is_file():
                    self._notify(f"ADC file not found: {path}")
                    return
            if key:
                self.store.set_secret("gemini", key)
            llm.set_vertex_project(self.store, proj or None)
            llm.set_vertex_adc(self.store, adc_v or None)
            ok2, detail2 = chat.provider_ready(self.store)
            self.update_status()
            if ok2:
                self._notify(f"Vertex ready · {detail2}")
            else:
                self._notify(detail2)
            if self.app is not None:
                self.app.request_draw()

        self.app.ask_form(
            "Google Gemini · F7 credentials",
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

    def pick_provider(self) -> None:
        from .app_api import require_app

        if not require_app(self, what="LLM channel"):
            return
        current = chat.provider_mode(self.store)
        rows: list[tuple[str, str]] = []
        for mode in llm.PROVIDERS:
            ok, detail = llm.provider_ready(self.store, mode)
            mark = "●" if mode == current else " "
            status = detail if ok else f"⚠ {detail}"
            rows.append((mode, f"{mark} {llm.provider_label(mode):<12} {status}"))

        def done(mode: str | None) -> None:
            if mode not in ("vertex", "aistudio"):
                return
            llm.set_provider(self.store, mode)  # type: ignore[arg-type]
            ok, detail = chat.provider_ready(self.store)
            self.update_status()
            label = llm.provider_label(mode)
            if ok:
                self._notify(f"LLM · {label} · {detail}")
            else:
                self._notify(f"LLM · {label} · {detail}")
                if mode == "vertex" and llm.vertex_gaps(self.store):
                    self._vertex_config()
            if self.app is not None:
                self.app.request_draw()

        self.app.ask_pick("LLM channel", rows, done, current=current)

    def _model_rows(self, models: list[chat.ChatModel]) -> list[tuple[str, str]]:
        """ModelScreen-style rows: ● label name · top/pro/lite tags."""
        current = chat.chosen_model(self.store, models)
        rows: list[tuple[str, str]] = []
        for m in models:
            on = (
                m.id == current
                or chat.short_model(m.id) == chat.short_model(current or "")
            )
            mark = "●" if on else " "
            tags = ""
            low = m.name.lower()
            if "argon" in low or "carbon" in low:
                tags += "  top"
            elif "pro" in low.split("-"):
                tags += "  pro"
            if "lite" in low:
                tags += "  lite"
            rows.append((m.id, f"{mark} {m.label:<28} {m.name}{tags}"))
        rows.append((_CUSTOM_MODEL, "… custom model id"))
        return rows

    def _apply_model(self, mid: str | None) -> None:
        if not mid:
            self._models_pick_open = False
            return
        if mid == _CUSTOM_MODEL:
            def custom(v: str | None) -> None:
                self._models_pick_open = False
                if not v or not v.strip():
                    return
                wired = chat.set_saved_model(self.store, v.strip())
                self.update_status()
                self._notify(f"Model · {chat.short_model(wired)}")
                if self.app is not None:
                    self.app.request_draw()

            from .app_api import require_app

            if not require_app(self, what="custom model"):
                return
            self.app.ask_prompt("Custom model id", "", custom)
            return
        self._models_pick_open = False
        wired = chat.set_saved_model(self.store, mid)
        self.update_status()
        self._notify(f"Model · {chat.short_model(wired)}")
        if self.app is not None:
            self.app.request_draw()

    def _open_models(self, models: list[chat.ChatModel]) -> None:
        from .app_api import require_app

        if not require_app(self, what="model pick"):
            return
        if not models:
            # Still allow custom id.
            models = []
        current = chat.chosen_model(self.store, models) if models else None
        rows = self._model_rows(models) if models else [(_CUSTOM_MODEL, "… custom model id")]
        mode = chat.provider_mode(self.store)
        channel = llm.provider_label(mode)
        self._models_pick_open = True
        self.app.ask_pick(
            f"{channel} models (tops first) · Enter use · Esc cancel",
            rows,
            self._apply_model,
            current=current,
        )

    def _refresh_models_if_open(self, models: list[chat.ChatModel]) -> None:
        if not self._models_pick_open or self.app is None:
            return
        pick = getattr(self.app, "_pick", None)
        title = str(pick.get("title") or "") if pick else ""
        if pick is None or "models" not in title.lower():
            return
        current = chat.chosen_model(self.store, models)
        rows = self._model_rows(models)
        pick["options"] = rows
        idx = 0
        for i, (oid, _) in enumerate(rows):
            if oid == current:
                idx = i
                break
        pick["index"] = idx
        self.app.request_draw()
        self._notify(f"Model list refreshed · {len(models)}")

    def pick_model(self) -> None:
        from .app_api import require_app

        if not require_app(self, what="model pick"):
            return
        mode = chat.provider_mode(self.store)
        try:
            models = chat._static_models(self.store, mode)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            models = []
        self._open_models(models)

        if chat.can_send(self.store) is not None:
            return

        self._notify(f"Refreshing {mode} model list…")

        def work() -> None:
            try:
                fresh = chat.list_models(self.store, force=True)
            except Exception:  # noqa: BLE001
                return
            if self.app is not None:
                self.app.call_from_thread(self._refresh_models_if_open, fresh)

        self.app.run_worker(work, name="chat-models", exclusive=True, group="models")
