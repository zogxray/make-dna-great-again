"""Тексты F6 Chat: строка в списке и история треда.

Два представления одного треда:
- thread_view — Rich Markdown (красивый просмотр)
- thread_text — plain (выделение кусков, как NOTES)
"""

from __future__ import annotations

from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text

from ...core.chat import REF_THREAD_ID, Thread
from ...theme.styles import (BAD, BAD_BOLD, DIM, FAINT, HI, HI_BOLD, HI_REV, MUTED,
                             OK_BOLD, WARN, WARN_BOLD)
from ..base import click, fit

LIST_W = 22
ROLE_STYLE = {"user": HI_REV, "model": OK_BOLD, "error": BAD_BOLD}
ROLE_LABEL = {"user": "YOU", "model": "GEMINI", "error": "ERROR"}
BODY_STYLE = {"user": HI, "model": "", "error": BAD}

# Live Markdown is re-measured/re-rendered on every Textual layout pass (F6 mount).
# Bake model bodies to Text once per (text, width) — same look, cheap Static height.
_MD_CACHE: dict[tuple[int, int], Text] = {}
_MD_CACHE_MAX = 48
_MD_WIDTH = 88


def clear_md_cache() -> None:
    _MD_CACHE.clear()


def chat_prompt(t: Thread) -> Text:
    out = Text()
    title = t.title or "New chat"
    if t.id == REF_THREAD_ID:
        out.append(fit(title, LIST_W) + "\n", style="bold #D4AD72")
    else:
        out.append(fit(title, LIST_W) + "\n", style="bold")
    if t.user_turns is not None:
        n = t.user_turns
    else:
        n = sum(1 for m in t.messages if m.role == "user")
    extra = " · notes" if t.notes.strip() else ""
    if t.id == REF_THREAD_ID:
        extra = " · F5" + extra
    out.append(f"  {n} turn{'s' if n != 1 else ''}{extra}\n", style=MUTED)
    out.append("  " + (t.updated.replace("T", " ")[5:16] if t.updated else ""), style=FAINT)
    return out

def _header_rich(role: str, at: str = "", edit_index: int | None = None) -> Text:
    d = Text()
    d.append(f"\n {ROLE_LABEL.get(role, role)} ", style=ROLE_STYLE.get(role, ""))
    if at:
        d.append(f" {at.replace('T', ' ')[11:19]}", style=DIM)
    if edit_index is not None:
        d.append("  ")
        d.append("edit", style=click(f"screen.edit_msg({edit_index})", HI_BOLD))
    d.append("\n")
    return d


def _plain(body: str, style: str = "") -> Text:
    d = Text()
    for line in (body.rstrip() or " ").splitlines() or [" "]:
        d.append(f"  {line}\n", style=style)
    return d


def _md(body: str, *, width: int = _MD_WIDTH) -> Text:
    """Parse Markdown once → styled Text (cached)."""
    text = body.rstrip() or " "
    key = (hash(text), width)
    cached = _MD_CACHE.get(key)
    if cached is not None:
        return cached
    console = Console(
        width=max(40, width),
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        legacy_windows=False,
    )
    with console.capture() as cap:
        console.print(Padding(Markdown(text), (0, 0, 0, 2)), end="")
    out = Text.from_ansi(cap.get())
    if len(_MD_CACHE) >= _MD_CACHE_MAX:
        # Drop an arbitrary old entry (order not meaningful for correctness).
        _MD_CACHE.pop(next(iter(_MD_CACHE)), None)
    _MD_CACHE[key] = out
    return out


def thread_view(t: Thread | None, waiting: bool, draft: str = "",
                *, width: int | None = None) -> RenderableType:
    """Markdown-вид для просмотра (model bodies baked to Text)."""
    w = width or _MD_WIDTH
    parts: list[RenderableType] = []
    if t is None:
        d = Text()
        d.append("  No chat. Press A for a new one.\n", style=MUTED)
        return d
    if not t.messages and not waiting:
        d = Text()
        d.append("  Type below and press Enter. Search: on for 2.5; off for Gemini 3.x (free tier).\n",
                 style=MUTED)
        d.append("  M picks a free Flash model · N last reply → NOTES · S select mode.\n", style=DIM)
        return d
    for i, m in enumerate(t.messages):
        edit = i if m.role == "user" and not waiting else None
        parts.append(_header_rich(m.role, m.at, edit))
        if m.role == "model":
            parts.append(_md(m.text, width=w))
        else:
            parts.append(_plain(m.text, BODY_STYLE.get(m.role, "")))
    if waiting:
        head = Text()
        head.append("\n GEMINI ", style=WARN_BOLD)
        if draft.strip():
            head.append(" streaming\n", style=WARN)
            parts.append(head)
            parts.append(_md(draft, width=w))
        else:
            head.append(" …\n", style=WARN)
            parts.append(head)
    return Group(*parts) if parts else Text()


def _header_plain(role: str, at: str = "") -> str:
    label = ROLE_LABEL.get(role, role)
    clock = f" {at.replace('T', ' ')[11:19]}" if at else ""
    return f"\n── {label}{clock} ──\n"


def _body_plain(text: str) -> str:
    body = text.rstrip() or " "
    return "".join(f"  {line}\n" for line in body.splitlines() or [" "])


def thread_text(t: Thread | None, waiting: bool, draft: str = "") -> str:
    """Plain-вид для select mode (drag / Shift+arrows / Ctrl+C)."""
    if t is None:
        return "  No chat. Press A for a new one.\n"
    if not t.messages and not waiting:
        return (
            "  Select mode · drag or Shift+arrows · Ctrl+C copies the selection.\n"
            "  S returns to Markdown view.\n"
        )
    parts: list[str] = []
    for m in t.messages:
        parts.append(_header_plain(m.role, m.at))
        parts.append(_body_plain(m.text))
    if waiting:
        if draft.strip():
            parts.append("\n── GEMINI  streaming ──\n")
            parts.append(_body_plain(draft))
        else:
            parts.append("\n── GEMINI  … ──\n")
    return "".join(parts)
