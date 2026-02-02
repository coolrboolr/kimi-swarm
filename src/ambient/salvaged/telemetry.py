"""Telemetry logging for ambient system.

Simplified version without Ray dependencies - writes directly to JSONL files.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

DEFAULT_TELEMETRY_PATH = ".ambient/telemetry.jsonl"


def log_event(
    run_id: str,
    event_type: str,
    data: dict[str, Any],
    telemetry_path: Path | str | None = None
) -> None:
    """
    Append event to JSONL telemetry log.

    Args:
        run_id: Unique identifier for this run/cycle
        event_type: Type of event (e.g., "cycle_started", "proposal", "apply_result")
        data: Event-specific data
        telemetry_path: Path to telemetry file (default: .ambient/telemetry.jsonl)

    Event types:
        - cycle_started: Run begins
        - proposal: Agent generated patch proposal
        - risk_trigger: Human approval required
        - apply_result: Patch application outcome
        - command_executed: Sandbox command run
        - verification_result: Verification check outcome
        - cycle_completed: Run ends with status
    """
    if telemetry_path is None:
        telemetry_path = DEFAULT_TELEMETRY_PATH

    telemetry_path = Path(telemetry_path)
    telemetry_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": time.time(),
        "run_id": run_id,
        "type": event_type,
        "data": data,
    }

    with open(telemetry_path, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
