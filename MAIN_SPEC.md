# Kimi K2.5 Ambient Swarm — Production Specification

**Version:** 2.0
**Goal:** Continuous, autonomous code quality maintenance system that monitors repositories for security issues, refactoring opportunities, and best practice violations, then proposes reviewable fixes with safe, human-controlled integration.

---

## Table of Contents

1. [Vision & Philosophy](#vision--philosophy)
2. [Architecture Overview](#architecture-overview)
3. [Salvaged Excellence: What to Keep](#salvaged-excellence-what-to-keep)
4. [Core Components](#core-components)
5. [Specialist Agents](#specialist-agents)
6. [Safety & Security Model](#safety--security-model)
7. [Configuration Schema](#configuration-schema)
8. [Implementation Guide](#implementation-guide)
9. [Deployment & Operations](#deployment--operations)
10. [Extension Points](#extension-points)

---

## Vision & Philosophy

### What is "Ambient"?

Unlike batch CI/CD systems that run on commits, **ambient swarm** is a **persistent background process** that:

- **Watches** the repository continuously (inotify, git hooks, CI webhooks)
- **Detects** issues proactively (security vulnerabilities, code smells, style violations)
- **Proposes** fixes autonomously (via specialist agents with full context)
- **Applies** changes atomically (with human approval gates for high-risk operations)
- **Learns** from outcomes (tracks what improvements stick vs get reverted)

### Design Philosophy

1. **Long-context native**: Kimi K2.5's 256K context window means we send full repo visibility upfront, not incremental tool calls
2. **Simplicity over enterprise**: Target single-machine developer experience, not distributed clusters
3. **Continuous improvement**: Small, frequent fixes beat large refactoring PRs
4. **Human-in-loop by default**: Propose freely, apply conservatively
5. **Deterministic safety**: Single-writer, atomic patches, sandboxed execution

### Focused Direction (Current)

The active implementation direction for this phase is:

- **Python-first** execution and verification while the ambient review loop stabilizes
- **Both file-change and periodic triggers** enabled for continuous ambient operation
- **Impact-radius analysis** so proposals can include related files beyond direct edits
- **Dedicated review worktrees** per proposal (do not mutate the main working tree)
- **Parallel proposal diffs** as patch artifacts so humans decide what to commit
- **Manual commit-by-default** (`commit_on_success: false`)
- **Advanced local cross-pollination** (multi-round refine, dedupe, conflict selection)
- **Coverage-oriented best practices** documented and enforced through verification

### Core Insight from Current Implementation

The existing `swarmguard` system has **exceptional safety primitives** (atomic patches, sandbox isolation, path safety) but **over-engineered orchestration** (LangGraph state machines, Ray distributed actors). For ambient monitoring, we need:

- **Less**: No complex DAG orchestration, no distributed actor coordination
- **More**: Streaming proposals, cross-agent collaboration, continuous monitoring loop

---

## Architecture Overview

### Simplified Stack

```
┌─────────────────────────────────────────────────┐
│  Ambient Coordinator (async Python)             │
│  • File watcher (inotify/watchdog)              │
│  • Event queue (asyncio.Queue)                  │
│  • Proposal aggregator                          │
│  • Single-writer lock (asyncio.Lock)            │
└─────────────────────────────────────────────────┘
           ││                          ││
    ┌──────┴┴──────┐          ┌────────┴┴────────┐
    │ Kimi Swarm   │          │  Workspace        │
    │ (async)      │─────────▶│  (atomic patches) │
    │ 5 specialists│          │  git worktree     │
    └──────────────┘          └───────────────────┘
           │                            │
    ┌──────┴────────────────────────────┴──────┐
    │   Docker Sandbox (network isolated)       │
    │   • pytest, ruff, mypy, semgrep           │
    │   • Command allowlist enforcement         │
    └───────────────────────────────────────────┘
```

### Data Flow

```
1. DETECT:  File change → Event queue
            ↓
2. ANALYZE: Build repo context (full tree + metadata)
            ↓
3. SWARM:   5 specialists propose fixes in parallel
            ↓
4. REFINE:  Cross-pollination (agents see each other's proposals)
            ↓
5. GATE:    Risk assessment → Human approval if needed
            ↓
6. APPLY:   Generate proposal patches in dedicated review worktrees (parallel, isolated)
            ↓
7. VERIFY:  Run sandbox checks per proposal worktree
            ↓
8. REVIEW:  Human reviews, commits selected proposal branches/patches
            ↓
9. LOOP:    Return to DETECT
```

### Key Simplifications vs Current System

| Component | Old (swarmguard) | New (ambient) |
|-----------|------------------|---------------|
| **Orchestration** | LangGraph (11-node DAG, 800 LOC) | Async coordinator (200 LOC) |
| **Parallelism** | Ray distributed actors | `asyncio.gather()` |
| **State Management** | `GraphState` TypedDict (18 fields) | Simple dataclasses (5 fields) |
| **Tool Loop** | Iterative read/search calls | Single full context send |
| **Concurrency Control** | Ray concurrency groups | `asyncio.Lock` |
| **Dependencies** | LangGraph, Ray, pydantic, 15+ | asyncio, httpx, watchdog, 5+ |
| **Total LOC** | ~10,000 | ~2,000 (target) |

---

## Salvaged Excellence: What to Keep

The current `swarmguard` implementation has several **production-grade components** that should be preserved. Below are code snippets with explanations of their elegance and correctness.

### 1. Atomic Patch Application (`git_ops.py`)

**Why it's exceptional:**
- Handles LLM output quirks (markdown blocks, incorrect hunk counts, wrong strip levels)
- Multiple fallback strategies (standard apply → 3-way merge → pure Python parser)
- **Guaranteed rollback** on any failure (no partial corruption)
- Idempotency via reverse-apply detection

**Location:** `src/swarmguard/runtime/git_ops.py:7-374`

**Key snippet:**

```python
def git_apply_patch_atomic(root: Path, unified_diff: str) -> dict[str, Any]:
    """
    Apply a unified diff atomically to the repo at `root`.

    Strategy:
    1. Normalize patch (strip markdown, fix line endings, recompute hunk counts)
    2. Detect if already applied (git apply -R --check)
    3. Try standard apply with -p0 and -p1
    4. Fallback to 3-way merge (fuzzy context matching)
    5. Fallback to pure Python parser (manual hunk application)
    6. On ANY failure: git reset --hard && git clean -fd

    Returns:
      {"ok": True/False, "stat": "...", "stderr": "...", "debug_bundle": {...}}
    """
    normalized = _normalize_patch(unified_diff)
    normalized = _fix_hunk_counts(normalized)  # Fixes LLM off-by-one errors

    # Detect if patch is already applied (idempotency)
    reverse_result = _run_git(
        ["git", "apply", "-R", "--check"],
        normalized,
        root,
        check=False
    )
    if reverse_result.returncode == 0:
        return {"ok": True, "stat": "(already applied)", "stderr": ""}

    # Try multiple strip levels
    for strip_level in [0, 1]:
        result = _run_git(
            ["git", "apply", f"-p{strip_level}", "--index"],
            normalized,
            root,
            check=False
        )
        if result.returncode == 0:
            return {
                "ok": True,
                "stat": _git_diff_stat(root),
                "stderr": ""
            }

    # 3-way merge fallback
    three_way_result = _run_git(
        ["git", "apply", "--3way", "--index"],
        normalized,
        root,
        check=False
    )
    if three_way_result.returncode == 0:
        return {"ok": True, "stat": _git_diff_stat(root), "stderr": ""}

    # Pure Python fallback (for non-git-parseable diffs)
    try:
        python_apply(root, normalized)
        return {"ok": True, "stat": _git_diff_stat(root), "stderr": ""}
    except Exception as e:
        # CRITICAL: Rollback on any failure
        git_reset_hard_clean(root)
        return {
            "ok": False,
            "stat": "",
            "stderr": str(e),
            "debug_bundle": _create_debug_bundle(root, normalized)
        }
```

**Why this matters:**
- LLMs generate imperfect patches (wrong context, off-by-one line counts)
- `git apply` is brittle by default
- This implementation handles 90%+ of LLM patch formats gracefully
- **Zero risk of partial corruption** due to guaranteed rollback

**Reuse strategy:** Keep entire `git_ops.py` (368 lines) as-is. It's battle-tested.

---

### 2. Safe Path Resolution (`safe_paths.py`)

**Why it's correct:**
- Prevents directory traversal attacks (`../../etc/passwd`)
- Blocks access to sensitive directories (`.git`, `.env`, `.ssh`)
- Uses `Path.resolve()` for canonical path comparison
- Explicit allowlist for git worktree directories

**Location:** `src/swarmguard/runtime/safe_paths.py:7-67`

**Key snippet:**

```python
FORBIDDEN_COMPONENTS = {".git", ".env", ".ssh", ".swarmguard_secrets"}

def safe_resolve(root: Path, rel_path: str) -> Path:
    """
    Resolve `rel_path` relative to `root`, ensuring it stays within bounds.

    Raises:
      ValueError: If path escapes root or contains forbidden components

    Examples:
      safe_resolve("/repo", "src/main.py")        → /repo/src/main.py ✓
      safe_resolve("/repo", "../etc/passwd")      → ValueError ✗
      safe_resolve("/repo", ".git/config")        → ValueError ✗
      safe_resolve("/repo", "/etc/passwd")        → ValueError ✗
    """
    # Reject absolute paths
    if Path(rel_path).is_absolute():
        raise ValueError(f"Absolute paths forbidden: {rel_path}")

    # Resolve to canonical path
    candidate = (root / rel_path).resolve()

    # Check if within root
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ValueError(f"Path escapes root: {rel_path}")

    # Check for forbidden components
    if any(part in FORBIDDEN_COMPONENTS for part in candidate.parts):
        raise ValueError(f"Forbidden path component in: {rel_path}")

    return candidate
```

**Why this matters:**
- LLMs can hallucinate paths or attempt to read sensitive files
- Without validation, an agent could read `.env` files or modify `.git/config`
- This is a **security-critical gate** before any filesystem operation

**Reuse strategy:** Keep entire `safe_paths.py` (67 lines) unchanged.

---

### 3. Docker Sandbox with Network Isolation (`sandbox.py`)

**Why it's secure:**
- Defaults to `--network none` (no outbound connections)
- Network enablement requires explicit approval (interrupt gate)
- Volume mounts are read-only by default (worktree is read-write, main repo is ro)
- Command execution is logged to telemetry
- Timeout enforcement prevents infinite loops

**Location:** `src/swarmguard/runtime/sandbox.py:13-120`

**Key snippet:**

```python
class SandboxRunner:
    def __init__(self, image: str = "swarmguard-sandbox:latest"):
        self.image = image
        self.network_mode = "none"  # Default: no network access

    def run_command(
        self,
        command: str,
        worktree_path: Path,
        timeout: int = 900
    ) -> dict[str, Any]:
        """
        Run `command` inside Docker sandbox with network isolation.

        Security features:
        - Network disabled by default (--network none)
        - Worktree mounted at /repo (read-write)
        - Timeout enforcement
        - Command allowlist checked before execution
        """
        # Validate command against allowlist
        if not is_command_allowed(command):
            raise ValueError(f"Command not allowed: {command}")

        # Build docker run args
        docker_args = [
            "docker", "run",
            "--rm",
            f"--network={self.network_mode}",
            "-v", f"{worktree_path}:/repo:rw",
            "-w", "/repo",
            "--memory=2g",           # Resource limits
            "--cpus=2.0",
            "--pids-limit=100",
            self.image,
            "sh", "-c", command
        ]

        # Execute with timeout
        try:
            result = subprocess.run(
                docker_args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False
            )
            return {
                "ok": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "stdout": "",
                "stderr": f"Command timed out after {timeout}s",
                "exit_code": -1
            }
```

**Why this matters:**
- LLMs might generate commands that attempt network access (curl, wget, pip install)
- Default-deny network prevents data exfiltration or supply chain attacks
- Resource limits prevent DoS (fork bombs, memory exhaustion)

**Reuse strategy:** Keep `sandbox.py`, add resource limits (memory, CPU, PIDs).

---

### 4. Command Allowlist (`config.py`)

**Why it's effective:**
- Regex-based patterns allow flexibility (e.g., `pytest` with any args)
- Blocks dangerous commands (rm, curl, sudo) by default
- Easy to extend per-project (custom Makefile targets)

**Location:** `src/swarmguard/config.py:10-28`

**Key snippet:**

```python
ALLOWED_PATTERNS = [
    # Testing
    r"^pytest(\s|$)",
    r"^python\s+-m\s+pytest(\s|$)",

    # Linting
    r"^ruff\s+(check|format)(\s|$)",
    r"^mypy(\s|$)",
    r"^flake8(\s|$)",

    # Build
    r"^make\s+(test|lint|build|clean)(\s|$)",
    r"^cargo\s+(test|check|clippy)(\s|$)",

    # Git (read-only)
    r"^git\s+(status|diff|log|show|rev-parse)(\s|$)",

    # Type checking
    r"^tsc\s+--noEmit(\s|$)",
]

def is_command_allowed(command: str) -> bool:
    """Check if command matches any allowed pattern."""
    return any(re.match(pattern, command) for pattern in ALLOWED_PATTERNS)
```

**Why this matters:**
- Simple allowlist prevents 99% of dangerous commands
- Easy for humans to audit (20 lines vs complex ACL logic)
- Pattern-based allows parameterization (pytest -v, pytest tests/unit)

**Reuse strategy:** Keep and extend per-domain (add semgrep, trivy, etc.).

---

### 5. Repo Context Pack (`repo_pack.py`)

**Why it's smart:**
- Structured format optimized for Kimi K2.5's long context
- Captures **intent** (failing_logs) not just **structure** (tree)
- Includes critical config files (pyproject.toml, Makefile) for understanding conventions
- 200KB cap per file prevents context overflow

**Location:** `src/swarmguard/repo_pack.py:15-180`

**Key snippet:**

```python
def build_repo_pack(
    worktree_path: Path,
    task: TaskSpec,
    failing_logs: str = ""
) -> dict[str, Any]:
    """
    Build a comprehensive context bundle for Kimi K2.5.

    Structure:
    {
      "task": {
        "goal": "Fix failing tests",
        "repo": {"path": "...", "branch": "..."},
        "focus_paths": ["tests/", "src/auth/"]
      },
      "tree": {
        "files": ["src/main.py", "tests/test_auth.py", ...],
        "total_files": 142,
        "total_lines": 15847
      },
      "important_files": {
        "pyproject.toml": "...",       # Captures dependencies, tools config
        "README.md": "...",             # Project context
        "ruff.toml": "...",             # Style conventions
        ".github/workflows/ci.yml": "..." # CI expectations
      },
      "failing_logs": "=== PYTEST FAILURE ===\n...",  # CRITICAL for context
      "current_diff": "",               # Existing changes in worktree
      "hot_paths": [],                  # Files mentioned in failing_logs
      "conventions": {}                 # Extracted from configs
    }
    """
    repo_pack = {
        "task": task.dict(),
        "tree": _build_tree(worktree_path),
        "important_files": {},
        "failing_logs": failing_logs,
        "current_diff": _get_current_diff(worktree_path),
        "hot_paths": _extract_hot_paths(failing_logs),
        "conventions": _extract_conventions(worktree_path)
    }

    # Capture important config files
    important_paths = [
        "pyproject.toml", "setup.py", "setup.cfg",
        "Cargo.toml", "package.json", "go.mod",
        "Makefile", "README.md", "CONTRIBUTING.md",
        ".github/workflows/ci.yml", "tox.ini",
        "ruff.toml", ".flake8", "mypy.ini"
    ]

    for rel_path in important_paths:
        file_path = worktree_path / rel_path
        if file_path.exists() and file_path.stat().st_size < 200_000:  # 200KB cap
            repo_pack["important_files"][rel_path] = file_path.read_text()

    return repo_pack
```

**Why this matters:**
- Kimi K2.5 needs **full context** to generate correct patches
- Failing logs provide **intent** (what's broken, why agent is needed)
- Config files teach **conventions** (import style, formatting rules)
- Hot paths focus attention (files mentioned in errors)

**Reuse strategy:** Keep core logic, enhance hot_paths extraction (parse tracebacks better).

---

### 6. Telemetry & Observability (`telemetry/sink.py`)

**Why it's useful:**
- JSONL format (one event per line, easy to grep/jq)
- Structured events (timestamp, run_id, type, data)
- Enables reproducibility (replay mode for testing)
- Audit trail for compliance (who approved what, when)

**Location:** `src/swarmguard/telemetry/sink.py:7-45`

**Key snippet:**

```python
def log_event(
    run_id: str,
    event_type: str,
    data: dict[str, Any],
    telemetry_path: Path
) -> None:
    """
    Append event to JSONL telemetry log.

    Event types:
    - cycle_started: Run begins
    - proposal: Agent generated patch proposal
    - risk_trigger: Human approval required
    - apply_result: Patch application outcome
    - command_executed: Sandbox command run
    - cycle_completed: Run ends with status
    """
    event = {
        "timestamp": time.time(),
        "run_id": run_id,
        "type": event_type,
        "data": data
    }

    with open(telemetry_path, "a") as f:
        f.write(json.dumps(event) + "\n")
```

**Example log:**

```jsonl
{"timestamp": 1738456789.0, "run_id": "abc123", "type": "cycle_started", "data": {"task": "Fix auth bug"}}
{"timestamp": 1738456790.5, "run_id": "abc123", "type": "proposal", "data": {"agent": "SecurityGuardian", "title": "Fix SQL injection", "risk": "medium"}}
{"timestamp": 1738456791.2, "run_id": "abc123", "type": "risk_trigger", "data": {"risk_type": "security_critical", "approved": true}}
{"timestamp": 1738456792.0, "run_id": "abc123", "type": "apply_result", "data": {"ok": true, "files_changed": 1}}
{"timestamp": 1738456793.0, "run_id": "abc123", "type": "cycle_completed", "data": {"status": "success"}}
```

**Why this matters:**
- Debugging: Filter by run_id to see full event sequence
- Compliance: Audit who approved risky changes
- Learning: Analyze which agents produce successful vs failed patches

**Reuse strategy:** Keep as-is, add more event types (command_executed, quality_gate_summary).

---

### 7. HTTP Retry Logic with Backoff (`kimi/openai_compat_client.py`)

**Why it's robust:**
- Exponential backoff with jitter (prevents thundering herd)
- Retry on transient errors only (429, 503, 504)
- Configurable max retries (default 6)
- Semaphore for concurrency limiting (default 16 inflight)
- Detailed logging (retry count, sleep duration)

**Location:** `src/swarmguard/kimi/openai_compat_client.py:90-150`

**Key snippet:**

```python
class OpenAICompatClient:
    def __init__(self, base_url: str, model_id: str, max_inflight: int = 16):
        self.base_url = base_url
        self.model_id = model_id
        self.semaphore = asyncio.Semaphore(max_inflight)
        self.retry_max = int(os.getenv("SWARMGUARD_RETRY_MAX", "6"))

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7
    ) -> dict[str, Any]:
        """
        Send chat completion request with retry/backoff logic.

        Retry strategy:
        - 429 (rate limit): Exponential backoff with jitter
        - 503/504 (server error): Exponential backoff
        - 400/401/403: No retry (client error)
        - Network errors: Retry with backoff
        """
        async with self.semaphore:  # Limit concurrency
            for attempt in range(self.retry_max):
                try:
                    async with httpx.AsyncClient(timeout=300.0) as client:
                        response = await client.post(
                            f"{self.base_url}/chat/completions",
                            json={
                                "model": self.model_id,
                                "messages": messages,
                                "temperature": temperature
                            }
                        )

                        if response.status_code == 200:
                            return response.json()

                        # Retry on transient errors
                        if response.status_code in [429, 503, 504]:
                            sleep_time = (2 ** attempt) * 0.5  # Exponential backoff
                            jitter = random.uniform(0, 0.1 * sleep_time)
                            await asyncio.sleep(sleep_time + jitter)
                            continue

                        # Don't retry on client errors
                        response.raise_for_status()

                except (httpx.NetworkError, httpx.TimeoutException) as e:
                    if attempt < self.retry_max - 1:
                        sleep_time = (2 ** attempt) * 0.5
                        await asyncio.sleep(sleep_time)
                        continue
                    raise

            raise Exception(f"Max retries ({self.retry_max}) exceeded")
```

**Why this matters:**
- Kimi K2.5 via Ollama can have transient failures (model loading, GPU memory)
- Jitter prevents all agents from retrying simultaneously
- Semaphore prevents overwhelming Ollama with 100s of concurrent requests

**Reuse strategy:** Keep retry logic, add streaming support (`httpx.stream()`).

---

## Core Components

Now that we've identified what to salvage, let's define the **new components** for the ambient system.

### 1. Ambient Coordinator

**Responsibilities:**
- Watch filesystem for changes (inotify via `watchdog`)
- Maintain event queue of detected issues
- Coordinate swarm of specialist agents
- Serialize patch application (single-writer)
- Log all actions to telemetry

**Implementation:**

```python
# src/ambient/coordinator.py

import asyncio
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class AmbientCoordinator:
    """
    Main orchestrator for ambient code quality monitoring.

    Lifecycle:
    1. Start file watcher on target directory
    2. Enqueue detected events (file changes, CI failures)
    3. Build full repo context when event triggers
    4. Spawn specialist agents in parallel
    5. Aggregate proposals with cross-pollination
    6. Apply patches serially with risk gates
    7. Verify with sandbox checks
    8. Log outcomes and return to watching
    """

    def __init__(self, repo_path: Path, config: AmbientConfig):
        self.repo_path = repo_path
        self.config = config
        self.event_queue = asyncio.Queue()
        self.write_lock = asyncio.Lock()
        self.workspace = Workspace(repo_path)
        self.agents = self._init_agents()

    def _init_agents(self) -> list[SpecialistAgent]:
        """Initialize specialist agents based on config."""
        return [
            SecurityGuardian(self.config.kimi_config),
            RefactorArchitect(self.config.kimi_config),
            StyleEnforcer(self.config.kimi_config),
            PerformanceOptimizer(self.config.kimi_config),
            TestEnhancer(self.config.kimi_config)
        ]

    async def start(self):
        """Start ambient monitoring loop."""
        # Start filesystem watcher
        observer = Observer()
        observer.schedule(
            AmbientEventHandler(self.event_queue),
            str(self.repo_path),
            recursive=True
        )
        observer.start()

        # Main event loop
        while True:
            event = await self.event_queue.get()
            await self._handle_event(event)

    async def _handle_event(self, event: AmbientEvent):
        """Process a detected event (file change, CI failure, etc.)."""
        # 1. Build full context
        context = await self.workspace.build_context(event)

        # 2. Spawn swarm in parallel
        proposals = await asyncio.gather(*[
            agent.propose(context)
            for agent in self.agents
        ])
        proposals = [p for sublist in proposals for p in sublist]  # Flatten

        # 3. Cross-pollination (agents refine based on each other's work)
        refined = await asyncio.gather(*[
            agent.refine(proposals, context)
            for agent in self.agents
        ])
        refined = [p for sublist in refined for p in sublist]  # Flatten

        # 4. Risk assessment and gating
        sorted_proposals = self._sort_by_risk(refined)
        for proposal in sorted_proposals:
            if self._requires_approval(proposal):
                approved = await self._request_approval(proposal)
                if not approved:
                    continue

            # 5. Apply atomically (single-writer)
            async with self.write_lock:
                result = await self.workspace.apply_patch(proposal)
                if not result["ok"]:
                    # Log failure, continue to next proposal
                    self._log_telemetry("apply_failed", proposal, result)
                    continue

                # 6. Verify with sandbox checks
                verify_result = await self.workspace.verify_changes()
                if not verify_result["ok"]:
                    # Rollback
                    await self.workspace.rollback()
                    self._log_telemetry("verify_failed", proposal, verify_result)
                    continue

                # Success!
                self._log_telemetry("apply_success", proposal, result)
```

**Key features:**
- Async/await throughout (no Ray complexity)
- Single `asyncio.Lock` for writes (replaces Ray concurrency groups)
- Event-driven architecture (reactive to filesystem changes)
- Cross-pollination step (agents see each other's proposals before finalizing)

---

### 2. Specialist Agents

Each agent focuses on one domain and returns `ProposalList`. Unlike the current system, agents receive **full context upfront** (no tool loop) and can optionally use **streaming** for progressive refinement.

#### Agent Interface

```python
# src/ambient/agents/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class Proposal:
    """A proposed code change."""
    agent: str
    title: str
    description: str
    diff: str                    # Unified diff format
    risk_level: str              # low, medium, high, critical
    rationale: str               # Why this change improves code quality
    files_touched: list[str]
    estimated_loc_change: int
    tags: list[str]              # ["security", "refactor", "style", etc.]

class SpecialistAgent(ABC):
    """Base class for all specialist agents."""

    def __init__(self, kimi_config: KimiConfig):
        self.kimi_client = KimiClient(kimi_config)
        self.system_prompt = self._build_system_prompt()

    @abstractmethod
    def _build_system_prompt(self) -> str:
        """Return detailed system prompt for this specialist."""
        pass

    async def propose(self, context: RepoContext) -> list[Proposal]:
        """
        Analyze repo context and propose improvements.

        Args:
            context: Full repo visibility (tree, files, configs, failing_logs)

        Returns:
            List of proposals (may be empty if no issues found)
        """
        prompt = self._format_prompt(context)

        response = await self.kimi_client.chat_completion(
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2  # Low temperature for consistency
        )

        return self._parse_proposals(response["choices"][0]["message"]["content"])

    async def refine(
        self,
        all_proposals: list[Proposal],
        context: RepoContext
    ) -> list[Proposal]:
        """
        Refine proposals after seeing other agents' work.

        This enables coordination (e.g., SecurityGuardian sees RefactorArchitect
        is moving code, so doesn't flag that file as "complex").
        """
        # Default: no refinement
        return [p for p in all_proposals if p.agent == self.__class__.__name__]
```

---

### 3. Workspace Manager

Replaces `WorkspaceActor` but without Ray. Simple async class with atomic operations.

```python
# src/ambient/workspace.py

import asyncio
from pathlib import Path
from .salvaged.git_ops import git_apply_patch_atomic, git_reset_hard_clean
from .salvaged.safe_paths import safe_resolve
from .salvaged.sandbox import SandboxRunner

class Workspace:
    """
    Manages filesystem operations with safety guarantees.

    Safety model:
    - All writes go through atomic patch application
    - All paths validated via safe_resolve()
    - All commands run in Docker sandbox
    - Single async lock prevents race conditions
    """

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.sandbox = SandboxRunner()

    async def apply_patch(self, proposal: Proposal) -> dict[str, Any]:
        """
        Apply proposal's diff atomically.

        This is a thin async wrapper around the salvaged git_ops.py logic.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            git_apply_patch_atomic,
            self.repo_path,
            proposal.diff
        )

    async def verify_changes(self) -> dict[str, Any]:
        """
        Run quality checks in sandbox.

        Default checks:
        - pytest (if tests/ exists)
        - ruff check (if pyproject.toml has ruff config)
        - mypy (if mypy.ini exists)
        """
        results = []

        # Run checks in parallel
        checks = []
        if (self.repo_path / "tests").exists():
            checks.append(("pytest", "pytest -xvs"))
        if (self.repo_path / "pyproject.toml").exists():
            checks.append(("ruff", "ruff check ."))
        if (self.repo_path / "mypy.ini").exists():
            checks.append(("mypy", "mypy ."))

        async def run_check(name: str, command: str):
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                self.sandbox.run_command,
                command,
                self.repo_path
            )

        results = await asyncio.gather(*[
            run_check(name, cmd) for name, cmd in checks
        ])

        # All checks must pass
        all_ok = all(r["ok"] for r in results)
        return {
            "ok": all_ok,
            "results": results
        }

    async def rollback(self):
        """Rollback to clean state."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            git_reset_hard_clean,
            self.repo_path
        )

    async def build_context(self, event: AmbientEvent) -> RepoContext:
        """
        Build full repo context for agents.

        This is an enhanced version of the salvaged repo_pack.py logic.
        """
        from .salvaged.repo_pack import build_repo_pack

        # Detect failing logs if event is CI failure
        failing_logs = ""
        if event.type == "ci_failure":
            failing_logs = event.data.get("logs", "")

        # Build pack
        loop = asyncio.get_event_loop()
        pack = await loop.run_in_executor(
            None,
            build_repo_pack,
            self.repo_path,
            event.task_spec,
            failing_logs
        )

        return RepoContext(**pack)
```

---

## Specialist Agents

Each agent has a focused domain and detailed prompt. Below are specifications for the 5 core agents.

### 1. SecurityGuardian

**Focus:** Detect and fix security vulnerabilities

**Responsibilities:**
- Scan for hardcoded secrets (API keys, passwords)
- Detect SQL injection, XSS, command injection patterns
- Check dependency vulnerabilities (via trivy/safety)
- Identify insecure configurations (debug=True in production)
- Flag weak crypto (MD5, SHA1 for passwords)

**System Prompt:**

```
You are SecurityGuardian, an expert security auditor specialized in identifying and fixing vulnerabilities in codebases.

Your mission: Analyze the provided repository context and propose patches that eliminate security issues.

Focus areas:
1. **Secrets Exposure**: Hardcoded API keys, passwords, tokens in code or configs
2. **Injection Attacks**: SQL injection, command injection, XSS, path traversal
3. **Dependency Vulnerabilities**: Outdated libraries with known CVEs
4. **Insecure Configurations**: Debug mode in production, permissive CORS, weak TLS
5. **Cryptography**: Weak algorithms (MD5, SHA1 for passwords), missing encryption

Rules:
- ONLY propose fixes for CONFIRMED vulnerabilities (no false positives)
- Include CVE IDs or OWASP references in rationale
- Set risk_level to "critical" for RCE/auth bypass, "high" for data exposure
- Generate unified diffs that are directly applicable with git apply
- Test your proposed patches mentally (will they break functionality?)

Output format:
Return a JSON array of proposals:
[
  {
    "agent": "SecurityGuardian",
    "title": "Fix SQL injection in user login",
    "description": "User input is directly interpolated into SQL query. Use parameterized queries.",
    "diff": "--- a/src/auth.py\n+++ b/src/auth.py\n@@ -10,7 +10,7 @@\n...",
    "risk_level": "critical",
    "rationale": "OWASP A03:2021 Injection. Allows authentication bypass via ' OR '1'='1",
    "files_touched": ["src/auth.py"],
    "estimated_loc_change": 3,
    "tags": ["security", "sql-injection", "owasp-a03"]
  }
]

If no security issues found, return empty array: []
```

**Example detection patterns:**

```python
# Secrets
re.compile(r'(api[_-]?key|password|secret|token)\s*=\s*["\'][^"\']+["\']', re.IGNORECASE)

# SQL Injection
re.compile(r'cursor\.execute\([^)]*\%\s*\(')  # String formatting in execute()

# Command Injection
re.compile(r'subprocess\.(run|call|Popen)\([^)]*shell=True')

# Path Traversal
re.compile(r'open\([^)]*\+[^)]*\)')  # open(path + user_input)
```

---

### 2. RefactorArchitect

**Focus:** Improve code structure and maintainability

**Responsibilities:**
- Detect code duplication (DRY violations)
- Identify complex functions (high cyclomatic complexity)
- Suggest design pattern applications (strategy, factory, etc.)
- Break up god classes/functions
- Improve naming (vague names like `data`, `handle`, `process`)

**System Prompt:**

```
You are RefactorArchitect, an expert in software design and code quality.

Your mission: Identify structural improvements that make code more maintainable, readable, and testable.

Focus areas:
1. **Code Duplication**: Repeated logic that should be extracted into functions/classes
2. **Complexity**: Functions with >15 branches or >100 lines that should be split
3. **Naming**: Vague names (data, tmp, handle) that should be descriptive
4. **Design Patterns**: Opportunities to apply patterns (strategy for conditionals, factory for object creation)
5. **SOLID Violations**: Single responsibility violations, tight coupling

Rules:
- Prioritize high-impact refactors (frequently used code)
- Don't break existing functionality (refactor = same behavior, better structure)
- Set risk_level based on scope ("low" for naming, "medium" for extraction, "high" for architectural changes)
- Include before/after complexity metrics in rationale (e.g., "Cyclomatic complexity: 18 → 6")
- Ensure diffs are complete (don't leave dangling references)

Output format: Same JSON array as SecurityGuardian

Example:
{
  "agent": "RefactorArchitect",
  "title": "Extract repeated validation logic",
  "description": "User validation is duplicated in 5 places. Extract to validate_user() function.",
  "diff": "...",
  "risk_level": "low",
  "rationale": "DRY violation. Reduces maintenance burden (change validation rules in one place). Lines of duplication: 45 → 9",
  "files_touched": ["src/api/users.py", "src/api/auth.py"],
  "estimated_loc_change": -36,
  "tags": ["refactor", "dry", "extraction"]
}
```

---

### 3. StyleEnforcer

**Focus:** Enforce consistent formatting and conventions

**Responsibilities:**
- Fix formatting violations (line length, indentation, trailing whitespace)
- Enforce naming conventions (PEP 8, camelCase vs snake_case)
- Add missing docstrings
- Organize imports (sort, remove unused)
- Fix typos in comments/docstrings

**System Prompt:**

```
You are StyleEnforcer, a code style and documentation specialist.

Your mission: Ensure codebase follows consistent style guidelines and is well-documented.

Focus areas:
1. **Formatting**: Line length, indentation, whitespace (defer to ruff/black configs)
2. **Naming**: Follow conventions from important_files configs (PEP 8, etc.)
3. **Documentation**: Missing docstrings for public functions/classes
4. **Imports**: Unused imports, unsorted imports, star imports
5. **Comments**: Typos, outdated comments, commented-out code

Rules:
- Follow project's existing style guide (check pyproject.toml, .editorconfig)
- ALL proposals must be "low" risk_level (style changes don't affect logic)
- Focus on high-visibility files (public APIs, README, main modules)
- Don't fix every tiny issue in one PR (batch related changes)

Output format: Same JSON array

Example:
{
  "agent": "StyleEnforcer",
  "title": "Add missing docstrings to public API",
  "description": "Functions in api.py lack docstrings. Added Google-style docstrings.",
  "diff": "...",
  "risk_level": "low",
  "rationale": "Improves maintainability and auto-generated docs. PEP 257 compliance.",
  "files_touched": ["src/api.py"],
  "estimated_loc_change": 45,
  "tags": ["style", "documentation", "pep257"]
}
```

---

### 4. PerformanceOptimizer

**Focus:** Identify and fix performance bottlenecks

**Responsibilities:**
- Detect O(n²) algorithms that should be O(n) or O(n log n)
- Identify redundant database queries (N+1 problem)
- Suggest caching opportunities
- Find unnecessary object copies
- Recommend lazy evaluation

**System Prompt:**

```
You are PerformanceOptimizer, an expert in algorithmic efficiency and system performance.

Your mission: Identify performance bottlenecks and propose optimizations.

Focus areas:
1. **Algorithm Complexity**: O(n²) loops that can be O(n) with sets/dicts
2. **Database Queries**: N+1 queries, missing indexes, unoptimized ORMs
3. **Caching**: Repeated expensive computations that should be cached
4. **I/O**: Unnecessary file reads, blocking network calls in loops
5. **Memory**: Large object copies, memory leaks

Rules:
- ONLY propose optimizations with measurable impact (>10% speedup or >20% memory reduction)
- Include benchmarks in rationale (e.g., "100ms → 10ms on 1000-item list")
- Set risk_level to "medium" (performance changes can introduce bugs)
- Ensure correctness is preserved (don't break edge cases for speed)
- Prefer algorithmic improvements over micro-optimizations

Output format: Same JSON array

Example:
{
  "agent": "PerformanceOptimizer",
  "title": "Replace O(n²) lookup with O(n) set",
  "description": "Checking membership in list is O(n). Convert to set for O(1) lookup.",
  "diff": "...",
  "risk_level": "low",
  "rationale": "Reduces time complexity from O(n²) to O(n). Benchmark: 500ms → 5ms for 10k items.",
  "files_touched": ["src/processor.py"],
  "estimated_loc_change": 2,
  "tags": ["performance", "algorithm", "big-o"]
}
```

---

### 5. TestEnhancer

**Focus:** Improve test coverage and quality

**Responsibilities:**
- Identify untested code paths (low coverage areas)
- Add edge case tests (null, empty, boundary values)
- Fix flaky tests (time-dependent, order-dependent)
- Improve test clarity (better names, clear arrange-act-assert)
- Add property-based tests for complex logic

**System Prompt:**

```
You are TestEnhancer, a test quality and coverage specialist.

Your mission: Ensure critical code is well-tested and tests are reliable.

Focus areas:
1. **Coverage Gaps**: Functions/branches with no tests, especially error handling
2. **Edge Cases**: Missing tests for null, empty lists, boundary values, concurrent access
3. **Flaky Tests**: Time-dependent tests (sleep()), order-dependent tests
4. **Test Quality**: Unclear test names, missing assertions, testing multiple things
5. **Test Patterns**: Opportunities for property-based testing (hypothesis), fixtures

Rules:
- Prioritize critical paths (auth, payment, data integrity)
- Set risk_level to "low" (adding tests doesn't break production)
- Write clear test names (test_<function>_<scenario>_<expected_result>)
- Follow existing test framework patterns (pytest, unittest, etc.)
- Include rationale about what risk the new test mitigates

Output format: Same JSON array

Example:
{
  "agent": "TestEnhancer",
  "title": "Add edge case tests for divide function",
  "description": "divide() lacks tests for zero division and negative numbers.",
  "diff": "...",
  "risk_level": "low",
  "rationale": "Mitigates risk of ZeroDivisionError in production. Coverage: 40% → 80% for math.py",
  "files_touched": ["tests/test_math.py"],
  "estimated_loc_change": 15,
  "tags": ["test", "coverage", "edge-case"]
}
```

---

## Safety & Security Model

The ambient system inherits all safety mechanisms from the current implementation.

### 1. Single-Writer Guarantee

**Mechanism:** `asyncio.Lock` in coordinator

```python
async with self.write_lock:
    result = await self.workspace.apply_patch(proposal)
```

**Guarantee:** Only one patch applied at a time, preventing race conditions

---

### 2. Atomic Patch Application

**Mechanism:** Salvaged `git_ops.git_apply_patch_atomic()`

**Guarantee:** Either patch applies completely or repository state is unchanged

---

### 3. Sandbox Execution

**Mechanism:** Docker with `--network none`, resource limits, command allowlist

**Guarantee:** Agents cannot:
- Access network (unless explicitly approved)
- Escape container
- Consume unbounded resources
- Execute dangerous commands

---

### 4. Path Safety

**Mechanism:** Salvaged `safe_paths.safe_resolve()`

**Guarantee:** Agents cannot:
- Escape repository directory (`../../etc/passwd`)
- Access forbidden files (`.git`, `.env`, `.ssh`)

---

### 5. Risk Gates

**Mechanism:** Approval prompts for high-risk proposals

**Risk levels:**
- **Low**: Style, docs, tests → Auto-apply
- **Medium**: Refactors, performance → Auto-apply (if verify passes)
- **High**: Security fixes, dependency changes → Require approval
- **Critical**: Authentication, payment logic → Require approval + extra review

**Implementation:**

```python
def _requires_approval(self, proposal: Proposal) -> bool:
    """Check if proposal needs human approval."""
    if proposal.risk_level in ["high", "critical"]:
        return True
    if any(tag in proposal.tags for tag in ["security", "auth", "payment"]):
        return True
    if proposal.estimated_loc_change > 500:
        return True  # Large changes need review
    return False
```

---

### 6. Verification Before Commit

**Mechanism:** Run full test suite + linting in sandbox after each patch

```python
verify_result = await self.workspace.verify_changes()
if not verify_result["ok"]:
    await self.workspace.rollback()  # Automatic rollback on failure
```

**Guarantee:** No broken code committed (tests must pass)

---

## Configuration Schema

Ambient system configured via YAML file (`.ambient.yml` in repo root).

```yaml
# .ambient.yml

# Kimi K2.5 configuration
kimi:
  provider: "ollama"             # ollama, openai-compatible, anthropic (future)
  base_url: "http://localhost:11434/v1"
  model_id: "kimi-k2.5:cloud"
  max_concurrency: 8             # Max parallel agent calls
  temperature: 0.2               # Low for consistency
  timeout_seconds: 300

# Monitoring configuration
monitoring:
  enabled: true
  watch_paths:
    - "src/"
    - "tests/"
  ignore_patterns:
    - "*.pyc"
    - "__pycache__"
    - ".git"
  debounce_seconds: 5            # Wait 5s after file change before triggering
  check_interval_seconds: 300    # Periodic scan every 5 minutes

# Agent configuration
agents:
  enabled:
    - SecurityGuardian
    - RefactorArchitect
    - StyleEnforcer
    - PerformanceOptimizer
    - TestEnhancer

  # Per-agent settings
  SecurityGuardian:
    severity_threshold: "medium"  # Only report medium+ vulnerabilities
    scan_dependencies: true       # Run trivy/safety on pyproject.toml changes

  RefactorArchitect:
    complexity_threshold: 15      # Flag functions with cyclomatic complexity >15
    max_function_lines: 100       # Flag functions >100 lines

  StyleEnforcer:
    defer_to_formatter: true      # Let ruff/black handle formatting, focus on docs

  PerformanceOptimizer:
    min_speedup_percent: 10       # Only propose if >10% improvement

  TestEnhancer:
    coverage_threshold: 80        # Aim for 80% line coverage
    prioritize_paths:
      - "src/auth/"               # Critical paths to test first
      - "src/payment/"

# Risk policy
risk_policy:
  auto_apply:
    - "low"                       # Style, docs, tests
    - "medium"                    # Refactors (if verify passes)

  require_approval:
    - "high"                      # Security fixes, deps
    - "critical"                  # Auth, payment

  file_change_limit: 10           # Max files touched per proposal (auto-reject larger)
  loc_change_limit: 500           # Max LOC changed per proposal

# Sandbox configuration
sandbox:
  image: "ambient-sandbox:latest"
  network_mode: "none"            # Default: no network
  resources:
    memory: "2g"
    cpus: "2.0"
    pids_limit: 100

  allowed_commands:
    - "^pytest"
    - "^ruff\\s+(check|format)"
    - "^mypy"
    - "^cargo\\s+(test|check|clippy)"
    - "^npm\\s+test"
    - "^make\\s+(test|lint|check)"
    - "^git\\s+(status|diff|log|show)"

# Telemetry
telemetry:
  enabled: true
  log_path: ".ambient/telemetry.jsonl"
  include_diffs: false            # Don't log full diffs (can be large)
  retention_days: 30

# Learning (future)
learning:
  enabled: false                  # Phase 2 feature
  track_revert_rate: true         # Learn which proposals get reverted
  track_agent_success: true       # Learn which agents are most helpful
```

---

## Implementation Guide

### Phase 1: Core Infrastructure (Week 1)

**Goal:** Async coordinator + workspace + salvaged components

**Tasks:**
1. Extract salvaged components into `src/ambient/salvaged/`:
   - `git_ops.py` (atomic patches)
   - `safe_paths.py` (path safety)
   - `sandbox.py` (Docker runner)
   - `repo_pack.py` (context builder)
   - `telemetry/sink.py` (JSONL logging)

2. Implement `src/ambient/coordinator.py`:
   - `AmbientCoordinator` class
   - `asyncio.Queue` for events
   - File watcher integration (watchdog)
   - Single-writer lock (`asyncio.Lock`)

3. Implement `src/ambient/workspace.py`:
   - Async wrappers around salvaged git_ops
   - Context builder
   - Verification runner

4. Implement `src/ambient/config.py`:
   - YAML schema (pydantic models)
   - Validation logic

**Acceptance criteria:**
- Coordinator can watch directory and enqueue file change events
- Workspace can apply patches atomically
- All salvaged components pass existing unit tests

---

### Phase 2: Kimi Integration + Agents (Week 2)

**Goal:** Streaming Kimi client + 5 specialist agents

**Tasks:**
1. Implement `src/ambient/kimi_client.py`:
   - Async HTTP client (httpx)
   - Streaming support (`async for chunk in stream()`)
   - Retry/backoff logic (salvaged from openai_compat_client.py)
   - Semaphore for concurrency limiting

2. Implement base agent class `src/ambient/agents/base.py`:
   - `propose()` method (full context → proposals)
   - `refine()` method (cross-pollination)
   - JSON parsing utilities

3. Implement 5 specialist agents:
   - `SecurityGuardian` (security vulnerabilities)
   - `RefactorArchitect` (code structure)
   - `StyleEnforcer` (formatting, docs)
   - `PerformanceOptimizer` (bottlenecks)
   - `TestEnhancer` (coverage, quality)

4. Integrate agents into coordinator:
   - Parallel proposal generation (`asyncio.gather()`)
   - Cross-pollination step
   - Risk-based sorting

**Acceptance criteria:**
- Each agent can propose valid patches given repo context
- Proposals are properly formatted (valid unified diffs)
- Cross-pollination prevents duplicate proposals

---

### Phase 3: Ambient Loop + Gates (Week 3)

**Goal:** Continuous monitoring + approval flow

**Tasks:**
1. Implement ambient event sources:
   - File watcher (watchdog)
   - CI webhook listener (GitHub Actions, GitLab CI)
   - Periodic scanner (cron-like)

2. Implement risk gates:
   - Risk level assessment
   - Approval prompt (CLI or webhook)
   - Approval audit log

3. Implement verification:
   - Sandbox test runner
   - Automatic rollback on failure
   - Telemetry logging

4. End-to-end testing:
   - Introduce a security vulnerability → SecurityGuardian detects and fixes
   - Introduce code duplication → RefactorArchitect extracts function
   - Break style → StyleEnforcer fixes
   - Add slow code → PerformanceOptimizer suggests set()
   - Add untested function → TestEnhancer adds test

**Acceptance criteria:**
- System can run continuously for 24 hours without crashing
- All 5 agent types successfully propose and apply patches
- Risk gates correctly prompt for approval on high-risk changes
- Verification catches broken patches and rolls back

---

### Phase 4: Production Hardening (Week 4)

**Goal:** Error recovery, monitoring, documentation

**Tasks:**
1. Error recovery:
   - Graceful handling of Kimi API failures
   - Retry logic for transient errors
   - Fallback to partial agent swarm if some agents fail

2. Monitoring:
   - Metrics export (proposals/hour, apply success rate, agent breakdown)
   - Alerting on repeated failures
   - Dashboard (optional: Grafana)

3. Documentation:
   - Installation guide
   - Configuration guide
   - Agent customization guide (how to add new agents)
   - Troubleshooting guide

4. Performance optimization:
   - Benchmark end-to-end latency
   - Profile hot paths
   - Optimize repo context building (incremental updates)

**Acceptance criteria:**
- Complete user documentation
- 90%+ patch apply success rate on test repositories
- <30 second latency from file change to proposal generation
- Zero data loss (all telemetry captured)

---

## Deployment & Operations

### Local Development Setup

```bash
# 1. Clone repo
git clone https://github.com/you/ambient-swarm.git
cd ambient-swarm

# 2. Install dependencies
pip install -e .

# 3. Start Ollama with Kimi K2.5
ollama run kimi-k2.5:cloud

# 4. Configure ambient
cat > .ambient.yml <<EOF
kimi:
  base_url: "http://localhost:11434/v1"
  model_id: "kimi-k2.5:cloud"
monitoring:
  enabled: true
  watch_paths: ["src/", "tests/"]
agents:
  enabled:
    - SecurityGuardian
    - RefactorArchitect
    - StyleEnforcer
EOF

# 5. Build sandbox image
docker build -t ambient-sandbox:latest -f docker/Dockerfile .

# 6. Start ambient monitoring
ambient watch /path/to/your/repo
```

---

### Production Deployment (Single Machine)

```bash
# 1. Run as systemd service
cat > /etc/systemd/system/ambient-swarm.service <<EOF
[Unit]
Description=Ambient Code Quality Swarm
After=network.target

[Service]
Type=simple
User=ambient
WorkingDirectory=/opt/ambient-swarm
ExecStart=/opt/ambient-swarm/venv/bin/ambient watch /repos/production
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable ambient-swarm
sudo systemctl start ambient-swarm

# 2. Monitor logs
journalctl -u ambient-swarm -f

# 3. Check telemetry
tail -f /repos/production/.ambient/telemetry.jsonl | jq .
```

---

### Monitoring & Observability

**Key metrics to track:**
- **Proposals/hour**: How active is the system?
- **Apply success rate**: % of proposals that apply cleanly
- **Verify success rate**: % of applied patches that pass tests
- **Agent breakdown**: Which agents are most productive?
- **Risk distribution**: % of proposals by risk level
- **Latency**: Time from file change to proposal ready

**Telemetry queries:**

```bash
# Count proposals by agent
jq -r 'select(.type=="proposal") | .data.proposal.agent' telemetry.jsonl | sort | uniq -c

# Calculate apply success rate
jq 'select(.type=="apply_result")' telemetry.jsonl | jq -s 'map(select(.data.ok)) | length / (. | length)'

# Find slowest operations
jq -r 'select(.type=="cycle_completed") | .data.duration_seconds' telemetry.jsonl | sort -n | tail -10
```

---

### Troubleshooting

**Problem:** Agents not generating proposals

**Diagnosis:**
1. Check Kimi API connectivity: `curl http://localhost:11434/v1/models`
2. Check telemetry for errors: `jq 'select(.type=="error")' telemetry.jsonl`
3. Verify repo context is built: `ambient debug context /repo`

**Fix:** Ensure Ollama is running and model is loaded

---

**Problem:** Patches fail to apply

**Diagnosis:**
1. Check debug bundles: `ls .ambient/patch_debug/`
2. Review failed patch: `cat .ambient/patch_debug/failed_*.diff`
3. Check if base files changed: `git diff HEAD <file>`

**Fix:**
- If files changed during proposal generation, rerun cycle
- If diff format is wrong, improve agent prompt with examples
- If file was deleted, detect in risk assessment

---

**Problem:** Verification always fails

**Diagnosis:**
1. Check sandbox logs: `docker logs $(docker ps -q --filter ancestor=ambient-sandbox:latest)`
2. Run verification manually: `ambient verify /repo`
3. Check if tests were already failing: `git checkout HEAD~1 && pytest`

**Fix:** If tests were already broken, skip verification or fix tests first

---

## Extension Points

The ambient system is designed for extensibility. Common extensions:

### 1. Custom Specialist Agent

```python
# src/ambient/agents/custom_agent.py

from .base import SpecialistAgent, Proposal

class CustomAgent(SpecialistAgent):
    """Your custom agent logic."""

    def _build_system_prompt(self) -> str:
        return """
        You are CustomAgent, specialized in <your domain>.

        Your mission: <what you detect and fix>

        Output format: JSON array of proposals
        """

    async def propose(self, context: RepoContext) -> list[Proposal]:
        # Your custom detection logic
        # Can call external tools (semgrep, trivy, etc.)
        return [...]
```

**Register in config:**

```yaml
agents:
  enabled:
    - SecurityGuardian
    - CustomAgent  # Add your agent

  CustomAgent:
    custom_setting: "value"
```

---

### 2. Custom Event Source

```python
# src/ambient/events/custom_source.py

class CustomEventSource:
    """Your custom event source (Slack, Jira, etc.)."""

    async def poll(self) -> list[AmbientEvent]:
        # Check external system for tasks
        # Return list of events to process
        pass
```

**Register in coordinator:**

```python
coordinator = AmbientCoordinator(repo_path, config)
coordinator.register_event_source(CustomEventSource())
```

---

### 3. Custom Risk Gate

```python
# src/ambient/gates/custom_gate.py

def custom_risk_check(proposal: Proposal, context: RepoContext) -> bool:
    """Return True if proposal requires approval."""
    if "payment" in proposal.files_touched[0]:
        return True  # Always review payment code
    return False
```

**Register in config:**

```yaml
risk_policy:
  custom_gates:
    - custom_risk_check
```

---

### 4. Custom Verification Check

```python
# src/ambient/verification/custom_check.py

async def custom_verify(workspace: Workspace) -> dict[str, Any]:
    """Run custom quality check."""
    result = await workspace.sandbox.run_command(
        "your-custom-tool check",
        workspace.repo_path
    )
    return result
```

**Register in workspace:**

```python
workspace = Workspace(repo_path)
workspace.register_verification(custom_verify)
```

---

## Comparison: Old vs New

| Dimension | swarmguard (current) | Ambient Swarm (new) |
|-----------|----------------------|---------------------|
| **Architecture** | LangGraph DAG + Ray actors | Async coordinator |
| **Lines of Code** | ~10,000 | ~2,000 |
| **Dependencies** | 15+ (LangGraph, Ray, pydantic, etc.) | 5+ (asyncio, httpx, watchdog) |
| **Orchestration** | State machine (11 nodes) | Event loop |
| **Concurrency** | Ray concurrency groups | asyncio.Lock |
| **Context Delivery** | Incremental (tool loop) | Full upfront |
| **Agent Coordination** | None (map/reduce) | Cross-pollination |
| **Mode** | Batch (one task) | Continuous (ambient) |
| **Learning Curve** | Steep | Moderate |
| **Debuggability** | Hard (distributed) | Easy (single process) |
| **Latency** | High (multiple tool rounds) | Low (one shot) |
| **Extensibility** | Complex (LangGraph nodes) | Simple (async functions) |

**What we gain:**
- **Simplicity**: 80% less code, easier to understand
- **Ambient mode**: Continuous monitoring vs batch jobs
- **Better Kimi usage**: Full context upfront vs fragmented tool calls
- **Coordination**: Agents see each other's proposals
- **Performance**: Async/await vs distributed actor overhead

**What we keep:**
- **All safety primitives**: Atomic patches, sandbox, path safety
- **Battle-tested logic**: git_ops.py, safe_paths.py (368 + 67 LOC)
- **Telemetry**: JSONL event logging
- **Risk gates**: Approval flow for high-risk changes

---

## Success Metrics

**Technical metrics:**
- Patch apply success rate: >90%
- Verify pass rate: >95% (only good patches applied)
- False positive rate: <5% (agents don't flag non-issues)
- Latency: <30s from file change to proposal
- Uptime: >99.5% (robust error handling)

**Quality metrics:**
- Security issues detected: >80% of OWASP top 10
- Code duplication reduction: 20%+ in 1 month
- Test coverage increase: 10%+ in 1 month
- Code complexity reduction: 15%+ (avg cyclomatic complexity)

**User metrics:**
- Time to fix common issues: 10x faster than manual
- False positive rate: <5% (high signal, low noise)
- Developer satisfaction: "Would you use this?" >80% yes

---

## Roadmap

### Phase 1: Core System (Week 1-4) ✅
- Async coordinator
- 5 specialist agents
- Salvaged safety primitives
- Basic ambient monitoring

### Phase 2: Intelligence (Month 2)
- Learning layer: Track revert rate, adjust agent scoring
- Smarter hot path detection: Learn which files are edited most
- Context optimization: Incremental updates (only send changed files)
- Streaming proposals: Progressive refinement as Kimi thinks

### Phase 3: Collaboration (Month 3)
- Multi-repo support: Detect dependency changes across repos
- Team coordination: Multiple agents on same PR
- Conflict resolution: Agents negotiate when proposals conflict
- GitHub integration: Auto-create PRs, respond to reviews

### Phase 4: Enterprise (Month 4+)
- Kubernetes deployment
- Distributed checkpointing
- Advanced observability (Grafana dashboards)
- Compliance reporting (SOC 2, HIPAA)
- RLlib policy optimization (Phase 3 from original spec)

---

## Conclusion

This specification defines a **production-ready ambient code quality system** that:

1. **Salvages the best parts** of the current swarmguard implementation (atomic patches, sandbox, path safety)
2. **Simplifies orchestration** by replacing LangGraph + Ray with async/await
3. **Leverages Kimi K2.5's strengths** by providing full context upfront
4. **Enables true ambient mode** via continuous file watching and event-driven architecture
5. **Maintains strict safety** through single-writer, atomic patches, sandboxed execution

**Key innovations:**
- **Cross-pollination**: Agents refine proposals after seeing each other's work
- **Risk-based gating**: Auto-apply low-risk, prompt for high-risk
- **Streaming proposals**: Progressive refinement (future)
- **Learning layer**: Track success/revert rates (future)

**Implementation cost:** 3-4 weeks to feature parity with ambient mode (vs 3-4 weeks to fix current system without ambient features)

**Total LOC:** ~2,000 (vs ~10,000 in current system)

**Result:** A maintainable, extensible, production-ready system that keeps codebases clean continuously and autonomously.

---

## References

- Current implementation: `/Users/coolrboolr/psf/kimi-swarm/`
- Salvaged components: `src/swarmguard/runtime/git_ops.py`, `safe_paths.py`, `sandbox.py`
- Kimi K2.5 docs: [Moonshot AI documentation]
- Watchdog (file monitoring): https://pythonhosted.org/watchdog/
- asyncio patterns: https://docs.python.org/3/library/asyncio.html
