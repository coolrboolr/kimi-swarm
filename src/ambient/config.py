"""Configuration schema for Ambient Swarm system.

Configuration is loaded from .ambient.yml in the repository root.
"""

from __future__ import annotations

import os
import re
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
    allowed_commands: list[str] = Field(
        default_factory=lambda: [
            r"^pytest",
            r"^python\s+-m\s+pytest",
            r"^ruff\s+(check|format)",
            r"^mypy",
            r"^flake8",
            r"^cargo\s+(test|check|clippy)",
            r"^npm\s+test",
            r"^make\s+(test|lint|check)",
            r"^git\s+(status|diff|log|show)",
        ]
    )

    def is_command_allowed(self, command: str) -> bool:
        """Check if command matches any allowed pattern."""
        return any(re.match(pattern, command) for pattern in self.allowed_commands)


class TelemetryConfig(BaseModel):
    """Telemetry and logging configuration."""

    enabled: bool = True
    log_path: str = ".ambient/telemetry.jsonl"
    include_diffs: bool = False
    retention_days: int = 30


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
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
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

        # Telemetry overrides
        if log_path := os.getenv("AMBIENT_TELEMETRY_PATH"):
            self.telemetry.log_path = log_path


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
