"""Фоновые задачи с прогрессом (скачивание, распаковка, проверка) и HTTP-загрузка с докачкой.

Задача живёт в daemon-потоке и не зависит от UI: экран просто опрашивает JOBS.
При выходе из приложения недокачанный файл остаётся как *.part и продолжается при следующем запуске."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

USER_AGENT = "Mozilla/5.0 (compatible; make-dna-great-again/0.1)"
CHUNK = 1 << 20
RETRIES = 5


class Cancelled(Exception):
    pass


@dataclass
class Job:
    key: str
    title: str
    status: str = "running"          # running · done · failed · cancelled
    stage: str = "starting"
    done: int = 0
    total: int | None = None
    unit: str = "B"                  # B · items
    message: str = ""
    draft: str = ""                   # partial stream text (chat etc.)
    started: float = field(default_factory=time.time)
    finished: float | None = None
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _mark: tuple[float, int] = (0.0, 0)
    rate: float = 0.0

    @property
    def running(self) -> bool:
        return self.status == "running"

    def set_stage(self, stage: str, total: int | None = None, done: int = 0) -> None:
        self.stage, self.total, self.done = stage, total, done
        self._mark, self.rate = (time.time(), done), 0.0

    def progress(self, done: int) -> None:
        self.done = done
        now = time.time()
        t0, d0 = self._mark
        if now - t0 >= 1.0:
            inst = (done - d0) / (now - t0)
            self.rate = inst if not self.rate else 0.7 * self.rate + 0.3 * inst
            self._mark = (now, done)

    @property
    def fraction(self) -> float | None:
        return min(1.0, self.done / self.total) if self.total else None

    @property
    def eta(self) -> float | None:
        if not self.total or self.rate <= 0:
            return None
        return max(0.0, (self.total - self.done) / self.rate)

    def cancel(self) -> None:
        self._cancel.set()

    def check(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()


class Jobs:
    def __init__(self) -> None:
        self.items: dict[str, Job] = {}

    def get(self, key: str) -> Job | None:
        return self.items.get(key)

    def running(self) -> list[Job]:
        return [j for j in self.items.values() if j.running]

    def start(self, key: str, title: str, fn: Callable[[Job], str | None]) -> Job:
        """fn(job) выполняется в потоке; её строковый результат становится job.message."""
        old = self.items.get(key)
        if old and old.running:
            raise RuntimeError(f"{title}: already running")
        job = Job(key, title)
        self.items[key] = job

        def run() -> None:
            try:
                job.message = fn(job) or "done"
                job.status = "done"
            except Cancelled:
                job.status, job.message = "cancelled", "cancelled"
            except Exception as exc:  # noqa: BLE001
                job.status, job.message = "failed", str(exc) or type(exc).__name__
            job.finished = time.time()

        threading.Thread(target=run, name=f"job-{key}", daemon=True).start()
        return job


JOBS = Jobs()


# ───────────────────────── операции для задач ─────────────────────────

def md5_file(path: Path, job: Job, label: str) -> str:
    digest = hashlib.md5()
    job.set_stage(f"checking MD5 · {label}", path.stat().st_size)
    done = 0
    with path.open("rb") as fh:
        while chunk := fh.read(8 * CHUNK):
            job.check()
            digest.update(chunk)
            done += len(chunk)
            job.progress(done)
    return digest.hexdigest()


def ensure_space(target: Path, need: int) -> None:
    probe = target
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < need:
        raise RuntimeError(f"not enough disk space in {probe}: need {need / 1e9:.1f} GB, "
                           f"free {free / 1e9:.1f} GB")


def fetch(url: str, dest: Path, job: Job, label: str, size: int | None = None,
          md5: str | None = None) -> str:
    """Скачивает url в dest через dest.part с докачкой (если сервер умеет Range) и повторами.
    Возвращает 'skipped' или 'downloaded'."""
    if dest.exists() and dest.stat().st_size > 0 and (size is None or dest.stat().st_size == size):
        return "skipped"
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, RETRIES + 1):
        have = part.stat().st_size if part.exists() else 0
        if size is not None and have > size:
            part.unlink()
            have = 0
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        if have and (size is None or have < size):
            req.add_header("Range", f"bytes={have}-")
        try:
            if size is not None and have == size:
                break
            with urllib.request.urlopen(req, timeout=60) as resp:
                if have and resp.status != 206:
                    have = 0
                length = resp.headers.get("Content-Length")
                total = have + int(length) if length and length.isdigit() else size
                job.set_stage(f"downloading · {label}", total, have)
                with part.open("ab" if have else "wb") as fh:
                    while chunk := resp.read(CHUNK):
                        job.check()
                        fh.write(chunk)
                        have += len(chunk)
                        job.progress(have)
            break
        except Cancelled:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 416 and size == have:
                break
            if isinstance(exc, urllib.error.HTTPError) and 400 <= exc.code < 500 and exc.code != 429:
                raise RuntimeError(f"{label}: HTTP {exc.code}") from exc
            if attempt == RETRIES:
                raise RuntimeError(f"{label}: {exc}") from exc
            job.set_stage(f"retry {attempt}/{RETRIES - 1} in {5 * attempt}s · {label}: {exc}")
            for _ in range(5 * attempt * 10):
                job.check()
                time.sleep(0.1)

    got = part.stat().st_size
    if size is not None and got != size:
        raise RuntimeError(f"{label}: size {got:,} bytes, expected {size:,}")
    if md5 and md5_file(part, job, label) != md5.lower():
        part.unlink()
        raise RuntimeError(f"{label}: MD5 mismatch, the partial file was removed")
    part.replace(dest)
    return "downloaded"


def run_tool(cmd: list[str], job: Job, out: Path, label: str) -> None:
    """Запускает внешнюю программу; прогресс — растущий размер out. Отмена завершает процесс."""
    job.set_stage(f"{label}", None)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        while proc.poll() is None:
            try:
                job.check()
            except Cancelled:
                proc.terminate()
                proc.wait()
                raise
            if out.exists():
                job.progress(out.stat().st_size)
            time.sleep(0.5)
    finally:
        if proc.poll() is None:
            proc.kill()
    if proc.returncode != 0:
        err = (proc.stderr.read().decode(errors="replace") if proc.stderr else "").strip()
        raise RuntimeError(f"{label} failed ({proc.returncode}): {err[-300:]}")
