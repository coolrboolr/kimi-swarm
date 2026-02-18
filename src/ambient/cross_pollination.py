"""Advanced local cross-pollination pipeline.

Uses deterministic, low-latency coordination techniques suitable for a
single-machine ambient loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from .types import Proposal

_RISK_WEIGHT = {"critical": 40, "high": 30, "medium": 20, "low": 10}
_TAG_BONUS = {
    "security": 6,
    "auth": 5,
    "test": 4,
    "performance": 4,
    "refactor": 3,
    "style": 1,
}


@dataclass
class CrossPollinationResult:
    """Output of multi-round cross-pollination."""

    proposals: list[Proposal]
    metadata: dict[str, Any]


def advanced_cross_pollinate(
    base_proposals: list[Proposal],
    refined_lists: list[list[Proposal]],
) -> CrossPollinationResult:
    """Run a deterministic multi-round proposal coordination pipeline."""
    round1 = _flatten_or_fallback(base_proposals, refined_lists)
    round2 = _dedupe(round1)
    clusters = _conflict_clusters(round2)
    round3 = _select_cluster_winners(clusters)
    round4 = sorted(
        round3,
        key=lambda p: (-_proposal_score(p), p.agent.lower(), p.title.lower()),
    )

    metadata = {
        "round1_count": len(round1),
        "round2_deduped_count": len(round2),
        "conflict_cluster_count": len(clusters),
        "final_count": len(round4),
    }
    return CrossPollinationResult(proposals=round4, metadata=metadata)


def _flatten_or_fallback(
    base_proposals: list[Proposal], refined_lists: list[list[Proposal]]
) -> list[Proposal]:
    flattened: list[Proposal] = []
    for lst in refined_lists:
        flattened.extend(lst)
    return flattened or list(base_proposals)


def _dedupe(proposals: list[Proposal]) -> list[Proposal]:
    """Deduplicate by normalized title+files+diff hash."""
    seen: set[str] = set()
    out: list[Proposal] = []

    for proposal in proposals:
        digest = sha256((proposal.diff or "").encode("utf-8", errors="replace")).hexdigest()
        key = "|".join(
            [
                proposal.agent.lower(),
                proposal.title.strip().lower(),
                ",".join(sorted(proposal.files_touched)),
                digest,
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(proposal)
    return out


def _conflict_clusters(proposals: list[Proposal]) -> list[list[Proposal]]:
    """Build connected components where proposals touch overlapping files."""
    if not proposals:
        return []

    n = len(proposals)
    adj: list[set[int]] = [set() for _ in range(n)]
    file_sets = [set(p.files_touched) for p in proposals]

    for i in range(n):
        for j in range(i + 1, n):
            if file_sets[i] & file_sets[j]:
                adj[i].add(j)
                adj[j].add(i)

    seen: set[int] = set()
    clusters: list[list[Proposal]] = []

    for i in range(n):
        if i in seen:
            continue
        stack = [i]
        component: list[int] = []
        seen.add(i)
        while stack:
            cur = stack.pop()
            component.append(cur)
            for nxt in adj[cur]:
                if nxt in seen:
                    continue
                seen.add(nxt)
                stack.append(nxt)
        clusters.append([proposals[idx] for idx in component])

    return clusters


def _select_cluster_winners(clusters: list[list[Proposal]]) -> list[Proposal]:
    """Pick the highest-scoring proposal per conflict cluster."""
    winners: list[Proposal] = []
    for cluster in clusters:
        if len(cluster) == 1:
            winners.append(cluster[0])
            continue
        ranked = sorted(
            cluster,
            key=lambda p: (
                -_proposal_score(p),
                abs(p.estimated_loc_change),
                p.agent.lower(),
                p.title.lower(),
            ),
        )
        winners.append(ranked[0])
    return winners


def _proposal_score(proposal: Proposal) -> int:
    risk_score = _RISK_WEIGHT.get(proposal.risk_level, 0)
    tag_score = sum(_TAG_BONUS.get(tag.lower(), 0) for tag in proposal.tags)
    size_penalty = min(abs(proposal.estimated_loc_change), 500) // 25
    return risk_score + tag_score - size_penalty
