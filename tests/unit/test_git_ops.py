"""Unit tests for git_ops.py - atomic patch application."""

import pytest
from pathlib import Path
import subprocess

from ambient.salvaged.git_ops import (
    git_apply_patch_atomic,
    git_reset_hard_clean,
    PatchApplyError,
)


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repository for testing."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    # Create initial file
    test_file = repo_path / "test.py"
    test_file.write_text("def hello():\n    print('Hello, World!')\n")

    # Initial commit
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    return repo_path


class TestGitResetHardClean:
    """Test git reset and clean operations."""

    def test_reset_modified_file(self, git_repo):
        """Test resetting a modified file."""
        test_file = git_repo / "test.py"
        test_file.write_text("def hello():\n    print('Modified!')\n")

        git_reset_hard_clean(git_repo)

        # File should be back to original
        content = test_file.read_text()
        assert "Hello, World!" in content
        assert "Modified!" not in content

    def test_clean_untracked_files(self, git_repo):
        """Test cleaning untracked files."""
        untracked = git_repo / "untracked.py"
        untracked.write_text("# This should be deleted")

        git_reset_hard_clean(git_repo)

        assert not untracked.exists()

    def test_clean_untracked_directory(self, git_repo):
        """Test cleaning untracked directories."""
        untracked_dir = git_repo / "untracked_dir"
        untracked_dir.mkdir()
        (untracked_dir / "file.py").write_text("# Untracked")

        git_reset_hard_clean(git_repo)

        assert not untracked_dir.exists()


class TestGitApplyPatchAtomic:
    """Test atomic patch application."""

    def test_apply_simple_patch(self, git_repo):
        """Test applying a simple valid patch."""
        patch = """--- a/test.py
+++ b/test.py
@@ -1,2 +1,2 @@
 def hello():
-    print('Hello, World!')
+    print('Hello, Universe!')
"""
        result = git_apply_patch_atomic(git_repo, patch)

        assert result["ok"] is True
        assert "1 file changed" in result["stat"]

        # Verify the change was applied
        content = (git_repo / "test.py").read_text()
        assert "Hello, Universe!" in content

    def test_apply_patch_with_markdown_fence(self, git_repo):
        """Test applying a patch wrapped in markdown code fence."""
        patch = """```diff
--- a/test.py
+++ b/test.py
@@ -1,2 +1,2 @@
 def hello():
-    print('Hello, World!')
+    print('Hello, from Markdown!')
```"""
        result = git_apply_patch_atomic(git_repo, patch)

        assert result["ok"] is True
        content = (git_repo / "test.py").read_text()
        assert "Hello, from Markdown!" in content

    @pytest.mark.xfail(reason="Idempotency detection not implemented in current version")
    def test_already_applied_patch(self, git_repo):
        """Test idempotency - applying the same patch twice."""
        patch = """--- a/test.py
+++ b/test.py
@@ -1,2 +1,2 @@
 def hello():
-    print('Hello, World!')
+    print('Hello, Again!')
"""
        # Apply first time
        result1 = git_apply_patch_atomic(git_repo, patch)
        assert result1["ok"] is True

        # Apply second time - should detect it's already applied
        result2 = git_apply_patch_atomic(git_repo, patch)
        assert result2["ok"] is True
        assert "already applied" in result2["stat"]

    def test_invalid_patch_rollback(self, git_repo):
        """Test that invalid patch triggers rollback."""
        # Create a valid initial state
        original_content = (git_repo / "test.py").read_text()

        # Try to apply invalid patch
        invalid_patch = """--- a/test.py
+++ b/test.py
@@ -100,2 +100,2 @@
 def nonexistent():
-    print('This does not exist')
+    print('So this will fail')
"""
        result = git_apply_patch_atomic(git_repo, invalid_patch)

        assert result["ok"] is False
        assert len(result["stderr"]) > 0

        # Verify rollback - file should be unchanged
        current_content = (git_repo / "test.py").read_text()
        assert current_content == original_content

    def test_patch_with_wrong_line_numbers(self, git_repo):
        """Test patch with incorrect hunk counts (LLM common error)."""
        # This patch has wrong line counts but should still work
        # due to hunk count fixing
        patch = """--- a/test.py
+++ b/test.py
@@ -1,5 +1,5 @@
 def hello():
-    print('Hello, World!')
+    print('Hello, Fixed!')
"""
        result = git_apply_patch_atomic(git_repo, patch)

        # Should succeed due to automatic fixing
        assert result["ok"] is True
        content = (git_repo / "test.py").read_text()
        assert "Hello, Fixed!" in content

    @pytest.mark.xfail(reason="File creation via /dev/null not supported in current version")
    def test_create_new_file_patch(self, git_repo):
        """Test creating a new file via patch."""
        patch = """--- /dev/null
+++ b/new_file.py
@@ -0,0 +1,3 @@
+def new_function():
+    return "I'm new!"
+
"""
        result = git_apply_patch_atomic(git_repo, patch)

        assert result["ok"] is True
        new_file = git_repo / "new_file.py"
        assert new_file.exists()
        assert "new_function" in new_file.read_text()

    @pytest.mark.xfail(reason="File deletion via /dev/null not supported in current version")
    def test_delete_file_patch(self, git_repo):
        """Test deleting a file via patch."""
        patch = """--- a/test.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def hello():
-    print('Hello, World!')
"""
        result = git_apply_patch_atomic(git_repo, patch)

        assert result["ok"] is True
        assert not (git_repo / "test.py").exists()

    def test_patch_with_context_lines(self, git_repo):
        """Test patch with proper context lines."""
        # Add more content to test file
        test_file = git_repo / "test.py"
        test_file.write_text(
            "def hello():\n"
            "    print('Hello, World!')\n"
            "\n"
            "def goodbye():\n"
            "    print('Goodbye!')\n"
        )
        subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Add goodbye"],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )

        patch = """--- a/test.py
+++ b/test.py
@@ -1,5 +1,5 @@
 def hello():
-    print('Hello, World!')
+    print('Hello, Universe!')

 def goodbye():
     print('Goodbye!')
"""
        result = git_apply_patch_atomic(git_repo, patch)

        assert result["ok"] is True
        content = test_file.read_text()
        assert "Hello, Universe!" in content
        assert "Goodbye!" in content

    def test_patch_with_windows_line_endings(self, git_repo):
        """Test patch with Windows (CRLF) line endings."""
        patch = "--- a/test.py\r\n+++ b/test.py\r\n@@ -1,2 +1,2 @@\r\n def hello():\r\n-    print('Hello, World!')\r\n+    print('Hello, Windows!')\r\n"

        result = git_apply_patch_atomic(git_repo, patch)

        assert result["ok"] is True
        content = (git_repo / "test.py").read_text()
        assert "Hello, Windows!" in content

    def test_empty_patch(self, git_repo):
        """Test handling of empty patch."""
        result = git_apply_patch_atomic(git_repo, "")

        # Empty patch should be rejected
        assert result["ok"] is False

    def test_malformed_patch(self, git_repo):
        """Test handling of completely malformed patch."""
        malformed = "This is not a valid patch at all!"

        result = git_apply_patch_atomic(git_repo, malformed)

        assert result["ok"] is False

    def test_concurrent_modification_protection(self, git_repo):
        """Test that modifications during patch application are detected."""
        patch = """--- a/test.py
+++ b/test.py
@@ -1,2 +1,2 @@
 def hello():
-    print('Hello, World!')
+    print('Hello, Modified!')
"""
        # First apply should work
        result1 = git_apply_patch_atomic(git_repo, patch)
        assert result1["ok"] is True

        # Now manually modify the file
        (git_repo / "test.py").write_text("def hello():\n    print('Concurrent edit!')\n")

        # Try to apply a different patch
        patch2 = """--- a/test.py
+++ b/test.py
@@ -1,2 +1,2 @@
 def hello():
-    print('Hello, World!')
+    print('Hello, Again!')
"""
        result2 = git_apply_patch_atomic(git_repo, patch2)

        # This should fail because the file no longer matches
        assert result2["ok"] is False
