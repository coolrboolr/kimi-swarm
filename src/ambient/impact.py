"""Impact radius analysis for ambient review.

Expands beyond directly changed files so agents and verification can inspect
adjacent modules and likely tests.
"""

from __future__ import annotations

import re
from pathlib import Path

_DIFF_PATH_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)
_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w\.]+)\s+import\s+|import\s+([\w\.,\s]+))",
    re.MULTILINE,
)


def extract_changed_paths(event_rel_path: str | None, current_diff: str) -> list[str]:
    """Extract changed repo-relative paths from event metadata and git diff."""
    seen: set[str] = set()
    ordered: list[str] = []

    if event_rel_path:
        p = event_rel_path.strip()
        if p and p not in seen:
            seen.add(p)
            ordered.append(p)

    for match in _DIFF_PATH_RE.finditer(current_diff or ""):
        p = match.group(1).strip()
        if p and p != "/dev/null" and p not in seen:
            seen.add(p)
            ordered.append(p)

    return ordered


def compute_impact_radius(
    repo_path: Path,
    tree_files: list[str],
    changed_paths: list[str],
    max_files: int = 120,
) -> list[str]:
    """Compute an impact radius around changed files for ambient analysis.

    Heuristics:
    - Changed files are always included.
    - For Python files, include direct import dependencies and reverse-importers.
    - Include nearby tests using common pytest naming conventions.
    """
    normalized_tree = [p for p in tree_files if p and not p.endswith("/")]
    tree_set = set(normalized_tree)

    changed = [p for p in changed_paths if p in tree_set]
    if not changed:
        return []

    module_by_path: dict[str, str] = {}
    path_by_module: dict[str, str] = {}
    for p in normalized_tree:
        if not p.endswith(".py"):
            continue
        module = _module_name_from_path(p)
        if not module:
            continue
        module_by_path[p] = module
        path_by_module[module] = p

    imports_by_path: dict[str, set[str]] = {}
    for p in module_by_path:
        imports_by_path[p] = _parse_python_imports(repo_path / p)

    importers_by_path: dict[str, set[str]] = {p: set() for p in module_by_path}
    for path, imports in imports_by_path.items():
        for imported_mod in imports:
            imported_path = _resolve_module_to_path(imported_mod, path_by_module)
            if imported_path:
                importers_by_path.setdefault(imported_path, set()).add(path)

    ordered: list[str] = []
    seen: set[str] = set()

    def add_path(path: str) -> None:
        if path not in seen and path in tree_set:
            seen.add(path)
            ordered.append(path)

    for path in changed:
        add_path(path)

    for path in changed:
        if path not in module_by_path:
            continue

        # Direct dependencies and reverse dependencies.
        for imported_mod in sorted(imports_by_path.get(path, set())):
            dep = _resolve_module_to_path(imported_mod, path_by_module)
            if dep:
                add_path(dep)
        for importer in sorted(importers_by_path.get(path, set())):
            add_path(importer)

        # Likely tests touching the changed module.
        for test_path in _candidate_test_paths(path):
            add_path(test_path)

    return ordered[: max(1, max_files)]


def _module_name_from_path(path: str) -> str:
    if not path.endswith(".py"):
        return ""
    if path.endswith("/__init__.py"):
        path = path[: -len("/__init__.py")]
    else:
        path = path[:-3]
    return path.replace("/", ".").strip(".")


def _parse_python_imports(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return set()

    imports: set[str] = set()
    for m in _IMPORT_RE.finditer(text):
        from_mod = (m.group(1) or "").strip()
        import_mods = (m.group(2) or "").strip()
        if from_mod:
            imports.add(from_mod)
            continue
        if import_mods:
            for part in import_mods.split(","):
                mod = part.strip().split(" as ", 1)[0].strip()
                if mod:
                    imports.add(mod)
    return imports


def _resolve_module_to_path(module: str, path_by_module: dict[str, str]) -> str | None:
    if module in path_by_module:
        return path_by_module[module]

    # Handle "import pkg.sub.mod" when only parent is directly tracked.
    parts = module.split(".")
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in path_by_module:
            return path_by_module[candidate]
    return None


def _candidate_test_paths(path: str) -> list[str]:
    p = Path(path)
    stem = p.stem

    if stem == "__init__":
        stem = p.parent.name

    candidates = [
        f"tests/test_{stem}.py",
        f"tests/{p.parent}/test_{stem}.py",
        f"test/test_{stem}.py",
    ]

    # Normalize any duplicate slashes.
    normalized: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        n = str(Path(c))
        if n not in seen:
            seen.add(n)
            normalized.append(n)
    return normalized
