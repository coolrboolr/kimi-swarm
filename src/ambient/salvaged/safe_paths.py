from pathlib import Path

FORBIDDEN_COMPONENTS = {".git", ".env", ".ssh", ".swarmguard_secrets"}


def safe_resolve(root: Path, rel_path: str) -> Path:
    if rel_path.startswith("/"):
        raise ValueError("Absolute paths not allowed")
    p = (root / rel_path).resolve()
    if root != p and root not in p.parents:
        raise ValueError("Path escapes repo root")
    for part in p.parts:
        if part in FORBIDDEN_COMPONENTS:
            raise ValueError(f"Forbidden path component: {part}")
    return p
