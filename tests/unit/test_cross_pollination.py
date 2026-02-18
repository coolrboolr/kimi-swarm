"""Unit tests for advanced cross-pollination."""

from ambient.cross_pollination import advanced_cross_pollinate
from ambient.types import Proposal


def _proposal(
    title: str,
    files: list[str],
    *,
    agent: str = "A",
    risk: str = "low",
    loc: int = 5,
    tags: list[str] | None = None,
) -> Proposal:
    return Proposal(
        agent=agent,
        title=title,
        description="d",
        diff=f"diff-{title}",
        risk_level=risk,
        rationale="r",
        files_touched=files,
        estimated_loc_change=loc,
        tags=tags or [],
    )


def test_cross_pollination_prefers_refined_lists() -> None:
    base = [_proposal("base", ["a.py"])]
    refined = [[_proposal("refined", ["a.py"], agent="B")]]

    result = advanced_cross_pollinate(base, refined)

    assert len(result.proposals) == 1
    assert result.proposals[0].title == "refined"


def test_cross_pollination_conflict_resolution_picks_one_per_cluster() -> None:
    p1 = _proposal("security", ["a.py"], agent="SecurityGuardian", risk="high", tags=["security"])
    p2 = _proposal("style", ["a.py"], agent="StyleEnforcer", risk="low", tags=["style"])
    p3 = _proposal("tests", ["tests/test_a.py"], agent="TestEnhancer", risk="low", tags=["test"])

    result = advanced_cross_pollinate([p1, p2, p3], [[p1, p2, p3]])

    titles = {p.title for p in result.proposals}
    assert "security" in titles
    assert "tests" in titles
    assert "style" not in titles
