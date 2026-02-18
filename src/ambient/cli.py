"""Command-line interface for Ambient Swarm.

Commands:
- ambient watch <repo_path>: Start continuous monitoring
- ambient run-once <repo_path>: Run single cycle
- ambient verify <repo_path>: Verify repository state
- ambient doctor <repo_path>: Preflight checks for sandbox + dependencies
- ambient debug-context <repo_path>: Show repository context
"""

from __future__ import annotations

import asyncio
import json
import shlex
import sys
from collections import deque
from pathlib import Path

import click

from .approval import (
    AlwaysApproveHandler,
    AlwaysRejectHandler,
    ApprovalHandler,
    WebhookApprovalHandler,
)
from .config import AmbientConfig, load_config
from .coordinator import AmbientCoordinator
from .status import StatusWindow, compute_status
from .types import AmbientEvent
from .workspace import Workspace


@click.group()
@click.version_option(version="2.0.0", prog_name="ambient")
def cli() -> None:
    """Ambient Swarm - Continuous code quality maintenance system."""
    pass


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
@click.option(
    "--auto-approve",
    is_flag=True,
    help="Auto-approve all proposals (dangerous!)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Don't apply any changes (dry run mode)",
)
@click.option(
    "--foreground",
    is_flag=True,
    help="Run in the foreground (no-op; suitable for supervision).",
)
@click.option(
    "--skip-doctor",
    is_flag=True,
    help="Skip startup preflight checks (not recommended).",
)
@click.option(
    "--approval-mode",
    type=click.Choice(["interactive", "webhook"]),
    default="interactive",
    show_default=True,
    help="Approval mechanism for high-risk changes.",
)
def watch(
    repo_path: str,
    config: str | None,
    auto_approve: bool,
    dry_run: bool,
    foreground: bool,
    skip_doctor: bool,
    approval_mode: str,
) -> None:
    """Start continuous monitoring of repository.

    Watches for file changes and continuously proposes improvements.

    Example:
        ambient watch /path/to/repo
        ambient watch /path/to/repo --auto-approve
        ambient watch /path/to/repo --dry-run
    """
    repo_path_obj = Path(repo_path).resolve()

    click.echo(f"Starting Ambient Swarm monitoring: {repo_path_obj}")
    click.echo()

    # Load config
    if config:
        ambient_config = AmbientConfig.load_from_file(config)
    else:
        ambient_config = load_config(repo_path_obj)

    ambient_config.apply_env_overrides()

    # Create approval handler
    approval_handler: ApprovalHandler
    if dry_run:
        click.echo("Mode: DRY RUN (no changes will be applied)")
        approval_handler = AlwaysRejectHandler(ambient_config.risk_policy)
    elif auto_approve:
        click.echo("Mode: AUTO-APPROVE (all proposals will be applied)")
        approval_handler = AlwaysApproveHandler(ambient_config.risk_policy)
    else:
        if approval_mode == "webhook":
            if not ambient_config.approval.webhook.url:
                raise click.ClickException(
                    "approval_mode=webhook requires approval.webhook.url (or AMBIENT_APPROVAL_WEBHOOK_URL)"
                )
            click.echo("Mode: WEBHOOK (approval required for high-risk changes)")
            approval_handler = WebhookApprovalHandler(
                ambient_config.risk_policy,
                ambient_config.approval.webhook.url,
                headers=ambient_config.approval.webhook.headers,
                timeout_seconds=ambient_config.approval.webhook.timeout_seconds,
            )
        else:
            click.echo("Mode: INTERACTIVE (approval required for high-risk changes)")
            approval_handler = ApprovalHandler(ambient_config.risk_policy, interactive=True)

    click.echo()
    click.echo("Enabled agents:")
    for agent_name in ambient_config.agents.enabled:
        click.echo(f"  ✓ {agent_name}")

    click.echo()
    click.echo("Press Ctrl+C to stop")
    click.echo("=" * 60)
    click.echo()

    if not skip_doctor:
        # Fail fast under supervision if the sandbox cannot start.
        w = Workspace(
            repo_path_obj,
            ambient_config.sandbox.image,
            sandbox_network=ambient_config.sandbox.network_mode,
            sandbox_memory=ambient_config.sandbox.resources.memory,
            sandbox_cpus=ambient_config.sandbox.resources.cpus,
            sandbox_pids_limit=ambient_config.sandbox.resources.pids_limit,
            sandbox_allowed_argv=ambient_config.sandbox.allowed_argv,
            sandbox_allowed_commands=ambient_config.sandbox.allowed_commands,
            sandbox_enforce_allowlist=ambient_config.sandbox.enforce_allowlist,
            sandbox_require_docker=ambient_config.sandbox.require_docker,
            sandbox_stub=ambient_config.sandbox.stub_mode,
            sandbox_repo_mount_mode=ambient_config.sandbox.repo_mount_mode,
            verification_timeout_seconds=ambient_config.verification.timeout_seconds,
        )
        probes: list[list[str]] = [["python", "--version"], ["git", "--version"]]
        for _, argv, _ in getattr(w, "_verification_checks", []):
            if not argv:
                continue
            if argv[:3] == ["python", "-m", "pytest"] or argv[0] == "pytest":
                probes.append(["python", "-m", "pytest", "--version"])
            if argv[:2] == ["ruff", "check"] or argv[:2] == ["ruff", "format"] or argv[0] == "ruff":
                probes.append(["ruff", "--version"])
            if argv[0] == "mypy":
                probes.append(["mypy", "--version"])

        seen: set[str] = set()
        unique: list[list[str]] = []
        for p in probes:
            key = "\x00".join(p)
            if key in seen:
                continue
            seen.add(key)
            unique.append(p)

        res = w.sandbox.doctor(unique)
        if not res.get("ok"):
            raise click.ClickException(f"Doctor failed: {res.get('error')}")

    # Create and start coordinator
    coordinator = AmbientCoordinator(repo_path_obj, ambient_config, approval_handler)

    try:
        asyncio.run(coordinator.start())
    except KeyboardInterrupt:
        click.echo()
        click.echo("Stopping Ambient Swarm...")
        sys.exit(0)


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
@click.option(
    "--auto-approve",
    is_flag=True,
    help="Auto-approve all proposals",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Don't apply any changes",
)
@click.option(
    "--approval-mode",
    type=click.Choice(["interactive", "webhook"]),
    default="interactive",
    show_default=True,
    help="Approval mechanism for high-risk changes.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Save results to JSON file",
)
def run_once(
    repo_path: str,
    config: str | None,
    auto_approve: bool,
    dry_run: bool,
    approval_mode: str,
    output: str | None,
) -> None:
    """Run a single analysis cycle.

    Analyzes repository once and applies proposals.

    Example:
        ambient run-once /path/to/repo
        ambient run-once /path/to/repo --dry-run -o results.json
    """
    repo_path_obj = Path(repo_path).resolve()

    click.echo(f"Running single cycle on: {repo_path_obj}")
    click.echo()

    # Load config
    if config:
        ambient_config = AmbientConfig.load_from_file(config)
    else:
        ambient_config = load_config(repo_path_obj)

    ambient_config.apply_env_overrides()

    # Create approval handler
    approval_handler: ApprovalHandler
    if dry_run:
        approval_handler = AlwaysRejectHandler(ambient_config.risk_policy)
        click.echo("Mode: DRY RUN")
    elif auto_approve:
        approval_handler = AlwaysApproveHandler(ambient_config.risk_policy)
        click.echo("Mode: AUTO-APPROVE")
    else:
        if approval_mode == "webhook":
            if not ambient_config.approval.webhook.url:
                raise click.ClickException(
                    "approval_mode=webhook requires approval.webhook.url (or AMBIENT_APPROVAL_WEBHOOK_URL)"
                )
            approval_handler = WebhookApprovalHandler(
                ambient_config.risk_policy,
                ambient_config.approval.webhook.url,
                headers=ambient_config.approval.webhook.headers,
                timeout_seconds=ambient_config.approval.webhook.timeout_seconds,
            )
            click.echo("Mode: WEBHOOK")
        else:
            approval_handler = ApprovalHandler(ambient_config.risk_policy, interactive=True)
            click.echo("Mode: INTERACTIVE")

    click.echo()

    # Create coordinator and run once
    coordinator = AmbientCoordinator(repo_path_obj, ambient_config, approval_handler)

    event = AmbientEvent(
        type="periodic_scan",
        data={"trigger": "manual"},
        task_spec={"goal": "Manual quality scan", "trigger": "cli"},
    )

    result = asyncio.run(coordinator.run_once(event))

    # Display results
    click.echo()
    click.echo("=" * 60)
    click.echo("RESULTS")
    click.echo("=" * 60)
    click.echo()
    click.echo(f"Status: {result['status']}")
    click.echo(f"Proposals generated: {len(result.get('proposals', []))}")
    click.echo(f"Applied successfully: {len(result.get('applied', []))}")
    click.echo(f"Failed/Rejected: {len(result.get('failed', []))}")

    if result.get("applied"):
        click.echo()
        click.echo("Applied changes:")
        for item in result["applied"]:
            proposal = item["proposal"]
            line = f"  ✓ {proposal.title} ({proposal.agent})"
            if item.get("review_branch"):
                line += f" [branch: {item['review_branch']}]"
            click.echo(line)
            if item.get("patch_path"):
                click.echo(f"    patch: {item['patch_path']}")

    if result.get("failed"):
        click.echo()
        click.echo("Failed/Rejected:")
        for item in result["failed"]:
            proposal = item["proposal"]
            reason = item.get("reason", "unknown")
            click.echo(f"  ✗ {proposal.title} - {reason}")

    # Save to file if requested
    if output:
        output_path = Path(output)
        # Convert result to JSON-serializable format
        json_result = {
            "status": result["status"],
            "run_id": result.get("run_id"),
            "proposals_count": len(result.get("proposals", [])),
            "applied_count": len(result.get("applied", [])),
            "failed_count": len(result.get("failed", [])),
        }
        output_path.write_text(json.dumps(json_result, indent=2))
        click.echo()
        click.echo(f"Results saved to: {output_path}")


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
def verify(repo_path: str, config: str | None) -> None:
    """Verify repository state.

    Runs all verification checks (tests, linters, etc.) without proposing changes.

    Example:
        ambient verify /path/to/repo
    """
    repo_path_obj = Path(repo_path).resolve()

    click.echo(f"Verifying repository: {repo_path_obj}")
    click.echo()

    # Load config
    if config:
        ambient_config = AmbientConfig.load_from_file(config)
    else:
        ambient_config = load_config(repo_path_obj)

    ambient_config.apply_env_overrides()

    # Create workspace and run verification
    workspace = Workspace(
        repo_path_obj,
        ambient_config.sandbox.image,
        sandbox_network=ambient_config.sandbox.network_mode,
        sandbox_memory=ambient_config.sandbox.resources.memory,
        sandbox_cpus=ambient_config.sandbox.resources.cpus,
        sandbox_pids_limit=ambient_config.sandbox.resources.pids_limit,
        sandbox_allowed_argv=ambient_config.sandbox.allowed_argv,
        sandbox_allowed_commands=ambient_config.sandbox.allowed_commands,
        sandbox_enforce_allowlist=ambient_config.sandbox.enforce_allowlist,
        sandbox_require_docker=ambient_config.sandbox.require_docker,
        sandbox_stub=ambient_config.sandbox.stub_mode,
        sandbox_repo_mount_mode=ambient_config.sandbox.repo_mount_mode,
        verification_timeout_seconds=ambient_config.verification.timeout_seconds,
    )

    click.echo("Running verification checks...")
    click.echo()

    result = asyncio.run(workspace.verify_changes())

    # Display results
    if result.ok:
        click.echo("✓ All checks passed!")
    else:
        click.echo("✗ Some checks failed:")
        click.echo()

    for check_result in result.results:
        name = check_result["name"]
        ok = check_result["ok"]
        duration = check_result.get("duration_s", 0)

        if ok:
            click.echo(f"  ✓ {name} ({duration:.2f}s)")
        else:
            click.echo(f"  ✗ {name} ({duration:.2f}s)")
            if check_result.get("stderr"):
                click.echo(f"    Error: {check_result['stderr'][:200]}")

    click.echo()
    click.echo(f"Total duration: {result.duration_s:.2f}s")

    sys.exit(0 if result.ok else 1)


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
def doctor(repo_path: str, config: str | None) -> None:
    """Run startup preflight checks (docker, image, and tool availability)."""
    repo_path_obj = Path(repo_path).resolve()

    if config:
        ambient_config = AmbientConfig.load_from_file(config)
    else:
        ambient_config = load_config(repo_path_obj)
    ambient_config.apply_env_overrides()

    workspace = Workspace(
        repo_path_obj,
        ambient_config.sandbox.image,
        sandbox_network=ambient_config.sandbox.network_mode,
        sandbox_memory=ambient_config.sandbox.resources.memory,
        sandbox_cpus=ambient_config.sandbox.resources.cpus,
        sandbox_pids_limit=ambient_config.sandbox.resources.pids_limit,
        sandbox_allowed_argv=ambient_config.sandbox.allowed_argv,
        sandbox_allowed_commands=ambient_config.sandbox.allowed_commands,
        sandbox_enforce_allowlist=ambient_config.sandbox.enforce_allowlist,
        sandbox_require_docker=ambient_config.sandbox.require_docker,
        sandbox_stub=ambient_config.sandbox.stub_mode,
        sandbox_repo_mount_mode=ambient_config.sandbox.repo_mount_mode,
        verification_timeout_seconds=ambient_config.verification.timeout_seconds,
    )

    # Basic probes; keep them quick and deterministic.
    probes: list[list[str]] = [["python", "--version"], ["git", "--version"]]

    # Probe tools implied by verification checks.
    for _, argv, _ in getattr(workspace, "_verification_checks", []):
        if not argv:
            continue
        if argv[:3] == ["python", "-m", "pytest"] or argv[0] == "pytest":
            probes.append(["python", "-m", "pytest", "--version"])
        if argv[:2] == ["ruff", "check"] or argv[:2] == ["ruff", "format"] or argv[0] == "ruff":
            probes.append(["ruff", "--version"])
        if argv[0] == "mypy":
            probes.append(["mypy", "--version"])

    # De-dupe probes while preserving order.
    seen: set[str] = set()
    unique_probes: list[list[str]] = []
    for p in probes:
        key = "\x00".join(p)
        if key in seen:
            continue
        seen.add(key)
        unique_probes.append(p)

    click.echo(f"ambient doctor: repo={repo_path_obj}")
    click.echo(f"Sandbox image: {ambient_config.sandbox.image}")
    click.echo(f"Repo mount mode: {ambient_config.sandbox.repo_mount_mode}")
    click.echo()

    res = workspace.sandbox.doctor(unique_probes)
    if res.get("ok"):
        click.echo("✓ Doctor checks passed")
        sys.exit(0)

    click.echo("✗ Doctor checks failed")
    if err := res.get("error"):
        click.echo(f"Error: {err}")
    if res.get("image"):
        click.echo(f"Image: {res.get('image')}")
    if res.get("stderr"):
        click.echo(f"Details: {res.get('stderr')}")
    if res.get("checks"):
        click.echo()
        for c in res["checks"]:
            status = "✓" if c.get("ok") else "✗"
            click.echo(f"  {status} {shlex.join(c.get('argv', []))}")
            if not c.get("ok"):
                head = (c.get("stderr_head") or c.get("stdout_head") or "").strip()
                if head:
                    click.echo(f"    {head[:200]}")
    sys.exit(1)


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format",
)
def debug_context(repo_path: str, config: str | None, format: str) -> None:
    """Show repository context that agents see.

    Displays the full context (file tree, configs, etc.) that is sent to agents.

    Example:
        ambient debug-context /path/to/repo
        ambient debug-context /path/to/repo -f json
    """
    repo_path_obj = Path(repo_path).resolve()

    # Load config
    if config:
        ambient_config = AmbientConfig.load_from_file(config)
    else:
        ambient_config = load_config(repo_path_obj)

    ambient_config.apply_env_overrides()

    # Build context
    workspace = Workspace(
        repo_path_obj,
        ambient_config.sandbox.image,
        sandbox_network=ambient_config.sandbox.network_mode,
        sandbox_memory=ambient_config.sandbox.resources.memory,
        sandbox_cpus=ambient_config.sandbox.resources.cpus,
        sandbox_pids_limit=ambient_config.sandbox.resources.pids_limit,
        sandbox_allowed_argv=ambient_config.sandbox.allowed_argv,
        sandbox_allowed_commands=ambient_config.sandbox.allowed_commands,
        sandbox_enforce_allowlist=ambient_config.sandbox.enforce_allowlist,
        sandbox_require_docker=ambient_config.sandbox.require_docker,
        sandbox_stub=ambient_config.sandbox.stub_mode,
        sandbox_repo_mount_mode=ambient_config.sandbox.repo_mount_mode,
        verification_timeout_seconds=ambient_config.verification.timeout_seconds,
    )

    event = AmbientEvent(
        type="debug",
        data={},
        task_spec={"goal": "Debug context", "trigger": "cli"},
    )

    context = asyncio.run(workspace.build_context(event))

    if format == "json":
        # JSON output
        context_dict = {
            "task": context.task,
            "tree": context.tree,
            "important_files": list(context.important_files.keys()),
            "hot_paths": context.hot_paths,
            "conventions": context.conventions,
        }
        click.echo(json.dumps(context_dict, indent=2))
    else:
        # Text output
        click.echo("=" * 60)
        click.echo("REPOSITORY CONTEXT")
        click.echo("=" * 60)
        click.echo()

        click.echo("Task:")
        click.echo(f"  Goal: {context.task.get('goal', 'N/A')}")
        click.echo()

        click.echo("File Tree:")
        click.echo(f"  Total files: {context.tree.get('total_files', 0)}")
        if context.tree.get("files"):
            click.echo("  Files (first 50):")
            for f in context.tree["files"][:50]:
                click.echo(f"    - {f}")
            if len(context.tree["files"]) > 50:
                click.echo(f"    ... and {len(context.tree['files']) - 50} more")
        click.echo()

        if context.important_files:
            click.echo("Important Files:")
            for filename in context.important_files.keys():
                click.echo(f"  - {filename}")
            click.echo()

        if context.hot_paths:
            click.echo("Hot Paths:")
            for path in context.hot_paths:
                click.echo(f"  - {path}")
            click.echo()


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
def init(repo_path: str) -> None:
    """Initialize ambient configuration in repository.

    Creates a default .ambient.yml configuration file.

    Example:
        ambient init /path/to/repo
    """
    repo_path_obj = Path(repo_path).resolve()
    config_path = repo_path_obj / ".ambient.yml"

    if config_path.exists():
        click.echo(f"Configuration already exists: {config_path}")
        if not click.confirm("Overwrite?"):
            return

    # Create default config
    default_config = """# Ambient Swarm Configuration

kimi:
  provider: ollama
  base_url: http://localhost:11434/v1
  model_id: kimi-k2.5:cloud
  max_concurrency: 8
  temperature: 0.2
  timeout_seconds: 300

monitoring:
  enabled: true
  watch_paths:
    - src/
    - tests/
  ignore_patterns:
    - "*.pyc"
    - __pycache__
    - .git
  debounce_seconds: 5
  check_interval_seconds: 300

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
    pids_limit: 100
  repo_mount_mode: ro
  allowed_argv:
    - ["pytest"]
    - ["python", "-m", "pytest"]
    - ["ruff", "check"]
    - ["ruff", "format"]
    - ["mypy"]
    - ["make", "test"]
    - ["make", "lint"]
    - ["make", "check"]

review_worktree:
  enabled: true
  base_dir: .ambient/reviews
  branch_prefix: ambient/review
  max_parallel: 4
  keep_worktrees: true

git:
  commit_on_success: false
  require_clean_before_apply: true

telemetry:
  enabled: true
  log_path: .ambient/telemetry.jsonl
  include_diffs: false
  retention_days: 30
"""

    config_path.write_text(default_config)
    click.echo(f"✓ Created configuration: {config_path}")
    click.echo()
    click.echo("Next steps:")
    click.echo("1. Edit .ambient.yml to customize settings")
    click.echo("2. Build sandbox: docker build -t ambient-sandbox:latest -f docker/Dockerfile .")
    click.echo("3. Start monitoring: ambient watch .")


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
@click.option(
    "--format",
    "-f",
    "format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
@click.option(
    "--window-minutes",
    type=int,
    default=60,
    show_default=True,
    help="Metrics window (best-effort from telemetry).",
)
@click.option(
    "--health",
    is_flag=True,
    help="Exit 0 if last cycle is healthy; 1 otherwise.",
)
def status(repo_path: str, config: str | None, format: str, window_minutes: int, health: bool) -> None:
    """Show operational status/metrics from telemetry."""
    repo_path_obj = Path(repo_path).resolve()

    if config:
        ambient_config = AmbientConfig.load_from_file(config)
    else:
        ambient_config = load_config(repo_path_obj)
    ambient_config.apply_env_overrides()

    telemetry_path = repo_path_obj / ambient_config.telemetry.log_path
    st = compute_status(telemetry_path, window=StatusWindow(seconds=max(60, window_minutes) * 60.0))

    if health:
        last = st.get("last_cycle") or {}
        status_val = (last.get("data") or {}).get("status")
        ok = status_val in {"success", "no_proposals"}
        sys.exit(0 if ok else 1)

    if format == "json":
        click.echo(json.dumps(st, indent=2))
        return

    click.echo(f"Telemetry: {telemetry_path}")
    click.echo(f"Window: {window_minutes} minutes")
    click.echo(f"Proposals/hour: {st.get('proposals_per_hour'):.2f}")
    click.echo(f"Apply success rate: {st.get('apply_success_rate')}")
    click.echo(f"Verify success rate: {st.get('verify_success_rate')}")
    click.echo(f"Queue depth p95: {st.get('queue_depth_p95')}")
    click.echo(f"Queue depth max: {st.get('queue_depth_max')}")
    click.echo(f"Cycle latency p50 (s): {st.get('cycle_latency_s_p50')}")
    click.echo(f"Cycle latency p95 (s): {st.get('cycle_latency_s_p95')}")

    last = st.get("last_cycle") or {}
    if last:
        click.echo()
        click.echo(f"Last cycle: run_id={last.get('run_id')} status={(last.get('data') or {}).get('status')}")


@cli.group()
def telemetry() -> None:
    """Telemetry utilities."""


@telemetry.command("tail")
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
@click.option(
    "--lines",
    "-n",
    type=int,
    default=50,
    show_default=True,
    help="Number of telemetry lines to show.",
)
def telemetry_tail(repo_path: str, config: str | None, lines: int) -> None:
    """Print the last N telemetry events."""
    repo_path_obj = Path(repo_path).resolve()

    if config:
        ambient_config = AmbientConfig.load_from_file(config)
    else:
        ambient_config = load_config(repo_path_obj)
    ambient_config.apply_env_overrides()

    telemetry_path = repo_path_obj / ambient_config.telemetry.log_path

    if not telemetry_path.exists():
        raise click.ClickException(f"Telemetry file not found: {telemetry_path}")

    with open(telemetry_path, encoding="utf-8") as f:
        tail = deque(f, maxlen=max(0, lines))

    for ln in tail:
        click.echo(ln, nl=False)


def main() -> None:
    """Entry point for CLI."""
    cli()


if __name__ == "__main__":
    main()
