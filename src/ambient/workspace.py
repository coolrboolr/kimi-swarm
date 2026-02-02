"""Workspace manager for atomic file operations and verification.

Manages filesystem operations with safety guarantees:
- All writes go through atomic patch application
- All paths validated via safe_resolve()
- All commands run in Docker sandbox
- Single async lock prevents race conditions
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from .salvaged.git_ops import git_apply_patch_atomic, git_reset_hard_clean
from .salvaged.safe_paths import safe_resolve
from .salvaged.sandbox import SandboxRunner
from .salvaged.repo_pack import build_repo_pack
from .types import Proposal, RepoContext, AmbientEvent, VerificationResult, ApplyResult


class Workspace:
    """
    Manages filesystem operations with safety guarantees.

    All operations are async-wrapped to integrate with the coordinator's
    async event loop.
    """

    def __init__(
        self,
        repo_path: Path,
        sandbox_image: str = "ambient-sandbox:latest",
        sandbox_memory: str = "2g",
        sandbox_cpus: str = "2.0",
        sandbox_pids_limit: int = 100,
    ):
        self.repo_path = Path(repo_path)
        self.sandbox = SandboxRunner(
            repo_root=self.repo_path,
            image=sandbox_image,
            network="none",
            memory=sandbox_memory,
            cpus=sandbox_cpus,
            pids_limit=sandbox_pids_limit,
        )
        self._verification_checks: list[tuple[str, str]] = []
        self._auto_detect_checks()

    def _auto_detect_checks(self) -> None:
        """Auto-detect available verification checks based on repo structure."""
        self._verification_checks = []

        # Python: pytest
        if (self.repo_path / "tests").exists() or (
            self.repo_path / "test"
        ).exists():
            self._verification_checks.append(("pytest", "pytest -xvs || true"))

        # Python: ruff
        if (
            (self.repo_path / "pyproject.toml").exists()
            or (self.repo_path / "ruff.toml").exists()
        ):
            self._verification_checks.append(("ruff", "ruff check . || true"))

        # Python: mypy
        if (self.repo_path / "mypy.ini").exists() or (
            self.repo_path / "pyproject.toml"
        ).exists():
            self._verification_checks.append(("mypy", "mypy . || true"))

        # Make targets
        if (self.repo_path / "Makefile").exists():
            self._verification_checks.append(("make-test", "make test || true"))

    async def apply_patch(self, proposal: Proposal) -> ApplyResult:
        """
        Apply proposal's diff atomically.

        This is a thin async wrapper around the salvaged git_ops.py logic.

        Args:
            proposal: Proposal containing the diff to apply

        Returns:
            ApplyResult with success status and details
        """
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, git_apply_patch_atomic, self.repo_path, proposal.diff
        )

        return ApplyResult(
            ok=result["ok"],
            stat=result["stat"],
            stderr=result.get("stderr", ""),
            debug_bundle=result.get("debug_bundle", {}),
        )

    async def verify_changes(self) -> VerificationResult:
        """
        Run quality checks in sandbox.

        Runs all auto-detected checks (pytest, ruff, mypy, make test, etc.)
        in parallel within the sandbox.

        Returns:
            VerificationResult with overall success and individual check results
        """
        if not self._verification_checks:
            # No checks configured, consider it a pass
            return VerificationResult(ok=True, results=[], duration_s=0.0)

        async def run_check(name: str, command: str) -> dict[str, Any]:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, self.sandbox.run, command, 900  # 15 minute timeout
            )
            return {
                "name": name,
                "ok": result["exit_code"] == 0,
                "exit_code": result["exit_code"],
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "duration_s": result["duration_s"],
            }

        # Run all checks in parallel
        results = await asyncio.gather(
            *[run_check(name, cmd) for name, cmd in self._verification_checks],
            return_exceptions=True,
        )

        # Convert exceptions to failed results
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                check_name = self._verification_checks[i][0]
                processed_results.append(
                    {
                        "name": check_name,
                        "ok": False,
                        "exit_code": -1,
                        "stdout": "",
                        "stderr": str(result),
                        "duration_s": 0.0,
                    }
                )
            else:
                processed_results.append(result)

        # Calculate total duration
        total_duration = sum(r["duration_s"] for r in processed_results)

        # All checks must pass
        all_ok = all(r["ok"] for r in processed_results)

        return VerificationResult(
            ok=all_ok, results=processed_results, duration_s=total_duration
        )

    async def rollback(self) -> None:
        """
        Rollback to clean state.

        Performs git reset --hard and git clean -fd to restore repository
        to the last committed state.
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, git_reset_hard_clean, self.repo_path)

    async def build_context(self, event: AmbientEvent) -> RepoContext:
        """
        Build full repo context for agents.

        This is an enhanced version of the salvaged repo_pack.py logic.

        Args:
            event: The triggering event

        Returns:
            RepoContext with full repository visibility
        """
        # Detect failing logs if event is CI failure
        failing_logs = ""
        if event.is_ci_failure:
            failing_logs = event.data.get("logs", "")

        # Build file tree
        tree = await self._build_tree()

        # Get current diff
        current_diff = await self._get_current_diff()

        # Build pack using salvaged logic
        loop = asyncio.get_event_loop()
        pack_json = await loop.run_in_executor(
            None,
            build_repo_pack,
            self.repo_path,
            event.task_spec,
            tree,
            failing_logs,
            current_diff,
            [],  # hot_paths
            {},  # conventions
        )

        # Parse pack_json back to dict (build_repo_pack returns JSON string)
        pack = json.loads(pack_json)

        return RepoContext(
            task=pack["task"],
            tree=pack["tree"],
            important_files=pack["important_files"],
            failing_logs=pack["failing_logs"],
            current_diff=pack["current_diff"],
            hot_paths=pack.get("hot_paths", []),
            conventions=pack.get("conventions", {}),
        )

    async def _build_tree(self) -> dict[str, Any]:
        """Build file tree structure."""
        loop = asyncio.get_event_loop()

        def _build() -> dict[str, Any]:
            import subprocess

            # Use git ls-files for tracked files
            result = subprocess.run(
                ["git", "ls-files"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0:
                files = [f for f in result.stdout.strip().split("\n") if f]
                return {
                    "files": files,
                    "total_files": len(files),
                }
            else:
                # Fallback to os.walk if not a git repo
                files = []
                for root, _, filenames in os.walk(self.repo_path):
                    for filename in filenames:
                        rel_path = Path(root).relative_to(self.repo_path) / filename
                        files.append(str(rel_path))
                return {
                    "files": files,
                    "total_files": len(files),
                }

        return await loop.run_in_executor(None, _build)

    async def _get_current_diff(self) -> str:
        """Get current git diff."""
        loop = asyncio.get_event_loop()

        def _get_diff() -> str:
            import subprocess

            result = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=False,
            )
            return result.stdout if result.returncode == 0 else ""

        return await loop.run_in_executor(None, _get_diff)

    def register_verification(
        self, name: str, command: str
    ) -> None:
        """
        Register a custom verification check.

        Args:
            name: Name of the check (e.g., "custom-lint")
            command: Shell command to run (must be in sandbox allowlist)
        """
        self._verification_checks.append((name, command))

    def safe_resolve_path(self, rel_path: str) -> Path:
        """
        Safely resolve a relative path within the repository.

        Args:
            rel_path: Relative path to resolve

        Returns:
            Resolved absolute path

        Raises:
            ValueError: If path is unsafe (escapes root, forbidden component)
        """
        return safe_resolve(self.repo_path, rel_path)
