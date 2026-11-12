"""Open F1–F10 in order, time until each mode is idle, cProfile hotspots.

Uses the real local ``data/`` tree (private — never commit kits). Safe to run
only on your machine; skips if another app instance holds the lock.

CLI::

    python -m ydna.bench.mode_load
    python -m ydna.bench.mode_load --timeout 300 --out ~/.cache/make-dna-great-again/bench

Pytest::

    MDGA_LOCAL_BENCH=1 pytest tests/test_mode_load_profile.py -s
"""

from __future__ import annotations

import argparse
import cProfile
import io
import os
import pstats
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from textual.worker import WorkerState

from ..workspaces.base import MODE_LABELS, BusyModal

# Workers that are "background housekeeping" and should not block "ready"
# forever if they linger (F2 source checks can run long; we still wait for them
# unless --quick).
# Background F2 source checks / Discover coverage — UI is usable without waiting.
_QUICK_IGNORE_PREFIXES = ("check-", "enrich-")


@dataclass
class ModeTiming:
    key: str
    title: str
    wall_s: float
    wait_s: float
    press_s: float = 0.0         # time inside pilot.press / first mount (before wait)
    ttfl_s: float | None = None  # time to first BusyModal; None = never shown
    loader_s: float = 0.0        # how long BusyModal stayed up
    busy_stages: list[str] = field(default_factory=list)
    # F6 Reference breakdown (worker / apply / GROUPS·MAP·GROUP)
    ref_load_s: float | None = None
    ref_apply_s: float | None = None
    ref_paint_s: float | None = None
    ref_fill_list_s: float | None = None
    ref_map_s: float | None = None
    ref_card_s: float | None = None
    profile_top: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class WaitResult:
    wait_s: float
    ttfl_s: float | None
    loader_s: float
    stages: list[str]


def _fmt_s(seconds: float | None, *, width: int = 7, none: str = "   —  ") -> str:
    """Format seconds for aligned columns; units are always seconds."""
    if seconds is None:
        return f"{none:>{width}}" if width else none
    return f"{seconds:{width}.2f}s"


@dataclass
class BenchReport:
    timings: list[ModeTiming]
    out_dir: Path
    total_s: float

    def summary_table(self) -> str:
        """Human-readable report. All durations are wall-clock seconds (s)."""
        lines = [
            "Mode load bench — все времена в секундах (s), wall-clock",
            "",
            "Что измеряем на каждый F1–F10:",
            "  wall     полное время режима: F-клавиша → UI готов (press + ready)",
            "  press    синхронный switch_mode / первый mount экрана (до ожидания)",
            "           сюда же попадает тяжёлый Rich/Markdown без BusyModal (F10)",
            "  to_busy  до первого BusyModal (спиннер); — = не было",
            "  busy     сколько спиннер реально висел на экране",
            "  ready    после press: до тишины (нет модалки / воркеров / paint)",
            "  stages   тексты стадий BusyModal по порядку",
            "",
            "F6 Reference дополнительно дробит busy на фазы:",
            "  worker   фон: каталог AADR / Y / results",
            "  apply    main thread: слои / бандлы после воркера",
            "  paint    main thread: GROUPS list + MAP + GROUP card",
            "",
            f"{'Mode':<4} {'Title':<12} {'wall':>8} {'press':>8} {'to_busy':>8} "
            f"{'busy':>8} {'ready':>8}  status",
            "-" * 80,
        ]
        for t in self.timings:
            label = f"F{self._fn(t.key)}"
            if t.error:
                status = f"ERROR: {t.error}"
            elif t.ttfl_s is None and t.press_s >= 0.5:
                status = "mount/paint (нет loader)"
            elif t.ttfl_s is None:
                status = "без BusyModal"
            else:
                status = "ok"
            if len(status) > 28:
                status = status[:25] + "…"
            lines.append(
                f"{label:<4} {t.title:<12} {_fmt_s(t.wall_s)} {_fmt_s(t.press_s)} "
                f"{_fmt_s(t.ttfl_s)} {_fmt_s(t.loader_s)} {_fmt_s(t.wait_s)}  {status}"
            )
            if t.ref_paint_s is not None and not t.error:
                lines.append(
                    f"     └─ F5 фазы:  worker {_fmt_s(t.ref_load_s or 0.0, width=6)}  "
                    f"apply {_fmt_s(t.ref_apply_s or 0.0, width=6)}  "
                    f"paint {_fmt_s(t.ref_paint_s, width=6)}"
                )
                lines.append(
                    f"        paint →  GROUPS {_fmt_s(t.ref_fill_list_s or 0.0, width=6)}  "
                    f"MAP {_fmt_s(t.ref_map_s or 0.0, width=6)}  "
                    f"card {_fmt_s(t.ref_card_s or 0.0, width=6)}"
                )
            if t.busy_stages and not t.error:
                shown = " → ".join(t.busy_stages[-4:])
                if len(shown) > 64:
                    shown = "…" + shown[-61:]
                lines.append(f"     stages: {shown}")
        lines.append("-" * 80)
        lines.append(f"TOTAL (весь прогон F1–F10): {_fmt_s(self.total_s, width=0)}")
        lines.append(f"отчёт → {self.out_dir}")
        lines.append("        summary.txt  — эта таблица")
        lines.append("        detail.md    — то же + cProfile сниппеты")
        return "\n".join(lines)

    def detail_markdown(self) -> str:
        """Per-mode narrative for detail.md (seconds throughout)."""
        blocks: list[str] = [
            "# Mode load detail",
            "",
            "Единицы: секунды (s), wall-clock (время по часам, не нагрузка CPU).",
            "",
            "| поле | смысл |",
            "|---|---|",
            "| wall | полное время режима: F-клавиша → UI готов (= press + ready) |",
            "| press | синхронный mount/switch до начала wait; Rich Markdown без loader |",
            "| to_busy | до первого появления BusyModal; never = спиннера не было |",
            "| busy | суммарное время, пока BusyModal на экране |",
            "| ready | после press: до тишины (нет модалки / воркеров / paint) |",
            "",
        ]
        for t in self.timings:
            fn = self._fn(t.key)
            blocks.append(f"## F{fn} {t.title} (`{t.key}`)")
            blocks.append("")
            if t.error:
                blocks.append(f"**ERROR:** `{t.error}`")
                blocks.append("")
                continue
            to_busy = f"{t.ttfl_s:.3f}s" if t.ttfl_s is not None else "never (спиннера не было)"
            blocks.append(f"- **wall** `{t.wall_s:.3f}s` — полное время режима")
            blocks.append(
                f"- **press** `{t.press_s:.3f}s` — mount/switch "
                f"(для F10 это почти весь wall: Markdown help, данных нет)"
            )
            blocks.append(f"- **to_busy** `{to_busy}` — до первого BusyModal")
            blocks.append(f"- **busy** `{t.loader_s:.3f}s` — спиннер на экране")
            blocks.append(f"- **ready** `{t.wait_s:.3f}s` — до тишины UI")
            if t.ref_paint_s is not None:
                blocks.append("")
                blocks.append("F5 фазы (внутри busy, после фона):")
                blocks.append(
                    f"- worker `{t.ref_load_s or 0:.3f}s` — каталог / Y / results в фоне"
                )
                blocks.append(
                    f"- apply `{t.ref_apply_s or 0:.3f}s` — слои/бандлы на main thread"
                )
                blocks.append(
                    f"- paint `{t.ref_paint_s:.3f}s` — отрисовка GROUPS + MAP + card"
                )
                blocks.append(
                    f"  - GROUPS list `{t.ref_fill_list_s or 0:.3f}s`"
                )
                blocks.append(f"  - MAP `{t.ref_map_s or 0:.3f}s`")
                blocks.append(f"  - GROUP card `{t.ref_card_s or 0:.3f}s`")
            if t.busy_stages:
                blocks.append("")
                blocks.append("stages: " + " → ".join(t.busy_stages))
            if t.profile_top:
                blocks.append("")
                blocks.append("```")
                blocks.append(t.profile_top)
                blocks.append("```")
            blocks.append("")
        return "\n".join(blocks)

    @staticmethod
    def _fn(key: str) -> str:
        for i, (k, _) in enumerate(MODE_LABELS):
            if k == key:
                return str(i + 1)
        return "?"


def _active_workers(app: Any, *, quick: bool) -> list[str]:
    out: list[str] = []
    for w in app.workers:
        if w.state not in (WorkerState.PENDING, WorkerState.RUNNING):
            continue
        name = (w.name or w.group or w.__class__.__name__ or "worker")
        group = getattr(w, "group", "") or ""
        label = f"{group}:{name}" if group else str(name)
        if quick and any(str(group).startswith(p) or str(name).startswith(p)
                         for p in _QUICK_IGNORE_PREFIXES):
            continue
        out.append(label)
    return out


def _busy_stage(app: Any) -> str | None:
    screen = app.screen
    if not isinstance(screen, BusyModal):
        return None
    try:
        node = screen.query_one("#busy-stage")
        raw = getattr(node, "renderable", None) or getattr(node, "_renderable", None)
        text = str(raw) if raw is not None else str(node.render())
        return text.strip() or screen._title
    except Exception:  # noqa: BLE001
        return screen._title


def _mode_flags(app: Any) -> dict[str, Any]:
    """Cheap readiness hints from the active workspace (no private paths)."""
    screen = app.screen
    if isinstance(screen, BusyModal):
        return {"busy": True, "stage": _busy_stage(app)}
    key = getattr(screen, "mode_key", None) or app._current_mode
    flags: dict[str, Any] = {"mode": key, "busy": False}
    if key == "genome":
        flags["phase"] = getattr(screen, "phase", None)
    elif key == "data":
        flags["checking"] = len(getattr(screen, "checking", ()) or ())
    elif key == "tree":
        flags["ready"] = bool(getattr(screen, "ready", False))
        flags["branch_busy"] = bool(getattr(screen, "branch_busy", False))
    elif key == "pgs":
        flags["refreshing"] = bool(getattr(screen, "refreshing", False))
    elif key == "reference":
        flags["loading"] = getattr(screen, "loading_msg", None)
        flags["ui_ready"] = bool(getattr(screen, "ui_ready", True))
        flags["paint_s"] = getattr(screen, "last_paint_s", None)
    elif key == "samples":
        flags["shown"] = len(getattr(screen, "shown", ()) or ())
        flags["catalog"] = getattr(getattr(screen, "cat", None), "n", 0)
    return flags


async def wait_until_idle(
    pilot: Any,
    *,
    timeout: float,
    quick: bool,
    settle: float = 0.2,
) -> WaitResult:
    """Wait until no BusyModal and no (relevant) workers.

    Also tracks:
    - ttfl_s: seconds until the first BusyModal appears (None if never)
    - loader_s: total time BusyModal was the top screen
    """
    app = pilot.app
    t0 = time.perf_counter()
    stages: list[str] = []
    last_stage = ""
    ttfl_s: float | None = None
    loader_s = 0.0
    loader_on_since: float | None = None
    while True:
        elapsed = time.perf_counter() - t0
        if elapsed > timeout:
            flags = _mode_flags(app)
            workers = _active_workers(app, quick=quick)
            raise TimeoutError(
                f"idle timeout {timeout:.0f}s · flags={flags} · workers={workers[:6]}"
            )
        stage = _busy_stage(app)
        now = time.perf_counter()
        if stage is not None:
            if ttfl_s is None:
                ttfl_s = now - t0
            if loader_on_since is None:
                loader_on_since = now
            if stage != last_stage:
                stages.append(stage)
                last_stage = stage
        elif loader_on_since is not None:
            loader_s += now - loader_on_since
            loader_on_since = None

        workers = _active_workers(app, quick=quick)
        screen = app.screen
        if not isinstance(screen, BusyModal):
            phase = getattr(screen, "phase", None)
            refreshing = bool(getattr(screen, "refreshing", False))
            loading_msg = getattr(screen, "loading_msg", None)
            branch_busy = bool(getattr(screen, "branch_busy", False))
            checking = getattr(screen, "checking", None)
            ui_ready = bool(getattr(screen, "ui_ready", True))
        else:
            phase = refreshing = loading_msg = branch_busy = False
            checking = None
            ui_ready = False  # still painting under BusyModal counts as not ready

        idle = (
            stage is None
            and not workers
            and not phase
            and not refreshing
            and not loading_msg
            and not branch_busy
            and ui_ready
            and not (checking if not quick else None)
        )
        if idle:
            await pilot.pause(settle)
            # re-check after settle
            if _busy_stage(app) is None and not _active_workers(app, quick=quick):
                screen2 = app.screen
                if isinstance(screen2, BusyModal):
                    await pilot.pause(0.05)
                    continue
                if getattr(screen2, "phase", None):
                    await pilot.pause(0.05)
                    continue
                if bool(getattr(screen2, "refreshing", False)):
                    await pilot.pause(0.05)
                    continue
                if getattr(screen2, "loading_msg", None):
                    await pilot.pause(0.05)
                    continue
                if bool(getattr(screen2, "branch_busy", False)):
                    await pilot.pause(0.05)
                    continue
                if not bool(getattr(screen2, "ui_ready", True)):
                    await pilot.pause(0.05)
                    continue
                if not quick and getattr(screen2, "checking", None):
                    await pilot.pause(0.05)
                    continue
                if loader_on_since is not None:
                    loader_s += time.perf_counter() - loader_on_since
                    loader_on_since = None
                return WaitResult(
                    wait_s=time.perf_counter() - t0,
                    ttfl_s=ttfl_s,
                    loader_s=loader_s,
                    stages=stages,
                )
        await pilot.pause(0.05)


def _profile_report(pr: cProfile.Profile, *, limit: int = 25) -> str:
    buf = io.StringIO()
    stats = pstats.Stats(pr, stream=buf)
    stats.sort_stats(pstats.SortKey.CUMULATIVE)
    stats.print_stats(limit)
    return buf.getvalue()


def _write_mode_profile(out_dir: Path, key: str, pr: cProfile.Profile, text: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    prof_path = out_dir / f"{key}.pstats"
    pr.dump_stats(str(prof_path))
    (out_dir / f"{key}.txt").write_text(text, encoding="utf-8")


async def run_mode_load_bench(
    *,
    timeout: float = 180.0,
    quick: bool = False,
    out_dir: Path | None = None,
    size: tuple[int, int] = (140, 40),
    profile: bool = True,
) -> BenchReport:
    """Drive YDnaApp through F1–F10; return timings + write profiles under out_dir."""
    from ..tui.app import YDnaApp
    from ..core import db
    from ..core.single import AlreadyRunning, acquire, release
    from ..core.store import cache_dir

    if out_dir is None:
        out_dir = cache_dir() / "bench" / time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        acquire()
    except AlreadyRunning as exc:
        raise RuntimeError(str(exc)) from exc

    timings: list[ModeTiming] = []
    t_all = time.perf_counter()
    try:
        app = YDnaApp()
        async with app.run_test(size=size) as pilot:
            # Cold start lands on F1 Genome
            for i, (key, title) in enumerate(MODE_LABELS):
                fn = f"f{i + 1}"
                pr = cProfile.Profile() if profile else None
                busy_stages: list[str] = []
                err = ""
                wait_s = 0.0
                press_s = 0.0
                ttfl_s: float | None = None
                loader_s = 0.0
                t0 = time.perf_counter()
                try:
                    if pr:
                        pr.enable()
                    if i == 0:
                        # Already on genome after mount — just wait until idle.
                        t_press0 = time.perf_counter()
                        await pilot.pause(0.05)
                        press_s = time.perf_counter() - t_press0
                    else:
                        t_press0 = time.perf_counter()
                        await pilot.press(fn)
                        press_s = time.perf_counter() - t_press0
                    wr = await wait_until_idle(
                        pilot, timeout=timeout, quick=quick,
                    )
                    wait_s, ttfl_s, loader_s = wr.wait_s, wr.ttfl_s, wr.loader_s
                    busy_stages = list(wr.stages)
                    # Confirm mode switched (except when F-key is current)
                    if app._current_mode != key:
                        # action_mode may have been blocked; try explicit
                        app.action_mode(key)
                        wr2 = await wait_until_idle(
                            pilot, timeout=timeout, quick=quick,
                        )
                        wait_s += wr2.wait_s
                        loader_s += wr2.loader_s
                        if ttfl_s is None:
                            ttfl_s = wr2.ttfl_s
                        busy_stages.extend(wr2.stages)
                except Exception as exc:  # noqa: BLE001
                    err = f"{type(exc).__name__}: {exc}"
                finally:
                    if pr:
                        pr.disable()
                wall = time.perf_counter() - t0
                top = ""
                if pr:
                    top = _profile_report(pr)
                    _write_mode_profile(out_dir, key, pr, top)
                    # keep a short snippet in the timing object
                    lines = [ln for ln in top.splitlines() if ln.strip()]
                    top = "\n".join(lines[0:1] + lines[5:15])
                ref_kw: dict[str, float | None] = {}
                if key == "reference":
                    scr = app.screen
                    if getattr(scr, "mode_key", None) == "reference":
                        ref_kw = {
                            "ref_load_s": float(getattr(scr, "last_load_s", 0.0) or 0.0),
                            "ref_apply_s": float(getattr(scr, "last_apply_s", 0.0) or 0.0),
                            "ref_paint_s": float(getattr(scr, "last_paint_s", 0.0) or 0.0),
                            "ref_fill_list_s": float(getattr(scr, "last_fill_list_s", 0.0) or 0.0),
                            "ref_map_s": float(getattr(scr, "last_refresh_map_s", 0.0) or 0.0),
                            "ref_card_s": float(getattr(scr, "last_refresh_card_s", 0.0) or 0.0),
                        }
                timings.append(ModeTiming(
                    key=key, title=title, wall_s=wall, wait_s=wait_s,
                    press_s=press_s, ttfl_s=ttfl_s, loader_s=loader_s,
                    busy_stages=busy_stages, profile_top=top, error=err,
                    **ref_kw,
                ))
                # Soft pause so the next mode starts from a quiet UI
                await pilot.pause(0.1)
            await pilot.pause(0.05)
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            release()
        except Exception:  # noqa: BLE001
            pass

    report = BenchReport(
        timings=timings,
        out_dir=out_dir,
        total_s=time.perf_counter() - t_all,
    )
    (out_dir / "summary.txt").write_text(report.summary_table() + "\n", encoding="utf-8")
    (out_dir / "detail.md").write_text(report.detail_markdown(), encoding="utf-8")
    return report


def run_deep_loaders(out_dir: Path) -> str:
    """cProfile known sync loaders against local data/ (no TUI, no kit paths in output)."""
    from ..core import openhumans as oh
    from ..core.store import Store, data_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    store = Store()
    lines: list[str] = ["# Deep loader profiles (local data/)", ""]

    def _run(name: str, fn) -> None:
        pr = cProfile.Profile()
        t0 = time.perf_counter()
        pr.enable()
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001
            pr.disable()
            lines.append(f"## {name}")
            lines.append(f"ERROR {type(exc).__name__}: {exc}")
            lines.append("")
            return
        pr.disable()
        wall = time.perf_counter() - t0
        text = _profile_report(pr, limit=20)
        _write_mode_profile(out_dir, f"deep_{name}", pr, text)
        summary = ""
        if isinstance(result, tuple):
            summary = f" result={result[0]!r}" if result else ""
        elif isinstance(result, (int, float)):
            summary = f" result={result}"
        elif hasattr(result, "n"):
            summary = f" n={getattr(result, 'n', '?')}"
        lines.append(f"## {name}  wall={wall:.3f}s{summary}")
        # strip absolute paths from profile text for safer sharing
        safe = text
        root = str(data_dir())
        if root:
            safe = safe.replace(root, "$DATA")
        lines.extend(safe.splitlines()[5:18])
        lines.append("")

    def oh_catalog():
        return oh.load_catalog(force=True)

    def oh_filter():
        cat = oh.load_catalog()
        return len(oh.apply_filters(cat.files, oh.OhFilters()))

    def tree_build():
        from ..workspaces.tree.data import load_or_build
        ht = store.path("ftdna_haplotree")
        if not ht or not Path(ht).is_file():
            raise FileNotFoundError("ftdna_haplotree not set")
        disc = store.path("ftdna_discover")
        disc_p = Path(disc) if disc and Path(disc).is_dir() else None
        data, cached = load_or_build(Path(ht), disc_p, lambda *_a, **_k: None)
        return ("cached" if cached else "built", data.get("stats", {}).get("nodes"))

    def aadr_catalog():
        from ..core import aadr
        folder = store.path("aadr")
        if not folder:
            raise FileNotFoundError("aadr not set")
        cat = aadr.load_catalog(folder)
        n = len(getattr(cat, "groups", ()) or getattr(cat, "people", ()) or ())
        return n

    def pgs_ensure():
        from ..core import pgs_db
        folder = store.path("pgs_models")
        if not folder:
            raise FileNotFoundError("pgs_models not set")
        if not pgs_db.catalog_fresh(folder):
            pgs_db.ensure_catalog(folder)
        return len(pgs_db.list_menus())

    for name, fn in (
        ("openhumans_catalog", oh_catalog),
        ("openhumans_filter_chip", oh_filter),
        ("tree_load_or_build", tree_build),
        ("aadr_catalog", aadr_catalog),
        ("pgs_ensure", pgs_ensure),
    ):
        _run(name, fn)

    text = "\n".join(lines) + "\n"
    (out_dir / "deep.md").write_text(text, encoding="utf-8")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Profile F1–F10 mode load times (local data/).")
    parser.add_argument("--timeout", type=float, default=180.0,
                        help="Per-mode idle timeout seconds (default 180)")
    parser.add_argument("--quick", action="store_true",
                        help="Do not wait for F2 source-check workers")
    parser.add_argument("--out", type=Path, default=None,
                        help="Report directory (default: cache/bench/<timestamp>)")
    parser.add_argument("--no-profile", action="store_true",
                        help="Wall times only (skip cProfile on UI thread)")
    parser.add_argument("--deep", action="store_true",
                        help="Also cProfile core loaders (catalog/tree/AADR/PGS) without TUI")
    parser.add_argument("--deep-only", action="store_true",
                        help="Only run --deep loaders (no F-key UI drive)")
    parser.add_argument("--cols", type=int, default=140)
    parser.add_argument("--rows", type=int, default=40)
    args = parser.parse_args(argv)

    import asyncio

    from ..core.store import cache_dir

    out = args.out or (cache_dir() / "bench" / time.strftime("%Y%m%d-%H%M%S"))
    out = Path(out)

    if args.deep_only or args.deep:
        from ..core.single import AlreadyRunning, acquire, release
        from ..core import db
        try:
            acquire()
        except AlreadyRunning as exc:
            print(exc, flush=True)
            return 1
        try:
            print(run_deep_loaders(out), flush=True)
        finally:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass
            release()
        if args.deep_only:
            return 0

    try:
        report = asyncio.run(run_mode_load_bench(
            timeout=args.timeout,
            quick=args.quick,
            out_dir=out,
            size=(args.cols, args.rows),
            profile=not args.no_profile,
        ))
    except RuntimeError as exc:
        print(exc, flush=True)
        return 1
    print(report.summary_table(), flush=True)
    failed = [t for t in report.timings if not t.ok]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
