import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any


class SandboxRunner:
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
        allowed_commands: list[str] | None = None,
        enforce_allowlist: bool = False,
        allow_shell_operators: bool = False,
        require_docker: bool = True,
    ):
        self.repo_root = repo_root
        self.image = image
        self.network = network
        self.fail_run = fail_run
        self.stub = stub
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.allowed_commands = allowed_commands or []
        self.enforce_allowlist = enforce_allowlist
        self.allow_shell_operators = allow_shell_operators
        self.require_docker = require_docker

        self._allowed_res = [re.compile(p) for p in self.allowed_commands]

    def _check_command_allowed(self, cmd: str) -> tuple[bool, str]:
        cmd = cmd.strip()

        if not cmd:
            return False, "Empty command"

        # Reject multi-line commands to prevent allowlist prefix tricks and implicit chaining.
        if any(ch in cmd for ch in ["\n", "\r", "\x00"]):
            return False, "Newlines/NUL not allowed"

        # Shell metacharacters are only meaningfully dangerous when allowlist enforcement
        # is enabled, since we execute via a shell (stub) / bash -lc (docker).
        if self.enforce_allowlist and (not self.allow_shell_operators):
            # Block obvious shell metacharacters/chaining unless explicitly allowed.
            # This is defense-in-depth since we execute via shell (stub) / bash -lc (docker).
            blocked = [
                ";",
                "&&",
                "||",
                "|",
                "&",
                ">",
                "<",
                "`",
                "$(",
                "${",  # avoid parameter expansions in command position
            ]
            for tok in blocked:
                if tok in cmd:
                    return False, f"Shell operator not allowed: {tok}"

        if self.enforce_allowlist:
            if not self._allowed_res:
                return False, "Allowlist enforcement enabled but allowlist is empty"

            # Use fullmatch to avoid allowing extra trailing payload beyond the allowlisted shape.
            if not any(r.fullmatch(cmd) for r in self._allowed_res):
                return False, "Command not in allowlist"

        return True, ""

    def run(self, cmd: str, timeout_s: int = 900) -> dict[str, Any]:
        t0 = time.time()
        if self.fail_run or os.getenv("SWARMGUARD_FAIL_SANDBOX_RUN") == "1":
            return {
                "cmd": cmd,
                "exit_code": 1,
                "stdout": "",
                "stderr": "Forced sandbox failure via SWARMGUARD_FAIL_SANDBOX_RUN",
                "duration_s": 0.0,
            }

        ok, reason = self._check_command_allowed(cmd)
        if not ok:
            return {
                "cmd": cmd,
                "exit_code": 126,
                "stdout": "",
                "stderr": f"Sandbox rejected command: {reason}",
                "duration_s": round(time.time() - t0, 3),
                "rejected": True,
                "reject_reason": reason,
            }

        if self.stub or os.getenv("SWARMGUARD_SANDBOX_STUB") == "1":
            p = subprocess.run(
                cmd,
                cwd=str(self.repo_root),
                text=True,
                capture_output=True,
                timeout=timeout_s,
                shell=True,
            )
            return {
                "cmd": cmd,
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
                    "cmd": cmd,
                    "exit_code": 127,
                    "stdout": "",
                    "stderr": "Docker is required but was not found on PATH",
                    "duration_s": round(time.time() - t0, 3),
                }

        docker_cmd = [
            "docker",
            "run",
            "--rm",
            "--network",
            self.network,
            "--memory",
            self.memory,
            "--cpus",
            self.cpus,
            "--pids-limit",
            str(self.pids_limit),
            "-v",
            f"{self.repo_root}:/repo",
            "-w",
            "/repo",
            self.image,
            "bash",
            "-lc",
            cmd,
        ]
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
                        docker_cmd[5:5] = ["-v", f"{main_git_dir}:{main_git_dir}"]
        try:
            p = subprocess.run(
                docker_cmd,
                text=True,
                capture_output=True,
                timeout=timeout_s,
            )
            return {
                "cmd": cmd,
                "exit_code": p.returncode,
                "stdout": p.stdout,
                "stderr": p.stderr,
                "duration_s": round(time.time() - t0, 3),
            }
        except FileNotFoundError:
            return {
                "cmd": cmd,
                "exit_code": 127,
                "stdout": "",
                "stderr": "Docker is required but was not found on PATH",
                "duration_s": round(time.time() - t0, 3),
            }
