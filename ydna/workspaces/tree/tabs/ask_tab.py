"""F3 · ASK tab: inline Gemini brief for the current haplogroup (Tree · F3 chat)."""

from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text

from ...base import click
from . import tab

_ASK_MD_CACHE: dict[tuple[int, int], Text] = {}
_ASK_MD_CACHE_MAX = 24


def _bake_md(body: str, *, width: int = 68) -> Text:
    text = (body or "").rstrip() or " "
    key = (hash(text), max(28, width))
    cached = _ASK_MD_CACHE.get(key)
    if cached is not None:
        return cached
    console = Console(
        width=max(28, width),
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        legacy_windows=False,
    )
    with console.capture() as cap:
        console.print(Padding(Markdown(text), (0, 0, 0, 0)), end="")
    out = Text.from_ansi(cap.get())
    if len(_ASK_MD_CACHE) >= _ASK_MD_CACHE_MAX:
        _ASK_MD_CACHE.pop(next(iter(_ASK_MD_CACHE)), None)
    _ASK_MD_CACHE[key] = out
    return out


def _refresh_ask(ctx) -> None:
    ws = getattr(ctx, "workspace", None)
    if ws is not None and hasattr(ws, "action_ask_gpt"):
        ws.action_ask_gpt(refresh=True)


@tab("ASK", keys={"r": _refresh_ask})
def render_ask(ctx, doc, node: dict) -> None:
    from ..ask import ask_subject, load_cached

    name = str(node.get("name") or "?")
    # Grounding tab = last non-ASK section the user asked from (or Overview).
    ground = getattr(ctx, "ask_ground_tab", None) or "Overview"
    subject = ask_subject(name, ground)
    cache = getattr(ctx, "ask_cache", None) or {}
    entry = cache.get(subject) if isinstance(cache, dict) else None
    if not entry:
        entry = load_cached(subject)

    job = getattr(ctx, "ask_job", None)
    busy = bool(job and getattr(job, "active", False))

    doc.title("ASK")
    doc.t.append("  Scientific note for ", style="#AEA5B8")
    doc.t.append(name, style="bold #FBF6F0")
    doc.t.append(f" · grounded on {ground}\n", style="#AEA5B8")
    doc.t.append("  Shared chat ", style="#AEA5B8")
    doc.t.append("Tree · F3", style="#D4AD72")
    doc.t.append(" (pinned, read-only in F7)\n\n", style="#AEA5B8")

    doc.t.append("  ", style="")
    doc.t.append("A", style=click("screen.ask_gpt", "bold #D4AD72"))
    doc.t.append(" ask / refresh · ", style="#8A8294")
    doc.t.append("R", style=click("screen.ask_gpt_refresh", "bold #D4AD72"))
    doc.t.append(" force refresh\n\n", style="#8A8294")

    if busy:
        draft = (getattr(job, "draft", None) or "").strip()
        stage = getattr(job, "stage", None) or "asking…"
        doc.t.append(f"  ⚙ {stage}\n\n", style="#D4AD72")
        if draft:
            width = max(40, int(getattr(ctx, "panel_width", 0) or 68))
            doc.t.append_text(_bake_md(draft, width=width))
        else:
            doc.t.append("  Waiting for first token…\n", style="#8A8294")
        return

    if entry and entry.get("text"):
        meta = " · ".join(
            x for x in (entry.get("model"), (entry.get("ts") or "")[:19], "Tree · F3")
            if x
        )
        if meta:
            doc.t.append(f"  {meta}\n\n", style="#8A8294")
        width = max(40, int(getattr(ctx, "panel_width", 0) or 68))
        doc.t.append_text(_bake_md(str(entry["text"]), width=width))
        return

    doc.t.append(
        "  No note yet for this haplogroup.\n"
        "  Press A to ask Gemini — answer appears here and in F7 · Tree · F3.\n",
        style="#8A8294",
    )
