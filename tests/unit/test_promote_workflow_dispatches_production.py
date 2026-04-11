from __future__ import annotations

from pathlib import Path


_DEPLOY_WORKFLOW = Path(".github/workflows/deploy.yml")


def test_deploy_workflow_supports_push_and_manual_production_deploy() -> None:
    content = _DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "name: Deploy" in content
    assert "workflow_dispatch:" in content
    assert "push:" in content
    assert "branches:" in content
    assert "- main" in content
    assert "deploy-production:" in content
