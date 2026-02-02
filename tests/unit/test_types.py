"""Unit tests for core data types."""

import pytest
from src.ambient.types import Proposal, RepoContext, AmbientEvent, VerificationResult, ApplyResult


class TestProposal:
    """Tests for Proposal dataclass."""

    def test_valid_proposal(self):
        """Test creating a valid proposal."""
        proposal = Proposal(
            agent="TestAgent",
            title="Fix bug",
            description="Fixes a critical bug",
            diff="--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new",
            risk_level="medium",
            rationale="Bug causes crashes",
            files_touched=["file.py"],
            estimated_loc_change=2,
            tags=["bugfix"],
        )
        assert proposal.agent == "TestAgent"
        assert proposal.risk_level == "medium"
        assert len(proposal.files_touched) == 1
        assert proposal.tags == ["bugfix"]

    def test_proposal_with_default_tags(self):
        """Test proposal with default empty tags."""
        proposal = Proposal(
            agent="TestAgent",
            title="Fix bug",
            description="Test",
            diff="test diff",
            risk_level="low",
            rationale="Test",
            files_touched=["test.py"],
            estimated_loc_change=1,
        )
        assert proposal.tags == []

    def test_proposal_invalid_risk_level(self):
        """Test that invalid risk level raises ValueError."""
        with pytest.raises(ValueError, match="Invalid risk_level"):
            Proposal(
                agent="TestAgent",
                title="Fix bug",
                description="Test",
                diff="test diff",
                risk_level="invalid",  # Invalid!
                rationale="Test",
                files_touched=["test.py"],
                estimated_loc_change=1,
            )

    @pytest.mark.parametrize("risk_level", ["low", "medium", "high", "critical"])
    def test_proposal_all_valid_risk_levels(self, risk_level):
        """Test all valid risk levels are accepted."""
        proposal = Proposal(
            agent="TestAgent",
            title="Fix bug",
            description="Test",
            diff="test diff",
            risk_level=risk_level,
            rationale="Test",
            files_touched=["test.py"],
            estimated_loc_change=1,
        )
        assert proposal.risk_level == risk_level


class TestRepoContext:
    """Tests for RepoContext dataclass."""

    def test_minimal_context(self):
        """Test creating minimal repo context."""
        context = RepoContext(
            task={"goal": "Test"},
            tree={"files": ["main.py"], "total_files": 1},
            important_files={"pyproject.toml": "[tool.pytest]"},
            failing_logs="",
            current_diff="",
        )
        assert context.task["goal"] == "Test"
        assert len(context.tree["files"]) == 1
        assert len(context.hot_paths) == 0  # Default
        assert len(context.conventions) == 0  # Default

    def test_full_context(self):
        """Test creating full repo context with all fields."""
        context = RepoContext(
            task={"goal": "Test", "trigger": "manual"},
            tree={"files": ["main.py", "test.py"], "total_files": 2},
            important_files={"pyproject.toml": "content", "requirements.txt": "pytest"},
            failing_logs="ERROR: test failed",
            current_diff="diff --git a/main.py",
            hot_paths=["main.py"],
            conventions={"style": "google"},
        )
        assert len(context.tree["files"]) == 2
        assert len(context.important_files) == 2
        assert len(context.hot_paths) == 1
        assert context.conventions["style"] == "google"
        assert "ERROR" in context.failing_logs


class TestAmbientEvent:
    """Tests for AmbientEvent dataclass."""

    def test_file_change_event(self):
        """Test file change event."""
        event = AmbientEvent(
            type="file_change",
            data={"src_path": "/path/to/file.py"},
            task_spec={"goal": "Monitor changes"},
        )
        assert event.is_file_change
        assert not event.is_ci_failure
        assert not event.is_periodic_scan

    def test_ci_failure_event(self):
        """Test CI failure event."""
        event = AmbientEvent(
            type="ci_failure",
            data={"logs": "test failed"},
            task_spec={"goal": "Fix CI"},
        )
        assert not event.is_file_change
        assert event.is_ci_failure
        assert not event.is_periodic_scan

    def test_periodic_scan_event(self):
        """Test periodic scan event."""
        event = AmbientEvent(
            type="periodic_scan",
            data={"timestamp": 123456},
            task_spec={"goal": "Routine scan"},
        )
        assert not event.is_file_change
        assert not event.is_ci_failure
        assert event.is_periodic_scan

    def test_manual_trigger_event(self):
        """Test manual trigger (custom type)."""
        event = AmbientEvent(
            type="manual_trigger",
            data={"user": "admin"},
        )
        # All property checks should return False for custom type
        assert not event.is_file_change
        assert not event.is_ci_failure
        assert not event.is_periodic_scan


class TestVerificationResult:
    """Tests for VerificationResult dataclass."""

    def test_successful_verification(self):
        """Test successful verification result."""
        result = VerificationResult(
            ok=True,
            results=[
                {"name": "pytest", "ok": True, "exit_code": 0},
                {"name": "ruff", "ok": True, "exit_code": 0},
            ],
            duration_s=5.2,
        )
        assert result.ok
        assert result.all_passed
        assert result.duration_s == 5.2
        assert len(result.results) == 2

    def test_failed_verification(self):
        """Test failed verification result."""
        result = VerificationResult(
            ok=False,
            results=[
                {"name": "pytest", "ok": True, "exit_code": 0},
                {"name": "ruff", "ok": False, "exit_code": 1},
            ],
            duration_s=3.1,
        )
        assert not result.ok
        assert not result.all_passed

    def test_empty_verification(self):
        """Test verification with no checks."""
        result = VerificationResult(ok=True, results=[], duration_s=0.0)
        assert result.ok
        assert result.all_passed
        assert len(result.results) == 0

    def test_mixed_results_all_passed_check(self):
        """Test all_passed with mixed ok/failed results."""
        result = VerificationResult(
            ok=True,  # Overall ok
            results=[
                {"name": "test1", "ok": True},
                {"name": "test2", "ok": False},  # One failed
            ],
            duration_s=1.0,
        )
        assert not result.all_passed  # Should be False due to failed check


class TestApplyResult:
    """Tests for ApplyResult dataclass."""

    def test_successful_apply(self):
        """Test successful patch application."""
        result = ApplyResult(
            ok=True,
            stat="1 file changed, 2 insertions(+), 1 deletion(-)",
            stderr="",
            debug_bundle={"strategy": "git-am"},
        )
        assert result.ok
        assert "1 file changed" in result.stat
        assert result.stderr == ""
        assert result.debug_bundle["strategy"] == "git-am"

    def test_failed_apply(self):
        """Test failed patch application."""
        result = ApplyResult(
            ok=False,
            stat="",
            stderr="error: patch failed: main.py:10",
            debug_bundle={"attempted_strategies": ["git-am", "git-apply"]},
        )
        assert not result.ok
        assert "error" in result.stderr
        assert result.stat == ""

    def test_apply_with_default_debug_bundle(self):
        """Test apply result with default empty debug bundle."""
        result = ApplyResult(
            ok=True,
            stat="1 file changed",
            stderr="",
        )
        assert result.debug_bundle == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
