import os
import re
import subprocess
from pathlib import Path
from typing import Any


class PatchApplyError(RuntimeError):
    pass


def _run(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(root), text=True, capture_output=True)


def git_reset_hard_clean(root: Path) -> None:
    _run(root, ["git", "reset", "--hard"])
    _run(root, ["git", "clean", "-fd"])


def git_status_porcelain(root: Path) -> list[str]:
    """Return `git status --porcelain` lines (empty list means clean)."""
    res = _run(root, ["git", "status", "--porcelain"])
    if res.returncode != 0:
        raise RuntimeError(f"git status failed: {res.stderr}")
    return [ln for ln in res.stdout.splitlines() if ln.strip()]


def git_has_staged_changes(root: Path) -> bool:
    """True if index has changes (`git diff --cached --quiet` is non-zero)."""
    res = _run(root, ["git", "diff", "--cached", "--quiet"])
    # 0 = no changes, 1 = changes, other = error
    if res.returncode == 0:
        return False
    if res.returncode == 1:
        return True
    raise RuntimeError(f"git diff --cached failed: {res.stderr}")


def git_is_clean(
    root: Path,
    ignored_untracked_prefixes: list[str] | None = None,
) -> bool:
    """True if worktree has no changes, ignoring selected untracked prefixes."""
    ignored_untracked_prefixes = ignored_untracked_prefixes or [
        ".ambient/",
        ".swarmguard/",
        ".swarmguard_artifacts/",
        ".pytest_cache/",
    ]
    for ln in git_status_porcelain(root):
        # "?? path" indicates untracked.
        if ln.startswith("?? "):
            path = ln[3:]
            if any(path.startswith(pfx) for pfx in ignored_untracked_prefixes):
                continue
        return False
    return True


def git_apply_patch_atomic(root: Path, unified_diff: str) -> dict[str, Any]:
    def clean_patch(diff: str) -> str:
        cleaned = diff.replace("\r\n", "\n").replace("\r", "\n").strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        idx = cleaned.find("diff --git")
        if idx != -1:
            cleaned = cleaned[idx:].strip()
        if not cleaned.endswith("\n"):
            cleaned += "\n"
        return cleaned

    def fix_hunk_counts(diff_text: str) -> str:
        lines = diff_text.splitlines()
        out: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("@@ "):
                match = re.match(
                    r"@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@",
                    line,
                )
                if not match:
                    out.append(line)
                    i += 1
                    continue
                old_start = int(match.group(1))
                new_start = int(match.group(3))
                old_count = 0
                new_count = 0
                j = i + 1
                while j < len(lines):
                    line_text = lines[j]
                    if line_text.startswith("diff --git") or line_text.startswith("@@ "):
                        break
                    if line_text.startswith("--- ") or line_text.startswith("+++ "):
                        break
                    if line_text.startswith("-"):
                        old_count += 1
                    elif line_text.startswith("+"):
                        new_count += 1
                    else:
                        old_count += 1
                        new_count += 1
                    j += 1
                out.append(f"@@ -{old_start},{old_count} +{new_start},{new_count} @@")
                out.extend(lines[i + 1 : j])
                i = j
                continue
            out.append(line)
            i += 1
        return "\n".join(out) + "\n"

    def detect_strip_level(diff_text: str) -> int:
        if "diff --git a/" in diff_text or "\n--- a/" in diff_text:
            return 1
        return 0

    def remove_index_lines(diff_text: str) -> str:
        if "index " not in diff_text:
            return diff_text
        return "\n".join(line for line in diff_text.splitlines() if not line.startswith("index "))

    def extract_paths(diff_text: str) -> list[str]:
        paths: list[str] = []
        for line in diff_text.splitlines():
            if not line.startswith("diff --git "):
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            b_path = parts[3]
            if b_path.startswith("b/"):
                b_path = b_path[2:]
            if b_path not in paths:
                paths.append(b_path)
        if paths:
            return paths
        for line in diff_text.splitlines():
            if line.startswith("+++ "):
                path = line[4:].strip()
                if path.startswith("b/"):
                    path = path[2:]
                if path not in paths:
                    paths.append(path)
        return paths

    def apply_unified_diff_fallback(diff_text: str) -> list[str]:
        files: dict[str, list[tuple[int, int, list[str]]]] = {}
        current_file: str | None = None
        current_hunks: list[tuple[int, int, list[str]]] | None = None
        hunk_lines: list[str] = []
        old_start = 0
        old_count = 0

        for line in diff_text.splitlines():
            if line.startswith("diff --git "):
                if current_file and hunk_lines and current_hunks is not None:
                    current_hunks.append((old_start, old_count, hunk_lines))
                    hunk_lines = []
                parts = line.split()
                if len(parts) >= 4:
                    b_path = parts[3]
                    if b_path.startswith("b/"):
                        b_path = b_path[2:]
                    current_file = b_path
                    current_hunks = files.setdefault(current_file, [])
                continue
            if line.startswith("--- ") or line.startswith("+++ ") or line.startswith("index "):
                continue
            if line.startswith("@@ "):
                if current_file is None:
                    continue
                if hunk_lines and current_hunks is not None:
                    current_hunks.append((old_start, old_count, hunk_lines))
                    hunk_lines = []
                match = re.match(
                    r"@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@",
                    line,
                )
                if not match:
                    continue
                old_start = int(match.group(1))
                old_count = int(match.group(2) or "1")
                continue
            if line.startswith((" ", "+", "-")):
                hunk_lines.append(line)

        if current_file and hunk_lines and current_hunks is not None:
            current_hunks.append((old_start, old_count, hunk_lines))

        written: list[str] = []
        for rel_path, hunks in files.items():
            if rel_path.startswith("/") or ".." in Path(rel_path).parts:
                raise PatchApplyError(f"unsafe path in patch: {rel_path}")
            file_path = root / rel_path
            if file_path.exists():
                original_lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
            else:
                original_lines = []
            if len(hunks) == 1:
                h_start, h_count, h_lines = hunks[0]
                if h_start == 1 and len(original_lines) == h_count:
                    replacement_lines = [
                        line[1:] + "\n"
                        for line in h_lines
                        if line.startswith(("+", " "))
                    ]
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text("".join(replacement_lines), encoding="utf-8")
                    written.append(rel_path)
                    continue
            new_lines: list[str] = []
            idx = 0
            for h_start, _h_count, h_lines in hunks:
                h_start_idx = max(h_start - 1, 0)
                new_lines.extend(original_lines[idx:h_start_idx])
                idx = h_start_idx
                for h_line in h_lines:
                    if h_line.startswith(" "):
                        if idx >= len(original_lines) or original_lines[idx].rstrip("\r\n") != h_line[1:]:
                            raise PatchApplyError("hunk context mismatch")
                        new_lines.append(original_lines[idx])
                        idx += 1
                    elif h_line.startswith("-"):
                        if idx >= len(original_lines) or original_lines[idx].rstrip("\r\n") != h_line[1:]:
                            raise PatchApplyError("hunk removal mismatch")
                        idx += 1
                    elif h_line.startswith("+"):
                        new_lines.append(h_line[1:] + "\n")
            new_lines.extend(original_lines[idx:])
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text("".join(new_lines), encoding="utf-8")
            written.append(rel_path)
        return written

    unified_diff = clean_patch(unified_diff)
    debug_path = os.getenv("SWARMGUARD_PATCH_DEBUG_PATH")
    if debug_path:
        try:
            Path(debug_path).write_text(unified_diff, encoding="utf-8")
        except OSError:
            pass
    scratch = root / ".swarmguard"
    scratch.mkdir(exist_ok=True)
    patch_path = scratch / "apply.patch"
    artifacts_dir = root / ".swarmguard_artifacts"
    debug_bundle_dir = artifacts_dir / "patch_debug"

    apply_attempts: list[dict[str, Any]] = []

    def _apply_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        c = _run(root, args)
        apply_attempts.append(
            {
                "args": args,
                "returncode": c.returncode,
                "stdout": c.stdout,
                "stderr": c.stderr,
            }
        )
        return c

    def _write_debug_bundle(diff_text: str) -> None:
        try:
            debug_bundle_dir.mkdir(parents=True, exist_ok=True)
            (debug_bundle_dir / "patch.diff").write_text(diff_text, encoding="utf-8")
            status = _run(root, ["git", "status", "--porcelain"]).stdout
            (debug_bundle_dir / "status.txt").write_text(status, encoding="utf-8")
            diff_stat = _run(root, ["git", "diff", "--stat"]).stdout
            (debug_bundle_dir / "diff_stat.txt").write_text(diff_stat, encoding="utf-8")
            errors = "\n\n".join(
                [
                    f"$ {' '.join(a['args'])}\nrc={a['returncode']}\nstdout={a['stdout']}\nstderr={a['stderr']}"
                    for a in apply_attempts
                ]
            )
            (debug_bundle_dir / "apply_errors.txt").write_text(errors, encoding="utf-8")
            for rel_path in extract_paths(diff_text):
                if rel_path.startswith("/") or ".." in Path(rel_path).parts:
                    continue
                file_path = root / rel_path
                if not file_path.exists():
                    continue
                try:
                    head_lines = file_path.read_text(encoding="utf-8").splitlines()[:80]
                except OSError:
                    continue
                safe_name = rel_path.replace("/", "__")
                (debug_bundle_dir / f"head_{safe_name}.txt").write_text(
                    "\n".join(head_lines) + "\n",
                    encoding="utf-8",
                )
        except OSError:
            pass

    def apply_with_git(diff_text: str) -> dict[str, Any]:
        patch_path.write_text(diff_text, encoding="utf-8")
        strip_primary = detect_strip_level(diff_text)
        strip_levels = [strip_primary, 1 - strip_primary]

        for strip in strip_levels:
            c_rev = _apply_run(
                ["git", "apply", "--check", "-R", f"-p{strip}", str(patch_path)]
            )
            if c_rev.returncode == 0:
                paths = extract_paths(diff_text)
                add_cmd = ["git", "add", "--"] + paths if paths else ["git", "add", "-A"]
                c_add = _run(root, add_cmd)
                if c_add.returncode != 0:
                    raise PatchApplyError(c_add.stderr)
                stat = _run(root, ["git", "diff", "--cached", "--stat"]).stdout
                return {"ok": True, "stat": stat, "stderr": "", "status": "already_applied"}

            c_check = _apply_run(["git", "apply", "--check", f"-p{strip}", str(patch_path)])
            if c_check.returncode == 0:
                c_apply = _apply_run(["git", "apply", f"-p{strip}", str(patch_path)])
                if c_apply.returncode == 0:
                    paths = extract_paths(diff_text)
                    add_cmd = ["git", "add", "--"] + paths if paths else ["git", "add", "-A"]
                    c_add = _run(root, add_cmd)
                    if c_add.returncode != 0:
                        raise PatchApplyError(c_add.stderr)
                    stat = _run(root, ["git", "diff", "--cached", "--stat"]).stdout
                    return {"ok": True, "stat": stat, "stderr": ""}

            c_3way = _apply_run(["git", "apply", "--3way", f"-p{strip}", str(patch_path)])
            if c_3way.returncode == 0:
                paths = extract_paths(diff_text)
                add_cmd = ["git", "add", "--"] + paths if paths else ["git", "add", "-A"]
                c_add = _run(root, add_cmd)
                if c_add.returncode != 0:
                    raise PatchApplyError(c_add.stderr)
                stat = _run(root, ["git", "diff", "--cached", "--stat"]).stdout
                return {"ok": True, "stat": stat, "stderr": ""}

        try:
            paths = apply_unified_diff_fallback(diff_text)
            if not paths:
                raise PatchApplyError("empty patch after fallback")
            add_cmd = ["git", "add", "--"] + paths
            c4 = _run(root, add_cmd)
            if c4.returncode != 0:
                raise PatchApplyError(c4.stderr)
            stat = _run(root, ["git", "diff", "--cached", "--stat"]).stdout
            return {"ok": True, "stat": stat, "stderr": ""}
        except Exception as err:  # noqa: BLE001
            raise PatchApplyError(str(err))

    try:
        if os.getenv("SWARMGUARD_PATCH_PREFER_FALLBACK") == "1":
            try:
                paths = apply_unified_diff_fallback(unified_diff)
                if paths:
                    add_cmd = ["git", "add", "--"] + paths if paths else ["git", "add", "-A"]
                    c0 = _run(root, add_cmd)
                    if c0.returncode != 0:
                        raise PatchApplyError(c0.stderr)
                    stat = _run(root, ["git", "diff", "--cached", "--stat"]).stdout
                    return {"ok": True, "stat": stat, "stderr": ""}
            except PatchApplyError:
                pass
        candidates = [unified_diff]
        fixed = fix_hunk_counts(unified_diff)
        if fixed != unified_diff:
            candidates.append(fixed)
        last_err: Exception | None = None
        for candidate in candidates:
            try:
                return apply_with_git(candidate)
            except PatchApplyError as exc:
                last_err = exc
        raise PatchApplyError(str(last_err) if last_err else "patch apply failed")
    except Exception as e:
        _write_debug_bundle(unified_diff)
        git_reset_hard_clean(root)
        return {"ok": False, "stat": "", "stderr": str(e)}


def git_create_branch(root: Path, branch_name: str) -> None:
    exists = _run(root, ["git", "rev-parse", "--verify", branch_name])
    if exists.returncode == 0:
        raise RuntimeError(f"Branch already exists: {branch_name}")
    _run(root, ["git", "checkout", "-b", branch_name])


def git_add(root: Path, paths: list[str] | None = None) -> None:
    args = ["git", "add"]
    if paths:
        args.extend(paths)
    else:
        args.append("-A")
    res = _run(root, args)
    if res.returncode != 0:
        raise RuntimeError(f"git add failed: {res.stderr}")


def git_commit(
    root: Path,
    message: str,
    author_name: str = "SwarmGuard Bot",
    author_email: str = "swarmguard@bot.com",
) -> None:
    # Configure user for commit if needed?
    # Assume global config or env vars set or we set local config
    _run(root, ["git", "config", "user.email", author_email])
    _run(root, ["git", "config", "user.name", author_name])

    res = _run(root, ["git", "commit", "-m", message])
    if res.returncode != 0 and "nothing to commit" not in res.stdout:
        raise RuntimeError(f"Commit failed: {res.stderr}")
