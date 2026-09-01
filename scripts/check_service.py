#!/usr/bin/env python3
"""External health probe for the active enhance router endpoint.

Reads ENHANCE_MONITOR_URL and ENHANCE_API_KEY. Never prints the key.
Exit 0 means healthy, exit 1 means an alert condition.
"""
import json
import os
import sys
from typing import Any, Dict, List
from urllib.request import Request, urlopen


def _fetch(base_url: str, path: str, api_key: str = "") -> Dict[str, Any]:
    headers = {"X-Enhance-Api-Key": api_key} if api_key else {}
    request = Request(f"{base_url.rstrip('/')}{path}", headers=headers)
    with urlopen(request, timeout=10) as response:  # noqa: S310 — operator-provided URL
        return json.loads(response.read().decode("utf-8"))


def evaluate(health: Dict[str, Any], metrics: Dict[str, Any], max_queue_age_s: float) -> List[str]:
    problems: List[str] = []
    if health.get("status") != "ok":
        problems.append(f"health={health.get('status', 'missing')}")
    if health.get("slot") != metrics.get("slot"):
        problems.append("health/metrics slot mismatch")
    queue_age = float(metrics.get("oldest_queue_age_s") or 0)
    if queue_age > max_queue_age_s:
        problems.append(f"oldest_queue_age_s={queue_age}")
    active = int(metrics.get("global_active") or 0)
    limit = int(metrics.get("global_concurrency") or 0)
    if limit > 0 and active > limit:
        problems.append(f"global_active={active} exceeds global_concurrency={limit}")
    return problems


def main() -> int:
    base_url = os.getenv("ENHANCE_MONITOR_URL", "http://127.0.0.1:8014")
    api_key = os.getenv("ENHANCE_API_KEY", "")
    max_queue_age_s = float(os.getenv("ENHANCE_MONITOR_MAX_QUEUE_AGE_S", "600"))
    try:
        health = _fetch(base_url, "/health")
        metrics = _fetch(base_url, "/metrics", api_key)
        problems = evaluate(health, metrics, max_queue_age_s)
    except Exception as error:  # noqa: BLE001 — probe must turn transport failures into alerts
        print(f"CRITICAL enhance monitor request failed: {type(error).__name__}")
        return 1

    summary = {
        "slot": metrics.get("slot"),
        "queue_len": metrics.get("queue_len"),
        "oldest_queue_age_s": metrics.get("oldest_queue_age_s"),
        "active": metrics.get("active"),
        "global_active": metrics.get("global_active"),
        "last_success_at": metrics.get("last_success_at"),
        "draining": metrics.get("draining"),
    }
    if problems:
        print("CRITICAL " + "; ".join(problems) + " " + json.dumps(summary, ensure_ascii=False))
        return 1
    print("OK " + json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
