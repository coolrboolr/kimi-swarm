"""Unit tests for status/metrics derived from telemetry."""

from __future__ import annotations

import json
import time
from pathlib import Path

from ambient.status import StatusWindow, compute_status


def _write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def test_compute_status_basic(tmp_path: Path) -> None:
    telemetry = tmp_path / "telemetry.jsonl"
    now = time.time()

    events = [
        {"timestamp": now - 10, "run_id": "a", "type": "cycle_started", "data": {"queue_depth": 3}},
        {"timestamp": now - 9, "run_id": "a", "type": "proposal", "data": {}},
        {"timestamp": now - 8, "run_id": "a", "type": "apply_succeeded", "data": {}},
        {"timestamp": now - 7, "run_id": "a", "type": "verify_succeeded", "data": {}},
        {"timestamp": now - 6, "run_id": "a", "type": "cycle_completed", "data": {"status": "success"}},
    ]
    _write_events(telemetry, events)

    st = compute_status(telemetry, window=StatusWindow(seconds=60))
    assert st["telemetry_path"] == str(telemetry)
    assert st["proposals_per_hour"] > 0
    assert st["apply_success_rate"] == 1.0
    assert st["verify_success_rate"] == 1.0
    assert st["queue_depth_max"] == 3
    assert st["last_cycle"]["run_id"] == "a"

