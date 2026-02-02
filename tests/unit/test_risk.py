"""Unit tests for risk assessment."""

import pytest
from pathlib import Path

from src.ambient.risk import (
    assess_risk,
    requires_approval,
    sort_by_risk_priority,
    filter_by_policy,
    generate_risk_report,
    _check_sensitive_files,
)
from src.ambient.types import Proposal
from src.ambient.config import RiskPolicyConfig


class TestAssessRisk:
    """Tests for assess_risk function."""

    def test_low_risk_proposal(self):
        """Test low risk proposal with no risk factors."""
        proposal = Proposal(
            agent="StyleEnforcer",
            title="Add docstring",
            description="Add missing docstring",
            diff="+ # docstring",
            risk_level="low",
            rationale="Improves documentation",
            files_touched=["utils.py"],
            estimated_loc_change=3,
            tags=["style"],
        )
        policy = RiskPolicyConfig()

        assessment = assess_risk(proposal, policy)

        assert not assessment["requires_approval"]
        assert assessment["auto_apply_eligible"]
        assert assessment["risk_score"] == 0
        assert len(assessment["risk_factors"]) == 0

    def test_high_risk_level_triggers_approval(self):
        """Test that high risk level triggers approval requirement."""
        proposal = Proposal(
            agent="SecurityGuardian",
            title="Fix vulnerability",
            description="Patch security issue",
            diff="- vulnerable code\n+ secure code",
            risk_level="high",
            rationale="Security fix",
            files_touched=["auth.py"],
            estimated_loc_change=5,
            tags=["security"],
        )
        policy = RiskPolicyConfig()

        assessment = assess_risk(proposal, policy)

        assert assessment["requires_approval"]
        assert not assessment["auto_apply_eligible"]
        assert "Risk level: high" in assessment["risk_factors"]
        assert "High-risk tags: security" in assessment["risk_factors"]

    def test_too_many_files_triggers_approval(self):
        """Test that exceeding file change limit triggers approval."""
        proposal = Proposal(
            agent="RefactorArchitect",
            title="Refactor module",
            description="Large refactor",
            diff="diff content",
            risk_level="medium",
            rationale="Improve structure",
            files_touched=[f"file{i}.py" for i in range(15)],  # More than limit (10)
            estimated_loc_change=50,
            tags=["refactor"],
        )
        policy = RiskPolicyConfig(file_change_limit=10)

        assessment = assess_risk(proposal, policy)

        assert assessment["requires_approval"]
        assert any("Too many files" in factor for factor in assessment["risk_factors"])

    def test_large_loc_change_triggers_approval(self):
        """Test that large LOC change triggers approval."""
        proposal = Proposal(
            agent="RefactorArchitect",
            title="Large change",
            description="Significant rewrite",
            diff="large diff",
            risk_level="medium",
            rationale="Modernize code",
            files_touched=["main.py"],
            estimated_loc_change=600,  # More than limit (500)
            tags=["refactor"],
        )
        policy = RiskPolicyConfig(loc_change_limit=500)

        assessment = assess_risk(proposal, policy)

        assert assessment["requires_approval"]
        assert any("Large change" in factor for factor in assessment["risk_factors"])

    def test_sensitive_files_trigger_approval(self):
        """Test that sensitive files trigger approval."""
        proposal = Proposal(
            agent="SecurityGuardian",
            title="Update credentials",
            description="Rotate API keys",
            diff="secret change",
            risk_level="critical",
            rationale="Security requirement",
            files_touched=["config/secrets.yml"],
            estimated_loc_change=2,
            tags=["security"],
        )
        policy = RiskPolicyConfig()

        assessment = assess_risk(proposal, policy)

        assert assessment["requires_approval"]
        assert any("Sensitive files" in factor for factor in assessment["risk_factors"])

    def test_high_risk_tags_trigger_approval(self):
        """Test that high-risk tags trigger approval."""
        for tag in ["security", "auth", "payment", "database"]:
            proposal = Proposal(
                agent="TestAgent",
                title="Change",
                description="Update",
                diff="diff",
                risk_level="low",
                rationale="Test",
                files_touched=["file.py"],
                estimated_loc_change=1,
                tags=[tag],
            )
            policy = RiskPolicyConfig()

            assessment = assess_risk(proposal, policy)

            assert assessment["requires_approval"]
            assert any("High-risk tags" in factor for factor in assessment["risk_factors"])

    def test_multiple_risk_factors(self):
        """Test proposal with multiple risk factors."""
        proposal = Proposal(
            agent="SecurityGuardian",
            title="Major security update",
            description="Critical changes",
            diff="large diff",
            risk_level="critical",
            rationale="Security",
            files_touched=[f"auth_{i}.py" for i in range(12)],  # Too many files
            estimated_loc_change=700,  # Too large
            tags=["security", "auth"],
        )
        policy = RiskPolicyConfig()

        assessment = assess_risk(proposal, policy)

        assert assessment["requires_approval"]
        assert assessment["risk_score"] >= 4  # Multiple factors
        assert len(assessment["risk_factors"]) >= 4


class TestRequiresApproval:
    """Tests for requires_approval function."""

    def test_requires_approval_true(self):
        """Test proposal that requires approval."""
        proposal = Proposal(
            agent="SecurityGuardian",
            title="Fix",
            description="Fix",
            diff="diff",
            risk_level="high",
            rationale="Fix",
            files_touched=["auth.py"],
            estimated_loc_change=5,
            tags=["security"],
        )
        policy = RiskPolicyConfig()

        assert requires_approval(proposal, policy)

    def test_requires_approval_false(self):
        """Test proposal that doesn't require approval."""
        proposal = Proposal(
            agent="StyleEnforcer",
            title="Format code",
            description="Auto-format",
            diff="formatting",
            risk_level="low",
            rationale="Style",
            files_touched=["utils.py"],
            estimated_loc_change=2,
            tags=["style"],
        )
        policy = RiskPolicyConfig()

        assert not requires_approval(proposal, policy)


class TestCheckSensitiveFiles:
    """Tests for _check_sensitive_files function."""

    @pytest.mark.parametrize("filename,expected", [
        ("config/.env", True),
        ("secrets.yml", True),
        ("password_manager.py", True),
        ("credentials.json", True),
        ("api_key.txt", True),
        ("private_key.pem", True),
        ("auth_handler.py", True),
        ("payment_processor.py", True),
        ("database_config.py", True),
        ("normal_file.py", False),
        ("test_file.py", False),
        ("README.md", False),
    ])
    def test_sensitive_file_patterns(self, filename, expected):
        """Test detection of sensitive file patterns."""
        files = [filename]
        result = _check_sensitive_files(files)

        if expected:
            assert len(result) == 1
            assert result[0] == filename
        else:
            assert len(result) == 0

    def test_multiple_files_mixed(self):
        """Test checking multiple files with mixed sensitivity."""
        files = [
            "utils.py",  # Not sensitive
            "secrets.yml",  # Sensitive
            "test.py",  # Not sensitive
            "auth.py",  # Sensitive
        ]
        result = _check_sensitive_files(files)

        assert len(result) == 2
        assert "secrets.yml" in result
        assert "auth.py" in result


class TestSortByRiskPriority:
    """Tests for sort_by_risk_priority function."""

    def test_sort_proposals_by_risk(self):
        """Test sorting proposals by risk level."""
        proposals = [
            Proposal(
                agent="A", title="Low", description="", diff="",
                risk_level="low", rationale="", files_touched=[], estimated_loc_change=1
            ),
            Proposal(
                agent="B", title="Critical", description="", diff="",
                risk_level="critical", rationale="", files_touched=[], estimated_loc_change=1
            ),
            Proposal(
                agent="C", title="Medium", description="", diff="",
                risk_level="medium", rationale="", files_touched=[], estimated_loc_change=1
            ),
            Proposal(
                agent="D", title="High", description="", diff="",
                risk_level="high", rationale="", files_touched=[], estimated_loc_change=1
            ),
        ]

        sorted_proposals = sort_by_risk_priority(proposals)

        # Should be: critical, high, medium, low
        assert sorted_proposals[0].risk_level == "critical"
        assert sorted_proposals[1].risk_level == "high"
        assert sorted_proposals[2].risk_level == "medium"
        assert sorted_proposals[3].risk_level == "low"

    def test_sort_empty_list(self):
        """Test sorting empty proposal list."""
        result = sort_by_risk_priority([])
        assert result == []


class TestFilterByPolicy:
    """Tests for filter_by_policy function."""

    def test_filter_auto_apply_only(self):
        """Test filtering for auto-apply eligible proposals."""
        proposals = [
            Proposal(
                agent="A", title="Low", description="", diff="",
                risk_level="low", rationale="", files_touched=["a.py"], estimated_loc_change=1
            ),
            Proposal(
                agent="B", title="High", description="", diff="",
                risk_level="high", rationale="", files_touched=["b.py"], estimated_loc_change=1
            ),
            Proposal(
                agent="C", title="Medium", description="", diff="",
                risk_level="medium", rationale="", files_touched=["c.py"], estimated_loc_change=1
            ),
        ]
        policy = RiskPolicyConfig(
            auto_apply=["low", "medium"],
            require_approval=["high", "critical"],
        )

        filtered = filter_by_policy(proposals, policy, auto_apply_only=True)

        # Only low and medium should pass (if they have no other risk factors)
        assert len(filtered) == 2
        assert all(p.risk_level in ["low", "medium"] for p in filtered)

    def test_filter_all_proposals(self):
        """Test filtering without auto_apply_only (returns all)."""
        proposals = [
            Proposal(
                agent="A", title="Low", description="", diff="",
                risk_level="low", rationale="", files_touched=["a.py"], estimated_loc_change=1
            ),
            Proposal(
                agent="B", title="High", description="", diff="",
                risk_level="high", rationale="", files_touched=["b.py"], estimated_loc_change=1
            ),
        ]
        policy = RiskPolicyConfig()

        filtered = filter_by_policy(proposals, policy, auto_apply_only=False)

        assert len(filtered) == 2  # All proposals included


class TestGenerateRiskReport:
    """Tests for generate_risk_report function."""

    def test_generate_risk_report(self):
        """Test generating risk report."""
        proposal = Proposal(
            agent="SecurityGuardian",
            title="Fix vulnerability",
            description="Patch XSS",
            diff="security fix",
            risk_level="critical",
            rationale="Prevents attacks",
            files_touched=["auth.py", "validator.py"],
            estimated_loc_change=10,
            tags=["security"],
        )
        assessment = {
            "requires_approval": True,
            "risk_factors": ["Risk level: critical", "High-risk tags: security"],
            "auto_apply_eligible": False,
            "risk_score": 2,
        }

        report = generate_risk_report(proposal, assessment)

        assert "Fix vulnerability" in report
        assert "SecurityGuardian" in report
        assert "critical" in report
        assert "2" in report  # Files touched count
        assert "+10" in report  # LOC change
        assert "Requires Approval: True" in report
        assert "Risk level: critical" in report

    def test_generate_risk_report_no_factors(self):
        """Test generating risk report with no risk factors."""
        proposal = Proposal(
            agent="StyleEnforcer",
            title="Format code",
            description="Auto-format",
            diff="formatting",
            risk_level="low",
            rationale="Style",
            files_touched=["utils.py"],
            estimated_loc_change=5,
            tags=["style"],
        )
        assessment = {
            "requires_approval": False,
            "risk_factors": [],
            "auto_apply_eligible": True,
            "risk_score": 0,
        }

        report = generate_risk_report(proposal, assessment)

        assert "Format code" in report
        assert "StyleEnforcer" in report
        assert "No risk factors identified" in report
        assert "Requires Approval: False" in report


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
