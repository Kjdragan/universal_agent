from __future__ import annotations

from pathlib import Path

_DEPLOY_WORKFLOW = Path(".github/workflows/deploy.yml")
_RUNTIME_HELPER = Path("scripts/deploy_validate_runtime.sh")
_SYSTEMD_INSTALLER = Path("scripts/install_vps_systemd_units.sh")


def test_runtime_helper_repairs_unreadable_stale_venv_before_uv_sync() -> None:
    content = _RUNTIME_HELPER.read_text(encoding="utf-8")

    assert 'echo "--> Checking whether the existing venv is usable by $SERVICE_USER..."' in content
    assert 'if [[ -e "$APP_ROOT/.venv/bin/python3" ]]; then' in content
    assert 'run_as_service_user "readlink -f "$APP_ROOT/.venv/bin/python3" >/dev/null 2>&1"' not in content
    assert 'run_as_service_user "readlink -f \\"$APP_ROOT/.venv/bin/python3\\" >/dev/null 2>&1"' in content
    assert (
        'echo "--> Existing venv interpreter is not accessible to $SERVICE_USER; removing stale .venv for clean rebuild..."'
        in content
    )
    assert 'remove_runtime_venv' in content
    assert 'uv sync --python 3.13 --no-install-package manim --no-install-package pycairo --no-install-package manimpango' in content
    assert 'scripts/verify_observability_runtime.py --json' in content
    assert 'scripts/verify_service_imports.py' in content


def test_deploy_workflow_uses_centralized_runtime_helper() -> None:
    content = _DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert 'bash "$PROD_DIR/scripts/deploy_validate_runtime.sh"' in content
    assert '--expect-environment production' in content
    assert '--expect-runtime-stage production' in content
    assert '--expect-machine-slug vps-hq-production' in content


def test_deploy_workflow_restarts_python_services_on_stale_interpreter() -> None:
    content = _DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert 'echo "--> Verifying Python services use current venv interpreter..."' in content
    assert 'ensure_current_venv_interpreter universal-agent-gateway' in content
    assert 'ensure_current_venv_interpreter universal-agent-api' in content
    assert 'ensure_current_venv_interpreter ua-discord-intelligence' in content
    assert 'ensure_current_venv_interpreter ua-discord-cc-bot' not in content
    assert 'actual_python="$(readlink -f "/proc/$pid/exe"' in content
    assert 'expected_python="$(readlink -f "$PROD_DIR/.venv/bin/python")"' in content
    assert 'sudo systemctl restart "$service_name"' in content


def test_deploy_workflow_fails_when_post_restart_health_fails() -> None:
    content = _DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "trap cleanup_deployment_window EXIT" in content
    assert 'echo "--> Verifying production service health..."' in content
    # Gateway window is 96×5s = 8 min (raised from 48×5s = 4 min on 2026-05-10
    # after Deploy #436 + #437 timed out at the previous window even though
    # the gateway came up healthy seconds later — the lifespan does ~734
    # lines of synchronous setup before yielding). api/webui windows
    # unchanged because they're not the bottleneck.
    assert 'run_health_check gateway "http://127.0.0.1:8002/api/v1/health" 96 5' in content
    assert 'run_health_check api "http://127.0.0.1:8001/api/health" 24 5' in content
    assert 'run_health_check webui "http://127.0.0.1:3000/dashboard" 24 5' in content
    assert 'health_pids="$health_pids $!"' in content
    assert 'for health_pid in $health_pids; do' in content
    assert 'wait "$health_pid" || true' in content
    assert 'echo "::error::$name did not become healthy at $url"' in content
    assert "sudo journalctl -u universal-agent-gateway -n 120 --no-pager" in content
    assert 'exit 1' in content


def test_deploy_workflow_paths_ignore_suppresses_docs_only_deploys() -> None:
    """Deploy must skip docs-only / report-only commits to main.

    The two scheduled jobs (nightly-doc-drift-audit, openclaw-release-sync)
    auto-merge their report PRs into `main` post-2026-05-10 (when develop
    was retired). Without paths-ignore, every nightly run would restart
    the gateway for zero behavior change. This guard pins the path
    filter so a future commit can't silently re-enable docs-triggered
    deploys.

    GitHub semantics: deploy is skipped only when EVERY changed file
    matches a paths-ignore glob — a mixed code+docs commit still deploys.
    """
    content = _DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "paths-ignore:" in content
    # Keep these exact globs in sync with .github/workflows/deploy.yml's on.push trigger.
    # `memory/**` was added 2026-05-10 follow-up after PR #182 merge triggered a
    # no-op gateway restart for harness session-memory updates.
    for glob in (
        "- 'docs/**'",
        "- '**.md'",
        "- 'reports/**'",
        "- 'state/**'",
        "- 'artifacts/**'",
        "- 'memory/**'",
    ):
        assert glob in content, f"paths-ignore missing required glob: {glob}"


def test_production_systemd_installer_manages_discord_services() -> None:
    content = _SYSTEMD_INSTALLER.read_text(encoding="utf-8")

    assert "ua-discord-cc-bot.service.template" in content
    assert "ua-discord-intelligence.service.template" in content
    assert '"ua-discord-cc-bot.service"' in content
    assert '"ua-discord-intelligence.service"' in content
    deploy_content = _DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    # Discord services must still be restarted by deploy.
    assert "ua-discord-cc-bot ua-discord-intelligence" in deploy_content
    # Discord services must still be health-checked, but the gate is now
    # baseline-aware (see `check_discord_regression`) so chronic crash
    # loops don't false-positive the deploy. Pin the new mechanism so a
    # future refactor can't silently drop discord from the health gate.
    assert "discord_cc_pre=" in deploy_content
    assert "discord_intel_pre=" in deploy_content
    assert "check_discord_regression()" in deploy_content
    assert "check_discord_regression ua-discord-cc-bot" in deploy_content
    assert "check_discord_regression ua-discord-intelligence" in deploy_content
