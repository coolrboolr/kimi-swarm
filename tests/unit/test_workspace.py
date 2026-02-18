"""Unit tests for workspace.py - async workspace operations."""

import subprocess

import pytest

from ambient.types import AmbientEvent, Proposal
from ambient.workspace import Workspace


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repository for testing."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    # Create initial file
    test_file = repo_path / "test.py"
    test_file.write_text("def hello():\n    print('Hello, World!')\n")

    # Initial commit
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    return repo_path


@pytest.mark.asyncio
class TestWorkspaceApplyPatch:
    """Test async patch application."""

    async def test_apply_simple_patch(self, git_repo):
        """Test applying a simple patch."""
        workspace = Workspace(git_repo, sandbox_image="unused")

        proposal = Proposal(
            agent="TestAgent",
            title="Test change",
            description="Test description",
            diff="""--- a/test.py
+++ b/test.py
@@ -1,2 +1,2 @@
 def hello():
-    print('Hello, World!')
+    print('Hello, Test!')
""",
            risk_level="low",
            rationale="Test rationale",
            files_touched=["test.py"],
            estimated_loc_change=2,
        )

        result = await workspace.apply_patch(proposal)

        assert result.ok is True
        assert "1 file changed" in result.stat

        # Verify the change
        content = (git_repo / "test.py").read_text()
        assert "Hello, Test!" in content

    async def test_apply_invalid_patch(self, git_repo):
        """Test applying an invalid patch triggers rollback."""
        workspace = Workspace(git_repo, sandbox_image="unused")

        proposal = Proposal(
            agent="TestAgent",
            title="Invalid change",
            description="Test description",
            diff="""--- a/nonexistent.py
+++ b/nonexistent.py
@@ -1,2 +1,2 @@
 def hello():
-    print('Old')
+    print('New')
""",
            risk_level="low",
            rationale="Test rationale",
            files_touched=["nonexistent.py"],
            estimated_loc_change=2,
        )

        result = await workspace.apply_patch(proposal)

        assert result.ok is False
        assert len(result.stderr) > 0


@pytest.mark.asyncio
class TestWorkspaceRollback:
    """Test rollback functionality."""

    async def test_rollback_modified_file(self, git_repo):
        """Test rollback restores original state."""
        workspace = Workspace(git_repo, sandbox_image="unused")

        # Modify file
        test_file = git_repo / "test.py"
        original_content = test_file.read_text()
        test_file.write_text("def hello():\n    print('Modified!')\n")

        # Rollback
        await workspace.rollback()

        # Verify restored
        assert test_file.read_text() == original_content


@pytest.mark.asyncio
class TestWorkspaceVerification:
    """Test verification checks."""

    async def test_no_checks_configured(self, git_repo):
        """Test workspace with no verification checks."""
        workspace = Workspace(git_repo, sandbox_image="unused")
        workspace._verification_checks = []  # Clear auto-detected checks

        result = await workspace.verify_changes()

        assert result.ok is True
        assert len(result.results) == 0

    async def test_stub_verification(self, git_repo, monkeypatch):
        """Test verification in stub mode."""
        monkeypatch.setenv("AMBIENT_SANDBOX_STUB", "1")

        # Create a dummy test that passes
        (git_repo / "test_dummy.py").write_text("def test_pass():\n    assert True\n")

        workspace = Workspace(git_repo, sandbox_image="unused")
        workspace._verification_checks = [
            (
                "pytest",
                ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp=/tmp/pytest"],
                {},
            )
        ]

        result = await workspace.verify_changes()

        assert result.ok is True
        assert len(result.results) == 1
        assert result.results[0]["name"] == "pytest"
        assert result.results[0]["ok"] is True


@pytest.mark.asyncio
class TestWorkspaceBuildContext:
    """Test context building."""

    async def test_build_context_file_change(self, git_repo):
        """Test building context for file change event."""
        workspace = Workspace(git_repo, sandbox_image="unused")

        event = AmbientEvent(
            type="file_change",
            data={"src_path": str(git_repo / "test.py")},
            task_spec={"goal": "Test goal"},
        )

        context = await workspace.build_context(event)

        assert context.task == event.task_spec
        assert isinstance(context.tree, dict)
        assert "files" in context.tree
        assert len(context.tree["files"]) > 0
        assert "test.py" in context.hot_paths

    async def test_build_context_ci_failure(self, git_repo):
        """Test building context for CI failure event."""
        workspace = Workspace(git_repo, sandbox_image="unused")

        failing_logs = "FAILED test_something.py::test_func - AssertionError"

        event = AmbientEvent(
            type="ci_failure",
            data={"logs": failing_logs},
            task_spec={"goal": "Fix failing tests"},
        )

        context = await workspace.build_context(event)

        assert context.failing_logs == failing_logs
        assert context.task == event.task_spec


@pytest.mark.asyncio
class TestWorkspaceSafePaths:
    """Test safe path resolution."""

    async def test_safe_resolve_valid_path(self, git_repo):
        """Test resolving a safe path."""
        workspace = Workspace(git_repo, sandbox_image="unused")

        path = workspace.safe_resolve_path("test.py")

        assert path == git_repo / "test.py"

    async def test_safe_resolve_reject_escape(self, git_repo):
        """Test rejecting path escape attempts."""
        workspace = Workspace(git_repo, sandbox_image="unused")

        with pytest.raises(ValueError, match="Path escapes repo root"):
            workspace.safe_resolve_path("../../etc/passwd")

    async def test_safe_resolve_reject_forbidden(self, git_repo):
        """Test rejecting forbidden paths."""
        workspace = Workspace(git_repo, sandbox_image="unused")

        with pytest.raises(ValueError, match="Forbidden path component"):
            workspace.safe_resolve_path(".git/config")


@pytest.mark.asyncio
class TestWorkspaceCustomVerification:
    """Test custom verification registration."""

    async def test_register_custom_check(self, git_repo):
        """Test registering a custom verification check."""
        workspace = Workspace(git_repo, sandbox_image="unused")

        workspace.register_verification("custom-lint", ["python", "-c", "print('Custom check')"])

        assert any(name == "custom-lint" for name, _, _ in workspace._verification_checks)
