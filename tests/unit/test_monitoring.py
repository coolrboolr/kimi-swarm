"""Unit tests for monitoring/event ingestion primitives."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

from ambient.config import AmbientConfig
from ambient.coordinator import AmbientCoordinator, AmbientEventHandler


@pytest.mark.asyncio
async def test_event_handler_enqueues_file_change(tmp_path: Path):
    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    loop = asyncio.get_running_loop()

    handler = AmbientEventHandler(
        queue,
        loop=loop,
        repo_root=tmp_path,
        ignore_patterns=[],
        telemetry_sink=None,
        debounce_seconds=0,
    )

    p = tmp_path / "foo.py"
    p.write_text("print('x')\n")

    handler.on_any_event(FileModifiedEvent(str(p)))

    ev = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert ev.type == "file_change"
    assert ev.data["rel_path"] == "foo.py"


@pytest.mark.asyncio
async def test_event_handler_ignores_forbidden_components(tmp_path: Path):
    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    loop = asyncio.get_running_loop()

    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    p = git_dir / "config"
    p.write_text("ignored\n")

    handler = AmbientEventHandler(
        queue,
        loop=loop,
        repo_root=tmp_path,
        ignore_patterns=[],
        telemetry_sink=None,
        debounce_seconds=0,
    )

    handler.on_any_event(FileModifiedEvent(str(p)))
    await asyncio.sleep(0.05)
    assert queue.empty()


@pytest.mark.asyncio
async def test_event_handler_debounces_by_path(tmp_path: Path):
    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    loop = asyncio.get_running_loop()

    p = tmp_path / "bar.py"
    p.write_text("x\n")

    handler = AmbientEventHandler(
        queue,
        loop=loop,
        repo_root=tmp_path,
        ignore_patterns=[],
        telemetry_sink=None,
        debounce_seconds=5,
    )

    handler.on_any_event(FileModifiedEvent(str(p)))
    handler.on_any_event(FileModifiedEvent(str(p)))

    ev = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert ev.data["rel_path"] == "bar.py"

    await asyncio.sleep(0.05)
    assert queue.empty()


@pytest.mark.asyncio
async def test_periodic_scan_loop_enqueues(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    config = AmbientConfig()
    config.monitoring.enabled = True
    config.monitoring.check_interval_seconds = 0  # coerced to 0.1s in loop
    config.telemetry.enabled = False

    coord = AmbientCoordinator(repo, config)
    coord._running = True

    task = asyncio.create_task(coord._periodic_scan_loop())
    try:
        ev = await asyncio.wait_for(coord.event_queue.get(), timeout=1.0)
        assert ev.type == "periodic_scan"
    finally:
        coord._running = False
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
