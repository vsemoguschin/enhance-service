"""Bounded job queue (hard backstop for the box).

Число воркеров зависит от провайдера (settings.workers): для local это 1 — ncnn упирается в CPU
и параллель только душит соседей по боксу; для magnific воркеры почти всё время ждут сеть,
поэтому их несколько. Bounded queue -> 429 when full.
Jobs/state live in memory; book-editor owns the durable queue (contract C3).
"""
import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from queue import Full, Queue
from typing import Dict, Iterator, List, Optional, Set

import fcntl

from . import codex_provider, engine, magnific
from .config import settings

log = logging.getLogger("enhance.queue")

# Все провайдеры реализуют одну сигнатуру enhance(), поэтому контракт C1 от выбора не зависит.
PROVIDERS = {"local": engine, "magnific": magnific, "codex": codex_provider}


class ProviderChainError(RuntimeError):
    """Все провайдеры завершились ошибкой; причины не перекрывают друг друга."""


class FileSlotSemaphore:
    """Межпроцессный семафор на advisory-lock файлах.

    Оба blue/green процесса используют один каталог. `flock` автоматически снимается
    ядром при завершении процесса, поэтому crash не оставляет вечный занятый слот.
    """

    def __init__(self, directory: Path, limit: int) -> None:
        self.directory = directory
        self.limit = max(1, limit)
        self.directory.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def acquire(self) -> Iterator[int]:
        while True:
            for slot in range(self.limit):
                path = self.directory / f"provider-{slot}.lock"
                fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(fd)
                    continue
                try:
                    yield slot
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
                return
            time.sleep(0.1)

    def occupied(self) -> int:
        """Best-effort snapshot числа занятых provider slots во всех процессах."""
        busy = 0
        for slot in range(self.limit):
            path = self.directory / f"provider-{slot}.lock"
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                busy += 1
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        return busy


@dataclass
class Job:
    id: str
    input_path: str
    output_path: str
    target_w: Optional[int] = None
    target_h: Optional[int] = None
    scale_cap: float = 4.0
    face_restore: bool = False
    status: str = "pending"  # pending | processing | done | error
    progress: int = 0
    message: str = "queued"
    width: Optional[int] = None
    height: Optional[int] = None
    error: Optional[str] = None
    created: float = field(default_factory=time.time)
    finished: Optional[float] = None


class JobManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._queue: "Queue[str]" = Queue(maxsize=settings.queue_max)
        self._active: Set[str] = set()
        self._stats = {"total": 0, "done": 0, "error": 0}
        self._provider_stats: Dict[str, Dict[str, int]] = {}
        self._durations: List[float] = []
        self._last_success: Optional[float] = None
        self._draining = False
        self._provider_slots = FileSlotSemaphore(
            settings.global_lock_dir, settings.global_concurrency
        )
        for i in range(max(1, settings.workers)):
            threading.Thread(target=self._run, daemon=True, name=f"enhance-worker-{i}").start()
        threading.Thread(target=self._sweep, daemon=True, name="enhance-sweeper").start()

    # ---- public API -------------------------------------------------------
    def submit(self, job: Job) -> str:
        """Enqueue; returns accepted, draining or full."""
        with self._lock:
            if self._draining:
                return "draining"
            # Lock закрывает гонку: worker после put не сможет прочитать registry до
            # того, как job появится в `_jobs`.
            try:
                self._queue.put_nowait(job.id)
            except Full:
                return "full"
            self._jobs[job.id] = job
            self._stats["total"] += 1
        return "accepted"

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    @property
    def busy(self) -> bool:
        """True, когда свободных воркеров не осталось."""
        with self._lock:
            return len(self._active) >= max(1, settings.workers)

    def queue_len(self) -> int:
        return self._queue.qsize()

    @property
    def draining(self) -> bool:
        with self._lock:
            return self._draining

    def drain(self) -> dict:
        """Запретить новые jobs; уже принятые продолжают выполняться."""
        with self._lock:
            self._draining = True
        return self.drain_state()

    def drain_state(self) -> dict:
        with self._lock:
            pending = sum(1 for job in self._jobs.values() if job.status == "pending")
            active = len(self._active)
            return {
                "draining": self._draining,
                "drained": self._draining and pending == 0 and active == 0,
                "pending": pending,
                "active": active,
            }

    @staticmethod
    def _iso_timestamp(value: Optional[float]) -> Optional[str]:
        if value is None:
            return None
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()

    def metrics(self) -> dict:
        with self._lock:
            durs = self._durations[-200:]
            avg = round(sum(durs) / len(durs), 1) if durs else 0
            p95 = round(sorted(durs)[max(0, int(len(durs) * 0.95) - 1)], 1) if durs else 0
            now = time.time()
            pending = [job for job in self._jobs.values() if job.status == "pending"]
            oldest_queue_age = round(now - min(job.created for job in pending), 1) if pending else 0
            return {
                **self._stats,
                "slot": settings.slot,
                "queue_len": self.queue_len(),
                "workers": max(1, settings.workers),
                "global_concurrency": max(1, settings.global_concurrency),
                "global_active": self._provider_slots.occupied(),
                "active": len(self._active),
                "busy": len(self._active) >= max(1, settings.workers),
                "resident_jobs": len(self._jobs),
                "oldest_queue_age_s": oldest_queue_age,
                "draining": self._draining,
                "drained": self._draining and not pending and not self._active,
                "last_success_at": self._iso_timestamp(self._last_success),
                "providers": {
                    name: dict(counts) for name, counts in self._provider_stats.items()
                },
                "avg_s": avg,
                "p95_s": p95,
            }

    def _record_provider(self, name: str, outcome: str) -> None:
        with self._lock:
            counts = self._provider_stats.setdefault(
                name, {"attempts": 0, "success": 0, "error": 0}
            )
            if outcome == "attempts":
                counts["attempts"] += 1
            else:
                counts[outcome] += 1

    # ---- worker -----------------------------------------------------------
    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                job = self.get(job_id)
                if job is None:
                    continue
                with self._lock:
                    self._active.add(job_id)
                try:
                    self._process(job)
                finally:
                    with self._lock:
                        self._active.discard(job_id)
            finally:
                self._queue.task_done()

    def _plan(self, job: Job) -> List[tuple]:
        """Кого звать: основной провайдер и запасной.

        Запасной нужен только после фактической ошибки основного провайдера. Ожидание
        в очереди не является ошибкой Codex и не должно пропускать основной маршрут.
        """
        primary = PROVIDERS.get(settings.provider, engine)
        plan = [(settings.provider, primary)]

        fallback = PROVIDERS.get(settings.enhance_fallback)
        if not fallback or fallback is primary:
            return plan

        plan.append((settings.enhance_fallback, fallback))
        return plan

    def _process(self, job: Job) -> None:
        t0 = time.time()
        try:
            job.status, job.progress, job.message = "processing", 10, "processing"

            def cb(pct: float) -> None:
                job.progress = 10 + int(pct * 0.8)  # map ncnn 0-100 -> 10-90
                job.message = f"upscaling {int(pct)}%"

            plan = self._plan(job)
            errors: List[str] = []
            w = h = None
            with self._provider_slots.acquire() as provider_slot:
                log.info("job %s получил provider-slot %d", job.id, provider_slot)
                for name, provider in plan:
                    try:
                        job.message = f"processing ({name})"
                        self._record_provider(name, "attempts")
                        w, h = provider.enhance(
                            job.input_path, job.output_path,
                            job.target_w, job.target_h, job.scale_cap, job.face_restore, cb,
                        )
                        self._record_provider(name, "success")
                        break
                    except Exception as e:  # noqa: BLE001 — fallback сохраняет job
                        self._record_provider(name, "error")
                        reason = " ".join(str(e).split())[:500]
                        errors.append(f"{name}: {reason}")
                        log.warning("job %s: провайдер %s упал (%s)", job.id, name, reason[:200])
            if w is None:
                raise ProviderChainError("; ".join(errors) or "no provider produced a result")
            job.width, job.height = w, h
            job.status, job.progress, job.message = "done", 100, "done"
            with self._lock:
                self._stats["done"] += 1
                self._durations.append(time.time() - t0)
                self._last_success = time.time()
        except Exception as e:  # noqa: BLE001 — surface any engine failure as job error
            log.exception("job %s failed", job.id)
            job.status, job.progress, job.message = "error", 0, "error"
            job.error = str(e)[:1000]
            with self._lock:
                self._stats["error"] += 1
        finally:
            job.finished = time.time()
            try:
                Path(job.input_path).unlink(missing_ok=True)
            except OSError:
                pass

    def _sweep(self) -> None:
        """Drop finished jobs + their result files past TTL."""
        while True:
            time.sleep(300)
            now = time.time()
            with self._lock:
                stale = [jid for jid, j in self._jobs.items()
                         if j.finished and now - j.finished > settings.result_ttl_s]
                for jid in stale:
                    j = self._jobs.pop(jid)
                    try:
                        Path(j.output_path).unlink(missing_ok=True)
                    except OSError:
                        pass
            if stale:
                log.info("swept %d expired jobs", len(stale))


manager = JobManager()
