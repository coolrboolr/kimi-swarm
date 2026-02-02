"""Approval flow for high-risk proposals.

Provides CLI-based approval prompts and webhook support for external approval systems.
"""

from __future__ import annotations

import sys
from typing import Any

from .types import Proposal
from .risk import assess_risk, generate_risk_report
from .config import RiskPolicyConfig


class ApprovalHandler:
    """Handles approval requests for high-risk proposals."""

    def __init__(self, policy: RiskPolicyConfig, interactive: bool = True):
        """
        Initialize approval handler.

        Args:
            policy: Risk policy configuration
            interactive: If True, use CLI prompts; if False, auto-reject
        """
        self.policy = policy
        self.interactive = interactive

    async def request_approval(
        self,
        proposal: Proposal,
        assessment: dict[str, Any] | None = None,
    ) -> bool:
        """
        Request approval for a proposal.

        Args:
            proposal: Proposal requiring approval
            assessment: Optional pre-computed risk assessment

        Returns:
            True if approved, False if rejected
        """
        if assessment is None:
            assessment = assess_risk(proposal, self.policy)

        if self.interactive:
            return self._cli_prompt(proposal, assessment)
        else:
            # Non-interactive mode: auto-reject
            return False

    def _cli_prompt(
        self,
        proposal: Proposal,
        assessment: dict[str, Any],
    ) -> bool:
        """
        Show CLI prompt for approval.

        Args:
            proposal: Proposal to approve/reject
            assessment: Risk assessment

        Returns:
            True if approved
        """
        # Print risk report
        print("\n" + "=" * 60)
        print("APPROVAL REQUIRED")
        print("=" * 60)
        print()
        print(generate_risk_report(proposal, assessment))
        print()
        print("Proposal Details:")
        print(f"  Title: {proposal.title}")
        print(f"  Description: {proposal.description}")
        print(f"  Rationale: {proposal.rationale}")
        print()
        print("Files to be modified:")
        for file_path in proposal.files_touched:
            print(f"  - {file_path}")
        print()

        # Show diff preview (first 50 lines)
        diff_lines = proposal.diff.split("\n")
        if len(diff_lines) > 50:
            print("Diff (first 50 lines):")
            print("\n".join(diff_lines[:50]))
            print(f"  ... ({len(diff_lines) - 50} more lines)")
        else:
            print("Diff:")
            print(proposal.diff)
        print()

        # Prompt for approval
        while True:
            response = input("Approve this change? [y/N/d(iff)/q(uit)]: ").strip().lower()

            if response in ["y", "yes"]:
                print("✓ Approved")
                return True
            elif response in ["n", "no", ""]:
                print("✗ Rejected")
                return False
            elif response in ["d", "diff"]:
                # Show full diff
                print("\nFull diff:")
                print(proposal.diff)
                print()
                continue
            elif response in ["q", "quit"]:
                print("Exiting approval process")
                sys.exit(0)
            else:
                print("Invalid response. Please enter y(es), n(o), d(iff), or q(uit).")


class WebhookApprovalHandler(ApprovalHandler):
    """
    Approval handler that sends requests to external webhook.

    For integration with Slack, Discord, GitHub, etc.
    """

    def __init__(
        self,
        policy: RiskPolicyConfig,
        webhook_url: str,
        timeout_seconds: int = 300,
    ):
        """
        Initialize webhook approval handler.

        Args:
            policy: Risk policy configuration
            webhook_url: URL to POST approval requests to
            timeout_seconds: How long to wait for response
        """
        super().__init__(policy, interactive=False)
        self.webhook_url = webhook_url
        self.timeout_seconds = timeout_seconds

    async def request_approval(
        self,
        proposal: Proposal,
        assessment: dict[str, Any] | None = None,
    ) -> bool:
        """
        Send approval request to webhook.

        Args:
            proposal: Proposal requiring approval
            assessment: Optional pre-computed risk assessment

        Returns:
            True if approved by webhook
        """
        if assessment is None:
            assessment = assess_risk(proposal, self.policy)

        # TODO: Implement webhook integration
        # For now, return False (reject)
        return False


class AlwaysApproveHandler(ApprovalHandler):
    """Approval handler that always approves (for testing/CI)."""

    def __init__(self, policy: RiskPolicyConfig):
        super().__init__(policy, interactive=False)

    async def request_approval(
        self,
        proposal: Proposal,
        assessment: dict[str, Any] | None = None,
    ) -> bool:
        """Always approve."""
        return True


class AlwaysRejectHandler(ApprovalHandler):
    """Approval handler that always rejects (for dry-run mode)."""

    def __init__(self, policy: RiskPolicyConfig):
        super().__init__(policy, interactive=False)

    async def request_approval(
        self,
        proposal: Proposal,
        assessment: dict[str, Any] | None = None,
    ) -> bool:
        """Always reject."""
        return False
