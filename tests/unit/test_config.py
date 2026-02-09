"""Unit tests for configuration loading and validation."""

import os
import tempfile
from pathlib import Path

import pytest

from ambient.config import (
    KimiConfig,
    MonitoringConfig,
    AgentsConfig,
    RiskPolicyConfig,
    SandboxConfig,
    TelemetryConfig,
    AmbientConfig,
    load_config,
)


class TestKimiConfig:
    """Tests for KimiConfig."""

    def test_default_config(self):
        """Test default Kimi configuration."""
        config = KimiConfig()
        assert config.provider == "ollama"
        assert config.base_url == "http://localhost:11434/v1"
        assert config.model_id == "kimi-k2.5:cloud"
        assert config.max_concurrency == 8
        assert config.temperature == 0.2
        assert config.timeout_seconds == 300

    def test_custom_config(self):
        """Test custom Kimi configuration."""
        config = KimiConfig(
            provider="openai-compatible",
            base_url="https://api.example.com/v1",
            model_id="custom-model",
            max_concurrency=16,
            temperature=0.5,
            timeout_seconds=600,
        )
        assert config.provider == "openai-compatible"
        assert config.base_url == "https://api.example.com/v1"
        assert config.max_concurrency == 16
        assert config.temperature == 0.5

    def test_invalid_provider(self):
        """Test that invalid provider raises ValueError."""
        with pytest.raises(ValueError, match="Invalid provider"):
            KimiConfig(provider="invalid-provider")

    @pytest.mark.parametrize("provider", ["ollama", "openai-compatible", "anthropic"])
    def test_valid_providers(self, provider):
        """Test all valid providers are accepted."""
        config = KimiConfig(provider=provider)
        assert config.provider == provider


class TestMonitoringConfig:
    """Tests for MonitoringConfig."""

    def test_default_monitoring_config(self):
        """Test default monitoring configuration."""
        config = MonitoringConfig()
        assert config.enabled is True
        assert "src/" in config.watch_paths
        assert "tests/" in config.watch_paths
        assert "*.pyc" in config.ignore_patterns
        assert config.debounce_seconds == 5
        assert config.check_interval_seconds == 300

    def test_custom_monitoring_config(self):
        """Test custom monitoring configuration."""
        config = MonitoringConfig(
            enabled=False,
            watch_paths=["app/", "lib/"],
            ignore_patterns=["*.log", "tmp/"],
            debounce_seconds=10,
            check_interval_seconds=600,
        )
        assert config.enabled is False
        assert config.watch_paths == ["app/", "lib/"]
        assert "*.log" in config.ignore_patterns
        assert config.debounce_seconds == 10


class TestAgentsConfig:
    """Tests for AgentsConfig."""

    def test_default_agents_config(self):
        """Test default agents configuration."""
        config = AgentsConfig()
        assert "SecurityGuardian" in config.enabled
        assert "RefactorArchitect" in config.enabled
        assert "StyleEnforcer" in config.enabled
        assert "PerformanceOptimizer" in config.enabled
        assert "TestEnhancer" in config.enabled
        assert len(config.enabled) == 5

    def test_custom_agents_config(self):
        """Test custom agents configuration."""
        config = AgentsConfig(enabled=["SecurityGuardian", "StyleEnforcer"])
        assert len(config.enabled) == 2
        assert "SecurityGuardian" in config.enabled
        assert "RefactorArchitect" not in config.enabled


class TestRiskPolicyConfig:
    """Tests for RiskPolicyConfig."""

    def test_default_risk_policy(self):
        """Test default risk policy configuration."""
        config = RiskPolicyConfig()
        assert "low" in config.auto_apply
        assert "medium" in config.auto_apply
        assert "high" in config.require_approval
        assert "critical" in config.require_approval
        assert config.file_change_limit == 10
        assert config.loc_change_limit == 500

    def test_custom_risk_policy(self):
        """Test custom risk policy configuration."""
        config = RiskPolicyConfig(
            auto_apply=["low"],
            require_approval=["medium", "high", "critical"],
            file_change_limit=5,
            loc_change_limit=100,
        )
        assert config.auto_apply == ["low"]
        assert "medium" in config.require_approval
        assert config.file_change_limit == 5
        assert config.loc_change_limit == 100


class TestSandboxConfig:
    """Tests for SandboxConfig."""

    def test_default_sandbox_config(self):
        """Test default sandbox configuration."""
        config = SandboxConfig()
        assert config.image == "ambient-sandbox:latest"
        assert config.network_mode == "none"
        assert config.resources.memory == "2g"
        assert config.resources.cpus == "2.0"
        assert config.resources.pids_limit == 100

    def test_custom_sandbox_config(self):
        """Test custom sandbox configuration."""
        config = SandboxConfig(
            image="custom-sandbox:v1",
            network_mode="bridge",
            resources={
                "memory": "4g",
                "cpus": "4.0",
                "pids_limit": 200,
            },
        )
        assert config.image == "custom-sandbox:v1"
        assert config.network_mode == "bridge"
        assert config.resources.memory == "4g"
        assert config.resources.cpus == "4.0"
        assert config.resources.pids_limit == 200

    def test_allowed_commands(self):
        """Test allowed commands configuration."""
        config = SandboxConfig()
        # Check that default allowed commands exist
        assert any("pytest" in cmd for cmd in config.allowed_commands)
        assert any("ruff" in cmd for cmd in config.allowed_commands)


class TestTelemetryConfig:
    """Tests for TelemetryConfig."""

    def test_default_telemetry_config(self):
        """Test default telemetry configuration."""
        config = TelemetryConfig()
        assert config.enabled is True
        assert config.log_path == ".ambient/telemetry.jsonl"
        assert config.include_diffs is False
        assert config.retention_days == 30

    def test_custom_telemetry_config(self):
        """Test custom telemetry configuration."""
        config = TelemetryConfig(
            enabled=False,
            log_path="logs/ambient.jsonl",
            include_diffs=True,
            retention_days=90,
        )
        assert config.enabled is False
        assert config.log_path == "logs/ambient.jsonl"
        assert config.include_diffs is True
        assert config.retention_days == 90


class TestAmbientConfig:
    """Tests for full AmbientConfig."""

    def test_default_ambient_config(self):
        """Test default ambient configuration."""
        config = AmbientConfig()
        assert isinstance(config.kimi, KimiConfig)
        assert isinstance(config.monitoring, MonitoringConfig)
        assert isinstance(config.agents, AgentsConfig)
        assert isinstance(config.risk_policy, RiskPolicyConfig)
        assert isinstance(config.sandbox, SandboxConfig)
        assert isinstance(config.telemetry, TelemetryConfig)

    def test_load_from_dict(self):
        """Test loading configuration from dictionary."""
        config_dict = {
            "kimi": {
                "provider": "ollama",
                "model_id": "test-model",
                "temperature": 0.1,
            },
            "agents": {
                "enabled": ["SecurityGuardian"],
            },
            "risk_policy": {
                "file_change_limit": 5,
            },
        }
        config = AmbientConfig(**config_dict)
        assert config.kimi.model_id == "test-model"
        assert config.kimi.temperature == 0.1
        assert len(config.agents.enabled) == 1
        assert config.risk_policy.file_change_limit == 5

    def test_load_from_yaml_file(self):
        """Test loading configuration from YAML file."""
        yaml_content = """
kimi:
  provider: ollama
  model_id: test-model
  temperature: 0.3

agents:
  enabled:
    - SecurityGuardian
    - StyleEnforcer

risk_policy:
  auto_apply:
    - low
  file_change_limit: 3
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            config = AmbientConfig.load_from_file(temp_path)
            assert config.kimi.model_id == "test-model"
            assert config.kimi.temperature == 0.3
            assert len(config.agents.enabled) == 2
            assert config.risk_policy.file_change_limit == 3
        finally:
            os.unlink(temp_path)

    def test_env_overrides(self):
        """Test environment variable overrides."""
        # Set environment variables (use correct names from config.py)
        os.environ["AMBIENT_KIMI_MODEL"] = "env-model"
        os.environ["AMBIENT_KIMI_TEMPERATURE"] = "0.7"
        os.environ["AMBIENT_SANDBOX_IMAGE"] = "custom-sandbox:test"

        try:
            config = AmbientConfig()
            config.apply_env_overrides()

            assert config.kimi.model_id == "env-model"
            assert config.kimi.temperature == 0.7
            assert config.sandbox.image == "custom-sandbox:test"
        finally:
            # Clean up environment
            os.environ.pop("AMBIENT_KIMI_MODEL", None)
            os.environ.pop("AMBIENT_KIMI_TEMPERATURE", None)
            os.environ.pop("AMBIENT_SANDBOX_IMAGE", None)


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_config_with_file(self):
        """Test loading config when .ambient.yml exists."""
        yaml_content = """
kimi:
  model_id: file-model
  temperature: 0.4
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".ambient.yml"
            config_path.write_text(yaml_content)

            config = load_config(Path(tmpdir))
            assert config.kimi.model_id == "file-model"
            assert config.kimi.temperature == 0.4

    def test_load_config_without_file(self):
        """Test loading config when no .ambient.yml exists (uses defaults)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_config(Path(tmpdir))
            # Should return default config
            assert config.kimi.model_id == "kimi-k2.5:cloud"
            assert config.kimi.provider == "ollama"

    def test_load_config_invalid_yaml(self):
        """Test loading config with invalid YAML."""
        invalid_yaml = """
kimi:
  model_id: test
  invalid: [unclosed
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".ambient.yml"
            config_path.write_text(invalid_yaml)

            # Should handle error gracefully and return default config
            with pytest.raises(Exception):
                load_config(Path(tmpdir))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
