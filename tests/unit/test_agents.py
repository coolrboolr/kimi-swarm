"""Unit tests for specialist agents."""

import pytest
from unittest.mock import AsyncMock, Mock

from ambient.agents import (
    SecurityGuardian,
    RefactorArchitect,
    StyleEnforcer,
    PerformanceOptimizer,
    TestEnhancer,
)
from ambient.config import KimiConfig
from ambient.types import RepoContext, Proposal


@pytest.fixture
def kimi_config():
    """Create test Kimi configuration."""
    return KimiConfig(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        model_id="test-model",
    )


@pytest.fixture
def mock_repo_context():
    """Create mock repository context."""
    return RepoContext(
        task={"goal": "Test goal"},
        tree={"files": ["test.py", "src/main.py"], "total_files": 2},
        important_files={"pyproject.toml": "[tool.pytest]\n"},
        failing_logs="",
        current_diff="",
        hot_paths=[],
        conventions={},
    )


class TestBaseAgent:
    """Test base agent functionality."""

    @pytest.mark.asyncio
    async def test_format_prompt_basic(self, kimi_config, mock_repo_context):
        """Test prompt formatting includes all sections."""
        agent = SecurityGuardian(kimi_config)
        prompt = agent._format_prompt(mock_repo_context)

        assert "# Task" in prompt
        assert "# Repository Structure" in prompt
        assert "test.py" in prompt
        assert "src/main.py" in prompt
        assert "# Instructions" in prompt

    @pytest.mark.asyncio
    async def test_format_prompt_with_failing_logs(self, kimi_config):
        """Test prompt includes failing logs when present."""
        context = RepoContext(
            task={"goal": "Fix tests"},
            tree={"files": ["test.py"], "total_files": 1},
            important_files={},
            failing_logs="FAILED test.py::test_func - AssertionError",
            current_diff="",
        )

        agent = SecurityGuardian(kimi_config)
        prompt = agent._format_prompt(context)

        assert "# Failing Logs / Errors" in prompt
        assert "FAILED test.py::test_func" in prompt

    def test_parse_proposals_valid_json(self, kimi_config):
        """Test parsing valid JSON proposals."""
        agent = SecurityGuardian(kimi_config)

        json_response = """[
  {
    "agent": "SecurityGuardian",
    "title": "Fix SQL injection",
    "description": "Use parameterized queries",
    "diff": "--- a/test.py\\n+++ b/test.py\\n@@ -1,1 +1,1 @@\\n-bad\\n+good\\n",
    "risk_level": "critical",
    "rationale": "OWASP A03",
    "files_touched": ["test.py"],
    "estimated_loc_change": 2,
    "tags": ["security"]
  }
]"""

        proposals = agent._parse_proposals(json_response)

        assert len(proposals) == 1
        assert proposals[0].agent == "SecurityGuardian"
        assert proposals[0].title == "Fix SQL injection"
        assert proposals[0].risk_level == "critical"

    def test_parse_proposals_markdown_wrapped(self, kimi_config):
        """Test parsing JSON wrapped in markdown code blocks."""
        agent = SecurityGuardian(kimi_config)

        markdown_response = """Here are my proposals:

```json
[
  {
    "agent": "SecurityGuardian",
    "title": "Test",
    "description": "Test desc",
    "diff": "--- a/test.py\\n+++ b/test.py\\n",
    "risk_level": "low",
    "rationale": "Test",
    "files_touched": ["test.py"],
    "estimated_loc_change": 1,
    "tags": []
  }
]
```

That's all!"""

        proposals = agent._parse_proposals(markdown_response)

        assert len(proposals) == 1
        assert proposals[0].title == "Test"

    def test_parse_proposals_empty_array(self, kimi_config):
        """Test parsing empty array (no issues found)."""
        agent = SecurityGuardian(kimi_config)

        proposals = agent._parse_proposals("[]")

        assert len(proposals) == 0

    def test_parse_proposals_invalid_json(self, kimi_config):
        """Test handling of invalid JSON."""
        agent = SecurityGuardian(kimi_config)

        proposals = agent._parse_proposals("This is not JSON")

        assert len(proposals) == 0

    def test_parse_proposals_malformed_proposal(self, kimi_config):
        """Test skipping malformed proposals."""
        agent = SecurityGuardian(kimi_config)

        json_response = """[
  {
    "agent": "SecurityGuardian",
    "title": "Valid",
    "description": "Valid desc",
    "diff": "valid diff",
    "risk_level": "low",
    "rationale": "Valid",
    "files_touched": ["test.py"],
    "estimated_loc_change": 1
  },
  {
    "title": "Missing required fields"
  }
]"""

        proposals = agent._parse_proposals(json_response)

        # Should only parse the valid proposal
        assert len(proposals) == 1
        assert proposals[0].title == "Valid"


class TestSecurityGuardian:
    """Test SecurityGuardian agent."""

    def test_system_prompt_contains_focus_areas(self, kimi_config):
        """Test system prompt includes all focus areas."""
        agent = SecurityGuardian(kimi_config)

        assert "Secrets Exposure" in agent.system_prompt
        assert "Injection Attacks" in agent.system_prompt
        assert "SQL injection" in agent.system_prompt
        assert "OWASP" in agent.system_prompt

    @pytest.mark.asyncio
    async def test_refine_returns_own_proposals(self, kimi_config, mock_repo_context):
        """Test refine returns only SecurityGuardian proposals."""
        agent = SecurityGuardian(kimi_config)

        all_proposals = [
            Proposal(
                agent="SecurityGuardian",
                title="Security fix",
                description="desc",
                diff="diff",
                risk_level="high",
                rationale="rationale",
                files_touched=["a.py"],
                estimated_loc_change=1,
            ),
            Proposal(
                agent="RefactorArchitect",
                title="Refactor",
                description="desc",
                diff="diff",
                risk_level="medium",
                rationale="rationale",
                files_touched=["b.py"],
                estimated_loc_change=2,
            ),
        ]

        refined = await agent.refine(all_proposals, mock_repo_context)

        assert len(refined) == 1
        assert refined[0].agent == "SecurityGuardian"


class TestRefactorArchitect:
    """Test RefactorArchitect agent."""

    def test_system_prompt_contains_focus_areas(self, kimi_config):
        """Test system prompt includes refactoring focus areas."""
        agent = RefactorArchitect(kimi_config)

        assert "Code Duplication" in agent.system_prompt
        assert "Complexity" in agent.system_prompt
        assert "DRY" in agent.system_prompt
        assert "SOLID" in agent.system_prompt


class TestStyleEnforcer:
    """Test StyleEnforcer agent."""

    def test_system_prompt_contains_focus_areas(self, kimi_config):
        """Test system prompt includes style focus areas."""
        agent = StyleEnforcer(kimi_config)

        assert "Formatting" in agent.system_prompt
        assert "Documentation" in agent.system_prompt
        assert "docstrings" in agent.system_prompt
        assert "PEP" in agent.system_prompt

    def test_all_proposals_low_risk(self, kimi_config):
        """Test that parsed style proposals are always low risk."""
        agent = StyleEnforcer(kimi_config)

        json_response = """[
  {
    "agent": "StyleEnforcer",
    "title": "Add docstrings",
    "description": "desc",
    "diff": "diff",
    "risk_level": "low",
    "rationale": "rationale",
    "files_touched": ["test.py"],
    "estimated_loc_change": 10
  }
]"""

        proposals = agent._parse_proposals(json_response)

        assert all(p.risk_level == "low" for p in proposals)


class TestPerformanceOptimizer:
    """Test PerformanceOptimizer agent."""

    def test_system_prompt_contains_focus_areas(self, kimi_config):
        """Test system prompt includes performance focus areas."""
        agent = PerformanceOptimizer(kimi_config)

        assert "Algorithm Complexity" in agent.system_prompt
        assert "O(n²)" in agent.system_prompt
        assert "Database Queries" in agent.system_prompt
        assert "Caching" in agent.system_prompt


class TestTestEnhancer:
    """Test TestEnhancer agent."""

    def test_system_prompt_contains_focus_areas(self, kimi_config):
        """Test system prompt includes test focus areas."""
        agent = TestEnhancer(kimi_config)

        assert "Coverage Gaps" in agent.system_prompt
        assert "Edge Cases" in agent.system_prompt
        assert "Flaky Tests" in agent.system_prompt
        assert "pytest" in agent.system_prompt.lower()


class TestAllAgents:
    """Test all agents together."""

    @pytest.mark.parametrize(
        "agent_class",
        [
            SecurityGuardian,
            RefactorArchitect,
            StyleEnforcer,
            PerformanceOptimizer,
            TestEnhancer,
        ],
    )
    def test_agent_initialization(self, kimi_config, agent_class):
        """Test all agents can be initialized."""
        agent = agent_class(kimi_config)

        assert agent.kimi_client is not None
        assert agent.system_prompt is not None
        assert len(agent.system_prompt) > 100

    @pytest.mark.parametrize(
        "agent_class",
        [
            SecurityGuardian,
            RefactorArchitect,
            StyleEnforcer,
            PerformanceOptimizer,
            TestEnhancer,
        ],
    )
    def test_agent_parse_empty_response(self, kimi_config, agent_class):
        """Test all agents handle empty responses."""
        agent = agent_class(kimi_config)

        proposals = agent._parse_proposals("[]")

        assert proposals == []

    @pytest.mark.parametrize(
        "agent_class,expected_tags",
        [
            (SecurityGuardian, ["security"]),
            (RefactorArchitect, ["refactor"]),
            (StyleEnforcer, ["style"]),
            (PerformanceOptimizer, ["performance"]),
            (TestEnhancer, ["test"]),
        ],
    )
    def test_agent_proposals_have_expected_tags(
        self, kimi_config, agent_class, expected_tags
    ):
        """Test proposals from each agent have expected tags."""
        import json as json_lib

        agent = agent_class(kimi_config)

        # Properly format tags as JSON array
        tags_json = json_lib.dumps(expected_tags)

        json_response = f"""[
  {{
    "agent": "{agent_class.__name__}",
    "title": "Test",
    "description": "desc",
    "diff": "diff",
    "risk_level": "low",
    "rationale": "rationale",
    "files_touched": ["test.py"],
    "estimated_loc_change": 1,
    "tags": {tags_json}
  }}
]"""

        proposals = agent._parse_proposals(json_response)

        assert len(proposals) == 1
        assert any(tag in proposals[0].tags for tag in expected_tags)


def test_agent_uses_injected_kimi_client(kimi_config):
    """Agents should use the injected KimiClient instance when provided."""
    from unittest.mock import Mock

    from ambient.kimi_client import KimiClient

    mock_client = Mock(spec=KimiClient)
    agent = SecurityGuardian(kimi_config, kimi_client=mock_client)

    assert agent.kimi_client is mock_client
