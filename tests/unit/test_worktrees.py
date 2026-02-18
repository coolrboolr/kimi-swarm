"""Unit tests for review worktree management."""

import subprocess
from pathlib import Path

from ambient.worktrees import ReviewWorktreeManager, slugify


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True)
    (repo / "main.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


def test_slugify_sanitizes_text() -> None:
    assert slugify(" Fix SQL Injection !!! ") == "fix-sql-injection"


def test_create_and_remove_candidate(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    manager = ReviewWorktreeManager(
        repo_path=repo,
        base_dir=repo / ".ambient" / "reviews",
        branch_prefix="ambient/review",
    )

    candidate = manager.create_candidate("run123", 1, "Add docs")

    assert candidate.worktree_path.exists()
    assert candidate.branch.startswith("ambient/review/run123/")
    assert candidate.patch_path.parent.exists()

    manager.remove_candidate(candidate)

    assert not candidate.worktree_path.exists()
