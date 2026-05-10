"""Regression guards for the post-2026-05-10 PR-only-to-main /ship redesign.

Pre-2026-05-10, /ship did a fast-forward chain `feature/latest2 → develop
→ main` and triggered Deploy directly. After the develop branch retirement,
/ship opens a PR to `main` and operator merges in GitHub UI. These tests
pin the new shape so a future edit can't silently regress to the old chain.

The slash-command file is pure markdown + bash; we can assert on its text
without executing anything.
"""
from __future__ import annotations

from pathlib import Path

_SHIP_PRIMARY = Path(".claude/commands/ship.md")
_SHIP_AGENTS_COPY = Path(".agents/workflows/ship.md")


# ─── primary slash command (.claude/commands/ship.md) ──────────────────


def test_ship_primary_does_not_reference_develop_branch() -> None:
    """The retired develop branch must not appear as an active workflow step.

    Allowed: anti-pattern warnings ('develop' was retired ...) that
    explicitly tell the operator NOT to use it. We detect those by
    checking for the retirement marker phrase.
    """
    content = _SHIP_PRIMARY.read_text(encoding="utf-8")

    # Old chain phrases that MUST be gone
    forbidden = [
        "git checkout develop",
        "git push origin develop",
        "merges them into `develop`",
        "fast-forwards `main` with `develop`",
        "Updates `develop`",
        "Merge into develop",
        "git merge develop",
    ]
    for needle in forbidden:
        assert needle not in content, (
            f"/ship still references the retired develop chain: {needle!r} "
            f"(see docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md)"
        )

    # Retirement marker must be present so future readers know why
    assert "retired" in content.lower() and "2026-05-10" in content, (
        "/ship missing the develop-retirement marker — future readers won't"
        " know why the chain was collapsed"
    )


def test_ship_primary_opens_pr_to_main() -> None:
    """The new flow must call gh pr create with --base main."""
    content = _SHIP_PRIMARY.read_text(encoding="utf-8")
    assert "gh pr create" in content, "/ship no longer opens a PR (the whole point)"
    assert "--base main" in content, "/ship PR target must be main, not develop or feature/latest2"


def test_ship_primary_refuses_main_branch() -> None:
    """/ship must refuse to run from main itself.

    The PR head must be a feature branch — running /ship from main would
    open a PR to itself (or leave the user wondering why nothing happened).
    """
    content = _SHIP_PRIMARY.read_text(encoding="utf-8")
    assert 'CURRENT_BRANCH" = "main"' in content, "/ship must refuse to run from main"


def test_ship_primary_refuses_develop_branch_explicitly() -> None:
    """If anyone accidentally creates a 'develop' branch locally, /ship must refuse.

    The retired branch could conceivably be recreated by accident; the
    refusal stops a developer from accidentally PRing it as 'feature work'.
    """
    content = _SHIP_PRIMARY.read_text(encoding="utf-8")
    assert 'CURRENT_BRANCH" = "develop"' in content, (
        "/ship should refuse 'develop' branch with a retirement-aware error "
        "(prevents accidental local recreation from re-entering the workflow)"
    )


def test_ship_primary_keeps_preflight_safety() -> None:
    """The 2026-05-07 import-storm guards must survive the redesign.

    Specifically:
      - .py.bak / .swp / .py.orig artifact tripwire (catches half-finished
        autonomous patcher runs)
      - py_compile syntax check on every changed .py file (catches the
        SyntaxError class)
    """
    content = _SHIP_PRIMARY.read_text(encoding="utf-8")
    # Artifact tripwire
    assert "*.py.bak" in content
    assert "*.swp" in content
    assert "*.py.orig" in content
    # Syntax check
    assert "compile(open(" in content, "/ship must keep its py_compile pre-flight check"


def test_ship_primary_verifies_branch_advanced_after_push() -> None:
    """The 2026-05-08 silent-no-op-push guard must survive.

    git push can exit 0 with 'Everything up-to-date' when the local
    branch is stale; if that happens, the PR contains no commits. /ship
    must verify origin/<branch> actually advanced.
    """
    content = _SHIP_PRIMARY.read_text(encoding="utf-8")
    assert "git fetch origin" in content
    # The shape: re-read origin/<branch> after push, compare to local HEAD
    assert "POST_SHA" in content, "/ship must re-read origin/<branch> after push to catch silent no-op"


def test_ship_primary_provides_no_gh_fallback() -> None:
    """When gh CLI is missing, /ship must still tell the operator how to open the PR.

    /ship runs in many environments (sandbox-Claude, fresh VPS dev tree,
    etc.) where gh isn't installed. The fallback is a printed compare URL.
    """
    content = _SHIP_PRIMARY.read_text(encoding="utf-8")
    assert "compare/main" in content, (
        "/ship must print a github.com/.../compare/main...<branch> URL when gh is unavailable"
    )


# ─── secondary slash command (.agents/workflows/ship.md) ───────────────


def test_ship_agents_copy_matches_primary() -> None:
    """The two ship.md files must stay in sync — both describe the same workflow.

    Some tools/operators read .agents/workflows/ship.md (per
    docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md),
    others read .claude/commands/ship.md. They must describe the same flow
    or operators will hit a workflow that doesn't match the docs.
    """
    primary = _SHIP_PRIMARY.read_text(encoding="utf-8")
    secondary = _SHIP_AGENTS_COPY.read_text(encoding="utf-8")
    assert primary == secondary, (
        ".agents/workflows/ship.md is out of sync with .claude/commands/ship.md. "
        "Re-run: cp .claude/commands/ship.md .agents/workflows/ship.md"
    )
