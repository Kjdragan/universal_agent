from __future__ import annotations

from pathlib import Path


_PROMOTE_WORKFLOW = Path(".github/workflows/promote-develop-to-main.yml")


def test_promote_workflow_dispatches_deploy_prod_after_advancing_main() -> None:
    content = _PROMOTE_WORKFLOW.read_text(encoding="utf-8")

    assert "actions: write" in content
    assert 'git push origin "${TARGET_SHA}:refs/heads/main"' in content
    assert 'name: Dispatch production deploy workflow' in content
    assert "/actions/workflows/deploy-prod.yml/dispatches" in content
    assert '-d \'{"ref":"main"}\'' in content
    assert 'echo "Triggered Deploy Production workflow for main."' in content
