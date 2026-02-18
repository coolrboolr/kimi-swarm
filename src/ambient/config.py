"""Configuration schema for Ambient Swarm system.

Configuration is loaded from .ambient.yml in the repository root.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class KimiConfig(BaseModel):
    """Kimi K2.5 client configuration."""

    provider: str = "ollama"
    base_url: str = "http://localhost:11434/v1"
    model_id: str = "kimi-k2.5:cloud"
    max_concurrency: int = 8
    temperature: float = 0.2
    timeout_seconds: int = 300

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        valid_providers = {"ollama", "openai-compatible", "anthropic"}
        if v not in valid_providers:
            raise ValueError(f"Invalid provider: {v}. Must be one of {valid_providers}")
        return v


class MonitoringConfig(BaseModel):
    """File watching and monitoring configuration."""

    enabled: bool = True
    watch_paths: list[str] = Field(default_factory=lambda: ["src/", "tests/"])
    ignore_patterns: list[str] = Field(
        default_factory=lambda: ["*.pyc", "__pycache__", ".git"]
    )
    debounce_seconds: int = 5
    check_interval_seconds: int = 300
    max_queue_size: int = 1000


class AgentSettings(BaseModel):
    """Per-agent configuration settings."""

    enabled: bool = True
    settings: dict[str, Any] = Field(default_factory=dict)


class SecurityGuardianSettings(BaseModel):
    """SecurityGuardian agent settings."""

    severity_threshold: str = "medium"
    scan_dependencies: bool = True


class RefactorArchitectSettings(BaseModel):
    """RefactorArchitect agent settings."""

    complexity_threshold: int = 15
    max_function_lines: int = 100


class StyleEnforcerSettings(BaseModel):
    """StyleEnforcer agent settings."""

    defer_to_formatter: bool = True


class PerformanceOptimizerSettings(BaseModel):
    """PerformanceOptimizer agent settings."""

    min_speedup_percent: int = 10


class TestEnhancerSettings(BaseModel):
    """TestEnhancer agent settings."""

    coverage_threshold: int = 80
    prioritize_paths: list[str] = Field(default_factory=list)


class AgentsConfig(BaseModel):
    """Agents configuration."""

    enabled: list[str] = Field(
        default_factory=lambda: [
            "SecurityGuardian",
            "RefactorArchitect",
            "StyleEnforcer",
            "PerformanceOptimizer",
            "TestEnhancer",
        ]
    )
    SecurityGuardian: SecurityGuardianSettings = Field(
        default_factory=SecurityGuardianSettings
    )
    RefactorArchitect: RefactorArchitectSettings = Field(
        default_factory=RefactorArchitectSettings
    )
    StyleEnforcer: StyleEnforcerSettings = Field(default_factory=StyleEnforcerSettings)
    PerformanceOptimizer: PerformanceOptimizerSettings = Field(
        default_factory=PerformanceOptimizerSettings
    )
    TestEnhancer: TestEnhancerSettings = Field(default_factory=TestEnhancerSettings)


class RiskPolicyConfig(BaseModel):
    """Risk assessment and approval policy."""

    auto_apply: list[str] = Field(default_factory=lambda: ["low", "medium"])
    require_approval: list[str] = Field(default_factory=lambda: ["high", "critical"])
    file_change_limit: int = 10
    loc_change_limit: int = 500


class SandboxResourcesConfig(BaseModel):
    """Sandbox resource limits."""

    memory: str = "2g"
    cpus: str = "2.0"
    pids_limit: int = 100


class SandboxConfig(BaseModel):
    """Docker sandbox configuration."""

    image: str = "ambient-sandbox:latest"
    network_mode: str = "none"
    resources: SandboxResourcesConfig = Field(default_factory=SandboxResourcesConfig)
    require_docker: bool = True
    stub_mode: bool = False
    enforce_allowlist: bool = True
    # Prefer read-only repo mounts for verification.
    repo_mount_mode: str = "ro"

    # New allowlist: a list of argv prefixes. If argv begins with an entry, it is allowed
    # (extra args are permitted).
    allowed_argv: list[list[str]] = Field(
        default_factory=lambda: [
            ["pytest"],
            ["python", "-m", "pytest"],
            ["ruff", "check"],
            ["ruff", "format"],
            ["mypy"],
            ["flake8"],
            ["cargo", "test"],
            ["cargo", "check"],
            ["cargo", "clippy"],
            ["npm", "test"],
            ["make", "test"],
            ["make", "lint"],
            ["make", "check"],
            ["git", "status"],
            ["git", "diff"],
            ["git", "log"],
            ["git", "show"],
            ["git", "rev-parse"],
        ]
    )

    # Back-compat: legacy regex allowlist. This is validated against normalized argv
    # (shlex.join(argv)). Prefer allowed_argv.
    allowed_commands: list[str] = Field(default_factory=list)

    @field_validator("repo_mount_mode")
    @classmethod
    def validate_repo_mount_mode(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"ro", "rw"}:
            raise ValueError("repo_mount_mode must be 'ro' or 'rw'")
        return v

    def is_argv_allowed(self, argv: list[str]) -> bool:
        """Check if argv begins with any allowed prefix, or matches legacy regex patterns."""
        if any(argv[: len(p)] == p for p in self.allowed_argv if p):
            return True
        if self.allowed_commands:
            s = shlex.join(argv)
            return any(re.fullmatch(pattern, s.strip()) for pattern in self.allowed_commands)
        return False


class VerificationConfig(BaseModel):
    """Verification behavior for checks run in the sandbox."""

    timeout_seconds: int = 900


class GitConfig(BaseModel):
    """Git recording behavior after successful apply + verify."""

    commit_on_success: bool = False
    require_clean_before_apply: bool = True
    commit_message_template: str = "ambient: {title} ({agent})"
    commit_author_name: str = "Ambient Swarm"
    commit_author_email: str = "ambient@bot.local"


class ReviewWorktreeConfig(BaseModel):
    """Parallel review-worktree configuration for manual proposal curation."""

    enabled: bool = True
    base_dir: str = ".ambient/reviews"
    branch_prefix: str = "ambient/review"
    max_parallel: int = 4
    keep_worktrees: bool = True


class WebhookApprovalConfig(BaseModel):
    """Synchronous webhook approval configuration."""

    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = 300


class ApprovalConfig(BaseModel):
    """Approval configuration (CLI interactive and/or webhook)."""

    webhook: WebhookApprovalConfig = Field(default_factory=WebhookApprovalConfig)


class TelemetryConfig(BaseModel):
    """Telemetry and logging configuration."""

    enabled: bool = True
    log_path: str = ".ambient/telemetry.jsonl"
    include_diffs: bool = False
    retention_days: int = 30


class ControlPlaneConfig(BaseModel):
    """Operational safety controls and kill-switches."""

    paused: bool = False
    max_proposals_per_hour: int = 0  # 0 means unlimited
    failure_rate_window: int = 20
    disable_auto_apply_on_failure_rate: bool = True
    failure_rate_threshold: float = 0.5
    min_failures_before_disable: int = 3
    backoff_base_seconds: int = 30
    backoff_max_seconds: int = 600


class LearningConfig(BaseModel):
    """Learning and adaptation configuration (future feature)."""

    enabled: bool = False
    track_revert_rate: bool = True
    track_agent_success: bool = True


class AmbientConfig(BaseModel):
    """Complete ambient system configuration."""

    kimi: KimiConfig = Field(default_factory=KimiConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    risk_policy: RiskPolicyConfig = Field(default_factory=RiskPolicyConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    review_worktree: ReviewWorktreeConfig = Field(default_factory=ReviewWorktreeConfig)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    control_plane: ControlPlaneConfig = Field(default_factory=ControlPlaneConfig)
    learning: LearningConfig = Field(default_factory=LearningConfig)

    @classmethod
    def load_from_file(cls, config_path: Path | str) -> AmbientConfig:
        """Load configuration from YAML file."""
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path) as f:
            data = yaml.safe_load(f)

        return cls(**data)

    @classmethod
    def load_from_repo(cls, repo_path: Path | str) -> AmbientConfig:
        """Load configuration from repository's .ambient.yml."""
        repo_path = Path(repo_path)
        config_path = repo_path / ".ambient.yml"

        if not config_path.exists():
            # Return default configuration
            return cls()

        return cls.load_from_file(config_path)

    def apply_env_overrides(self) -> None:
        """Apply environment variable overrides to configuration."""
        # Kimi overrides
        if url := os.getenv("AMBIENT_KIMI_BASE_URL"):
            self.kimi.base_url = url
        if model := os.getenv("AMBIENT_KIMI_MODEL"):
            self.kimi.model_id = model
        if temp := os.getenv("AMBIENT_KIMI_TEMPERATURE"):
            self.kimi.temperature = float(temp)

        # Sandbox overrides
        if image := os.getenv("AMBIENT_SANDBOX_IMAGE"):
            self.sandbox.image = image
        if network := os.getenv("AMBIENT_SANDBOX_NETWORK"):
            self.sandbox.network_mode = network
        if os.getenv("AMBIENT_SANDBOX_STUB") == "1":
            self.sandbox.stub_mode = True
        if os.getenv("AMBIENT_SANDBOX_DISABLE_ALLOWLIST") == "1":
            self.sandbox.enforce_allowlist = False

        # Verification overrides
        if timeout := os.getenv("AMBIENT_VERIFY_TIMEOUT_SECONDS"):
            self.verification.timeout_seconds = int(timeout)

        # Git overrides
        if os.getenv("AMBIENT_GIT_NO_COMMIT") == "1":
            self.git.commit_on_success = False
        if os.getenv("AMBIENT_GIT_ALLOW_DIRTY") == "1":
            self.git.require_clean_before_apply = False
        if tmpl := os.getenv("AMBIENT_GIT_COMMIT_TEMPLATE"):
            self.git.commit_message_template = tmpl
        if name := os.getenv("AMBIENT_GIT_AUTHOR_NAME"):
            self.git.commit_author_name = name
        if email := os.getenv("AMBIENT_GIT_AUTHOR_EMAIL"):
            self.git.commit_author_email = email

        # Review worktree overrides
        if os.getenv("AMBIENT_REVIEW_WORKTREE_DISABLED") == "1":
            self.review_worktree.enabled = False
        if v := os.getenv("AMBIENT_REVIEW_MAX_PARALLEL"):
            self.review_worktree.max_parallel = int(v)
        if v := os.getenv("AMBIENT_REVIEW_BASE_DIR"):
            self.review_worktree.base_dir = v

        # Approval overrides
        if webhook_url := os.getenv("AMBIENT_APPROVAL_WEBHOOK_URL"):
            self.approval.webhook.url = webhook_url
        if webhook_timeout := os.getenv("AMBIENT_APPROVAL_WEBHOOK_TIMEOUT_SECONDS"):
            self.approval.webhook.timeout_seconds = int(webhook_timeout)

        # Telemetry overrides
        if log_path := os.getenv("AMBIENT_TELEMETRY_PATH"):
            self.telemetry.log_path = log_path

        # Control-plane overrides
        if os.getenv("AMBIENT_PAUSED") == "1":
            self.control_plane.paused = True
        if v := os.getenv("AMBIENT_MAX_PROPOSALS_PER_HOUR"):
            self.control_plane.max_proposals_per_hour = int(v)
        if v := os.getenv("AMBIENT_FAILURE_RATE_THRESHOLD"):
            self.control_plane.failure_rate_threshold = float(v)


def load_config(repo_path: Path | str) -> AmbientConfig:
    """
    Load configuration for a repository.

    Args:
        repo_path: Path to the repository

    Returns:
        Loaded and validated configuration
    """
    config = AmbientConfig.load_from_repo(repo_path)
    config.apply_env_overrides()
    return config
