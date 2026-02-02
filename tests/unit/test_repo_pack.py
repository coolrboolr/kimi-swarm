"""Unit tests for repository context packing."""

import json
import tempfile
from pathlib import Path

import pytest

from src.ambient.salvaged.repo_pack import build_repo_pack, _read_cap, IMPORTANT_FILES


class TestReadCap:
    """Tests for _read_cap function."""

    def test_read_small_file(self):
        """Test reading a small file."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("Hello, World!")
            temp_path = Path(f.name)

        try:
            content = _read_cap(temp_path)
            assert content == "Hello, World!"
        finally:
            temp_path.unlink()

    def test_read_large_file_capped(self):
        """Test reading a large file gets capped."""
        large_content = "x" * 300000  # 300KB
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write(large_content)
            temp_path = Path(f.name)

        try:
            content = _read_cap(temp_path, cap=200000)
            assert len(content) == 200000  # Capped at 200KB
            assert content == "x" * 200000
        finally:
            temp_path.unlink()

    def test_read_nonexistent_file(self):
        """Test reading nonexistent file returns empty string."""
        content = _read_cap(Path("/nonexistent/file.txt"))
        assert content == ""

    def test_read_with_unicode(self):
        """Test reading file with unicode characters."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt", encoding="utf-8") as f:
            f.write("Hello 世界 🌍")
            temp_path = Path(f.name)

        try:
            content = _read_cap(temp_path)
            assert "世界" in content
            assert "🌍" in content
        finally:
            temp_path.unlink()


class TestBuildRepoPack:
    """Tests for build_repo_pack function."""

    def test_minimal_pack(self):
        """Test building minimal repo pack."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            task = {"goal": "Test task"}
            tree = {"files": ["main.py"], "total_files": 1}
            failing_logs = ""
            current_diff = ""

            pack_json = build_repo_pack(root, task, tree, failing_logs, current_diff)
            pack = json.loads(pack_json)

            assert pack["task"] == task
            assert pack["tree"] == tree
            assert pack["failing_logs"] == ""
            assert pack["current_diff"] == ""
            assert pack["important_files"] == {}
            assert pack["hot_paths"] == []
            assert pack["conventions"] == {}

    def test_pack_with_important_files(self):
        """Test pack includes important config files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # Create some important files
            (root / "pyproject.toml").write_text("[tool.pytest]\ntestpaths = ['tests']")
            (root / "requirements.txt").write_text("pytest>=7.0.0\nruff>=0.1.0")
            (root / "README.md").write_text("# Test Project")

            task = {"goal": "Test"}
            tree = {"files": ["pyproject.toml", "requirements.txt", "README.md"], "total_files": 3}

            pack_json = build_repo_pack(root, task, tree, "", "")
            pack = json.loads(pack_json)

            assert "pyproject.toml" in pack["important_files"]
            assert "requirements.txt" in pack["important_files"]
            assert "README.md" in pack["important_files"]
            assert "[tool.pytest]" in pack["important_files"]["pyproject.toml"]
            assert "pytest>=7.0.0" in pack["important_files"]["requirements.txt"]

    def test_pack_with_python_files(self):
        """Test pack includes Python source files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # Create Python files
            (root / "main.py").write_text("def main():\n    pass")
            (root / "utils.py").write_text("def helper():\n    return 42")

            task = {"goal": "Test"}
            tree = {"files": ["main.py", "utils.py"], "total_files": 2}

            pack_json = build_repo_pack(root, task, tree, "", "")
            pack = json.loads(pack_json)

            # Python files should be included in important_files
            assert "main.py" in pack["important_files"]
            assert "utils.py" in pack["important_files"]
            assert "def main()" in pack["important_files"]["main.py"]
            assert "def helper()" in pack["important_files"]["utils.py"]

    def test_pack_with_failing_logs(self):
        """Test pack includes failing logs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            task = {"goal": "Fix test"}
            tree = {"files": ["test.py"], "total_files": 1}
            failing_logs = "FAILED test.py::test_function - AssertionError"
            current_diff = ""

            pack_json = build_repo_pack(root, task, tree, failing_logs, current_diff)
            pack = json.loads(pack_json)

            assert pack["failing_logs"] == failing_logs
            assert "FAILED" in pack["failing_logs"]

    def test_pack_with_current_diff(self):
        """Test pack includes current diff."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            task = {"goal": "Test"}
            tree = {"files": ["main.py"], "total_files": 1}
            failing_logs = ""
            current_diff = "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-old\n+new"

            pack_json = build_repo_pack(root, task, tree, failing_logs, current_diff)
            pack = json.loads(pack_json)

            assert pack["current_diff"] == current_diff
            assert "--- a/main.py" in pack["current_diff"]

    def test_pack_with_hot_paths(self):
        """Test pack includes hot paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            task = {"goal": "Test"}
            tree = {"files": ["main.py", "utils.py"], "total_files": 2}
            hot_paths = ["main.py", "utils.py"]

            pack_json = build_repo_pack(root, task, tree, "", "", hot_paths=hot_paths)
            pack = json.loads(pack_json)

            assert pack["hot_paths"] == hot_paths
            assert "main.py" in pack["hot_paths"]

    def test_pack_with_conventions(self):
        """Test pack includes conventions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            task = {"goal": "Test"}
            tree = {"files": ["main.py"], "total_files": 1}
            conventions = {"style": "google", "line_length": 100}

            pack_json = build_repo_pack(root, task, tree, "", "", conventions=conventions)
            pack = json.loads(pack_json)

            assert pack["conventions"] == conventions
            assert pack["conventions"]["style"] == "google"

    def test_pack_limits_python_files(self):
        """Test pack limits Python files to first 50."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # Create 60 Python files
            python_files = []
            for i in range(60):
                filename = f"file_{i}.py"
                (root / filename).write_text(f"# File {i}")
                python_files.append(filename)

            task = {"goal": "Test"}
            tree = {"files": python_files, "total_files": 60}

            pack_json = build_repo_pack(root, task, tree, "", "")
            pack = json.loads(pack_json)

            # Should only include first 50 Python files
            python_in_pack = [k for k in pack["important_files"].keys() if k.endswith(".py")]
            assert len(python_in_pack) <= 50

    def test_pack_ignores_nonexistent_important_files(self):
        """Test pack doesn't fail on missing important files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # Don't create any files - all IMPORTANT_FILES are missing

            task = {"goal": "Test"}
            tree = {"files": [], "total_files": 0}

            pack_json = build_repo_pack(root, task, tree, "", "")
            pack = json.loads(pack_json)

            # Should have empty important_files
            assert pack["important_files"] == {}

    def test_pack_json_serialization(self):
        """Test pack is valid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text("[tool.pytest]")

            task = {"goal": "Test"}
            tree = {"files": ["pyproject.toml"], "total_files": 1}

            pack_json = build_repo_pack(root, task, tree, "", "")

            # Should be valid JSON
            pack = json.loads(pack_json)
            assert isinstance(pack, dict)

            # Should be re-serializable
            json.dumps(pack)


class TestImportantFiles:
    """Tests for IMPORTANT_FILES constant."""

    def test_important_files_list(self):
        """Test IMPORTANT_FILES contains expected files."""
        assert "pyproject.toml" in IMPORTANT_FILES
        assert "requirements.txt" in IMPORTANT_FILES
        assert "README.md" in IMPORTANT_FILES
        assert "Makefile" in IMPORTANT_FILES

    def test_important_files_all_strings(self):
        """Test all important files are strings."""
        assert all(isinstance(f, str) for f in IMPORTANT_FILES)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
