"""Core data types for Ambient Swarm system."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Proposal:
    """A proposed code change from a specialist agent."""

    agent: str
    title: str
    description: str
    diff: str  # Unified diff format
    risk_level: str  # "low", "medium", "high", "critical"
    rationale: str  # Why this change improves code quality
    files_touched: list[str]
    estimated_loc_change: int
    tags: list[str] = field(default_factory=list)  # ["security", "refactor", "style", etc.]

    def __post_init__(self) -> None:
        """Validate proposal fields."""
        valid_risk_levels = {"low", "medium", "high", "critical"}
        if self.risk_level not in valid_risk_levels:
            raise ValueError(
                f"Invalid risk_level: {self.risk_level}. "
                f"Must be one of {valid_risk_levels}"
            )


@dataclass
class RepoContext:
    """Full repository context provided to agents."""

    task: dict[str, Any]
    tree: dict[str, Any]  # File tree structure
    important_files: dict[str, str]  # Config files and their contents
    failing_logs: str
    current_diff: str
    hot_paths: list[str] = field(default_factory=list)  # Files mentioned in errors
    conventions: dict[str, Any] = field(default_factory=dict)  # Extracted conventions


@dataclass
class AmbientEvent:
    """An event that triggers agent analysis."""

    type: str  # "file_change", "ci_failure", "periodic_scan", "manual_trigger"
    data: dict[str, Any]  # Event-specific data
    task_spec: dict[str, Any] = field(default_factory=dict)  # Task specification

    @property
    def is_file_change(self) -> bool:
        return self.type == "file_change"

    @property
    def is_ci_failure(self) -> bool:
        return self.type == "ci_failure"

    @property
    def is_periodic_scan(self) -> bool:
        return self.type == "periodic_scan"


@dataclass
class VerificationResult:
    """Result of verification checks after patch application."""

    ok: bool
    results: list[dict[str, Any]]  # Individual check results
    duration_s: float = 0.0

    @property
    def all_passed(self) -> bool:
        """Check if all verification checks passed."""
        return self.ok and all(r.get("ok", False) for r in self.results)


@dataclass
class ApplyResult:
    """Result of patch application."""

    ok: bool
    stat: str  # Git diff stat
    stderr: str
    debug_bundle: dict[str, Any] = field(default_factory=dict)
