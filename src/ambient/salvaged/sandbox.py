import os
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
    ):
        self.repo_root = repo_root
        self.image = image
        self.network = network
        self.fail_run = fail_run
        self.stub = stub
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit

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
