"""Unit tests for impact radius analysis."""

from pathlib import Path

from ambient.impact import compute_impact_radius, extract_changed_paths


def test_extract_changed_paths_uses_event_and_diff() -> None:
    diff = """diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -1 +1 @@
-x
+y
"""
    paths = extract_changed_paths("src/main.py", diff)
    assert paths[0] == "src/main.py"
    assert "src/a.py" in paths


def test_compute_impact_radius_includes_neighbors_and_tests(tmp_path: Path) -> None:
    repo = tmp_path
    (repo / "src").mkdir()
    (repo / "tests").mkdir()

    (repo / "src" / "main.py").write_text("from src.helpers import util\n")
    (repo / "src" / "helpers.py").write_text("def util():\n    return 1\n")
    (repo / "src" / "consumer.py").write_text("from src.main import run\n")
    (repo / "tests" / "test_main.py").write_text("def test_main():\n    assert True\n")

    tree_files = [
        "src/main.py",
        "src/helpers.py",
        "src/consumer.py",
        "tests/test_main.py",
    ]

    impacted = compute_impact_radius(
        repo,
        tree_files,
        changed_paths=["src/main.py"],
        max_files=50,
    )

    assert "src/main.py" in impacted
    assert "src/helpers.py" in impacted
    assert "src/consumer.py" in impacted
    assert "tests/test_main.py" in impacted
