"""Risk assessment and policy enforcement for proposals.

Determines whether proposals require human approval based on:
- Risk level (critical, high, medium, low)
- Tags (security, auth, payment, etc.)
- Scope (number of files, LOC changed)
- File patterns (sensitive files)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import RiskPolicyConfig
from .types import Proposal

# Sensitive file patterns that always require approval
SENSITIVE_FILE_PATTERNS = [
    ".env",
    "secret",
    "password",
    "credentials",
    "api_key",
    "private_key",
    "auth",
    "payment",
    "billing",
    "database",
    "config/production",
]


def assess_risk(
    proposal: Proposal,
    policy: RiskPolicyConfig,
    repo_path: Path | None = None,
) -> dict[str, Any]:
    """
    Assess risk level and determine if approval is required.

    Args:
        proposal: Proposal to assess
        policy: Risk policy configuration
        repo_path: Optional repository path for file analysis

    Returns:
        Dict with:
        - requires_approval: bool
        - risk_factors: list of identified risk factors
        - auto_apply_eligible: bool
    """
    risk_factors = []

    # Check risk level against policy
    risk_level_requires_approval = proposal.risk_level in policy.require_approval
    if risk_level_requires_approval:
        risk_factors.append(f"Risk level: {proposal.risk_level}")

    # Check file count
    if len(proposal.files_touched) > policy.file_change_limit:
        risk_factors.append(
            f"Too many files: {len(proposal.files_touched)} > {policy.file_change_limit}"
        )

    # Check LOC change
    if abs(proposal.estimated_loc_change) > policy.loc_change_limit:
        risk_factors.append(
            f"Large change: {abs(proposal.estimated_loc_change)} LOC > {policy.loc_change_limit}"
        )

    # Check for sensitive file patterns
    sensitive_files = _check_sensitive_files(proposal.files_touched)
    if sensitive_files:
        risk_factors.append(f"Sensitive files: {', '.join(sensitive_files)}")

    # Check tags for high-risk operations
    high_risk_tags = ["security", "auth", "authentication", "payment", "billing", "database"]
    risky_tags = [tag for tag in proposal.tags if tag.lower() in high_risk_tags]
    if risky_tags:
        risk_factors.append(f"High-risk tags: {', '.join(risky_tags)}")

    # Determine if approval required
    requires_approval = len(risk_factors) > 0

    # Determine if auto-apply eligible (no risk factors AND low/medium risk level)
    auto_apply_eligible = (
        not requires_approval and proposal.risk_level in policy.auto_apply
    )

    return {
        "requires_approval": requires_approval,
        "risk_factors": risk_factors,
        "auto_apply_eligible": auto_apply_eligible,
        "risk_score": len(risk_factors),
    }


def requires_approval(
    proposal: Proposal,
    policy: RiskPolicyConfig,
    repo_path: Path | None = None,
) -> bool:
    """
    Quick check if proposal requires approval.

    Args:
        proposal: Proposal to check
        policy: Risk policy configuration
        repo_path: Optional repository path

    Returns:
        True if approval required
    """
    assessment = assess_risk(proposal, policy, repo_path)
    return bool(assessment.get("requires_approval", False))


def _check_sensitive_files(files: list[str]) -> list[str]:
    """
    Check if any files match sensitive patterns.

    Args:
        files: List of file paths

    Returns:
        List of files matching sensitive patterns
    """
    sensitive = []
    for file_path in files:
        file_lower = file_path.lower()
        for pattern in SENSITIVE_FILE_PATTERNS:
            if pattern in file_lower:
                sensitive.append(file_path)
                break
    return sensitive


def sort_by_risk_priority(proposals: list[Proposal]) -> list[Proposal]:
    """
    Sort proposals by risk level (critical > high > medium > low).

    Apply high-priority/critical fixes first.

    Args:
        proposals: List of proposals to sort

    Returns:
        Sorted list (highest risk first)
    """
    risk_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(proposals, key=lambda p: risk_order.get(p.risk_level, 4))


def filter_by_policy(
    proposals: list[Proposal],
    policy: RiskPolicyConfig,
    auto_apply_only: bool = False,
) -> list[Proposal]:
    """
    Filter proposals based on policy.

    Args:
        proposals: List of proposals
        policy: Risk policy configuration
        auto_apply_only: If True, only return auto-apply eligible proposals

    Returns:
        Filtered list of proposals
    """
    filtered = []
    for proposal in proposals:
        assessment = assess_risk(proposal, policy)

        if auto_apply_only:
            if assessment["auto_apply_eligible"]:
                filtered.append(proposal)
        else:
            # Include all unless explicitly filtered by some criteria
            filtered.append(proposal)

    return filtered


def generate_risk_report(
    proposal: Proposal,
    assessment: dict[str, Any],
) -> str:
    """
    Generate human-readable risk report for a proposal.

    Args:
        proposal: Proposal to report on
        assessment: Risk assessment dict from assess_risk()

    Returns:
        Formatted risk report string
    """
    lines = []
    lines.append(f"Risk Assessment: {proposal.title}")
    lines.append("=" * 60)
    lines.append(f"Agent: {proposal.agent}")
    lines.append(f"Risk Level: {proposal.risk_level}")
    lines.append(f"Files Touched: {len(proposal.files_touched)}")
    lines.append(f"Estimated LOC Change: {proposal.estimated_loc_change:+d}")
    lines.append("")

    if assessment["risk_factors"]:
        lines.append("Risk Factors:")
        for factor in assessment["risk_factors"]:
            lines.append(f"  - {factor}")
    else:
        lines.append("No risk factors identified.")

    lines.append("")
    lines.append(f"Requires Approval: {assessment['requires_approval']}")
    lines.append(f"Auto-Apply Eligible: {assessment['auto_apply_eligible']}")
    lines.append(f"Risk Score: {assessment['risk_score']}")

    return "\n".join(lines)
