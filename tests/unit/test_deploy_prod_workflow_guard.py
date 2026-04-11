from __future__ import annotations

from pathlib import Path


_DEPLOY_WORKFLOW = Path(".github/workflows/deploy.yml")
_RUNTIME_HELPER = Path("scripts/deploy_validate_runtime.sh")


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
    assert 'uv sync --python 3.12 --no-install-package manim --no-install-package pycairo --no-install-package manimpango' in content
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
    assert 'actual_python="$(readlink -f "/proc/$pid/exe"' in content
    assert 'expected_python="$(readlink -f "$PROD_DIR/.venv/bin/python")"' in content
    assert 'sudo systemctl restart "$service_name"' in content


def test_deploy_workflow_fails_when_post_restart_health_fails() -> None:
    content = _DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert 'echo "--> Verifying production service health..."' in content
    assert 'check_local_health gateway "http://127.0.0.1:8002/api/v1/health"' in content
    assert 'check_local_health api "http://127.0.0.1:8001/api/health"' in content
    assert 'check_local_health webui "http://127.0.0.1:3000/dashboard"' in content
    assert 'echo "::error::$name did not become healthy at $url"' in content
    assert "sudo journalctl -u universal-agent-gateway -n 120 --no-pager" in content
    assert 'exit 1' in content
