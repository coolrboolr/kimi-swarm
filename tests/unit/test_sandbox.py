"""Unit tests for sandbox.py - Docker sandbox execution."""

import os
import subprocess

import pytest

from ambient.salvaged.sandbox import SandboxRunner


@pytest.fixture
def test_repo(tmp_path):
    """Create a temporary test repository."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Create a simple test file
    test_file = repo_path / "test.py"
    test_file.write_text("print('Hello from test repo')\n")

    return repo_path


@pytest.fixture
def sandbox_image():
    """Get or skip if sandbox image doesn't exist."""
    # Check if Docker is available
    try:
        subprocess.run(
            ["docker", "--version"],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("Docker not available")

    # For testing, we'll use a minimal image
    return "python:3.11-slim"


class TestSandboxRunner:
    """Test sandbox command execution."""

    def test_stub_mode_execution(self, test_repo):
        """Test stub mode (no Docker, direct execution)."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image="unused",
            stub=True,
        )

        result = sandbox.run(["python", "-c", "print('Hello from stub')"])

        assert result["exit_code"] == 0
        assert "Hello from stub" in result["stdout"]
        assert result["duration_s"] >= 0

    def test_stub_mode_python_script(self, test_repo):
        """Test running Python script in stub mode."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image="unused",
            stub=True,
        )

        result = sandbox.run(["python", "test.py"])

        assert result["exit_code"] == 0
        assert "Hello from test repo" in result["stdout"]

    def test_stub_mode_command_failure(self, test_repo):
        """Test failed command in stub mode."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image="unused",
            stub=True,
        )

        result = sandbox.run(["python", "-c", "import sys; sys.exit(42)"])

        assert result["exit_code"] == 42

    def test_stub_mode_missing_executable_returns_structured_error(self, test_repo):
        """Missing binaries should not raise; return structured 127 result."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image="unused",
            stub=True,
        )

        result = sandbox.run(["definitely-not-a-real-binary-ambient-test"])

        assert result["exit_code"] == 127
        assert "Command not found" in result["stderr"]

    def test_forced_failure_mode(self, test_repo):
        """Test forced failure mode via fail_run parameter."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image="unused",
            fail_run=True,
        )

        result = sandbox.run(["python", "-c", "print('This should fail')"])

        assert result["exit_code"] == 1
        assert "Forced sandbox failure" in result["stderr"]

    def test_forced_failure_via_env_var(self, test_repo, monkeypatch):
        """Test forced failure mode via environment variable."""
        monkeypatch.setenv("AMBIENT_FAIL_SANDBOX_RUN", "1")

        sandbox = SandboxRunner(
            repo_root=test_repo,
            image="unused",
        )

        result = sandbox.run(["python", "-c", "print('This should fail')"])

        assert result["exit_code"] == 1
        assert "Forced sandbox failure" in result["stderr"]

    def test_stub_mode_via_env_var(self, test_repo, monkeypatch):
        """Test stub mode via environment variable."""
        monkeypatch.setenv("AMBIENT_SANDBOX_STUB", "1")

        sandbox = SandboxRunner(
            repo_root=test_repo,
            image="unused",
        )

        result = sandbox.run(["python", "-c", "print('Stub via env')"])

        assert result["exit_code"] == 0
        assert "Stub via env" in result["stdout"]

    def test_network_mode_configuration(self, test_repo):
        """Test network mode configuration."""
        sandbox_none = SandboxRunner(
            repo_root=test_repo,
            image="test-image",
            network="none",
            stub=True,
        )
        assert sandbox_none.network == "none"

        sandbox_host = SandboxRunner(
            repo_root=test_repo,
            image="test-image",
            network="host",
            stub=True,
        )
        assert sandbox_host.network == "host"

    def test_back_compat_swarmguard_env_vars(self, test_repo, monkeypatch):
        """Old SWARMGUARD_* env vars still work for compatibility."""
        monkeypatch.setenv("SWARMGUARD_SANDBOX_STUB", "1")

        sandbox = SandboxRunner(
            repo_root=test_repo,
            image="unused",
        )
        result = sandbox.run(["python", "-c", "print('ok')"])
        assert result["exit_code"] == 0

    def test_timeout_in_stub_mode(self, test_repo):
        """Test timeout enforcement in stub mode."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image="unused",
            stub=True,
        )

        # This should timeout after 1 second
        with pytest.raises(subprocess.TimeoutExpired):
            sandbox.run(["sleep", "10"], timeout_s=1)

    def test_argv_preserved(self, test_repo):
        """Test that argv is preserved in result."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image="unused",
            stub=True,
        )

        argv = ["python", "-c", "print('test command')"]
        result = sandbox.run(argv)

        assert result["argv"] == argv

    def test_allowlist_enforced_rejects_disallowed(self, test_repo):
        """Test allowlist enforcement rejects commands not matching patterns."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image="unused",
            stub=True,
            allowed_argv=[["python", "-c"]],
            enforce_allowlist=True,
        )

        allowed = sandbox.run(["python", "-c", "print('ok')"])
        assert allowed["exit_code"] == 0

        rejected = sandbox.run(["ls"])
        assert rejected["exit_code"] == 126
        assert "rejected" in rejected and rejected["rejected"] is True

    def test_newlines_rejected_even_if_prefix_allowed(self, test_repo):
        """Prevent allowlist bypass via embedded newlines."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image="unused",
            stub=True,
            allowed_argv=[["python", "-c"]],
            enforce_allowlist=True,
        )

        rejected = sandbox.run(["python", "-c", "print('x')\nuname -a"])
        assert rejected["exit_code"] == 126
        assert "Newlines" in rejected["stderr"]

    def test_fail_closed_when_allowlist_empty(self, test_repo):
        """If allowlist enforcement is enabled with an empty allowlist, reject all."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image="unused",
            stub=True,
            allowed_argv=[],
            enforce_allowlist=True,
        )

        rejected = sandbox.run(["python", "-c", "print('ok')"])
        assert rejected["exit_code"] == 126
        assert "allowlist is empty" in rejected["stderr"].lower()

    @pytest.mark.skipif(
        os.getenv("SKIP_DOCKER_TESTS") == "1",
        reason="Docker tests skipped",
    )
    def test_docker_execution(self, test_repo, sandbox_image):
        """Test actual Docker execution (integration test)."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image=sandbox_image,
            network="none",
        )

        result = sandbox.run(["python", "-c", "print('Hello from Docker')"])

        assert result["exit_code"] == 0
        assert "Hello from Docker" in result["stdout"]

    @pytest.mark.skipif(
        os.getenv("SKIP_DOCKER_TESTS") == "1",
        reason="Docker tests skipped",
    )
    def test_docker_network_isolation(self, test_repo, sandbox_image):
        """Test that network isolation works (should fail to ping)."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image=sandbox_image,
            network="none",
        )

        # This should fail because network is disabled (best-effort; environment-dependent).
        result = sandbox.run(
            [
                "python",
                "-c",
                "import socket; s=socket.socket(); s.settimeout(1); s.connect(('1.1.1.1', 53))",
            ]
        )
        assert result["exit_code"] != 0

    @pytest.mark.skipif(
        os.getenv("SKIP_DOCKER_TESTS") == "1",
        reason="Docker tests skipped",
    )
    def test_docker_working_directory(self, test_repo, sandbox_image):
        """Test that working directory is set correctly."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image=sandbox_image,
            network="none",
        )

        result = sandbox.run(["pwd"])

        assert result["exit_code"] == 0
        assert "/repo" in result["stdout"]

    @pytest.mark.skipif(
        os.getenv("SKIP_DOCKER_TESTS") == "1",
        reason="Docker tests skipped",
    )
    def test_docker_volume_mount(self, test_repo, sandbox_image):
        """Test that repository is mounted correctly."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image=sandbox_image,
            network="none",
        )

        result = sandbox.run(["ls", "test.py"])

        assert result["exit_code"] == 0
        assert "test.py" in result["stdout"]

    @pytest.mark.skipif(
        os.getenv("SKIP_DOCKER_TESTS") == "1",
        reason="Docker tests skipped",
    )
    def test_docker_python_execution(self, test_repo, sandbox_image):
        """Test running Python code in Docker."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image=sandbox_image,
            network="none",
        )

        result = sandbox.run(["python", "test.py"])

        assert result["exit_code"] == 0
        assert "Hello from test repo" in result["stdout"]


class TestSandboxResultStructure:
    """Test the structure of sandbox results."""

    def test_result_contains_all_fields(self, test_repo):
        """Test that result dict contains all expected fields."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image="unused",
            stub=True,
        )

        result = sandbox.run(["python", "-c", "print('test')"])

        # Check all expected fields are present
        assert "argv" in result
        assert "exit_code" in result
        assert "stdout" in result
        assert "stderr" in result
        assert "duration_s" in result

    def test_result_types(self, test_repo):
        """Test that result field types are correct."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image="unused",
            stub=True,
        )

        result = sandbox.run(["python", "-c", "print('test')"])

        assert isinstance(result["argv"], list)
        assert isinstance(result["exit_code"], int)
        assert isinstance(result["stdout"], str)
        assert isinstance(result["stderr"], str)
        assert isinstance(result["duration_s"], (int, float))

    def test_stderr_capture(self, test_repo):
        """Test that stderr is captured correctly."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image="unused",
            stub=True,
        )

        result = sandbox.run(
            ["python", "-c", "import sys; sys.stderr.write('error message\\n')"]
        )

        assert result["exit_code"] == 0
        assert "error message" in result["stderr"]

    def test_empty_stdout_stderr(self, test_repo):
        """Test handling of empty stdout/stderr."""
        sandbox = SandboxRunner(
            repo_root=test_repo,
            image="unused",
            stub=True,
        )

        result = sandbox.run(["python", "-c", "import sys"])

        assert result["exit_code"] == 0
        assert result["stdout"] == ""
        assert result["stderr"] == ""
