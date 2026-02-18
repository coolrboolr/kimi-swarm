"""Review worktree orchestration for parallel proposal diffs."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ReviewCandidate:
    """A dedicated worktree/branch pair for reviewing one proposal."""

    index: int
    title_slug: str
    branch: str
    worktree_path: Path
    patch_path: Path


class ReviewWorktreeManager:
    """Create and manage per-proposal review worktrees."""

    def __init__(self, repo_path: Path, base_dir: Path, branch_prefix: str) -> None:
        self.repo_path = Path(repo_path)
        self.base_dir = Path(base_dir)
        self.branch_prefix = branch_prefix.strip().rstrip("/")

    def prepare_run_dir(self, run_id: str) -> Path:
        run_dir = self.base_dir / run_id
        (run_dir / "worktrees").mkdir(parents=True, exist_ok=True)
        (run_dir / "patches").mkdir(parents=True, exist_ok=True)
        return run_dir

    def create_candidate(self, run_id: str, index: int, title: str) -> ReviewCandidate:
        run_dir = self.prepare_run_dir(run_id)
        slug = slugify(title)

        worktree_path = run_dir / "worktrees" / f"{index:02d}-{slug}"
        patch_path = run_dir / "patches" / f"{index:02d}-{slug}.diff"
        branch = f"{self.branch_prefix}/{run_id}/{index:02d}-{slug}"

        if worktree_path.exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree_path)],
                cwd=self.repo_path,
                check=False,
                capture_output=True,
                text=True,
            )

        res = subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(worktree_path), "HEAD"],
            cwd=self.repo_path,
            check=False,
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            raise RuntimeError(f"failed to create review worktree: {res.stderr.strip()}")

        return ReviewCandidate(
            index=index,
            title_slug=slug,
            branch=branch,
            worktree_path=worktree_path,
            patch_path=patch_path,
        )

    def remove_candidate(self, candidate: ReviewCandidate) -> None:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(candidate.worktree_path)],
            cwd=self.repo_path,
            check=False,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "branch", "-D", candidate.branch],
            cwd=self.repo_path,
            check=False,
            capture_output=True,
            text=True,
        )


def slugify(text: str) -> str:
    """Normalize titles for branch/file naming."""
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", text.strip().lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-._")
    return cleaned[:48] or "proposal"
