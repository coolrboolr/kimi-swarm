from __future__ import annotations

import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Common API key shapes (best-effort).
    (re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"), "sk-REDACTED"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA_REDACTED"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "PRIVATE_KEY_REDACTED"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "ghp_REDACTED"),
]


def redact_text(s: str, *, max_len: int = 400) -> str:
    """Redact common secret patterns and truncate.

    This is intentionally conservative. Telemetry should avoid capturing full outputs
    (diffs, tokens, secrets) unless explicitly enabled.
    """
    if not s:
        return ""
    out = s
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    out = out.strip()
    if len(out) > max_len:
        out = out[:max_len] + "...(truncated)"
    return out

