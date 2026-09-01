import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_service.py"
SPEC = importlib.util.spec_from_file_location("check_service", SCRIPT)
assert SPEC and SPEC.loader
check_service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_service)


class MonitorTests(unittest.TestCase):
    def test_evaluate_accepts_healthy_active_slot(self) -> None:
        health = {"status": "ok", "slot": "b"}
        metrics = {
            "slot": "b",
            "oldest_queue_age_s": 12,
            "global_active": 2,
            "global_concurrency": 3,
        }

        self.assertEqual(check_service.evaluate(health, metrics, 600), [])

    def test_evaluate_reports_degraded_and_stale_queue(self) -> None:
        health = {"status": "degraded", "slot": "a"}
        metrics = {
            "slot": "b",
            "oldest_queue_age_s": 601,
            "global_active": 4,
            "global_concurrency": 3,
        }

        problems = check_service.evaluate(health, metrics, 600)

        self.assertEqual(len(problems), 4)


if __name__ == "__main__":
    unittest.main()
