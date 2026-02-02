# Ambient Swarm v2.0

**Continuous, autonomous code quality maintenance system**

Ambient Swarm monitors your repository continuously, detects issues proactively, and proposes fixes autonomously using 5 specialist AI agents.

## Features

✅ **5 Specialist Agents**
- **SecurityGuardian** - Detects vulnerabilities (secrets, injection, weak crypto)
- **RefactorArchitect** - Improves code structure (DRY, complexity, SOLID)
- **StyleEnforcer** - Enforces consistent style and documentation
- **PerformanceOptimizer** - Identifies bottlenecks (algorithm complexity, caching)
- **TestEnhancer** - Improves test coverage and quality

✅ **Safety First**
- Atomic patch application with automatic rollback
- Sandboxed execution (Docker with network isolation)
- Path safety validation
- Risk-based approval gates

✅ **Production Ready**
- Async/await architecture
- Comprehensive telemetry (JSONL logs)
- Cross-agent coordination
- Full test coverage (88+ unit tests)

## Quick Start

### Installation

```bash
pip install -e .
```

### Prerequisites

- Python 3.11+
- Docker (for sandboxed execution)
- Ollama with Kimi K2.5 model

```bash
# Install Ollama
ollama pull kimi-k2.5:cloud
```

### Initialize a Repository

```bash
cd /path/to/your/repo
ambient init .
```

This creates `.ambient.yml` with default configuration.

### Run Single Analysis

```bash
# Dry run (no changes applied)
ambient run-once . --dry-run

# Interactive (asks for approval on high-risk changes)
ambient run-once .

# Auto-approve everything (dangerous!)
ambient run-once . --auto-approve
```

### Start Continuous Monitoring

```bash
# Watch for changes and continuously improve
ambient watch .

# Dry run mode (shows what would be done)
ambient watch . --dry-run

# Auto-approve mode (for trusted environments)
ambient watch . --auto-approve
```

### Verify Repository

```bash
# Run all verification checks (tests, linters)
ambient verify .
```

### Debug Context

```bash
# Show context that agents see
ambient debug-context .

# JSON output
ambient debug-context . -f json
```

## Configuration

Edit `.ambient.yml` to customize:

```yaml
kimi:
  provider: ollama
  base_url: http://localhost:11434/v1
  model_id: kimi-k2.5:cloud
  max_concurrency: 8

monitoring:
  watch_paths:
    - src/
    - tests/
  debounce_seconds: 5

agents:
  enabled:
    - SecurityGuardian
    - RefactorArchitect
    - StyleEnforcer
    - PerformanceOptimizer
    - TestEnhancer

risk_policy:
  auto_apply:
    - low
    - medium
  require_approval:
    - high
    - critical
  file_change_limit: 10
  loc_change_limit: 500

sandbox:
  image: ambient-sandbox:latest
  network_mode: none
  resources:
    memory: 2g
    cpus: "2.0"
```

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Ambient Coordinator (async Python)             │
│  • File watcher (watchdog)                      │
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

## Data Flow

1. **DETECT**: File change → Event queue
2. **ANALYZE**: Build repo context (full tree + metadata)
3. **SWARM**: 5 specialists propose fixes in parallel
4. **REFINE**: Cross-pollination (agents see each other's proposals)
5. **GATE**: Risk assessment → Human approval if needed
6. **APPLY**: Atomic patch application (serial, deterministic)
7. **VERIFY**: Run checks in sandbox → Rollback if failed
8. **LEARN**: Log outcome → Update agent scoring
9. **LOOP**: Return to DETECT

## Development

### Run Tests

```bash
# All unit tests
pytest tests/unit/ -v

# Integration tests
pytest tests/integration/ -v

# Coverage report
pytest --cov=src/ambient --cov-report=html
```

### Build Sandbox Image

```bash
# TODO: Phase 6
docker build -t ambient-sandbox:latest -f docker/Dockerfile .
```

## CLI Commands

### `ambient watch <repo_path>`
Start continuous monitoring. Watches for file changes and continuously proposes improvements.

Options:
- `--config PATH` - Custom config file
- `--auto-approve` - Auto-approve all proposals
- `--dry-run` - Don't apply changes

### `ambient run-once <repo_path>`
Run a single analysis cycle.

Options:
- `--config PATH` - Custom config file
- `--auto-approve` - Auto-approve all proposals
- `--dry-run` - Don't apply changes
- `--output PATH` - Save results to JSON

### `ambient verify <repo_path>`
Run verification checks (tests, linters) without proposing changes.

### `ambient debug-context <repo_path>`
Show repository context that agents see.

Options:
- `--format [text|json]` - Output format

### `ambient init <repo_path>`
Initialize ambient configuration in repository.

## Safety Model

### Single-Writer Guarantee
Only one patch applied at a time via `asyncio.Lock`, preventing race conditions.

### Atomic Patch Application
Patches apply completely or repository state is unchanged. Automatic rollback on failure.

### Sandbox Execution
All commands run in Docker with:
- `--network none` (no network access)
- Resource limits (memory, CPU, PIDs)
- Command allowlist

### Path Safety
All file operations validated via `safe_resolve()`:
- Blocks directory traversal (`../../etc/passwd`)
- Blocks access to sensitive files (`.git`, `.env`, `.ssh`)

### Risk Gates
Approval required for:
- High/critical risk levels
- Large changes (>10 files or >500 LOC)
- Sensitive files (auth, payment, secrets)
- Security-related tags

## Telemetry

All events logged to `.ambient/telemetry.jsonl`:

```jsonl
{"timestamp": 1738456789.0, "run_id": "abc123", "type": "cycle_started", "data": {...}}
{"timestamp": 1738456790.5, "run_id": "abc123", "type": "proposal", "data": {...}}
{"timestamp": 1738456791.2, "run_id": "abc123", "type": "apply_result", "data": {...}}
```

Query with `jq`:
```bash
# Count proposals by agent
jq -r 'select(.type=="proposal") | .data.proposal.agent' .ambient/telemetry.jsonl | sort | uniq -c

# Calculate apply success rate
jq 'select(.type=="apply_result")' .ambient/telemetry.jsonl | jq -s 'map(select(.data.ok)) | length / (. | length)'
```

## License

MIT

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Support

- Issues: https://github.com/you/ambient-swarm/issues
- Docs: https://ambient-swarm.readthedocs.io/
