from pathlib import Path

FORBIDDEN_COMPONENTS = {".git", ".env", ".ssh", ".swarmguard_secrets"}


def safe_resolve(root: Path, rel_path: str) -> Path:
    # Normalize root to avoid false "escape" on platforms where `resolve()`
    # canonicalizes paths (e.g., macOS /var -> /private/var).
    root = root.resolve()
    if rel_path.startswith("/"):
        raise ValueError("Absolute paths not allowed")
    p = (root / rel_path).resolve()
    if root != p and root not in p.parents:
        raise ValueError("Path escapes repo root")
    for part in p.parts:
        if part in FORBIDDEN_COMPONENTS:
            raise ValueError(f"Forbidden path component: {part}")
    return p
