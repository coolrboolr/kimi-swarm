"""Global pytest configuration for hermetic test runs."""

from __future__ import annotations

import os


def pytest_sessionstart(session):  # noqa: ARG001
    # Prevent accidental outbound network during tests (integration/unit).
    os.environ.setdefault("AMBIENT_DISABLE_NETWORK", "1")

    # Most environments won't have Docker available; allow explicit opt-in.
    os.environ.setdefault("SKIP_DOCKER_TESTS", "1")

