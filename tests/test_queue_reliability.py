import threading
import time
import tempfile
import unittest
from pathlib import Path
from queue import Queue
from unittest.mock import patch

from app import codex_provider
from app.config import settings
from app.queue import FileSlotSemaphore, Job, JobManager


class QueueReliabilityTests(unittest.TestCase):
    @staticmethod
    def _manager() -> JobManager:
        manager = JobManager.__new__(JobManager)
        manager._jobs = {}
        manager._lock = threading.Lock()
        manager._queue = Queue(maxsize=2)
        manager._active = set()
        manager._stats = {"total": 0, "done": 0, "error": 0}
        manager._provider_stats = {}
        manager._durations = []
        manager._last_success = None
        manager._draining = False
        manager._provider_slots = FileSlotSemaphore(Path(tempfile.gettempdir()) / "enhance-test-locks", 1)
        return manager

    def test_submit_rejects_new_job_after_drain(self) -> None:
        manager = self._manager()
        manager.drain()

        result = manager.submit(Job("ej_a_1", "/tmp/in", "/tmp/out"))

        self.assertEqual(result, "draining")
        self.assertEqual(manager.queue_len(), 0)
        self.assertEqual(manager.metrics()["resident_jobs"], 0)

    def test_plan_never_skips_primary_because_job_waited(self) -> None:
        manager = self._manager()
        job = Job("ej_a_2", "/tmp/in", "/tmp/out", created=time.time() - 3600)

        with (
            patch.object(settings, "provider", "codex"),
            patch.object(settings, "enhance_fallback", "magnific"),
            patch.object(settings, "fallback_after_wait_s", 1),
        ):
            plan = manager._plan(job)

        self.assertEqual([name for name, _ in plan], ["codex", "magnific"])

    def test_file_slot_semaphore_serializes_process_slots(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            semaphore_a = FileSlotSemaphore(Path(td), 1)
            semaphore_b = FileSlotSemaphore(Path(td), 1)
            first_acquired = threading.Event()
            release_first = threading.Event()
            second_acquired = threading.Event()

            def first() -> None:
                with semaphore_a.acquire():
                    first_acquired.set()
                    release_first.wait(timeout=2)

            def second() -> None:
                first_acquired.wait(timeout=2)
                with semaphore_b.acquire():
                    second_acquired.set()

            thread_a = threading.Thread(target=first)
            thread_b = threading.Thread(target=second)
            thread_a.start()
            thread_b.start()
            self.assertTrue(first_acquired.wait(timeout=1))
            self.assertFalse(second_acquired.wait(timeout=0.2))
            release_first.set()
            self.assertTrue(second_acquired.wait(timeout=1))
            thread_a.join(timeout=1)
            thread_b.join(timeout=1)

    def test_safe_codex_tail_redacts_credentials(self) -> None:
        value = "Selected model is at capacity Authorization: Bearer-secret token=abc123"

        tail = codex_provider._safe_tail(value)

        self.assertIn("Selected model is at capacity", tail)
        self.assertNotIn("Bearer-secret", tail)
        self.assertNotIn("abc123", tail)
        self.assertIn("Authorization=***", tail)
        self.assertIn("token=***", tail)

    def test_provider_error_chain_keeps_primary_and_fallback_reasons(self) -> None:
        class FailingProvider:
            def __init__(self, reason: str) -> None:
                self.reason = reason

            def enhance(self, *args, **kwargs):
                raise RuntimeError(self.reason)

        with tempfile.TemporaryDirectory() as td:
            manager = self._manager()
            manager._provider_slots = FileSlotSemaphore(Path(td) / "locks", 1)
            input_path = Path(td) / "input.jpg"
            input_path.write_bytes(b"input")
            job = Job("ej_a_3", str(input_path), str(Path(td) / "output.jpg"))
            plan = [
                ("codex", FailingProvider("Selected model is at capacity")),
                ("magnific", FailingProvider("HTTP 402 insufficient credits")),
            ]

            with patch.object(manager, "_plan", return_value=plan):
                manager._process(job)

            self.assertEqual(job.status, "error")
            self.assertIn("codex: Selected model is at capacity", job.error or "")
            self.assertIn("magnific: HTTP 402 insufficient credits", job.error or "")
            metrics = manager.metrics()
            self.assertEqual(metrics["providers"]["codex"]["error"], 1)
            self.assertEqual(metrics["providers"]["magnific"]["error"], 1)


if __name__ == "__main__":
    unittest.main()
