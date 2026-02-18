"""Ambient Coordinator - Main orchestrator for continuous code quality monitoring.

The coordinator manages the full lifecycle:
1. Watch filesystem for changes
2. Enqueue detected events
3. Build full repo context
4. Spawn specialist agents in parallel
5. Aggregate proposals with cross-pollination
6. Apply patches serially with risk gates
7. Verify with sandbox checks
8. Log outcomes and return to watching
"""

from __future__ import annotations

import asyncio
import fnmatch
import signal
import time
import uuid
from collections import deque
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .agents import (
    PerformanceOptimizer,
    RefactorArchitect,
    SecurityGuardian,
    SpecialistAgent,
    StyleEnforcer,
    TestEnhancer,
)
from .approval import AlwaysRejectHandler, ApprovalHandler
from .config import AmbientConfig
from .kimi_client import KimiClient
from .risk import assess_risk, sort_by_risk_priority
from .salvaged.git_ops import git_commit, git_has_staged_changes, git_is_clean
from .salvaged.redaction import redact_text
from .salvaged.telemetry import TelemetrySink, prune_telemetry_file
from .types import AmbientEvent, Proposal
from .workspace import Workspace
from .worktrees import ReviewCandidate, ReviewWorktreeManager


class AmbientEventHandler(FileSystemEventHandler):
    """File system event handler that enqueues changes."""

    def __init__(
        self,
        event_queue: asyncio.Queue[AmbientEvent],
        loop: asyncio.AbstractEventLoop,
        repo_root: Path,
        ignore_patterns: list[str] | None = None,
        telemetry_sink: TelemetrySink | None = None,
        debounce_seconds: int = 5,
    ):
        self.event_queue = event_queue
        self.loop = loop
        self.repo_root = Path(repo_root).resolve()
        self.ignore_patterns = ignore_patterns or []
        self.telemetry_sink = telemetry_sink
        self.debounce_seconds = debounce_seconds
        self._last_event_by_path: dict[str, float] = {}

        # Defense-in-depth ignores so we don't self-trigger or watch secrets.
        self._always_ignore_components = {
            ".git",
            ".ambient",
            ".swarmguard",
            ".swarmguard_artifacts",
            ".pytest_cache",
            "__pycache__",
        }

    def on_any_event(self, event: FileSystemEvent) -> None:
        """Handle any filesystem event."""
        # Ignore directory events and non-modify events
        if event.is_directory:
            return

        # Resolve path relative to repo_root (best-effort).
        try:
            src_abs = Path(str(event.src_path)).resolve()
            rel = str(src_abs.relative_to(self.repo_root))
        except Exception:
            # Ignore events outside repo root or invalid paths.
            return

        # Always ignore certain directories/components.
        parts = Path(rel).parts
        if any(p in self._always_ignore_components for p in parts):
            if self.telemetry_sink:
                self.telemetry_sink.log(
                    "monitor",
                    "event_dropped",
                    {"reason": "always_ignore", "path": rel, "event_type": event.event_type},
                )
            return

        # User-configured ignore patterns (glob-style).
        for pat in self.ignore_patterns:
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(Path(rel).name, pat):
                if self.telemetry_sink:
                    self.telemetry_sink.log(
                        "monitor",
                        "event_dropped",
                        {"reason": "ignore_pattern", "pattern": pat, "path": rel, "event_type": event.event_type},
                    )
                return

        # Simple debouncing
        current_time = time.time()
        last = self._last_event_by_path.get(rel, 0.0)
        if current_time - last < self.debounce_seconds:
            return

        self._last_event_by_path[rel] = current_time

        # Create ambient event
        ambient_event = AmbientEvent(
            type="file_change",
            data={
                "event_type": event.event_type,
                "src_path": str(src_abs),
                "rel_path": rel,
                "timestamp": current_time,
            },
            task_spec={
                "goal": "Continuous code quality monitoring",
                "trigger": "file_change",
            },
        )

        def _put_nowait() -> None:
            try:
                self.event_queue.put_nowait(ambient_event)
                if self.telemetry_sink:
                    self.telemetry_sink.log(
                        "monitor",
                        "event_enqueued",
                        {"path": rel, "event_type": event.event_type},
                    )
            except asyncio.QueueFull:
                if self.telemetry_sink:
                    self.telemetry_sink.log(
                        "monitor",
                        "event_dropped",
                        {"reason": "queue_full", "path": rel, "event_type": event.event_type},
                    )

        # Enqueue in the coordinator loop (thread-safe).
        self.loop.call_soon_threadsafe(_put_nowait)


class AmbientCoordinator:
    """
    Main orchestrator for ambient code quality monitoring.

    Manages the full event loop from detection to application.
    """

    def __init__(
        self,
        repo_path: Path,
        config: AmbientConfig,
        approval_handler: ApprovalHandler | None = None,
    ):
        self.repo_path = Path(repo_path)
        self.config = config
        self.telemetry = TelemetrySink(
            enabled=self.config.telemetry.enabled,
            path=self.repo_path / self.config.telemetry.log_path,
        )
        self.event_queue: asyncio.Queue[AmbientEvent] = asyncio.Queue(
            maxsize=self.config.monitoring.max_queue_size
        )
        self.write_lock = asyncio.Lock()
        self.workspace = Workspace(
            self.repo_path,
            self.config.sandbox.image,
            sandbox_network=self.config.sandbox.network_mode,
            sandbox_memory=self.config.sandbox.resources.memory,
            sandbox_cpus=self.config.sandbox.resources.cpus,
            sandbox_pids_limit=self.config.sandbox.resources.pids_limit,
            sandbox_allowed_argv=self.config.sandbox.allowed_argv,
            sandbox_allowed_commands=self.config.sandbox.allowed_commands,
            sandbox_enforce_allowlist=self.config.sandbox.enforce_allowlist,
            sandbox_require_docker=self.config.sandbox.require_docker,
            sandbox_stub=self.config.sandbox.stub_mode,
            sandbox_repo_mount_mode=self.config.sandbox.repo_mount_mode,
            verification_timeout_seconds=self.config.verification.timeout_seconds,
        )
        self.review_manager = ReviewWorktreeManager(
            repo_path=self.repo_path,
            base_dir=self.repo_path / self.config.review_worktree.base_dir,
            branch_prefix=self.config.review_worktree.branch_prefix,
        )
        self.kimi_client = KimiClient(self.config.kimi)
        self.agents: list[SpecialistAgent] = []
        self._running = False

        # Control-plane state (in-memory, resets on restart).
        self._proposal_timestamps: deque[float] = deque()
        self._apply_outcomes: deque[bool] = deque(
            maxlen=max(1, int(self.config.control_plane.failure_rate_window))
        )
        self._verify_outcomes: deque[bool] = deque(
            maxlen=max(1, int(self.config.control_plane.failure_rate_window))
        )
        self._backoff_seconds: int = 0
        self._backoff_until: float = 0.0

        # Initialize approval handler
        if approval_handler is None:
            self.approval_handler = ApprovalHandler(
                self.config.risk_policy, interactive=True
            )
        else:
            self.approval_handler = approval_handler

        self._periodic_task: asyncio.Task[None] | None = None

    def _workspace_for_path(self, repo_path: Path) -> Workspace:
        """Create a workspace bound to a specific path with current sandbox policy."""
        return Workspace(
            repo_path,
            self.config.sandbox.image,
            sandbox_network=self.config.sandbox.network_mode,
            sandbox_memory=self.config.sandbox.resources.memory,
            sandbox_cpus=self.config.sandbox.resources.cpus,
            sandbox_pids_limit=self.config.sandbox.resources.pids_limit,
            sandbox_allowed_argv=self.config.sandbox.allowed_argv,
            sandbox_allowed_commands=self.config.sandbox.allowed_commands,
            sandbox_enforce_allowlist=self.config.sandbox.enforce_allowlist,
            sandbox_require_docker=self.config.sandbox.require_docker,
            sandbox_stub=self.config.sandbox.stub_mode,
            sandbox_repo_mount_mode=self.config.sandbox.repo_mount_mode,
            verification_timeout_seconds=self.config.verification.timeout_seconds,
        )

    def _init_agents(self) -> None:
        """Initialize specialist agents based on config."""
        self.agents = []

        enabled_agents = self.config.agents.enabled

        # Map agent names to classes
        agent_classes: dict[str, type[Any]] = {
            "SecurityGuardian": SecurityGuardian,
            "RefactorArchitect": RefactorArchitect,
            "StyleEnforcer": StyleEnforcer,
            "PerformanceOptimizer": PerformanceOptimizer,
            "TestEnhancer": TestEnhancer,
        }

        # Instantiate enabled agents
        for agent_name in enabled_agents:
            if agent_name in agent_classes:
                agent_class = agent_classes[agent_name]
                agent = cast(SpecialistAgent, agent_class(self.config.kimi, kimi_client=self.kimi_client))
                self.agents.append(agent)

    async def start(self) -> None:
        """Start ambient monitoring loop."""
        self._running = True
        self._init_agents()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: setattr(self, "_running", False))
            except (NotImplementedError, RuntimeError):
                # Not supported on some platforms / event loops.
                pass

        # Start filesystem watcher
        observer: Any | None = None
        if self.telemetry.enabled:
            prune_telemetry_file(self.telemetry.path, self.config.telemetry.retention_days)

        if self.config.monitoring.enabled:
            observer = Observer()
            event_handler = AmbientEventHandler(
                self.event_queue,
                loop=loop,
                repo_root=self.repo_path,
                ignore_patterns=self.config.monitoring.ignore_patterns,
                telemetry_sink=self.telemetry if self.telemetry.enabled else None,
                debounce_seconds=self.config.monitoring.debounce_seconds,
            )

            for watch_path in self.config.monitoring.watch_paths:
                full_path = self.repo_path / watch_path
                if full_path.exists():
                    observer.schedule(event_handler, str(full_path), recursive=True)

            observer.start()

            # Periodic scan loop
            self._periodic_task = asyncio.create_task(self._periodic_scan_loop())

        try:
            # Main event loop
            while self._running:
                now = time.time()
                if now < self._backoff_until:
                    await asyncio.sleep(min(1.0, self._backoff_until - now))
                    continue
                try:
                    # Wait for event with timeout to allow clean shutdown
                    event = await asyncio.wait_for(
                        self.event_queue.get(), timeout=1.0
                    )
                    await self._handle_event(event)
                except TimeoutError:
                    continue
        finally:
            if self._periodic_task:
                self._periodic_task.cancel()
                try:
                    await self._periodic_task
                except asyncio.CancelledError:
                    pass

            if observer is not None:
                observer.stop()
                observer.join()

    async def _periodic_scan_loop(self) -> None:
        """Enqueue periodic_scan events on an interval while running."""
        interval = max(0.1, float(self.config.monitoring.check_interval_seconds))
        while self._running:
            await asyncio.sleep(interval)
            ev = AmbientEvent(
                type="periodic_scan",
                data={"timestamp": time.time(), "trigger": "timer"},
                task_spec={"goal": "Periodic quality scan", "trigger": "periodic"},
            )
            try:
                self.event_queue.put_nowait(ev)
            except asyncio.QueueFull:
                # Drop silently; queue_full is already handled for file events and
                # will be visible via stalled cycles.
                pass

    async def stop(self) -> None:
        """Stop ambient monitoring."""
        self._running = False

    async def run_once(self, event: AmbientEvent | None = None) -> dict[str, Any]:
        """
        Run a single cycle without starting the watcher.

        Args:
            event: Optional event to process (default: periodic scan)

        Returns:
            Dict with cycle results
        """
        if event is None:
            event = AmbientEvent(
                type="periodic_scan",
                data={"timestamp": time.time()},
                task_spec={"goal": "Periodic quality scan", "trigger": "manual"},
            )

        self._init_agents()
        return await self._handle_event(event)

    async def _handle_event(self, event: AmbientEvent) -> dict[str, Any]:
        """
        Process a detected event through the full pipeline.

        Returns:
            Dict with cycle results (proposals, applications, verifications)
        """
        run_id = str(uuid.uuid4())[:8]

        # Log cycle start
        self.telemetry.log(
            run_id,
            "cycle_started",
            {"event_type": event.type, "event_data": event.data, "queue_depth": self.event_queue.qsize()},
        )

        try:
            if self.config.control_plane.paused:
                self.telemetry.log(run_id, "cycle_completed", {"status": "paused"})
                return {"run_id": run_id, "status": "paused"}

            # 1. Build full context
            context = await self.workspace.build_context(event)

            # 2. Spawn swarm in parallel
            proposals = await self._generate_proposals(context, run_id)

            if not proposals:
                self.telemetry.log(
                    run_id,
                    "cycle_completed",
                    {"status": "no_proposals", "proposals_count": 0},
                )
                return {
                    "run_id": run_id,
                    "status": "no_proposals",
                    "proposals": [],
                    "applied": [],
                }

            # Control-plane throttling: max proposals per hour.
            max_ph = int(self.config.control_plane.max_proposals_per_hour)
            if max_ph > 0:
                now = time.time()
                for _ in proposals:
                    self._proposal_timestamps.append(now)
                cutoff = now - 3600.0
                while self._proposal_timestamps and self._proposal_timestamps[0] < cutoff:
                    self._proposal_timestamps.popleft()
                if len(self._proposal_timestamps) > max_ph:
                    self.telemetry.log(
                        run_id,
                        "control_plane_throttled",
                        {"max_proposals_per_hour": max_ph, "current_window": len(self._proposal_timestamps)},
                    )
                    return {
                        "run_id": run_id,
                        "status": "throttled",
                        "proposals": proposals,
                        "applied": [],
                        "failed": [{"proposal": p, "reason": "throttled"} for p in proposals],
                    }

            # 3. Cross-pollination (agents refine based on each other's work)
            refined = await self._cross_pollinate(proposals, context, run_id)

            # 4. Risk-based sorting
            sorted_proposals = sort_by_risk_priority(refined)

            # 5-7. Apply patches serially with gates and verification
            # Check if we're in dry-run mode (AlwaysRejectHandler)
            dry_run = isinstance(self.approval_handler, AlwaysRejectHandler)
            results = await self._apply_proposals(
                sorted_proposals, run_id, dry_run
            )

            self.telemetry.log(
                run_id,
                "cycle_completed",
                {
                    "status": "success",
                    "proposals_count": len(proposals),
                    "applied_count": len(results["applied"]),
                    "failed_count": len(results["failed"]),
                },
            )

            return {
                "run_id": run_id,
                "status": "success",
                "proposals": proposals,
                "refined": refined,
                **results,
            }

        except Exception as e:
            self._backoff_seconds = min(
                int(self.config.control_plane.backoff_max_seconds),
                max(int(self.config.control_plane.backoff_base_seconds), self._backoff_seconds * 2 or 0),
            )
            if self._backoff_seconds:
                self._backoff_until = time.time() + self._backoff_seconds
            self.telemetry.log(
                run_id,
                "cycle_completed",
                {"status": "error", "error": redact_text(str(e), max_len=200)},
            )
            return {
                "run_id": run_id,
                "status": "error",
                "error": str(e),
            }

    async def _generate_proposals(
        self,
        context: Any,
        run_id: str,
    ) -> list[Proposal]:
        """
        Generate proposals from all agents in parallel.

        Args:
            context: Repository context
            run_id: Unique run identifier

        Returns:
            List of proposals from all agents
        """
        if not self.agents:
            # No agents configured
            return []

        # Run all agents in parallel
        proposal_lists: list[list[Proposal] | BaseException] = await asyncio.gather(
            *[agent.propose(context) for agent in self.agents], return_exceptions=True
        )

        # Flatten and log
        proposals: list[Proposal] = []
        for i, result in enumerate(proposal_lists):
            if isinstance(result, BaseException):
                agent_name = self.agents[i].__class__.__name__
                self.telemetry.log(
                    run_id,
                    "agent_error",
                    {"agent": agent_name, "error": str(result)},
                )
            elif result:
                proposals.extend(result)
                for proposal in result:
                    data: dict[str, Any] = {
                        "agent": proposal.agent,
                        "title": proposal.title,
                        "risk_level": proposal.risk_level,
                        "files_touched": proposal.files_touched,
                        "estimated_loc_change": proposal.estimated_loc_change,
                    }
                    if self.config.telemetry.include_diffs:
                        diff = proposal.diff or ""
                        data["diff_sha256"] = sha256(diff.encode("utf-8", errors="replace")).hexdigest()
                        data["diff_len"] = len(diff)
                        data["diff_excerpt"] = redact_text(diff, max_len=2000)
                    self.telemetry.log(
                        run_id,
                        "proposal",
                        data,
                    )

        return proposals

    async def _cross_pollinate(
        self,
        proposals: list[Proposal],
        context: Any,
        run_id: str,
    ) -> list[Proposal]:
        """
        Cross-pollination: agents refine proposals after seeing each other's work.

        This enables coordination (e.g., SecurityGuardian sees RefactorArchitect
        is moving code, so doesn't flag that file as "complex").

        Args:
            proposals: Initial proposals from all agents
            context: Repository context
            run_id: Unique run identifier

        Returns:
            Refined list of proposals
        """
        if not self.agents:
            return proposals

        # Run refinement in parallel
        refined_lists: list[list[Proposal] | BaseException] = await asyncio.gather(
            *[agent.refine(proposals, context) for agent in self.agents],
            return_exceptions=True,
        )

        # Flatten
        refined: list[Proposal] = []
        for result in refined_lists:
            if isinstance(result, list):
                refined.extend(result)

        self.telemetry.log(
            run_id,
            "cross_pollination",
            {"original_count": len(proposals), "refined_count": len(refined)},
        )

        return refined if refined else proposals

    async def _apply_proposals(
        self,
        proposals: list[Proposal],
        run_id: str,
        dry_run: bool = False,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Apply proposals serially with risk gates and verification.

        Args:
            proposals: Sorted list of proposals
            run_id: Unique run identifier
            dry_run: If True, skip all applications (only show proposals)

        Returns:
            Dict with "applied" and "failed" lists
        """
        applied: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        # Kill-switch: disable auto-apply if failure rate exceeds threshold.
        if self.config.control_plane.disable_auto_apply_on_failure_rate:
            threshold = float(self.config.control_plane.failure_rate_threshold)
            min_failures = int(self.config.control_plane.min_failures_before_disable)
            recent = list(self._verify_outcomes) + list(self._apply_outcomes)
            failures = sum(1 for ok in recent if not ok)
            if failures >= min_failures and recent:
                rate = failures / len(recent)
                if rate > threshold:
                    self.telemetry.log(
                        run_id,
                        "control_plane_auto_apply_disabled",
                        {"failure_rate": rate, "threshold": threshold, "window": len(recent)},
                    )
                    for proposal in proposals:
                        failed.append(
                            {
                                "proposal": proposal,
                                "reason": "auto_apply_disabled",
                                "details": "Auto-apply disabled due to elevated failure rate",
                            }
                        )
                    return {"applied": applied, "failed": failed}

        # In dry-run mode, mark all proposals as rejected without applying
        if dry_run:
            for proposal in proposals:
                self.telemetry.log(
                    run_id,
                    "dry_run_skip",
                    {"proposal_title": proposal.title},
                )
                failed.append(
                    {
                        "proposal": proposal,
                        "reason": "dry_run",
                        "details": "Skipped in dry-run mode",
                    }
                )
            return {"applied": applied, "failed": failed}

        if self.config.review_worktree.enabled:
            return await self._apply_proposals_review_worktrees(proposals, run_id)

        for proposal in proposals:
            if self.config.git.require_clean_before_apply:
                try:
                    if not git_is_clean(self.repo_path):
                        self.telemetry.log(
                            run_id,
                            "git_dirty_worktree",
                            {"proposal_title": proposal.title},
                        )
                        failed.append(
                            {
                                "proposal": proposal,
                                "reason": "dirty_worktree",
                                "details": "Repository has uncommitted changes",
                            }
                        )
                        continue
                except Exception as e:
                    failed.append(
                        {
                            "proposal": proposal,
                            "reason": "git_status_failed",
                            "details": str(e),
                        }
                    )
                    continue

            # Risk assessment
            risk_assessment = assess_risk(proposal, self.config.risk_policy, self.repo_path)

            # Check if approval required
            if risk_assessment["requires_approval"]:
                self.telemetry.log(
                    run_id,
                    "risk_gate_triggered",
                    {
                        "proposal_title": proposal.title,
                        "risk_level": proposal.risk_level,
                        "risk_factors": risk_assessment["risk_factors"],
                        "risk_score": risk_assessment["risk_score"],
                    },
                )

                # Request approval
                assessment_payload = dict(risk_assessment)
                assessment_payload["run_id"] = run_id
                approved = await self.approval_handler.request_approval(
                    proposal, assessment_payload
                )

                if not approved:
                    self.telemetry.log(
                        run_id,
                        "approval_rejected",
                        {"proposal_title": proposal.title},
                    )
                    failed.append(
                        {
                            "proposal": proposal,
                            "reason": "approval_rejected",
                            "details": "User rejected the proposal",
                        }
                    )
                    continue

                self.telemetry.log(
                    run_id,
                    "approval_granted",
                    {"proposal_title": proposal.title},
                )

            # Apply atomically (single-writer)
            async with self.write_lock:
                result = await self.workspace.apply_patch(proposal)

                if not result.ok:
                    self._apply_outcomes.append(False)
                    self._backoff_seconds = min(
                        int(self.config.control_plane.backoff_max_seconds),
                        max(int(self.config.control_plane.backoff_base_seconds), self._backoff_seconds * 2 or 0),
                    )
                    if self._backoff_seconds:
                        self._backoff_until = time.time() + self._backoff_seconds
                    self.telemetry.log(
                        run_id,
                        "apply_failed",
                        {
                            "proposal_title": proposal.title,
                            "stderr_head": redact_text(result.stderr, max_len=200),
                        },
                    )
                    failed.append(
                        {
                            "proposal": proposal,
                            "reason": "patch_failed",
                            "details": result.stderr,
                        }
                    )
                    continue

                self._apply_outcomes.append(True)
                self.telemetry.log(
                    run_id,
                    "apply_succeeded",
                    {"proposal_title": proposal.title, "risk_level": proposal.risk_level},
                )

                # Verify with sandbox checks
                verify_result = await self.workspace.verify_changes()

                if not verify_result.ok:
                    # Rollback
                    await self.workspace.rollback()
                    self._verify_outcomes.append(False)
                    self._backoff_seconds = min(
                        int(self.config.control_plane.backoff_max_seconds),
                        max(int(self.config.control_plane.backoff_base_seconds), self._backoff_seconds * 2 or 0),
                    )
                    if self._backoff_seconds:
                        self._backoff_until = time.time() + self._backoff_seconds
                    self.telemetry.log(
                        run_id,
                        "verify_failed",
                        {
                            "proposal_title": proposal.title,
                            "results": [
                                {
                                    "name": r.get("name"),
                                    "ok": r.get("ok"),
                                    "exit_code": r.get("exit_code"),
                                    "duration_s": r.get("duration_s"),
                                    "message": redact_text((r.get("stderr") or r.get("stdout") or ""), max_len=200)
                                    if not r.get("ok")
                                    else "",
                                }
                                for r in verify_result.results
                            ],
                        },
                    )
                    failed.append(
                        {
                            "proposal": proposal,
                            "reason": "verification_failed",
                            "details": verify_result.results,
                        }
                    )
                    continue

                self._verify_outcomes.append(True)
                self.telemetry.log(
                    run_id,
                    "verify_succeeded",
                    {"proposal_title": proposal.title},
                )

                # Commit (optional)
                if self.config.git.commit_on_success:
                    try:
                        if git_has_staged_changes(self.repo_path):
                            try:
                                subject = self.config.git.commit_message_template.format(
                                    title=proposal.title, agent=proposal.agent
                                )
                            except Exception:
                                subject = f"ambient: {proposal.title} ({proposal.agent})"

                            body_lines = [
                                f"run_id: {run_id}",
                                f"risk_level: {proposal.risk_level}",
                                f"tags: {', '.join(proposal.tags) if proposal.tags else ''}",
                                "files_touched:",
                                *[f"- {p}" for p in proposal.files_touched],
                            ]
                            message = subject + "\n\n" + "\n".join(body_lines) + "\n"

                            self.telemetry.log(
                                run_id,
                                "git_commit_started",
                                {"proposal_title": proposal.title, "subject": subject},
                            )
                            git_commit(
                                self.repo_path,
                                message,
                                author_name=self.config.git.commit_author_name,
                                author_email=self.config.git.commit_author_email,
                            )
                            self.telemetry.log(
                                run_id,
                                "git_commit_succeeded",
                                {"proposal_title": proposal.title, "subject": subject},
                            )
                            self._backoff_seconds = 0
                            self._backoff_until = 0.0
                    except Exception as e:
                        # Commit failure: rollback to avoid leaving partial/staged state.
                        await self.workspace.rollback()
                        self.telemetry.log(
                            run_id,
                            "git_commit_failed",
                            {"proposal_title": proposal.title, "error": redact_text(str(e), max_len=200)},
                        )
                        failed.append(
                            {
                                "proposal": proposal,
                                "reason": "git_commit_failed",
                                "details": str(e),
                            }
                        )
                        continue

                # Success!
                self.telemetry.log(
                    run_id,
                    "apply_success",
                    {
                        "proposal_title": proposal.title,
                        "stat": result.stat,
                        "verification_duration": verify_result.duration_s,
                    },
                )
                applied.append(
                    {
                        "proposal": proposal,
                        "stat": result.stat,
                        "verification": verify_result,
                    }
                )

        return {"applied": applied, "failed": failed}

    async def _apply_proposals_review_worktrees(
        self,
        proposals: list[Proposal],
        run_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """Apply proposals in dedicated review worktrees and emit per-proposal diffs."""
        applied: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        queue: list[tuple[Proposal, ReviewCandidate]] = []
        for idx, proposal in enumerate(proposals, start=1):
            risk_assessment = assess_risk(proposal, self.config.risk_policy, self.repo_path)
            if risk_assessment["requires_approval"]:
                self.telemetry.log(
                    run_id,
                    "risk_gate_triggered",
                    {
                        "proposal_title": proposal.title,
                        "risk_level": proposal.risk_level,
                        "risk_factors": risk_assessment["risk_factors"],
                        "risk_score": risk_assessment["risk_score"],
                    },
                )
                assessment_payload = dict(risk_assessment)
                assessment_payload["run_id"] = run_id
                approved = await self.approval_handler.request_approval(
                    proposal, assessment_payload
                )
                if not approved:
                    failed.append(
                        {
                            "proposal": proposal,
                            "reason": "approval_rejected",
                            "details": "User rejected the proposal",
                        }
                    )
                    continue

            try:
                candidate = self.review_manager.create_candidate(run_id, idx, proposal.title)
            except Exception as e:
                failed.append(
                    {
                        "proposal": proposal,
                        "reason": "review_worktree_failed",
                        "details": str(e),
                    }
                )
                continue

            queue.append((proposal, candidate))

        if not queue:
            return {"applied": applied, "failed": failed}

        max_parallel = max(1, int(self.config.review_worktree.max_parallel))
        semaphore = asyncio.Semaphore(max_parallel)

        async def _worker(
            proposal: Proposal, candidate: ReviewCandidate
        ) -> tuple[str, dict[str, Any]]:
            async with semaphore:
                workspace = self._workspace_for_path(candidate.worktree_path)
                result = await workspace.apply_patch(proposal)
                if not result.ok:
                    self._apply_outcomes.append(False)
                    return (
                        "failed",
                        {
                            "proposal": proposal,
                            "reason": "patch_failed",
                            "details": result.stderr,
                            "review_branch": candidate.branch,
                            "review_worktree": str(candidate.worktree_path),
                        },
                    )

                self._apply_outcomes.append(True)
                verify_result = await workspace.verify_changes()
                if not verify_result.ok:
                    await workspace.rollback()
                    self._verify_outcomes.append(False)
                    return (
                        "failed",
                        {
                            "proposal": proposal,
                            "reason": "verification_failed",
                            "details": verify_result.results,
                            "review_branch": candidate.branch,
                            "review_worktree": str(candidate.worktree_path),
                        },
                    )

                self._verify_outcomes.append(True)
                patch_text = await workspace.get_staged_diff()
                candidate.patch_path.parent.mkdir(parents=True, exist_ok=True)
                candidate.patch_path.write_text(patch_text, encoding="utf-8")

                if self.config.git.commit_on_success:
                    try:
                        if git_has_staged_changes(candidate.worktree_path):
                            try:
                                subject = self.config.git.commit_message_template.format(
                                    title=proposal.title, agent=proposal.agent
                                )
                            except Exception:
                                subject = f"ambient: {proposal.title} ({proposal.agent})"
                            message = (
                                subject
                                + "\n\n"
                                + f"run_id: {run_id}\n"
                                + f"risk_level: {proposal.risk_level}\n"
                            )
                            git_commit(
                                candidate.worktree_path,
                                message,
                                author_name=self.config.git.commit_author_name,
                                author_email=self.config.git.commit_author_email,
                            )
                    except Exception as e:
                        return (
                            "failed",
                            {
                                "proposal": proposal,
                                "reason": "git_commit_failed",
                                "details": str(e),
                                "review_branch": candidate.branch,
                                "review_worktree": str(candidate.worktree_path),
                                "patch_path": str(candidate.patch_path),
                            },
                        )

                return (
                    "applied",
                    {
                        "proposal": proposal,
                        "stat": result.stat,
                        "verification": verify_result,
                        "review_branch": candidate.branch,
                        "review_worktree": str(candidate.worktree_path),
                        "patch_path": str(candidate.patch_path),
                    },
                )

        worker_results = await asyncio.gather(
            *[_worker(p, c) for p, c in queue],
            return_exceptions=True,
        )

        for i, item in enumerate(worker_results):
            proposal, candidate = queue[i]
            if isinstance(item, BaseException):
                failed.append(
                    {
                        "proposal": proposal,
                        "reason": "review_processing_failed",
                        "details": str(item),
                        "review_branch": candidate.branch,
                        "review_worktree": str(candidate.worktree_path),
                    }
                )
                if not self.config.review_worktree.keep_worktrees:
                    self.review_manager.remove_candidate(candidate)
                continue

            kind, payload = item
            if kind == "applied":
                applied.append(payload)
            else:
                failed.append(payload)
                self.telemetry.log(
                    run_id,
                    "review_candidate_failed",
                    {
                        "proposal_title": proposal.title,
                        "reason": payload.get("reason", "unknown"),
                        "review_branch": payload.get("review_branch"),
                    },
                )

            if not self.config.review_worktree.keep_worktrees:
                self.review_manager.remove_candidate(candidate)

        self.telemetry.log(
            run_id,
            "review_worktree_batch_completed",
            {
                "applied_count": len(applied),
                "failed_count": len(failed),
                "max_parallel": max_parallel,
                "keep_worktrees": self.config.review_worktree.keep_worktrees,
            },
        )

        return {"applied": applied, "failed": failed}
