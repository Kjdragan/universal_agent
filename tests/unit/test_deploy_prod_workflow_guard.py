from __future__ import annotations

from pathlib import Path


_DEPLOY_PROD_WORKFLOW = Path(".github/workflows/deploy-prod.yml")
_DEPLOY_STAGING_WORKFLOW = Path(".github/workflows/deploy-staging.yml")
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


def test_deploy_prod_workflow_uses_centralized_runtime_helper() -> None:
    content = _DEPLOY_PROD_WORKFLOW.read_text(encoding="utf-8")

    assert 'bash "$PROD_DIR/scripts/deploy_validate_runtime.sh"' in content
    assert '--expect-environment production' in content
    assert '--expect-runtime-stage production' in content
    assert '--expect-machine-slug vps-hq-production' in content


def test_deploy_staging_workflow_uses_centralized_runtime_helper() -> None:
    content = _DEPLOY_STAGING_WORKFLOW.read_text(encoding="utf-8")

    assert 'bash "\\$STAGING_DIR/scripts/deploy_validate_runtime.sh"' in content
    assert '--expect-environment staging' in content
    assert '--expect-runtime-stage staging' in content
    assert '--expect-machine-slug vps-hq-staging' in content
