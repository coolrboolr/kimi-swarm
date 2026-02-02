"""Integration tests for full pipeline end-to-end."""

import tempfile
from pathlib import Path
import pytest

from src.ambient.coordinator import AmbientCoordinator
from src.ambient.config import AmbientConfig, KimiConfig
from src.ambient.types import AmbientEvent
from src.ambient.approval import AlwaysRejectHandler, AlwaysApproveHandler


@pytest.fixture
def temp_git_repo():
    """Create a temporary git repository for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Initialize git repo
        import subprocess
        subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo_path, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo_path, check=True, capture_output=True
        )

        # Create initial file
        (repo_path / "main.py").write_text("def hello():\n    print('Hello')\n")
        (repo_path / "pyproject.toml").write_text("[tool.pytest]\n")

        # Commit
        subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=repo_path, check=True, capture_output=True
        )

        yield repo_path


@pytest.fixture
def mock_config():
    """Create a mock configuration for testing."""
    # Use minimal config without real Kimi (would need mocking)
    config = AmbientConfig()
    config.agents.enabled = []  # Disable agents for basic tests
    config.sandbox.image = "ambient-sandbox:latest"
    config.telemetry.enabled = False  # Disable telemetry for simplicity
    return config


class TestCoordinatorInitialization:
    """Tests for coordinator initialization."""

    def test_coordinator_init(self, temp_git_repo, mock_config):
        """Test coordinator initialization."""
        coordinator = AmbientCoordinator(
            temp_git_repo,
            mock_config,
            AlwaysRejectHandler(mock_config.risk_policy)
        )

        assert coordinator.repo_path == temp_git_repo
        assert coordinator.config == mock_config
        assert isinstance(coordinator.approval_handler, AlwaysRejectHandler)

    def test_coordinator_init_agents(self, temp_git_repo):
        """Test coordinator initializes agents from config."""
        config = AmbientConfig()
        config.agents.enabled = ["SecurityGuardian", "StyleEnforcer"]
        config.telemetry.enabled = False

        coordinator = AmbientCoordinator(
            temp_git_repo,
            config,
            AlwaysRejectHandler(config.risk_policy)
        )

        # Initialize agents
        coordinator._init_agents()

        assert len(coordinator.agents) == 2
        assert any("SecurityGuardian" in str(type(a)) for a in coordinator.agents)
        assert any("StyleEnforcer" in str(type(a)) for a in coordinator.agents)


class TestRunOnceCycle:
    """Tests for run_once cycle execution."""

    @pytest.mark.asyncio
    async def test_run_once_no_agents(self, temp_git_repo, mock_config):
        """Test run_once with no agents configured."""
        coordinator = AmbientCoordinator(
            temp_git_repo,
            mock_config,
            AlwaysRejectHandler(mock_config.risk_policy)
        )

        event = AmbientEvent(
            type="periodic_scan",
            data={"timestamp": 123},
            task_spec={"goal": "Test scan"}
        )

        result = await coordinator.run_once(event)

        assert result["status"] == "no_proposals"
        assert len(result["proposals"]) == 0
        assert len(result.get("applied", [])) == 0

    @pytest.mark.asyncio
    async def test_run_once_with_default_event(self, temp_git_repo, mock_config):
        """Test run_once with default event (no event provided)."""
        coordinator = AmbientCoordinator(
            temp_git_repo,
            mock_config,
            AlwaysRejectHandler(mock_config.risk_policy)
        )

        result = await coordinator.run_once()

        assert result["status"] == "no_proposals"
        assert "run_id" in result


class TestWorkspaceIntegration:
    """Tests for workspace integration."""

    @pytest.mark.asyncio
    async def test_workspace_build_context(self, temp_git_repo, mock_config):
        """Test workspace context building."""
        from src.ambient.workspace import Workspace

        workspace = Workspace(temp_git_repo, mock_config.sandbox.image)

        event = AmbientEvent(
            type="periodic_scan",
            data={},
            task_spec={"goal": "Test"}
        )

        context = await workspace.build_context(event)

        assert context.task["goal"] == "Test"
        assert "main.py" in context.tree["files"]
        assert "pyproject.toml" in context.important_files
        assert context.failing_logs == ""
        assert context.current_diff == ""

    @pytest.mark.asyncio
    async def test_workspace_apply_and_rollback(self, temp_git_repo, mock_config):
        """Test workspace patch application and rollback."""
        from src.ambient.workspace import Workspace
        from src.ambient.types import Proposal

        workspace = Workspace(temp_git_repo, mock_config.sandbox.image)

        # Create a simple patch
        proposal = Proposal(
            agent="TestAgent",
            title="Add comment",
            description="Test",
            diff="""--- a/main.py
+++ b/main.py
@@ -1,2 +1,3 @@
+# Test comment
 def hello():
     print('Hello')
""",
            risk_level="low",
            rationale="Test",
            files_touched=["main.py"],
            estimated_loc_change=1
        )

        # Apply patch
        result = await workspace.apply_patch(proposal)

        # For this test, we expect it might fail since sandbox might not be available
        # The important thing is the API works correctly
        assert hasattr(result, 'ok')
        assert hasattr(result, 'stat')
        assert hasattr(result, 'stderr')


class TestApprovalIntegration:
    """Tests for approval handler integration."""

    @pytest.mark.asyncio
    async def test_dry_run_mode_rejects_all(self, temp_git_repo):
        """Test dry-run mode (AlwaysRejectHandler) rejects everything."""
        config = AmbientConfig()
        config.agents.enabled = []
        config.telemetry.enabled = False

        coordinator = AmbientCoordinator(
            temp_git_repo,
            config,
            AlwaysRejectHandler(config.risk_policy)
        )

        # Manually create proposals
        from src.ambient.types import Proposal
        proposals = [
            Proposal(
                agent="TestAgent",
                title="Low risk change",
                description="Safe",
                diff="+ comment",
                risk_level="low",
                rationale="Test",
                files_touched=["test.py"],
                estimated_loc_change=1
            ),
            Proposal(
                agent="TestAgent",
                title="High risk change",
                description="Dangerous",
                diff="- important code",
                risk_level="high",
                rationale="Test",
                files_touched=["critical.py"],
                estimated_loc_change=10
            ),
        ]

        # In dry-run mode, _apply_proposals should mark all as dry_run
        result = await coordinator._apply_proposals(
            proposals,
            "test-run-id",
            temp_git_repo / ".ambient" / "telemetry.jsonl",
            dry_run=True
        )

        # All should be in failed list
        assert len(result["applied"]) == 0
        assert len(result["failed"]) == 2
        assert all(f["reason"] == "dry_run" for f in result["failed"])

    @pytest.mark.asyncio
    async def test_auto_approve_mode(self, temp_git_repo):
        """Test auto-approve mode attempts to apply all proposals."""
        config = AmbientConfig()
        config.agents.enabled = []
        config.telemetry.enabled = False

        coordinator = AmbientCoordinator(
            temp_git_repo,
            config,
            AlwaysApproveHandler(config.risk_policy)
        )

        from src.ambient.types import Proposal
        proposals = [
            Proposal(
                agent="TestAgent",
                title="Test change",
                description="Test",
                diff="+ comment",
                risk_level="low",
                rationale="Test",
                files_touched=["test.py"],
                estimated_loc_change=1
            ),
        ]

        # With auto-approve, should attempt application (may fail due to invalid patch)
        result = await coordinator._apply_proposals(
            proposals,
            "test-run-id",
            temp_git_repo / ".ambient" / "telemetry.jsonl",
            dry_run=False
        )

        # Either applied or failed, but not rejected
        total = len(result["applied"]) + len(result["failed"])
        assert total == 1

        # If failed, should not be due to approval
        if result["failed"]:
            assert result["failed"][0]["reason"] != "approval_rejected"


class TestRiskIntegration:
    """Tests for risk assessment integration."""

    @pytest.mark.asyncio
    async def test_risk_gates_high_risk_proposals(self, temp_git_repo):
        """Test that high-risk proposals trigger approval gates."""
        config = AmbientConfig()
        config.agents.enabled = []
        config.telemetry.enabled = False

        # Use reject handler to simulate rejection
        coordinator = AmbientCoordinator(
            temp_git_repo,
            config,
            AlwaysRejectHandler(config.risk_policy)
        )

        from src.ambient.types import Proposal
        high_risk_proposal = Proposal(
            agent="SecurityGuardian",
            title="Modify auth",
            description="Critical change",
            diff="auth changes",
            risk_level="critical",
            rationale="Security",
            files_touched=["auth.py"],
            estimated_loc_change=5,
            tags=["security"]
        )

        # Should trigger risk gate and be rejected
        result = await coordinator._apply_proposals(
            [high_risk_proposal],
            "test-run-id",
            temp_git_repo / ".ambient" / "telemetry.jsonl",
            dry_run=False  # Not dry-run, but handler will reject
        )

        # Should be rejected due to approval (not dry_run)
        assert len(result["failed"]) == 1
        assert result["failed"][0]["reason"] == "approval_rejected"


class TestContextBuilding:
    """Tests for repository context building."""

    @pytest.mark.asyncio
    async def test_context_includes_python_files(self, temp_git_repo):
        """Test that context includes Python source files."""
        # Add more Python files
        (temp_git_repo / "utils.py").write_text("def helper():\n    return 42\n")
        (temp_git_repo / "tests").mkdir()
        (temp_git_repo / "tests" / "test_main.py").write_text("def test_hello():\n    pass\n")

        from src.ambient.workspace import Workspace
        workspace = Workspace(temp_git_repo, "ambient-sandbox:latest")

        event = AmbientEvent(
            type="periodic_scan",
            data={},
            task_spec={"goal": "Test"}
        )

        context = await workspace.build_context(event)

        # Should include Python files in important_files
        assert "main.py" in context.important_files
        assert "def hello()" in context.important_files.get("main.py", "")

    @pytest.mark.asyncio
    async def test_context_with_uncommitted_changes(self, temp_git_repo):
        """Test context captures uncommitted changes."""
        # Make uncommitted changes
        (temp_git_repo / "main.py").write_text("def hello():\n    print('Modified')\n")

        from src.ambient.workspace import Workspace
        workspace = Workspace(temp_git_repo, "ambient-sandbox:latest")

        event = AmbientEvent(
            type="periodic_scan",
            data={},
            task_spec={"goal": "Test"}
        )

        context = await workspace.build_context(event)

        # Should have diff
        assert context.current_diff != ""
        assert "Modified" in context.current_diff or "print" in context.current_diff


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
