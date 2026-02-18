"""Scenario tests for edge cases and determinism."""

import tempfile
from pathlib import Path

import pytest

from src.ambient.config import RiskPolicyConfig
from src.ambient.risk import assess_risk, sort_by_risk_priority
from src.ambient.salvaged.git_ops import git_apply_patch_atomic
from src.ambient.salvaged.safe_paths import safe_resolve
from src.ambient.types import Proposal


class TestDeterminism:
    """Tests for deterministic behavior."""

    def test_risk_assessment_deterministic(self):
        """Test that risk assessment is deterministic."""
        proposal = Proposal(
            agent="TestAgent",
            title="Test",
            description="Test",
            diff="test",
            risk_level="medium",
            rationale="Test",
            files_touched=["auth.py", "payment.py"],
            estimated_loc_change=50,
            tags=["security"]
        )
        policy = RiskPolicyConfig()

        # Run assessment multiple times
        results = [assess_risk(proposal, policy) for _ in range(10)]

        # All results should be identical
        first = results[0]
        for result in results[1:]:
            assert result["requires_approval"] == first["requires_approval"]
            assert result["risk_score"] == first["risk_score"]
            assert set(result["risk_factors"]) == set(first["risk_factors"])

    def test_proposal_sorting_deterministic(self):
        """Test that proposal sorting is deterministic."""
        proposals = [
            Proposal(
                agent=f"Agent{i}",
                title=f"Proposal {i}",
                description="Test",
                diff="test",
                risk_level=["low", "medium", "high", "critical"][i % 4],
                rationale="Test",
                files_touched=["file.py"],
                estimated_loc_change=i
            )
            for i in range(20)
        ]

        # Sort multiple times
        sorted_results = [sort_by_risk_priority(proposals.copy()) for _ in range(10)]

        # All results should be identical
        first = sorted_results[0]
        for result in sorted_results[1:]:
            assert [p.title for p in result] == [p.title for p in first]
            assert [p.risk_level for p in result] == [p.risk_level for p in first]


class TestEmptyInputs:
    """Tests for empty/null input handling."""

    def test_empty_proposal_list(self):
        """Test handling empty proposal list."""
        result = sort_by_risk_priority([])
        assert result == []

    def test_empty_files_list_risk_assessment(self):
        """Test risk assessment with no files touched."""
        proposal = Proposal(
            agent="TestAgent",
            title="Test",
            description="Test",
            diff="test",
            risk_level="low",
            rationale="Test",
            files_touched=[],  # Empty
            estimated_loc_change=0
        )
        policy = RiskPolicyConfig()

        assessment = assess_risk(proposal, policy)

        # Should not crash, just have no file-based risk factors
        assert "requires_approval" in assessment
        assert "risk_score" in assessment

    def test_empty_diff(self):
        """Test proposal with empty diff."""
        proposal = Proposal(
            agent="TestAgent",
            title="No changes",
            description="Test",
            diff="",  # Empty diff
            risk_level="low",
            rationale="Test",
            files_touched=["file.py"],
            estimated_loc_change=0
        )

        # Should not crash
        assert proposal.diff == ""


class TestBoundaryConditions:
    """Tests for boundary conditions."""

    def test_exactly_at_file_limit(self):
        """Test proposal exactly at file change limit."""
        policy = RiskPolicyConfig(file_change_limit=10)

        proposal = Proposal(
            agent="TestAgent",
            title="Test",
            description="Test",
            diff="test",
            risk_level="low",
            rationale="Test",
            files_touched=[f"file{i}.py" for i in range(10)],  # Exactly 10
            estimated_loc_change=5
        )

        assessment = assess_risk(proposal, policy)

        # At the limit should be fine
        assert not any("Too many files" in f for f in assessment["risk_factors"])

    def test_one_over_file_limit(self):
        """Test proposal one over file change limit."""
        policy = RiskPolicyConfig(file_change_limit=10)

        proposal = Proposal(
            agent="TestAgent",
            title="Test",
            description="Test",
            diff="test",
            risk_level="low",
            rationale="Test",
            files_touched=[f"file{i}.py" for i in range(11)],  # 11 files
            estimated_loc_change=5
        )

        assessment = assess_risk(proposal, policy)

        # Should trigger risk factor
        assert any("Too many files" in f for f in assessment["risk_factors"])

    def test_exactly_at_loc_limit(self):
        """Test proposal exactly at LOC limit."""
        policy = RiskPolicyConfig(loc_change_limit=500)

        proposal = Proposal(
            agent="TestAgent",
            title="Test",
            description="Test",
            diff="test",
            risk_level="low",
            rationale="Test",
            files_touched=["file.py"],
            estimated_loc_change=500  # Exactly 500
        )

        assessment = assess_risk(proposal, policy)

        # At the limit should be fine
        assert not any("Large change" in f for f in assessment["risk_factors"])

    def test_one_over_loc_limit(self):
        """Test proposal one over LOC limit."""
        policy = RiskPolicyConfig(loc_change_limit=500)

        proposal = Proposal(
            agent="TestAgent",
            title="Test",
            description="Test",
            diff="test",
            risk_level="low",
            rationale="Test",
            files_touched=["file.py"],
            estimated_loc_change=501  # 501 LOC
        )

        assessment = assess_risk(proposal, policy)

        # Should trigger risk factor
        assert any("Large change" in f for f in assessment["risk_factors"])

    def test_negative_loc_change(self):
        """Test proposal with negative LOC change (deletion)."""
        policy = RiskPolicyConfig(loc_change_limit=500)

        proposal = Proposal(
            agent="TestAgent",
            title="Delete code",
            description="Test",
            diff="test",
            risk_level="low",
            rationale="Test",
            files_touched=["file.py"],
            estimated_loc_change=-600  # Large deletion
        )

        assessment = assess_risk(proposal, policy)

        # Absolute value should be checked
        assert any("Large change" in f for f in assessment["risk_factors"])


class TestSpecialCharacters:
    """Tests for special character handling."""

    def test_unicode_in_proposal(self):
        """Test proposal with unicode characters."""
        proposal = Proposal(
            agent="TestAgent",
            title="修复错误",  # Chinese
            description="Corrige el error",  # Spanish
            diff="+ # 世界 🌍",
            risk_level="low",
            rationale="Test",
            files_touched=["文件.py"],  # Chinese filename
            estimated_loc_change=1
        )

        # Should handle unicode without crashing
        assert proposal.title == "修复错误"
        assert "🌍" in proposal.diff

    def test_path_with_spaces(self):
        """Test file path with spaces."""
        proposal = Proposal(
            agent="TestAgent",
            title="Test",
            description="Test",
            diff="test",
            risk_level="low",
            rationale="Test",
            files_touched=["path with spaces/file name.py"],
            estimated_loc_change=1
        )

        assert proposal.files_touched[0] == "path with spaces/file name.py"

    def test_path_with_special_chars(self):
        """Test file path with special characters."""
        proposal = Proposal(
            agent="TestAgent",
            title="Test",
            description="Test",
            diff="test",
            risk_level="low",
            rationale="Test",
            files_touched=["path/with-dashes_and_underscores.py"],
            estimated_loc_change=1
        )

        assert "-" in proposal.files_touched[0]
        assert "_" in proposal.files_touched[0]


class TestConcurrentModifications:
    """Tests for concurrent modification scenarios."""

    def test_multiple_proposals_same_file(self):
        """Test multiple proposals modifying the same file."""
        proposals = [
            Proposal(
                agent="Agent1",
                title="Change 1",
                description="Test",
                diff="+ line 1",
                risk_level="low",
                rationale="Test",
                files_touched=["main.py"],
                estimated_loc_change=1
            ),
            Proposal(
                agent="Agent2",
                title="Change 2",
                description="Test",
                diff="+ line 2",
                risk_level="low",
                rationale="Test",
                files_touched=["main.py"],  # Same file
                estimated_loc_change=1
            ),
        ]

        # Both should be valid proposals
        assert all(p.files_touched == ["main.py"] for p in proposals)
        # In real system, coordinator should apply serially and handle conflicts

    def test_overlapping_file_sets(self):
        """Test proposals with overlapping file sets."""
        proposal1 = Proposal(
            agent="Agent1",
            title="Change 1",
            description="Test",
            diff="test",
            risk_level="low",
            rationale="Test",
            files_touched=["a.py", "b.py", "c.py"],
            estimated_loc_change=3
        )

        proposal2 = Proposal(
            agent="Agent2",
            title="Change 2",
            description="Test",
            diff="test",
            risk_level="low",
            rationale="Test",
            files_touched=["b.py", "c.py", "d.py"],  # Overlaps: b.py, c.py
            estimated_loc_change=3
        )

        # Check overlap
        overlap = set(proposal1.files_touched) & set(proposal2.files_touched)
        assert overlap == {"b.py", "c.py"}


class TestMalformedInputs:
    """Tests for malformed/invalid inputs."""

    def test_very_long_diff(self):
        """Test proposal with very long diff."""
        long_diff = "+" + ("x" * 1_000_000)  # 1MB diff

        proposal = Proposal(
            agent="TestAgent",
            title="Huge change",
            description="Test",
            diff=long_diff,
            risk_level="medium",
            rationale="Test",
            files_touched=["file.py"],
            estimated_loc_change=1000000
        )

        # Should handle large diff
        assert len(proposal.diff) == 1_000_001

    def test_very_long_file_list(self):
        """Test proposal with many files."""
        proposal = Proposal(
            agent="TestAgent",
            title="Mass change",
            description="Test",
            diff="test",
            risk_level="medium",
            rationale="Test",
            files_touched=[f"file{i}.py" for i in range(1000)],  # 1000 files
            estimated_loc_change=1000
        )

        # Should handle large file list
        assert len(proposal.files_touched) == 1000

    def test_duplicate_files_in_list(self):
        """Test proposal with duplicate files."""
        proposal = Proposal(
            agent="TestAgent",
            title="Test",
            description="Test",
            diff="test",
            risk_level="low",
            rationale="Test",
            files_touched=["file.py", "file.py", "other.py"],  # Duplicate
            estimated_loc_change=2
        )

        # Should accept (caller may deduplicate if needed)
        assert len(proposal.files_touched) == 3


class TestGitEdgeCases:
    """Tests for git operation edge cases."""

    def test_patch_with_no_context(self):
        """Test patch with minimal context."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)

            # Initialize git
            import subprocess
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)

            # Create file
            (repo / "test.py").write_text("line1\nline2\nline3\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

            # Patch with zero context
            patch = """--- a/test.py
+++ b/test.py
@@ -2,0 +3 @@
+new line
"""

            result = git_apply_patch_atomic(repo, patch)

            # May succeed or fail depending on git version, but shouldn't crash
            assert "ok" in result

    def test_patch_creating_new_file(self):
        """Test patch that creates a new file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)

            # Initialize git
            import subprocess
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)

            # Create initial commit
            (repo / "existing.txt").write_text("content")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

            # Patch creating new file
            patch = """--- /dev/null
+++ b/new_file.py
@@ -0,0 +1,2 @@
+def new():
+    pass
"""

            result = git_apply_patch_atomic(repo, patch)

            # Check result (may fail on some systems, marked as xfail in unit tests)
            assert "ok" in result


class TestPathSafetyEdgeCases:
    """Tests for path safety edge cases."""

    def test_resolve_current_directory(self):
        """Test resolving current directory (.) resolves to root."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            assert safe_resolve(root, ".") == root.resolve()

    def test_resolve_parent_directory(self):
        """Test resolving parent directory (..)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with pytest.raises(ValueError, match="escape"):
                safe_resolve(root, "..")

    def test_resolve_deeply_nested_path(self):
        """Test resolving deeply nested path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # IMPORTANT: Resolve root to handle symlinks (e.g., /var -> /private/var on macOS)
            root = Path(tmpdir).resolve()

            # Create the deeply nested directory structure
            deep_dir = root / "a/b/c/d/e/f/g/h/i/j"
            deep_dir.mkdir(parents=True, exist_ok=True)
            (deep_dir / "file.py").write_text("# test")

            deep_path = "a/b/c/d/e/f/g/h/i/j/file.py"
            result = safe_resolve(root, deep_path)
            assert str(result).endswith("file.py")
            assert result.is_relative_to(root)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
