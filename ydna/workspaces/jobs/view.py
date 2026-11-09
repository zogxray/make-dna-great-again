"""Форматирование списка и карточки задачи для F7 Jobs."""

from __future__ import annotations

from rich.text import Text

from ...core.jobs import Job
from ...core.sources import human_size
from ..base import fit
from ..data.workspace import amount, bar, duration, job_line

STATUS_ICON = {
    "running": ("▶", "#53A94B"),
    "paused": ("Ⅱ", "#D4AD72"),
    "queued": ("○", "#8A8294"),
    "done": ("■", "#5882C7"),
    "failed": ("✕", "#B04A4A"),
    "cancelled": ("·", "#8A8294"),
}

FILTERS = ("all", "running", "paused", "queued", "failed", "done", "cancelled")


def status_of(job: Job) -> str:
    return job.status if job.status in STATUS_ICON else "running"


def kind_guess(job: Job) -> str:
    """Грубая метка по key/title/stage — только для UI."""
    k = (job.key or "").lower()
    t = (job.title or "").lower()
    s = (job.stage or "").lower()
    if "gemini" in t or k == "chat":
        return "chat"
    if "pgs" in k or t.startswith("pgs"):
        return "score"
    if s.startswith(("downloading", "retry")) or "download" in t:
        return "download"
    if "fetch" in t or "discover" in k:
        return "fetch"
    if "ingest" in t or "genome" in k:
        return "ingest"
    if "md5" in s or "check" in s:
        return "verify"
    if "unpack" in s or "extract" in s:
        return "unpack"
    return "task"


def list_prompt(job: Job, width: int = 52) -> Text:
    st = status_of(job)
    icon, style = STATUS_ICON[st]
    t = Text()
    t.append(f"{icon} ", style=style)
    t.append(fit(job.title, width) + "\n", style="bold")
    line = f"   {job.key} · {kind_guess(job)}"
    if st == "running":
        line += f" · {job_line(job)}"
    elif st == "paused":
        line += f" · paused · {job_line(job)}"
    elif st == "queued":
        slot = job.slot or job.key
        line += f" · waiting on {slot}"
    elif job.message:
        line += f" · {fit(job.message, max(12, width - 20))}"
    t.append(line, style="#8A8294")
    return t


def detail_text(job: Job | None, *, filter_name: str, n_total: int) -> Text:
    t = Text()
    if job is None:
        t.append("JOBS · background manager\n\n", style="bold")
        t.append(
            "Two panels: list ← → detail. Tasks still start from F2 / F3 / F4 / F6;\n"
            "this screen mirrors JOBS (pause, cancel, clear; Discover stages queue).\n\n",
            style="#AEA5B8",
        )
        t.append("What shows up here\n", style="bold #D4AD72")
        t.append("  download   F2 files (.part resume)\n", style="#8A8294")
        t.append("  fetch      Discover JSON packs (queue stages 1–5)\n", style="#8A8294")
        t.append("  score      F4 PGS\n", style="#8A8294")
        t.append("  chat       F6 Gemini\n", style="#8A8294")
        t.append("  verify     MD5 / checks\n\n", style="#8A8294")
        t.append("Status legend\n", style="bold #D4AD72")
        for key, (icon, style) in STATUS_ICON.items():
            note = {
                "paused": "P toggles · check() waits",
                "queued": "same slot busy · starts after current",
            }.get(key, "")
            t.append(f"  {icon} ", style=style)
            t.append(f"{key}", style="bold")
            if note:
                t.append(f"  · {note}", style="#8A8294")
            t.append("\n")
        t.append(f"\nFilter: {filter_name} · {n_total} in registry\n", style="#8A8294")
        return t

    st = status_of(job)
    icon, style = STATUS_ICON[st]
    t.append(f"{icon} ", style=style)
    t.append(f"{job.title}\n", style="bold")
    t.append(f"key     {job.key}\n", style="#AEA5B8")
    if job.slot and job.slot != job.key:
        t.append(f"slot    {job.slot}\n", style="#AEA5B8")
    t.append(f"kind    {kind_guess(job)}\n", style="#AEA5B8")
    t.append(f"status  {st}", style=style)
    if job.finished and job.started:
        t.append(f"  ·  {duration(job.finished - job.started)} elapsed", style="#8A8294")
    t.append("\n\n")

    t.append("Progress\n", style="bold #D4AD72")
    frac = job.fraction
    if frac is not None:
        t.append(f"  [{bar(frac, 28)}]  {frac:.0%}\n", style="#FBF6F0")
    else:
        t.append("  [············ indeterminate ············]\n", style="#8A8294")
    t.append(f"  stage  {job.stage or '—'}\n", style="#AEA5B8")
    if job.total is not None:
        t.append(f"  done   {amount(job, job.done)} / {amount(job, job.total)}\n",
                 style="#AEA5B8")
    elif job.done:
        t.append(f"  done   {amount(job, job.done)}\n", style="#AEA5B8")
    if job.rate:
        rate = (f"{human_size(job.rate)}/s" if job.unit == "B"
                else f"{job.rate * 60:.0f}/min")
        t.append(f"  rate   {rate}\n", style="#AEA5B8")
    if job.eta is not None:
        t.append(f"  ETA    {duration(job.eta)}\n", style="#AEA5B8")
    if job.message:
        t.append(f"\nMessage\n  {job.message}\n", style="#AEA5B8")
    if job.draft:
        preview = job.draft.strip().replace("\n", " ")
        t.append(f"\nDraft\n  {fit(preview, 72)}\n", style="#8A8294")

    t.append("\nActions\n", style="bold #D4AD72")
    if st == "running":
        t.append("  [P]  pause\n", style="#FBF6F0")
        t.append("  [C]  cancel\n", style="#FBF6F0")
    elif st == "paused":
        t.append("  [P]  resume\n", style="#FBF6F0")
        t.append("  [C]  cancel\n", style="#FBF6F0")
    elif st == "queued":
        t.append("  [C]  remove from queue\n", style="#FBF6F0")
    elif st == "failed":
        t.append("  [R]  retry (start again from the source screen)\n", style="#8A8294")
    elif st in ("done", "cancelled"):
        t.append("  [X]  clear finished from this list\n", style="#FBF6F0")
    else:
        t.append("  —\n", style="#8A8294")
    t.append("\nNote  tasks are still started in F2/F3/F4/F6; F7 is the monitor.\n",
             style="#8A8294")
    return t
