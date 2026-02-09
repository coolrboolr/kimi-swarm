"""Base class for specialist agents.

All specialist agents inherit from SpecialistAgent and implement:
- _build_system_prompt(): Domain-specific system prompt
- propose(): Generate proposals from repo context
- refine(): Optionally refine proposals after cross-pollination
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

from ..kimi_client import KimiClient
from ..config import KimiConfig
from ..types import Proposal, RepoContext


class SpecialistAgent(ABC):
    """Base class for all specialist agents."""

    def __init__(self, kimi_config: KimiConfig, kimi_client: KimiClient | None = None):
        # Allow dependency injection for testing and for sharing a single client
        # instance across all agents (shared concurrency limits, shared mocking).
        self.kimi_client = kimi_client or KimiClient(kimi_config)
        self.system_prompt = self._build_system_prompt()

    @abstractmethod
    def _build_system_prompt(self) -> str:
        """Return detailed system prompt for this specialist."""
        pass

    async def propose(self, context: RepoContext) -> list[Proposal]:
        """
        Analyze repo context and propose improvements.

        Args:
            context: Full repo visibility (tree, files, configs, failing_logs)

        Returns:
            List of proposals (may be empty if no issues found)
        """
        prompt = self._format_prompt(context)

        response = await self.kimi_client.chat_completion(
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,  # Low temperature for consistency
        )

        content = response["choices"][0]["message"]["content"]
        return self._parse_proposals(content)

    async def refine(
        self,
        all_proposals: list[Proposal],
        context: RepoContext,
    ) -> list[Proposal]:
        """
        Refine proposals after seeing other agents' work.

        This enables coordination (e.g., SecurityGuardian sees RefactorArchitect
        is moving code, so doesn't flag that file as "complex").

        Default implementation: return own proposals unchanged.
        Subclasses can override for more sophisticated cross-pollination.

        Args:
            all_proposals: All proposals from all agents
            context: Repository context

        Returns:
            Refined proposals (typically filtered or adjusted versions)
        """
        # Default: no refinement, just return own proposals
        agent_name = self.__class__.__name__
        return [p for p in all_proposals if p.agent == agent_name]

    def _format_prompt(self, context: RepoContext) -> str:
        """
        Format repository context into a prompt for the agent.

        Args:
            context: Repository context

        Returns:
            Formatted prompt string
        """
        sections = []

        # Task description
        sections.append("# Task")
        sections.append(f"Goal: {context.task.get('goal', 'Code quality analysis')}")
        sections.append("")

        # File tree
        sections.append("# Repository Structure")
        if context.tree and "files" in context.tree:
            files = context.tree["files"]
            total = context.tree.get("total_files", len(files))
            # Limit to first 200 files to avoid token overflow
            displayed_files = files[:200]
            sections.append(f"Total files: {total}")
            sections.append("Files:")
            for f in displayed_files:
                sections.append(f"  - {f}")
            if len(files) > 200:
                sections.append(f"  ... and {len(files) - 200} more files")
        sections.append("")

        # Important config files
        if context.important_files:
            sections.append("# Important Configuration Files")
            for filename, content in context.important_files.items():
                sections.append(f"\n## {filename}")
                # Limit each file to 1000 chars to avoid overflow
                content_preview = content[:1000]
                if len(content) > 1000:
                    content_preview += "\n... (truncated)"
                sections.append(f"```\n{content_preview}\n```")
            sections.append("")

        # Current diff (if any)
        if context.current_diff:
            sections.append("# Current Uncommitted Changes")
            diff_preview = context.current_diff[:2000]
            if len(context.current_diff) > 2000:
                diff_preview += "\n... (truncated)"
            sections.append(f"```diff\n{diff_preview}\n```")
            sections.append("")

        # Failing logs (if any)
        if context.failing_logs:
            sections.append("# Failing Logs / Errors")
            logs_preview = context.failing_logs[:2000]
            if len(context.failing_logs) > 2000:
                logs_preview += "\n... (truncated)"
            sections.append(f"```\n{logs_preview}\n```")
            sections.append("")

        # Hot paths (files mentioned in errors)
        if context.hot_paths:
            sections.append("# Hot Paths (Files Mentioned in Errors)")
            for path in context.hot_paths[:20]:
                sections.append(f"  - {path}")
            sections.append("")

        # Instructions
        sections.append("# Instructions")
        sections.append(
            "Analyze the repository and generate proposals following the JSON format specified in your system prompt."
        )
        sections.append(
            "Return a JSON array of proposals. If no issues found, return empty array: []"
        )

        return "\n".join(sections)

    def _parse_proposals(self, content: str) -> list[Proposal]:
        """
        Parse JSON proposals from LLM response.

        Args:
            content: Raw LLM response content

        Returns:
            List of Proposal objects

        The LLM may return:
        - Valid JSON array
        - JSON wrapped in markdown code blocks
        - Empty array if no issues
        - Malformed JSON (we'll try to extract)
        """
        # Try to extract JSON from markdown code blocks
        json_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", content, re.DOTALL)
        if json_match:
            content = json_match.group(1)

        # Try direct JSON parsing
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # Try to find JSON array in the content
            array_match = re.search(r"\[.*\]", content, re.DOTALL)
            if array_match:
                try:
                    data = json.loads(array_match.group(0))
                except json.JSONDecodeError:
                    return []
            else:
                return []

        if not isinstance(data, list):
            return []

        proposals = []
        for item in data:
            try:
                proposal = Proposal(
                    agent=item.get("agent", self.__class__.__name__),
                    title=item["title"],
                    description=item["description"],
                    diff=item["diff"],
                    risk_level=item["risk_level"],
                    rationale=item["rationale"],
                    files_touched=item["files_touched"],
                    estimated_loc_change=item["estimated_loc_change"],
                    tags=item.get("tags", []),
                )
                proposals.append(proposal)
            except (KeyError, TypeError, ValueError) as e:
                # Skip malformed proposals
                continue

        return proposals
