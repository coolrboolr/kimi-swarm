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
import time
import uuid
from pathlib import Path
from typing import Any

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

from .config import AmbientConfig
from .workspace import Workspace
from .kimi_client import KimiClient
from .types import AmbientEvent, Proposal
from .salvaged.telemetry import log_event
from .agents import (
    SecurityGuardian,
    RefactorArchitect,
    StyleEnforcer,
    PerformanceOptimizer,
    TestEnhancer,
)
from .risk import assess_risk, sort_by_risk_priority
from .approval import ApprovalHandler, AlwaysRejectHandler


class AmbientEventHandler(FileSystemEventHandler):
    """File system event handler that enqueues changes."""

    def __init__(
        self,
        event_queue: asyncio.Queue[AmbientEvent],
        debounce_seconds: int = 5,
    ):
        self.event_queue = event_queue
        self.debounce_seconds = debounce_seconds
        self._last_event_time = 0.0

    def on_any_event(self, event: FileSystemEvent) -> None:
        """Handle any filesystem event."""
        # Ignore directory events and non-modify events
        if event.is_directory:
            return

        # Simple debouncing
        current_time = time.time()
        if current_time - self._last_event_time < self.debounce_seconds:
            return

        self._last_event_time = current_time

        # Create ambient event
        ambient_event = AmbientEvent(
            type="file_change",
            data={
                "event_type": event.event_type,
                "src_path": event.src_path,
                "timestamp": current_time,
            },
            task_spec={
                "goal": "Continuous code quality monitoring",
                "trigger": "file_change",
            },
        )

        # Enqueue in thread-safe manner
        asyncio.run_coroutine_threadsafe(
            self.event_queue.put(ambient_event), asyncio.get_event_loop()
        )


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
        self.event_queue: asyncio.Queue[AmbientEvent] = asyncio.Queue()
        self.write_lock = asyncio.Lock()
        self.workspace = Workspace(
            self.repo_path,
            self.config.sandbox.image,
            self.config.sandbox.resources.memory,
            self.config.sandbox.resources.cpus,
            self.config.sandbox.resources.pids_limit,
        )
        self.kimi_client = KimiClient(self.config.kimi)
        self.agents: list[Any] = []
        self._running = False

        # Initialize approval handler
        if approval_handler is None:
            self.approval_handler = ApprovalHandler(
                self.config.risk_policy, interactive=True
            )
        else:
            self.approval_handler = approval_handler

    def _init_agents(self) -> None:
        """Initialize specialist agents based on config."""
        self.agents = []

        enabled_agents = self.config.agents.enabled

        # Map agent names to classes
        agent_classes = {
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
                agent = agent_class(self.config.kimi)
                self.agents.append(agent)

    async def start(self) -> None:
        """Start ambient monitoring loop."""
        self._running = True
        self._init_agents()

        # Start filesystem watcher
        observer = Observer()
        event_handler = AmbientEventHandler(
            self.event_queue,
            self.config.monitoring.debounce_seconds,
        )

        for watch_path in self.config.monitoring.watch_paths:
            full_path = self.repo_path / watch_path
            if full_path.exists():
                observer.schedule(event_handler, str(full_path), recursive=True)

        observer.start()

        try:
            # Main event loop
            while self._running:
                try:
                    # Wait for event with timeout to allow clean shutdown
                    event = await asyncio.wait_for(
                        self.event_queue.get(), timeout=1.0
                    )
                    await self._handle_event(event)
                except asyncio.TimeoutError:
                    continue
        finally:
            observer.stop()
            observer.join()

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
        # Resolve telemetry path relative to repo_path
        telemetry_path = self.repo_path / self.config.telemetry.log_path

        # Log cycle start
        log_event(
            run_id,
            "cycle_started",
            {
                "event_type": event.type,
                "event_data": event.data,
            },
            telemetry_path,
        )

        try:
            # 1. Build full context
            context = await self.workspace.build_context(event)

            # 2. Spawn swarm in parallel
            proposals = await self._generate_proposals(context, run_id, telemetry_path)

            if not proposals:
                log_event(
                    run_id,
                    "cycle_completed",
                    {"status": "no_proposals", "proposals_count": 0},
                    telemetry_path,
                )
                return {
                    "run_id": run_id,
                    "status": "no_proposals",
                    "proposals": [],
                    "applied": [],
                }

            # 3. Cross-pollination (agents refine based on each other's work)
            refined = await self._cross_pollinate(proposals, context, run_id, telemetry_path)

            # 4. Risk-based sorting
            sorted_proposals = sort_by_risk_priority(refined)

            # 5-7. Apply patches serially with gates and verification
            # Check if we're in dry-run mode (AlwaysRejectHandler)
            dry_run = isinstance(self.approval_handler, AlwaysRejectHandler)
            results = await self._apply_proposals(
                sorted_proposals, run_id, telemetry_path, dry_run
            )

            log_event(
                run_id,
                "cycle_completed",
                {
                    "status": "success",
                    "proposals_count": len(proposals),
                    "applied_count": len(results["applied"]),
                    "failed_count": len(results["failed"]),
                },
                telemetry_path,
            )

            return {
                "run_id": run_id,
                "status": "success",
                "proposals": proposals,
                "refined": refined,
                **results,
            }

        except Exception as e:
            log_event(
                run_id,
                "cycle_completed",
                {"status": "error", "error": str(e)},
                telemetry_path,
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
        telemetry_path: Path,
    ) -> list[Proposal]:
        """
        Generate proposals from all agents in parallel.

        Args:
            context: Repository context
            run_id: Unique run identifier
            telemetry_path: Path to telemetry log

        Returns:
            List of proposals from all agents
        """
        if not self.agents:
            # No agents configured
            return []

        # Run all agents in parallel
        proposal_lists = await asyncio.gather(*[
            agent.propose(context)
            for agent in self.agents
        ], return_exceptions=True)

        # Flatten and log
        proposals = []
        for i, result in enumerate(proposal_lists):
            if isinstance(result, Exception):
                agent_name = self.agents[i].__class__.__name__
                log_event(
                    run_id,
                    "agent_error",
                    {"agent": agent_name, "error": str(result)},
                    telemetry_path,
                )
            elif result:
                proposals.extend(result)
                for proposal in result:
                    log_event(
                        run_id,
                        "proposal",
                        {
                            "agent": proposal.agent,
                            "title": proposal.title,
                            "risk_level": proposal.risk_level,
                            "files_touched": proposal.files_touched,
                            "estimated_loc_change": proposal.estimated_loc_change,
                        },
                        telemetry_path,
                    )

        return proposals

    async def _cross_pollinate(
        self,
        proposals: list[Proposal],
        context: Any,
        run_id: str,
        telemetry_path: Path,
    ) -> list[Proposal]:
        """
        Cross-pollination: agents refine proposals after seeing each other's work.

        This enables coordination (e.g., SecurityGuardian sees RefactorArchitect
        is moving code, so doesn't flag that file as "complex").

        Args:
            proposals: Initial proposals from all agents
            context: Repository context
            run_id: Unique run identifier
            telemetry_path: Path to telemetry log

        Returns:
            Refined list of proposals
        """
        if not self.agents:
            return proposals

        # Run refinement in parallel
        refined_lists = await asyncio.gather(*[
            agent.refine(proposals, context)
            for agent in self.agents
        ], return_exceptions=True)

        # Flatten
        refined = []
        for result in refined_lists:
            if isinstance(result, list):
                refined.extend(result)

        log_event(
            run_id,
            "cross_pollination",
            {
                "original_count": len(proposals),
                "refined_count": len(refined),
            },
            telemetry_path,
        )

        return refined if refined else proposals

    async def _apply_proposals(
        self,
        proposals: list[Proposal],
        run_id: str,
        telemetry_path: Path,
        dry_run: bool = False,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Apply proposals serially with risk gates and verification.

        Args:
            proposals: Sorted list of proposals
            run_id: Unique run identifier
            telemetry_path: Path to telemetry log
            dry_run: If True, skip all applications (only show proposals)

        Returns:
            Dict with "applied" and "failed" lists
        """
        applied = []
        failed = []

        # In dry-run mode, mark all proposals as rejected without applying
        if dry_run:
            for proposal in proposals:
                log_event(
                    run_id,
                    "dry_run_skip",
                    {"proposal_title": proposal.title},
                    telemetry_path,
                )
                failed.append(
                    {
                        "proposal": proposal,
                        "reason": "dry_run",
                        "details": "Skipped in dry-run mode",
                    }
                )
            return {"applied": applied, "failed": failed}

        for proposal in proposals:
            # Risk assessment
            risk_assessment = assess_risk(proposal, self.config.risk_policy, self.repo_path)

            # Check if approval required
            if risk_assessment["requires_approval"]:
                log_event(
                    run_id,
                    "risk_gate_triggered",
                    {
                        "proposal_title": proposal.title,
                        "risk_level": proposal.risk_level,
                        "risk_factors": risk_assessment["risk_factors"],
                        "risk_score": risk_assessment["risk_score"],
                    },
                    telemetry_path,
                )

                # Request approval
                approved = await self.approval_handler.request_approval(
                    proposal, risk_assessment
                )

                if not approved:
                    log_event(
                        run_id,
                        "approval_rejected",
                        {"proposal_title": proposal.title},
                        telemetry_path,
                    )
                    failed.append(
                        {
                            "proposal": proposal,
                            "reason": "approval_rejected",
                            "details": "User rejected the proposal",
                        }
                    )
                    continue

                log_event(
                    run_id,
                    "approval_granted",
                    {"proposal_title": proposal.title},
                    telemetry_path,
                )

            # Apply atomically (single-writer)
            async with self.write_lock:
                result = await self.workspace.apply_patch(proposal)

                if not result.ok:
                    log_event(
                        run_id,
                        "apply_failed",
                        {
                            "proposal_title": proposal.title,
                            "stderr": result.stderr,
                        },
                        telemetry_path,
                    )
                    failed.append(
                        {
                            "proposal": proposal,
                            "reason": "patch_failed",
                            "details": result.stderr,
                        }
                    )
                    continue

                # Verify with sandbox checks
                verify_result = await self.workspace.verify_changes()

                if not verify_result.ok:
                    # Rollback
                    await self.workspace.rollback()
                    log_event(
                        run_id,
                        "verify_failed",
                        {
                            "proposal_title": proposal.title,
                            "results": verify_result.results,
                        },
                        telemetry_path,
                    )
                    failed.append(
                        {
                            "proposal": proposal,
                            "reason": "verification_failed",
                            "details": verify_result.results,
                        }
                    )
                    continue

                # Success!
                log_event(
                    run_id,
                    "apply_success",
                    {
                        "proposal_title": proposal.title,
                        "stat": result.stat,
                        "verification_duration": verify_result.duration_s,
                    },
                    telemetry_path,
                )
                applied.append(
                    {
                        "proposal": proposal,
                        "stat": result.stat,
                        "verification": verify_result,
                    }
                )

        return {"applied": applied, "failed": failed}
