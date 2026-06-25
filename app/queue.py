"""Single-worker bounded job queue (hard backstop for the codex box).

One job processed at a time (the codex box is the bottleneck). Bounded queue -> 429 when full.
Jobs/state live in memory; book-editor owns the durable queue (contract C3).
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from queue import Full, Queue
from typing import Dict, List, Optional

from . import engine
from .config import settings

log = logging.getLogger("enhance.queue")


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
        self._active: Optional[str] = None
        self._stats = {"total": 0, "done": 0, "error": 0}
        self._durations: List[float] = []
        threading.Thread(target=self._run, daemon=True, name="enhance-worker").start()
        threading.Thread(target=self._sweep, daemon=True, name="enhance-sweeper").start()

    # ---- public API -------------------------------------------------------
    def submit(self, job: Job) -> bool:
        """Enqueue; returns False if the bounded queue is full."""
        try:
            self._queue.put_nowait(job.id)
        except Full:
            return False
        with self._lock:
            self._jobs[job.id] = job
            self._stats["total"] += 1
        return True

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._active is not None

    def queue_len(self) -> int:
        return self._queue.qsize()

    def metrics(self) -> dict:
        with self._lock:
            durs = self._durations[-200:]
            avg = round(sum(durs) / len(durs), 1) if durs else 0
            p95 = round(sorted(durs)[max(0, int(len(durs) * 0.95) - 1)], 1) if durs else 0
            return {
                **self._stats,
                "queue_len": self.queue_len(),
                "busy": self._active is not None,
                "avg_s": avg,
                "p95_s": p95,
            }

    # ---- worker -----------------------------------------------------------
    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            job = self.get(job_id)
            if job is None:
                continue
            with self._lock:
                self._active = job_id
            try:
                self._process(job)
            finally:
                with self._lock:
                    self._active = None

    def _process(self, job: Job) -> None:
        t0 = time.time()
        try:
            job.status, job.progress, job.message = "processing", 10, "processing"

            def cb(pct: float) -> None:
                job.progress = 10 + int(pct * 0.8)  # map ncnn 0-100 -> 10-90
                job.message = f"upscaling {int(pct)}%"

            w, h = engine.enhance(
                job.input_path, job.output_path,
                job.target_w, job.target_h, job.scale_cap, job.face_restore, cb,
            )
            job.width, job.height = w, h
            job.status, job.progress, job.message = "done", 100, "done"
            with self._lock:
                self._stats["done"] += 1
                self._durations.append(time.time() - t0)
        except Exception as e:  # noqa: BLE001 — surface any engine failure as job error
            log.exception("job %s failed", job.id)
            job.status, job.progress, job.message = "error", 0, "error"
            job.error = str(e)[:300]
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
