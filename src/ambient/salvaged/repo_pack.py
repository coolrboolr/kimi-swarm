import json
from pathlib import Path
from typing import Any

IMPORTANT_FILES = [
    "pyproject.toml",
    "ruff.toml",
    "setup.cfg",
    "requirements.txt",
    "Makefile",
    "README.md",
    ".github/workflows/ci.yml",
]


def _read_cap(p: Path, cap: int = 200_000) -> str:
    try:
        return p.read_text(encoding="utf-8")[:cap]
    except Exception:
        return ""


def build_repo_pack(
    root: Path,
    task: dict[str, Any],
    tree: dict[str, Any],
    failing_logs: str,
    current_diff: str,
    hot_paths: list[str] | None = None,
    conventions: dict[str, Any] | None = None,
) -> str:
    pack: dict[str, Any] = {
        "task": task,
        "tree": tree,
        "important_files": {},
        "failing_logs": failing_logs,
        "current_diff": current_diff,
        "hot_paths": hot_paths or [],
        "conventions": conventions or {},
    }

    # Include important config files
    for f in IMPORTANT_FILES:
        fp = root / f
        if fp.exists() and fp.is_file():
            if "important_files" in pack and isinstance(pack["important_files"], dict):
                pack["important_files"][f] = _read_cap(fp)

    # Include Python source files, prioritizing hot_paths for impact-aware context.
    python_files: list[str] = []
    seen_python: set[str] = set()

    for file_path in (hot_paths or []):
        if file_path.endswith((".py", ".pyi")) and file_path not in seen_python:
            python_files.append(file_path)
            seen_python.add(file_path)

    if tree and "files" in tree:
        for file_path in tree["files"]:
            if file_path.endswith((".py", ".pyi")) and file_path not in seen_python:
                python_files.append(file_path)
                seen_python.add(file_path)

    # Cap included source files to avoid context overflow.
    for file_path in python_files[:50]:
        fp = root / file_path
        if fp.exists() and fp.is_file():
            pack["important_files"][file_path] = _read_cap(fp)

    return json.dumps(pack, ensure_ascii=False)
