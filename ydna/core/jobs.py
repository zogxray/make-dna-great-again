"""Фоновые задачи с прогрессом (скачивание, распаковка, проверка) и HTTP-загрузка с докачкой.

Задача живёт в daemon-потоке и не зависит от UI: экран просто опрашивает JOBS.
Пауза: check() ждёт, пока не resume/cancel. Очередь: enqueue на занятый key →
после done/failed/cancelled стартует следующий.
При выходе из приложения недокачанный файл остаётся как *.part и продолжается при следующем запуске."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
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
    status: str = "running"          # running · paused · queued · done · failed · cancelled
    stage: str = "starting"
    done: int = 0
    total: int | None = None
    unit: str = "B"                  # B · items
    message: str = ""
    draft: str = ""                   # partial stream text (chat etc.)
    started: float = field(default_factory=time.time)
    finished: float | None = None
    slot: str | None = None           # ключ слота очереди (обычно = key у running)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _paused: threading.Event = field(default_factory=threading.Event, repr=False)
    _mark: tuple[float, int] = (0.0, 0)
    rate: float = 0.0
    _fn: Callable[["Job"], str | None] | None = field(default=None, repr=False)

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def active(self) -> bool:
        """Занимает слот: выполняется или на паузе (не queued/finished)."""
        return self.status in ("running", "paused")

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
        if not self.total or self.rate <= 0 or self.status == "paused":
            return None
        return max(0.0, (self.total - self.done) / self.rate)

    def pause(self) -> bool:
        """Поставить на паузу (check() будет ждать). False если не running."""
        if self.status != "running":
            return False
        self._paused.set()
        self.status = "paused"
        self.rate = 0.0
        if not (self.stage or "").startswith("paused"):
            self.stage = f"paused · {self.stage}"
        return True

    def resume(self) -> bool:
        """Снять паузу. False если не paused."""
        if self.status != "paused":
            return False
        if self.stage.startswith("paused · "):
            self.stage = self.stage[len("paused · "):]
        self.status = "running"
        self._mark = (time.time(), self.done)
        self._paused.clear()
        return True

    def cancel(self) -> None:
        self._cancel.set()
        self._paused.clear()  # разбудить check()
        if self.status == "queued":
            self.status = "cancelled"
            self.message = "cancelled"
            self.finished = time.time()

    def check(self) -> None:
        """Отмена → Cancelled; пауза → ждать resume/cancel."""
        while True:
            if self._cancel.is_set():
                raise Cancelled()
            if not self._paused.is_set():
                return
            time.sleep(0.1)


@dataclass
class _Queued:
    title: str
    fn: Callable[[Job], str | None]
    ui_key: str


class Jobs:
    def __init__(self) -> None:
        self.items: dict[str, Job] = {}
        self._queues: dict[str, deque[_Queued]] = {}
        self._qseq = 0
        self._lock = threading.Lock()
        self._shutting_down = False
        self._threads: list[threading.Thread] = []

    def get(self, key: str) -> Job | None:
        return self.items.get(key)

    def running(self) -> list[Job]:
        return [j for j in self.items.values() if j.running]

    def active_for(self, key: str) -> Job | None:
        j = self.items.get(key)
        if j and j.active:
            return j
        return None

    def queued_for(self, key: str) -> list[Job]:
        """UI-джобы со status=queued для слота key."""
        return [j for j in self.items.values()
                if j.status == "queued" and (j.slot or j.key) == key]

    def list_visible(self) -> list[Job]:
        return list(self.items.values())

    def start(self, key: str, title: str, fn: Callable[[Job], str | None]) -> Job:
        """fn(job) в потоке; строковый результат → job.message. Слот key должен быть свободен."""
        with self._lock:
            if self._shutting_down:
                raise RuntimeError(f"{title}: shutting down")
            old = self.items.get(key)
            if old and old.active:
                raise RuntimeError(f"{title}: already running")
            # убрать старый finished с тем же key
            job = Job(key, title, slot=key, _fn=fn)
            self.items[key] = job
        self._spawn(job, fn)
        return job

    def enqueue(self, key: str, title: str, fn: Callable[[Job], str | None]) -> Job:
        """Если слот свободен — start; иначе в очередь (status=queued в списке F7)."""
        with self._lock:
            if self._shutting_down:
                raise RuntimeError(f"{title}: shutting down")
            cur = self.items.get(key)
            if cur and cur.active:
                self._qseq += 1
                ui_key = f"{key}·q{self._qseq}"
                qjob = Job(ui_key, title, status="queued", stage="waiting", slot=key, _fn=fn)
                self.items[ui_key] = qjob
                self._queues.setdefault(key, deque()).append(_Queued(title, fn, ui_key))
                return qjob
            job = Job(key, title, slot=key, _fn=fn)
            self.items[key] = job
        self._spawn(job, fn)
        return job

    def cancel_queued(self, ui_key: str) -> bool:
        """Убрать элемент очереди по ui_key. True если был queued."""
        with self._lock:
            job = self.items.get(ui_key)
            if not job or job.status != "queued":
                return False
            slot = job.slot or job.key
            q = self._queues.get(slot)
            if q:
                self._queues[slot] = deque(x for x in q if x.ui_key != ui_key)
            job.cancel()
            return True

    def cancel_all(self) -> int:
        """Отменить active + сбросить очереди (для shutdown). Возвращает число затронутых."""
        with self._lock:
            self._shutting_down = True
            self._queues.clear()
            n = 0
            for j in list(self.items.values()):
                if j.status == "queued":
                    j.cancel()
                    n += 1
                elif j.active:
                    j.cancel()
                    n += 1
            return n

    def wait_idle(self, timeout: float = 6.0) -> bool:
        """Ждать, пока не останется active. True если успели."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not any(j.active for j in self.items.values()):
                # добить короткие хвосты потоков
                alive = [t for t in self._threads if t.is_alive()]
                if not alive:
                    return True
                for t in alive:
                    t.join(0.05)
                if not any(t.is_alive() for t in self._threads):
                    return True
            time.sleep(0.05)
        return not any(j.active for j in self.items.values())

    def _spawn(self, job: Job, fn: Callable[[Job], str | None]) -> None:
        key = job.key

        def run() -> None:
            try:
                job.message = fn(job) or "done"
                if job._cancel.is_set():
                    job.status, job.message = "cancelled", "cancelled"
                else:
                    job.status = "done"
            except Cancelled:
                job.status, job.message = "cancelled", "cancelled"
            except Exception as exc:  # noqa: BLE001
                job.status, job.message = "failed", str(exc) or type(exc).__name__
            job.finished = time.time()
            self._pump(key)

        th = threading.Thread(target=run, name=f"job-{key}", daemon=True)
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()]
            self._threads.append(th)
        th.start()

    def _pump(self, key: str) -> None:
        """После завершения слота — взять следующий из очереди."""
        with self._lock:
            if self._shutting_down:
                return
            if self.active_for(key):
                return
            q = self._queues.get(key)
            if not q:
                return
            nxt = q.popleft()
            self.items.pop(nxt.ui_key, None)
            old = self.items.get(key)
            if old is not None and old.status in ("done", "failed", "cancelled"):
                # архив, чтобы F2/F7 успели показать результат до старта следующего
                self._qseq += 1
                hist = Job(
                    key=f"{key}·h{self._qseq}",
                    title=old.title,
                    status=old.status,
                    stage=old.stage,
                    done=old.done,
                    total=old.total,
                    unit=old.unit,
                    message=old.message,
                    draft=old.draft,
                    started=old.started,
                    finished=old.finished,
                    slot=key,
                )
                self.items[hist.key] = hist
            job = Job(key, nxt.title, slot=key, _fn=nxt.fn)
            self.items[key] = job
            fn = nxt.fn
        self._spawn(job, fn)


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
