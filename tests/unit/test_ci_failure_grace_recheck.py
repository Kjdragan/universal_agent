"""Unit tests for the CI-failure grace-recheck decision + gating logic.

These cover the operator's required scenarios without touching the network or
the DB:
  - orphaned, still-red, autofixable  -> dispatch Cody
  - actively-owned (fix pushed in grace window) -> stand down (coordination)
  - resolved / merged / already-claimed -> stand down
  - not-autofixable (deploy/infra/no-PR) -> escalate
"""

from __future__ import annotations

import json

import pytest

from universal_agent.scripts import ci_failure_grace_recheck as mod
from universal_agent.scripts.ci_failure_grace_recheck import (
    FailureContext,
    RepoState,
    classify_failure,
    decide_action,
    gather_repo_state,
    run_grace_recheck,
)

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeGh:
    """Maps a gh subcommand (argv[0], argv[1]) to a canned (rc, stdout)."""

    def __init__(self, responses: dict[tuple[str, str], tuple[int, str]]):
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> tuple[int, str, str]:
        self.calls.append(args)
        key = (args[0], args[1] if len(args) > 1 else "")
        rc, out = self.responses.get(key, (0, ""))
        return rc, out, ""


def _ctx(**overrides) -> FailureContext:
    base = dict(
        workflow="PR Validate",
        head_sha="abc123def456",
        run_id="555",
        head_branch="claude/feature-x",
        pr_number=42,
        run_url="https://example/run/555",
    )
    base.update(overrides)
    return FailureContext(**base)


# --------------------------------------------------------------------------- #
# classify_failure
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "workflow,pr_number,expected",
    [
        ("PR Validate", 42, "cody"),
        ("Documentation Audit", 42, "cody"),
        ("Nightly Documentation Health", 7, "cody"),
        ("PR Validate", None, "escalate"),  # no PR -> can't push a branch fix
        ("Deploy", 42, "escalate"),  # infra
        ("PR Auto-Merge", 42, "escalate"),  # mechanical/merge-state
        ("PR Rebase Watchdog", 42, "escalate"),
    ],
)
def test_classify_failure(workflow, pr_number, expected):
    assert classify_failure(workflow, pr_number) == expected


# --------------------------------------------------------------------------- #
# decide_action — the re-verify gate
# --------------------------------------------------------------------------- #


def _healthy_orphan_state() -> RepoState:
    """Issue open, PR open at the same failing SHA, run still red, no claim."""
    return RepoState(
        issue_open=True,
        issue_number=99,
        issue_labels=["ci-failure"],
        pr_state="OPEN",
        pr_merged=False,
        pr_head_sha="abc123def456",
        run_conclusion="failure",
    )


def test_orphaned_autofixable_dispatches_cody():
    action, reason = decide_action(_ctx(), _healthy_orphan_state())
    assert action == "dispatch_cody"
    assert reason == "orphaned_autofixable"


def test_actively_owned_newer_push_stands_down():
    """The coordination case: owner pushed a fix during the grace window."""
    state = _healthy_orphan_state()
    state.pr_head_sha = "newsha999"  # head moved past the failing SHA
    action, reason = decide_action(_ctx(), state)
    assert action == "stand_down"
    assert reason == "newer_push"


def test_issue_closed_stands_down():
    state = _healthy_orphan_state()
    state.issue_open = False
    action, reason = decide_action(_ctx(), state)
    assert action == "stand_down"
    assert reason == "issue_closed_or_missing"


def test_pr_merged_stands_down():
    state = _healthy_orphan_state()
    state.pr_merged = True
    action, reason = decide_action(_ctx(), state)
    assert action == "stand_down"
    assert reason == "pr_merged"


def test_run_now_green_stands_down():
    state = _healthy_orphan_state()
    state.run_conclusion = "success"
    action, reason = decide_action(_ctx(), state)
    assert action == "stand_down"
    assert reason == "run_no_longer_failing"


def test_already_dispatched_stands_down():
    state = _healthy_orphan_state()
    state.issue_labels = ["ci-failure", "ci-autofix-dispatched"]
    action, reason = decide_action(_ctx(), state)
    assert action == "stand_down"
    assert reason == "already_dispatched"


def test_already_escalated_stands_down():
    state = _healthy_orphan_state()
    state.issue_labels = ["ci-failure", "needs-operator"]
    action, reason = decide_action(_ctx(), state)
    assert action == "stand_down"
    assert reason == "already_escalated"


def test_orphaned_not_autofixable_escalates():
    state = _healthy_orphan_state()
    action, reason = decide_action(_ctx(workflow="Deploy", pr_number=None), state)
    assert action == "escalate"
    assert reason == "orphaned_needs_operator"


# --------------------------------------------------------------------------- #
# gather_repo_state — parsing gh JSON
# --------------------------------------------------------------------------- #


def test_gather_repo_state_parses_open_orphan():
    gh = FakeGh(
        {
            ("issue", "list"): (0, json.dumps([{"number": 99}])),
            ("issue", "view"): (
                0,
                json.dumps({"state": "OPEN", "labels": [{"name": "ci-failure"}]}),
            ),
            ("pr", "view"): (
                0,
                json.dumps({"state": "OPEN", "mergedAt": None, "headRefOid": "abc123def456"}),
            ),
            ("run", "view"): (0, json.dumps({"conclusion": "failure"})),
        }
    )
    state = gather_repo_state(_ctx(), gh)
    assert state.issue_open is True
    assert state.issue_number == 99
    assert state.pr_state == "OPEN"
    assert state.pr_merged is False
    assert state.pr_head_sha == "abc123def456"
    assert state.run_conclusion == "failure"


def test_gather_repo_state_no_issue_means_resolved():
    gh = FakeGh({("issue", "list"): (0, "[]")})
    state = gather_repo_state(_ctx(), gh)
    assert state.issue_open is False
    assert state.issue_number is None


def test_gather_repo_state_uses_explicit_issue_number():
    gh = FakeGh(
        {
            ("issue", "view"): (
                0,
                json.dumps({"state": "OPEN", "labels": []}),
            ),
            ("pr", "view"): (
                0,
                json.dumps({"state": "MERGED", "mergedAt": "2026-05-30T00:00:00Z", "headRefOid": "x"}),
            ),
            ("run", "view"): (0, json.dumps({"conclusion": "failure"})),
        }
    )
    state = gather_repo_state(_ctx(issue_number=123), gh)
    assert state.issue_number == 123
    assert state.pr_merged is True
    # No issue-list call should have happened (explicit number provided).
    assert not any(c[0] == "issue" and c[1] == "list" for c in gh.calls)


# --------------------------------------------------------------------------- #
# run_grace_recheck — orchestration routing (executors monkeypatched)
# --------------------------------------------------------------------------- #


def test_run_grace_recheck_stand_down_does_not_dispatch(monkeypatch):
    called = {"cody": False, "escalate": False}
    monkeypatch.setattr(
        mod, "dispatch_cody_fix", lambda *a, **k: called.__setitem__("cody", True)
    )
    monkeypatch.setattr(
        mod, "escalate_to_operator", lambda *a, **k: called.__setitem__("escalate", True)
    )
    gh = FakeGh({("issue", "list"): (0, "[]")})  # no open issue -> stand down
    result = run_grace_recheck(_ctx(), gh=gh)
    assert result["action"] == "stand_down"
    assert called == {"cody": False, "escalate": False}


def test_run_grace_recheck_orphan_dispatches_cody(monkeypatch):
    called = {"cody": False, "escalate": False}
    monkeypatch.setattr(
        mod,
        "dispatch_cody_fix",
        lambda *a, **k: (called.__setitem__("cody", True) or {"action": "dispatch_cody"}),
    )
    monkeypatch.setattr(
        mod,
        "escalate_to_operator",
        lambda *a, **k: (called.__setitem__("escalate", True) or {"action": "escalate"}),
    )
    gh = FakeGh(
        {
            ("issue", "list"): (0, json.dumps([{"number": 99}])),
            ("issue", "view"): (0, json.dumps({"state": "OPEN", "labels": [{"name": "ci-failure"}]})),
            ("pr", "view"): (
                0,
                json.dumps({"state": "OPEN", "mergedAt": None, "headRefOid": "abc123def456"}),
            ),
            ("run", "view"): (0, json.dumps({"conclusion": "failure"})),
        }
    )
    result = run_grace_recheck(_ctx(), gh=gh)
    assert result["action"] == "dispatch_cody"
    assert called["cody"] is True
    assert called["escalate"] is False


def test_run_grace_recheck_orphan_escalates(monkeypatch):
    called = {"cody": False, "escalate": False}
    monkeypatch.setattr(
        mod,
        "dispatch_cody_fix",
        lambda *a, **k: (called.__setitem__("cody", True) or {"action": "dispatch_cody"}),
    )
    monkeypatch.setattr(
        mod,
        "escalate_to_operator",
        lambda *a, **k: (called.__setitem__("escalate", True) or {"action": "escalate"}),
    )
    gh = FakeGh(
        {
            ("issue", "list"): (0, json.dumps([{"number": 99}])),
            ("issue", "view"): (0, json.dumps({"state": "OPEN", "labels": [{"name": "ci-failure"}]})),
            ("run", "view"): (0, json.dumps({"conclusion": "failure"})),
        }
    )
    # Deploy on main: no PR, not autofixable.
    result = run_grace_recheck(_ctx(workflow="Deploy", pr_number=None), gh=gh)
    assert result["action"] == "escalate"
    assert called["escalate"] is True
    assert called["cody"] is False


# --------------------------------------------------------------------------- #
# payload decode
# --------------------------------------------------------------------------- #


def test_failure_context_from_payload_coerces_pr_number():
    ctx = FailureContext.from_payload(
        {"workflow": "PR Validate", "run_id": "9", "head_sha": "s", "pr_number": "42"}
    )
    assert ctx.pr_number == 42
    assert ctx.workflow == "PR Validate"


def test_failure_context_from_payload_handles_blank_pr():
    ctx = FailureContext.from_payload(
        {"workflow_name": "Deploy", "run_id": "9", "head_sha": "s", "pr_number": ""}
    )
    assert ctx.pr_number is None
    assert ctx.workflow == "Deploy"
