"""Command-line interface for Ambient Swarm.

Commands:
- ambient watch <repo_path>: Start continuous monitoring
- ambient run-once <repo_path>: Run single cycle
- ambient verify <repo_path>: Verify repository state
- ambient debug context <repo_path>: Show repository context
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click

from .config import load_config, AmbientConfig
from .coordinator import AmbientCoordinator
from .workspace import Workspace
from .types import AmbientEvent
from .approval import ApprovalHandler, AlwaysApproveHandler, AlwaysRejectHandler


@click.group()
@click.version_option(version="2.0.0", prog_name="ambient")
def cli():
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
def watch(repo_path: str, config: str | None, auto_approve: bool, dry_run: bool):
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
    if dry_run:
        click.echo("Mode: DRY RUN (no changes will be applied)")
        approval_handler = AlwaysRejectHandler(ambient_config.risk_policy)
    elif auto_approve:
        click.echo("Mode: AUTO-APPROVE (all proposals will be applied)")
        approval_handler = AlwaysApproveHandler(ambient_config.risk_policy)
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
    output: str | None,
):
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
    if dry_run:
        approval_handler = AlwaysRejectHandler(ambient_config.risk_policy)
        click.echo("Mode: DRY RUN")
    elif auto_approve:
        approval_handler = AlwaysApproveHandler(ambient_config.risk_policy)
        click.echo("Mode: AUTO-APPROVE")
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
            click.echo(f"  ✓ {proposal.title} ({proposal.agent})")

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
def verify(repo_path: str, config: str | None):
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
    workspace = Workspace(repo_path_obj, ambient_config.sandbox.image)

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
@click.option(
    "--format",
    "-f",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format",
)
def debug_context(repo_path: str, config: str | None, format: str):
    """Show repository context that agents see.

    Displays the full context (file tree, configs, etc.) that is sent to agents.

    Example:
        ambient debug context /path/to/repo
        ambient debug context /path/to/repo -f json
    """
    repo_path_obj = Path(repo_path).resolve()

    # Load config
    if config:
        ambient_config = AmbientConfig.load_from_file(config)
    else:
        ambient_config = load_config(repo_path_obj)

    ambient_config.apply_env_overrides()

    # Build context
    workspace = Workspace(repo_path_obj, ambient_config.sandbox.image)

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
def init(repo_path: str):
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
  allowed_commands:
    - ^pytest
    - ^python\\s+-m\\s+pytest
    - ^ruff\\s+(check|format)
    - ^mypy
    - ^make\\s+(test|lint|check)

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


def main():
    """Entry point for CLI."""
    cli()


if __name__ == "__main__":
    main()
