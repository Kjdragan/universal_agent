from __future__ import annotations

from pathlib import Path


_DEPLOY_PROD_WORKFLOW = Path(".github/workflows/deploy-prod.yml")


def test_deploy_prod_workflow_repairs_unreadable_stale_venv_before_uv_sync() -> None:
    content = _DEPLOY_PROD_WORKFLOW.read_text(encoding="utf-8")

    assert 'echo "--> Checking whether the existing venv is usable by ua..."' in content
    assert 'if [ -e "$PROD_DIR/.venv/bin/python3" ]; then' in content
    assert (
        'sudo -H -u ua bash -c "readlink -f \\"$PROD_DIR/.venv/bin/python3\\" >/dev/null 2>&1"'
        in content
    )
    assert (
        'echo "--> Existing venv interpreter is not accessible to ua; removing stale .venv for clean rebuild..."'
        in content
    )
    assert 'sudo rm -rf "$PROD_DIR/.venv"' in content
    assert (
        'sudo -H -E -u ua bash -c "export PATH=\\"$PATH\\"; uv sync --python 3.12'
        in content
    )
