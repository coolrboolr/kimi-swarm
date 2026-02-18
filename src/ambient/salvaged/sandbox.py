import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any


class SandboxRunner:
    """
    Sandbox command runner.

    Key security properties:
    - Executes an argv list (no shell, no bash -lc).
    - Allowlist is validated against parsed argv (command + args prefix).
    """

    def __init__(
        self,
        repo_root: Path,
        image: str,
        network: str = "none",
        fail_run: bool = False,
        stub: bool = False,
        memory: str = "2g",
        cpus: str = "2.0",
        pids_limit: int = 100,
        allowed_argv: list[list[str]] | None = None,
        # Back-compat: legacy regex allowlist applied to normalized argv (shlex.join(argv)).
        allowed_commands: list[str] | None = None,
        enforce_allowlist: bool = False,
        require_docker: bool = True,
        # Prefer "ro" for verification. Use "rw" only if you explicitly need to write to the repo.
        repo_mount_mode: str = "ro",
    ):
        self.repo_root = repo_root
        self.image = image
        self.network = network
        self.fail_run = fail_run
        self.stub = stub
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.allowed_argv = allowed_argv or []
        self.allowed_commands = allowed_commands or []
        self.enforce_allowlist = enforce_allowlist
        self.require_docker = require_docker
        self.repo_mount_mode = repo_mount_mode

        self._allowed_res = [re.compile(p) for p in self.allowed_commands]

    def _check_argv_allowed(self, argv: list[str]) -> tuple[bool, str]:
        if not argv:
            return False, "Empty argv"

        for a in argv:
            if any(ch in a for ch in ["\n", "\r", "\x00"]):
                return False, "Newlines/NUL not allowed"

        if self.enforce_allowlist:
            if not self.allowed_argv and not self._allowed_res:
                return False, "Allowlist enforcement enabled but allowlist is empty"

            # Preferred: argv prefix allowlist (command + fixed args; extra args allowed).
            for allowed in self.allowed_argv:
                if not allowed:
                    continue
                if argv[: len(allowed)] == allowed:
                    return True, ""

            # Back-compat: legacy regex allowlist over normalized argv.
            if self._allowed_res:
                normalized = shlex.join(argv)
                if any(r.fullmatch(normalized) for r in self._allowed_res):
                    return True, ""

            return False, "Command not in allowlist"

        return True, ""

    def _docker_mounts(self) -> list[str]:
        repo_mode = "rw" if self.repo_mount_mode == "rw" else "ro"
        mounts = ["-v", f"{self.repo_root}:/repo:{repo_mode}", "-w", "/repo"]

        # Support git worktrees where `.git` is a file that points at the gitdir.
        git_file = self.repo_root / ".git"
        if git_file.is_file():
            content = git_file.read_text(encoding="utf-8").strip()
            if content.startswith("gitdir:"):
                gitdir = content.split("gitdir:", 1)[1].strip()
                gitdir_path = Path(gitdir)
                if gitdir_path.is_absolute():
                    main_git_dir = gitdir_path
                    if "worktrees" in gitdir_path.parts:
                        idx = gitdir_path.parts.index("worktrees")
                        main_git_dir = Path(*gitdir_path.parts[:idx])
                    if main_git_dir.exists():
                        mounts[0:0] = ["-v", f"{main_git_dir}:{main_git_dir}:ro"]
        return mounts

    def run(
        self,
        argv: list[str],
        timeout_s: int = 900,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        t0 = time.time()

        if self.fail_run or os.getenv("AMBIENT_FAIL_SANDBOX_RUN") == "1" or os.getenv("SWARMGUARD_FAIL_SANDBOX_RUN") == "1":
            return {
                "argv": argv,
                "exit_code": 1,
                "stdout": "",
                "stderr": "Forced sandbox failure via AMBIENT_FAIL_SANDBOX_RUN",
                "duration_s": 0.0,
            }

        ok, reason = self._check_argv_allowed(argv)
        if not ok:
            return {
                "argv": argv,
                "exit_code": 126,
                "stdout": "",
                "stderr": f"Sandbox rejected command: {reason}",
                "duration_s": round(time.time() - t0, 3),
                "rejected": True,
                "reject_reason": reason,
            }

        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)

        if self.stub or os.getenv("AMBIENT_SANDBOX_STUB") == "1" or os.getenv("SWARMGUARD_SANDBOX_STUB") == "1":
            p = subprocess.run(
                argv,
                cwd=str(self.repo_root),
                text=True,
                capture_output=True,
                timeout=timeout_s,
                shell=False,
                env=merged_env,
            )
            return {
                "argv": argv,
                "exit_code": p.returncode,
                "stdout": p.stdout,
                "stderr": p.stderr,
                "duration_s": round(time.time() - t0, 3),
            }

        # Docker execution path
        if self.require_docker:
            try:
                subprocess.run(
                    ["docker", "--version"],
                    text=True,
                    capture_output=True,
                    check=False,
                )
            except FileNotFoundError:
                return {
                    "argv": argv,
                    "exit_code": 127,
                    "stdout": "",
                    "stderr": "Docker is required but was not found on PATH",
                    "duration_s": round(time.time() - t0, 3),
                }

        docker_cmd: list[str] = [
            "docker",
            "run",
            "--rm",
            "--init",
            "--network",
            self.network,
            "--memory",
            self.memory,
            "--cpus",
            self.cpus,
            "--pids-limit",
            str(self.pids_limit),
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=256m",
            "--tmpfs",
            "/var/tmp:rw,noexec,nosuid,nodev,size=128m",
            "--ulimit",
            "nofile=1024:1024",
            "--ulimit",
            "nproc=256:256",
            # Writable HOME in a read-only container.
            "-e",
            "HOME=/tmp",
            "-e",
            "XDG_CACHE_HOME=/tmp/xdg-cache",
        ]

        # Provide env overrides (e.g., cache dirs) without involving a shell.
        for k, v in (env or {}).items():
            docker_cmd += ["-e", f"{k}={v}"]

        docker_cmd += self._docker_mounts()
        docker_cmd += [self.image, *argv]

        try:
            p = subprocess.run(
                docker_cmd,
                text=True,
                capture_output=True,
                timeout=timeout_s,
            )
            return {
                "argv": argv,
                "exit_code": p.returncode,
                "stdout": p.stdout,
                "stderr": p.stderr,
                "duration_s": round(time.time() - t0, 3),
            }
        except FileNotFoundError:
            return {
                "argv": argv,
                "exit_code": 127,
                "stdout": "",
                "stderr": "Docker is required but was not found on PATH",
                "duration_s": round(time.time() - t0, 3),
            }

    def doctor(self, required_commands: list[list[str]] | None = None) -> dict[str, Any]:
        """Preflight: docker present, image present, and required commands runnable."""
        required_commands = required_commands or []

        try:
            v = subprocess.run(["docker", "--version"], text=True, capture_output=True)
        except FileNotFoundError:
            return {"ok": False, "error": "docker_not_found", "checks": []}

        if v.returncode != 0:
            return {
                "ok": False,
                "error": "docker_unhealthy",
                "docker_stdout": v.stdout.strip(),
                "docker_stderr": v.stderr.strip(),
                "checks": [],
            }

        img = subprocess.run(
            ["docker", "image", "inspect", self.image],
            text=True,
            capture_output=True,
        )
        if img.returncode != 0:
            return {
                "ok": False,
                "error": "image_missing",
                "image": self.image,
                "stderr": img.stderr.strip(),
                "checks": [],
            }

        checks: list[dict[str, Any]] = []
        for argv in required_commands:
            if not argv:
                continue
            # Doctor probes should not be blocked by the allowlist; the purpose is to
            # validate the sandbox boundary itself.
            probe = SandboxRunner(
                repo_root=self.repo_root,
                image=self.image,
                network=self.network,
                fail_run=False,
                stub=self.stub,
                memory=self.memory,
                cpus=self.cpus,
                pids_limit=self.pids_limit,
                allowed_argv=self.allowed_argv,
                allowed_commands=self.allowed_commands,
                enforce_allowlist=False,
                require_docker=self.require_docker,
                repo_mount_mode=self.repo_mount_mode,
            )
            out = probe.run(argv, timeout_s=60, env={"HOME": "/tmp"})
            checks.append(
                {
                    "argv": argv,
                    "ok": out["exit_code"] == 0,
                    "exit_code": out["exit_code"],
                    "stderr_head": (out.get("stderr") or "")[:400],
                    "stdout_head": (out.get("stdout") or "")[:400],
                }
            )

        ok = all(c["ok"] for c in checks)
        return {"ok": ok, "error": None if ok else "command_failed", "checks": checks}
