"""Unit tests for safe_paths.py - path safety validation."""


import pytest

from ambient.salvaged.safe_paths import FORBIDDEN_COMPONENTS, safe_resolve


class TestSafeResolve:
    """Test safe path resolution."""

    def test_resolve_simple_relative_path(self, tmp_path):
        """Test resolving a simple relative path."""
        result = safe_resolve(tmp_path, "src/main.py")
        assert result == tmp_path / "src/main.py"

    def test_resolve_nested_relative_path(self, tmp_path):
        """Test resolving a nested relative path."""
        result = safe_resolve(tmp_path, "src/ambient/config.py")
        assert result == tmp_path / "src/ambient/config.py"

    def test_reject_absolute_path(self, tmp_path):
        """Test that absolute paths are rejected."""
        with pytest.raises(ValueError, match="Absolute paths not allowed"):
            safe_resolve(tmp_path, "/etc/passwd")

    def test_reject_parent_directory_escape(self, tmp_path):
        """Test that parent directory escapes are rejected."""
        with pytest.raises(ValueError, match="Path escapes repo root"):
            safe_resolve(tmp_path, "../../etc/passwd")

    def test_reject_multiple_parent_escapes(self, tmp_path):
        """Test multiple levels of parent directory escape."""
        with pytest.raises(ValueError, match="Path escapes repo root"):
            safe_resolve(tmp_path, "../../../root/.ssh/id_rsa")

    def test_reject_git_directory(self, tmp_path):
        """Test that .git directory access is forbidden."""
        with pytest.raises(ValueError, match="Forbidden path component"):
            safe_resolve(tmp_path, ".git/config")

    def test_reject_env_file(self, tmp_path):
        """Test that .env file access is forbidden."""
        with pytest.raises(ValueError, match="Forbidden path component"):
            safe_resolve(tmp_path, ".env")

    def test_reject_ssh_directory(self, tmp_path):
        """Test that .ssh directory access is forbidden."""
        with pytest.raises(ValueError, match="Forbidden path component"):
            safe_resolve(tmp_path, ".ssh/id_rsa")

    def test_reject_swarmguard_secrets(self, tmp_path):
        """Test that .swarmguard_secrets access is forbidden."""
        with pytest.raises(ValueError, match="Forbidden path component"):
            safe_resolve(tmp_path, ".swarmguard_secrets/api_key")

    def test_allow_dotfile_not_forbidden(self, tmp_path):
        """Test that non-forbidden dotfiles are allowed."""
        result = safe_resolve(tmp_path, ".gitignore")
        assert result == tmp_path / ".gitignore"

    def test_allow_nested_forbidden_in_filename(self, tmp_path):
        """Test that forbidden component in filename (not directory) is still caught."""
        # This should be rejected because ".git" is in the path components
        with pytest.raises(ValueError, match="Forbidden path component"):
            safe_resolve(tmp_path, "src/.git/hooks")

    def test_symlink_escape_attempt(self, tmp_path):
        """Test that symlink escapes are caught."""
        # Create a directory structure
        safe_dir = tmp_path / "safe"
        safe_dir.mkdir()

        # Create a symlink that points outside
        symlink_path = safe_dir / "escape"
        try:
            symlink_path.symlink_to("/etc")
        except OSError:
            pytest.skip("Cannot create symlinks on this system")

        # Attempting to resolve through the symlink should fail
        with pytest.raises(ValueError, match="Path escapes repo root"):
            safe_resolve(tmp_path, "safe/escape/passwd")

    def test_current_directory_reference(self, tmp_path):
        """Test that current directory references work."""
        result = safe_resolve(tmp_path, "./src/main.py")
        assert result == tmp_path / "src/main.py"

    def test_normalize_path_separators(self, tmp_path):
        """Test that different path separators are normalized."""
        result = safe_resolve(tmp_path, "src/ambient/config.py")
        expected = tmp_path / "src" / "ambient" / "config.py"
        assert result == expected

    def test_empty_path(self, tmp_path):
        """Test handling of empty path."""
        result = safe_resolve(tmp_path, "")
        assert result == tmp_path

    def test_root_directory_itself(self, tmp_path):
        """Test resolving to root directory itself."""
        result = safe_resolve(tmp_path, ".")
        assert result == tmp_path

    def test_all_forbidden_components(self, tmp_path):
        """Test all forbidden components are blocked."""
        for component in FORBIDDEN_COMPONENTS:
            with pytest.raises(ValueError, match="Forbidden path component"):
                safe_resolve(tmp_path, component)

    def test_forbidden_component_in_subdirectory(self, tmp_path):
        """Test forbidden components in subdirectories are caught."""
        with pytest.raises(ValueError, match="Forbidden path component"):
            safe_resolve(tmp_path, "src/.env/config")
