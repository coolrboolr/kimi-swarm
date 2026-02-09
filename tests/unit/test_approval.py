"""Unit tests for approval handlers."""

import pytest

from ambient.approval import (
    ApprovalHandler,
    AlwaysApproveHandler,
    AlwaysRejectHandler,
    WebhookApprovalHandler,
)
from ambient.types import Proposal
from ambient.config import RiskPolicyConfig


@pytest.fixture
def sample_proposal():
    """Create a sample proposal for testing."""
    return Proposal(
        agent="TestAgent",
        title="Test change",
        description="Test description",
        diff="--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new",
        risk_level="medium",
        rationale="Test rationale",
        files_touched=["file.py"],
        estimated_loc_change=2,
        tags=["test"],
    )


@pytest.fixture
def sample_assessment():
    """Create a sample risk assessment."""
    return {
        "requires_approval": True,
        "risk_factors": ["Risk level: medium"],
        "auto_apply_eligible": False,
        "risk_score": 1,
    }


class TestApprovalHandler:
    """Tests for base ApprovalHandler."""

    @pytest.mark.asyncio
    async def test_non_interactive_mode(self, sample_proposal, sample_assessment):
        """Test non-interactive mode auto-rejects."""
        policy = RiskPolicyConfig()
        handler = ApprovalHandler(policy, interactive=False)

        approved = await handler.request_approval(sample_proposal, sample_assessment)

        assert not approved

    @pytest.mark.asyncio
    async def test_request_approval_without_assessment(self, sample_proposal):
        """Test requesting approval without pre-computed assessment."""
        policy = RiskPolicyConfig()
        handler = ApprovalHandler(policy, interactive=False)

        # Should compute assessment internally
        approved = await handler.request_approval(sample_proposal)

        assert not approved  # Non-interactive always rejects

    def test_handler_initialization(self):
        """Test handler initialization."""
        policy = RiskPolicyConfig()
        handler = ApprovalHandler(policy, interactive=True)

        assert handler.policy == policy
        assert handler.interactive is True


class TestAlwaysApproveHandler:
    """Tests for AlwaysApproveHandler."""

    @pytest.mark.asyncio
    async def test_always_approves(self, sample_proposal, sample_assessment):
        """Test that handler always approves."""
        policy = RiskPolicyConfig()
        handler = AlwaysApproveHandler(policy)

        approved = await handler.request_approval(sample_proposal, sample_assessment)

        assert approved

    @pytest.mark.asyncio
    async def test_approves_without_assessment(self, sample_proposal):
        """Test approval without pre-computed assessment."""
        policy = RiskPolicyConfig()
        handler = AlwaysApproveHandler(policy)

        approved = await handler.request_approval(sample_proposal)

        assert approved

    @pytest.mark.asyncio
    async def test_approves_high_risk(self):
        """Test that even high-risk proposals are approved."""
        policy = RiskPolicyConfig()
        handler = AlwaysApproveHandler(policy)

        high_risk_proposal = Proposal(
            agent="SecurityGuardian",
            title="Critical change",
            description="Dangerous change",
            diff="risky diff",
            risk_level="critical",
            rationale="Test",
            files_touched=["auth.py", "payment.py"],
            estimated_loc_change=100,
            tags=["security", "payment"],
        )

        approved = await handler.request_approval(high_risk_proposal)

        assert approved


class TestAlwaysRejectHandler:
    """Tests for AlwaysRejectHandler."""

    @pytest.mark.asyncio
    async def test_always_rejects(self, sample_proposal, sample_assessment):
        """Test that handler always rejects."""
        policy = RiskPolicyConfig()
        handler = AlwaysRejectHandler(policy)

        approved = await handler.request_approval(sample_proposal, sample_assessment)

        assert not approved

    @pytest.mark.asyncio
    async def test_rejects_without_assessment(self, sample_proposal):
        """Test rejection without pre-computed assessment."""
        policy = RiskPolicyConfig()
        handler = AlwaysRejectHandler(policy)

        approved = await handler.request_approval(sample_proposal)

        assert not approved

    @pytest.mark.asyncio
    async def test_rejects_low_risk(self):
        """Test that even low-risk proposals are rejected."""
        policy = RiskPolicyConfig()
        handler = AlwaysRejectHandler(policy)

        low_risk_proposal = Proposal(
            agent="StyleEnforcer",
            title="Format code",
            description="Safe change",
            diff="formatting",
            risk_level="low",
            rationale="Style",
            files_touched=["utils.py"],
            estimated_loc_change=1,
            tags=["style"],
        )

        approved = await handler.request_approval(low_risk_proposal)

        assert not approved

    @pytest.mark.asyncio
    async def test_dry_run_use_case(self):
        """Test typical dry-run use case (always reject)."""
        policy = RiskPolicyConfig()
        handler = AlwaysRejectHandler(policy)

        # Multiple proposals of varying risk
        proposals = [
            Proposal(
                agent="A", title="Low", description="", diff="",
                risk_level="low", rationale="", files_touched=["a.py"], estimated_loc_change=1
            ),
            Proposal(
                agent="B", title="Critical", description="", diff="",
                risk_level="critical", rationale="", files_touched=["b.py"], estimated_loc_change=1
            ),
        ]

        # All should be rejected
        for proposal in proposals:
                approved = await handler.request_approval(proposal)
                assert not approved


class TestWebhookApprovalHandler:
    """Tests for webhook approval handler (synchronous decision)."""

    @pytest.mark.asyncio
    async def test_webhook_approves(self, sample_proposal, sample_assessment, monkeypatch):
        class DummyResponse:
            status_code = 200

            def json(self):
                return {"approved": True, "reason": "ok"}

        class DummyClient:
            def __init__(self, timeout):  # noqa: D401
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, json, headers=None):  # noqa: ARG002
                return DummyResponse()

        import ambient.approval as approval_mod

        monkeypatch.setattr(approval_mod.httpx, "AsyncClient", DummyClient)

        handler = WebhookApprovalHandler(
            RiskPolicyConfig(),
            webhook_url="https://example.test/approve",
            headers={"X-Test": "1"},
            timeout_seconds=5,
        )

        assert await handler.request_approval(sample_proposal, sample_assessment) is True

    @pytest.mark.asyncio
    async def test_webhook_fail_closed_on_error(self, sample_proposal, sample_assessment, monkeypatch):
        class DummyClient:
            def __init__(self, timeout):  # noqa: D401,ARG002
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, json, headers=None):  # noqa: ARG002
                raise RuntimeError("boom")

        import ambient.approval as approval_mod

        monkeypatch.setattr(approval_mod.httpx, "AsyncClient", DummyClient)

        handler = WebhookApprovalHandler(
            RiskPolicyConfig(),
            webhook_url="https://example.test/approve",
            timeout_seconds=1,
        )

        assert await handler.request_approval(sample_proposal, sample_assessment) is False


class TestApprovalHandlerInheritance:
    """Tests for approval handler inheritance."""

    @pytest.mark.asyncio
    async def test_always_approve_inherits_from_base(self):
        """Test that AlwaysApproveHandler inherits correctly."""
        policy = RiskPolicyConfig()
        handler = AlwaysApproveHandler(policy)

        assert isinstance(handler, ApprovalHandler)
        assert handler.policy == policy
        assert handler.interactive is False  # Set by subclass

    @pytest.mark.asyncio
    async def test_always_reject_inherits_from_base(self):
        """Test that AlwaysRejectHandler inherits correctly."""
        policy = RiskPolicyConfig()
        handler = AlwaysRejectHandler(policy)

        assert isinstance(handler, ApprovalHandler)
        assert handler.policy == policy
        assert handler.interactive is False  # Set by subclass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
