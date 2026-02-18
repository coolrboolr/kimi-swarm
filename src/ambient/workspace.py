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
import shlex
from pathlib import Path
from typing import Any

from .impact import compute_impact_radius, extract_changed_paths
from .salvaged.git_ops import git_apply_patch_atomic, git_reset_hard_clean
from .salvaged.repo_pack import build_repo_pack
from .salvaged.safe_paths import safe_resolve
from .salvaged.sandbox import SandboxRunner
from .types import AmbientEvent, ApplyResult, Proposal, RepoContext, VerificationResult


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
        sandbox_network: str = "none",
        sandbox_memory: str = "2g",
        sandbox_cpus: str = "2.0",
        sandbox_pids_limit: int = 100,
        sandbox_allowed_argv: list[list[str]] | None = None,
        sandbox_allowed_commands: list[str] | None = None,
        sandbox_enforce_allowlist: bool = True,
        sandbox_require_docker: bool = True,
        sandbox_stub: bool = False,
        sandbox_repo_mount_mode: str = "ro",
        verification_timeout_seconds: int = 900,
    ):
        self.repo_path = Path(repo_path)

        if sandbox_allowed_argv is None and sandbox_allowed_commands is None:
            # Default to config's allowlist so CLI usage remains safe even if callers
            # only provide an image string.
            from .config import SandboxConfig

            cfg = SandboxConfig()
            sandbox_allowed_argv = cfg.allowed_argv
            sandbox_allowed_commands = cfg.allowed_commands

        self.sandbox = SandboxRunner(
            repo_root=self.repo_path,
            image=sandbox_image,
            network=sandbox_network,
            memory=sandbox_memory,
            cpus=sandbox_cpus,
            pids_limit=sandbox_pids_limit,
            allowed_commands=sandbox_allowed_commands,
            allowed_argv=sandbox_allowed_argv,
            enforce_allowlist=sandbox_enforce_allowlist,
            require_docker=sandbox_require_docker,
            stub=sandbox_stub,
            repo_mount_mode=sandbox_repo_mount_mode,
        )
        self.verification_timeout_seconds = verification_timeout_seconds
        self._verification_checks: list[tuple[str, list[str], dict[str, str]]] = []
        self._auto_detect_checks()

    def _auto_detect_checks(self) -> None:
        """Auto-detect available verification checks based on repo structure."""
        self._verification_checks = []

        base_env = {
            # Keep verification from writing into the mounted repo when it is mounted read-only.
            "HOME": "/tmp",
            "XDG_CACHE_HOME": "/tmp/xdg-cache",
            "PYTHONPYCACHEPREFIX": "/tmp/pycache",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

        # Python: pytest
        if (self.repo_path / "tests").exists() or (
            self.repo_path / "test"
        ).exists():
            self._verification_checks.append(
                (
                    "pytest",
                    ["pytest", "-xvs", "-p", "no:cacheprovider", "--basetemp=/tmp/pytest"],
                    dict(base_env),
                )
            )

        # Python: ruff
        if (
            (self.repo_path / "pyproject.toml").exists()
            or (self.repo_path / "ruff.toml").exists()
        ):
            env = dict(base_env)
            self._verification_checks.append(
                ("ruff", ["ruff", "check", ".", "--cache-dir", "/tmp/ruff-cache"], env)
            )

        # Python: mypy
        if (self.repo_path / "mypy.ini").exists() or (
            self.repo_path / "pyproject.toml"
        ).exists():
            env = dict(base_env)
            self._verification_checks.append(
                ("mypy", ["mypy", ".", "--cache-dir", "/tmp/mypy-cache"], env)
            )

        # Make targets
        if (self.repo_path / "Makefile").exists():
            self._verification_checks.append(("make-test", ["make", "test"], dict(base_env)))

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

        # Run all checks in parallel
        async def run_check_argv(name: str, argv: list[str], env: dict[str, str]) -> dict[str, Any]:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self.sandbox.run,
                argv,
                self.verification_timeout_seconds,
                env,
            )
            return {
                "name": name,
                "ok": result["exit_code"] == 0,
                "exit_code": result["exit_code"],
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "duration_s": result["duration_s"],
                "argv": result["argv"],
                "cmd": shlex.join(result["argv"]),
                "rejected": result.get("rejected", False),
                "reject_reason": result.get("reject_reason", ""),
            }

        results = await asyncio.gather(
            *[
                run_check_argv(name, argv, env)
                for name, argv, env in self._verification_checks
            ],
            return_exceptions=True,
        )

        # Convert exceptions to failed results
        processed_results: list[dict[str, Any]] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
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
        total_duration = float(sum(float(r.get("duration_s", 0.0) or 0.0) for r in processed_results))

        # All checks must pass
        all_ok = all(bool(r.get("ok", False)) for r in processed_results)

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

        event_rel_path = event.data.get("rel_path")
        if not event_rel_path and event.data.get("src_path"):
            try:
                event_rel_path = str(
                    Path(str(event.data["src_path"])).resolve().relative_to(self.repo_path.resolve())
                )
            except Exception:
                event_rel_path = None

        changed_paths = extract_changed_paths(event_rel_path, current_diff)
        loop = asyncio.get_event_loop()
        impact_paths = await loop.run_in_executor(
            None,
            compute_impact_radius,
            self.repo_path,
            list(tree.get("files", [])),
            changed_paths,
        )

        # Build pack using salvaged logic
        pack_json = await loop.run_in_executor(
            None,
            build_repo_pack,
            self.repo_path,
            event.task_spec,
            tree,
            failing_logs,
            current_diff,
            impact_paths,
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
            hot_paths=impact_paths or pack.get("hot_paths", []),
            conventions={
                **pack.get("conventions", {}),
                "analysis_scope": "impact_radius",
            },
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

    async def get_staged_diff(self) -> str:
        """Get staged diff from the current workspace."""
        loop = asyncio.get_event_loop()

        def _get_diff() -> str:
            import subprocess

            result = subprocess.run(
                ["git", "diff", "--cached"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=False,
            )
            return result.stdout if result.returncode == 0 else ""

        return await loop.run_in_executor(None, _get_diff)

    def register_verification(
        self, name: str, argv: list[str] | str, env: dict[str, str] | None = None
    ) -> None:
        """
        Register a custom verification check.

        Args:
            name: Name of the check (e.g., "custom-lint")
            argv: argv list to run (or a command line string, which will be shlex-split)
            env: Optional environment overrides for the sandboxed command
        """
        if isinstance(argv, str):
            argv_list = shlex.split(argv)
        else:
            argv_list = argv
        self._verification_checks.append((name, argv_list, env or {}))

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
