from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StatusWindow:
    seconds: float


def _iter_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                events.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return events


def compute_status(telemetry_path: Path, *, window: StatusWindow | None = None) -> dict[str, Any]:
    """Compute basic ops metrics from telemetry.jsonl (best-effort)."""
    window = window or StatusWindow(seconds=3600.0)
    now = time.time()
    cutoff = now - float(window.seconds)

    events = _iter_events(telemetry_path)
    recent = [e for e in events if float(e.get("timestamp", 0.0) or 0.0) >= cutoff]

    proposals = [e for e in recent if e.get("type") == "proposal"]
    apply_ok = [e for e in recent if e.get("type") == "apply_succeeded"]
    apply_fail = [e for e in recent if e.get("type") == "apply_failed"]
    verify_ok = [e for e in recent if e.get("type") == "verify_succeeded"]
    verify_fail = [e for e in recent if e.get("type") == "verify_failed"]

    # Cycle latencies from start->completed (match by run_id).
    starts: dict[str, float] = {}
    latencies: list[float] = []
    queue_depths: list[int] = []
    for e in recent:
        rid = str(e.get("run_id") or "")
        ts = float(e.get("timestamp", 0.0) or 0.0)
        if e.get("type") == "cycle_started":
            starts[rid] = ts
            try:
                qd = int((e.get("data") or {}).get("queue_depth", 0))
                queue_depths.append(qd)
            except Exception:
                pass
        elif e.get("type") == "cycle_completed":
            if rid in starts:
                latencies.append(max(0.0, ts - starts[rid]))

    def _p(values: list[float] | list[int], pct: float) -> float | None:
        if not values:
            return None
        if pct <= 0:
            return float(sorted(values)[0])
        if pct >= 100:
            return float(sorted(values)[-1])
        s = sorted(values)
        idx = int(round((pct / 100.0) * (len(s) - 1)))
        return float(s[max(0, min(len(s) - 1, idx))])

    def _rate(ok_count: int, fail_count: int) -> float | None:
        denom = ok_count + fail_count
        return (ok_count / denom) if denom else None

    last_cycle = next((e for e in reversed(events) if e.get("type") == "cycle_completed"), None)

    return {
        "window_seconds": window.seconds,
        "telemetry_path": str(telemetry_path),
        "proposals_per_hour": (len(proposals) / (window.seconds / 3600.0)) if window.seconds else 0.0,
        "apply_success_rate": _rate(len(apply_ok), len(apply_fail)),
        "verify_success_rate": _rate(len(verify_ok), len(verify_fail)),
        "queue_depth_p95": _p(queue_depths, 95.0),
        "queue_depth_max": (max(queue_depths) if queue_depths else None),
        "cycle_latency_s_p50": _p(latencies, 50.0),
        "cycle_latency_s_p95": _p(latencies, 95.0),
        "last_cycle": last_cycle,
    }
