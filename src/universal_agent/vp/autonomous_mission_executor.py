"""Tier-2 autonomous-mission orchestrator.

Implements the 8-step contract defined in
``docs/deployment/ai_coder_instructions.md`` ("Autonomous Mission Workflow").
Designed to be the single entry point any tier-2 caller (CODIE proactive
cleanup, Cody scaffold-builder follow-ups, scheduled VP coder missions)
uses to mutate the repo. Direct edits inside ``/opt/universal_agent/src/``
are forbidden by the contract; routing every mutation through this
executor is how we enforce the rule in code, not just in docs.

Sequence (see ``ai_coder_instructions.md:152-182``):

    1.  Caller hands us the claimed Task Hub item.
    2.  We provision a fresh worktree off ``origin/main``.
    3.  Caller's ``patch_fn`` writes changes inside the worktree only.
    4.  We compile() every modified ``.py`` and revert + abort on failure.
    5.  We run a targeted test pass and revert + abort on failure.
    6.  We commit + push to ``<bot>/<task_id>``.
    7.  We open a PR targeting ``main``.
    8.  Any failure in 3-7 reverts the worktree and tears it down.

Failure modes are reported via :class:`MissionResult` rather than raised,
so a worker loop can record the outcome in Task Hub without an extra
try/except layer. Side-effecting steps (push, PR creation, test runner)
are dependency-injected so unit tests can exercise the orchestrator
without actually hitting GitHub or running pytest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping, Optional, Protocol
import urllib.error
import urllib.request

from universal_agent.vp.worktree_utils import (
    ArtifactLeakError,
    SyntaxCheckResult,
    WorktreeError,
    WorktreeProvisionResult,
    assert_no_artifacts,
    detect_repo_root,
    list_changed_py_files,
    provision_worktree,
    revert_changed_files,
    syntax_check_changed_py,
    teardown_worktree,
)

logger = logging.getLogger(__name__)


__all__ = [
    "MissionResult",
    "PatchFn",
    "PRCreator",
    "TestRunner",
    "default_pr_creator",
    "default_test_runner",
    "execute_autonomous_mission",
]


_GH_REPO_DEFAULT = os.getenv("UA_GH_REPO", "Kjdragan/universal_agent")


class PatchFn(Protocol):
    """Caller-supplied function that writes patches inside the worktree.

    Should return the list of paths it modified (absolute or
    worktree-relative). Returning an empty list signals "no-op" and the
    executor will short-circuit before pushing.
    """

    def __call__(self, worktree_path: Path) -> list[Path]: ...  # pragma: no cover


class TestRunner(Protocol):
    """Caller-supplied function that runs the relevant test pass.

    Returns ``(ok, combined_output)``. Defaults to a ``uv run pytest``
    invocation scoped to the changed modules; tests inject a no-op.
    """

    def __call__(
        self, worktree_path: Path, changed_files: list[Path],
    ) -> tuple[bool, str]: ...  # pragma: no cover


class PRCreator(Protocol):
    """Pushes the branch and opens a PR. Returns the PR URL or None."""

    def __call__(
        self,
        *,
        worktree_path: Path,
        branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> Optional[str]: ...  # pragma: no cover


@dataclass
class MissionResult:
    """Structured outcome the caller can persist back to Task Hub."""

    succeeded: bool
    bot: str
    task_id: str
    base_branch: str
    branch: str | None = None
    worktree_path: Path | None = None
    changed_files: list[Path] = field(default_factory=list)
    pushed: bool = False
    pr_url: str | None = None
    syntax_failures: list[tuple[Path, str]] = field(default_factory=list)
    test_output: str | None = None
    failure_reason: str | None = None
    reverted: bool = False
    teardown_ok: bool = False

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly representation suitable for Task Hub notes."""

        return {
            "succeeded": self.succeeded,
            "bot": self.bot,
            "task_id": self.task_id,
            "base_branch": self.base_branch,
            "branch": self.branch,
            "worktree_path": str(self.worktree_path) if self.worktree_path else None,
            "changed_files": [str(p) for p in self.changed_files],
            "pushed": self.pushed,
            "pr_url": self.pr_url,
            "syntax_failures": [(str(p), msg) for p, msg in self.syntax_failures],
            "test_output_excerpt": (self.test_output or "")[-2000:] or None,
            "failure_reason": self.failure_reason,
            "reverted": self.reverted,
            "teardown_ok": self.teardown_ok,
        }


def default_test_runner(
    worktree_path: Path, changed_files: list[Path],
) -> tuple[bool, str]:
    """Run ``uv run pytest -x -q`` scoped to changed modules, if any.

    The contract step 5 says "Run a fast targeted test pass on the
    changed modules". When we cannot infer a sensible scope (e.g. only
    docs changed) we return ``(True, "skipped: no python changes")``
    rather than running the full suite, which would defeat the
    "fast" requirement.
    """

    py_changes = [p for p in changed_files if p.suffix == ".py"]
    if not py_changes:
        return True, "skipped: no python changes"

    cmd = ["uv", "run", "pytest", "-x", "-q"]
    result = subprocess.run(
        cmd, cwd=str(worktree_path), capture_output=True, text=True, timeout=600,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, combined


def default_pr_creator(
    *,
    worktree_path: Path,
    branch: str,
    base_branch: str,
    title: str,
    body: str,
    repo: str | None = None,
    token: str | None = None,
    timeout: int = 30,
) -> Optional[str]:
    """Push the branch and open a PR via the GitHub REST API.

    Mirrors the urllib pattern in ``vp/worker_loop.py:_post_mission_push_pr_merge``
    but targets the tier-2 contract instead of the docs-PR flow:

    * branch name is caller-supplied (``<bot>/<task_id>``)
    * PR target is caller-supplied (``main`` by default)
    * NEVER auto-merges. The contract requires human review.
    """

    repo_full = repo or _GH_REPO_DEFAULT
    gh_token = (token or os.getenv("GITHUB_TOKEN") or "").strip()
    if not gh_token:
        # As a final fallback, try extracting from the remote URL
        # (matches the legacy worker_loop pattern).
        url_proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(worktree_path), capture_output=True, text=True,
        )
        remote_url = (url_proc.stdout or "").strip()
        import re

        match = re.search(r"x-access-token:([^@]+)@", remote_url)
        if match:
            gh_token = match.group(1)

    if not gh_token:
        logger.warning("default_pr_creator: no GitHub token available; cannot push")
        return None

    push_proc = subprocess.run(
        ["git", "push", "-u", "origin", branch],
        cwd=str(worktree_path), capture_output=True, text=True, timeout=timeout,
    )
    if push_proc.returncode != 0:
        logger.warning(
            "default_pr_creator: git push failed for %s: %s",
            branch, (push_proc.stderr or push_proc.stdout).strip(),
        )
        return None

    payload = json.dumps({
        "title": title,
        "head": branch,
        "base": base_branch,
        "body": body,
    }).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo_full}/pulls",
        data=payload,
        headers={
            "Authorization": f"token {gh_token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return data.get("html_url")
    except urllib.error.HTTPError as exc:
        body_excerpt = exc.read().decode("utf-8", errors="replace")[:500]
        logger.warning(
            "default_pr_creator: PR creation failed (HTTP %d): %s",
            exc.code, body_excerpt,
        )
        return None
    except Exception as exc:  # pragma: no cover - network failures
        logger.warning("default_pr_creator: PR creation failed: %s", exc)
        return None


def _commit_changes(
    worktree_path: Path, message: str,
) -> tuple[bool, str]:
    """Stage every change and create a single commit. Returns (ok, output)."""

    add = subprocess.run(
        ["git", "add", "-A"],
        cwd=str(worktree_path), capture_output=True, text=True,
    )
    if add.returncode != 0:
        return False, (add.stderr or add.stdout).strip()

    commit = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=str(worktree_path), capture_output=True, text=True,
    )
    if commit.returncode != 0:
        return False, (commit.stderr or commit.stdout).strip()
    return True, commit.stdout.strip()


def _extract_task_summary(task: Mapping[str, Any]) -> str:
    """Pull a one-line summary from a Task Hub-style dict for the PR title."""

    title = str(task.get("title") or "").strip()
    if title:
        return title.splitlines()[0][:100]
    desc = str(task.get("description") or "").strip()
    if desc:
        return desc.splitlines()[0][:100]
    return f"task {task.get('task_id', '?')}"


def execute_autonomous_mission(
    *,
    task: Mapping[str, Any],
    patch_fn: PatchFn,
    bot_name: str,
    base_branch: str = "origin/main",
    repo_root: Path | None = None,
    workspace_root: Path | None = None,
    pr_creator: PRCreator | None = None,
    test_runner: TestRunner | None = None,
    commit_message: str | None = None,
    pr_body: str | None = None,
) -> MissionResult:
    """Run the 8-step tier-2 contract for a single Task Hub item.

    Failure paths always end with the worktree torn down (or attempted)
    so a stuck mission cannot block the next run. The result object
    captures every observable artifact (branch, files touched, PR URL,
    syntax-check failures, test output) so the caller can record a
    durable trace back into Task Hub.
    """

    task_id = str(task.get("task_id") or task.get("id") or "unknown-task")
    result = MissionResult(
        succeeded=False, bot=bot_name, task_id=task_id, base_branch=base_branch,
    )

    repo = (repo_root or detect_repo_root()).resolve()
    runner: TestRunner = test_runner or default_test_runner
    creator: PRCreator = pr_creator or default_pr_creator

    # --- Step 2: provision worktree ---------------------------------------
    provisioned: WorktreeProvisionResult = provision_worktree(
        bot_name=bot_name,
        task_id=task_id,
        base_branch=base_branch,
        repo_root=repo,
        workspace_root=workspace_root,
    )
    result.worktree_path = provisioned.worktree_path
    result.branch = provisioned.branch
    if not provisioned.succeeded:
        result.failure_reason = (
            f"worktree-provision-failed: {provisioned.error or 'unknown'}"
        )
        result.teardown_ok = teardown_worktree(provisioned.worktree_path, repo_root=repo)
        return result

    try:
        # --- Step 3: apply patches ---------------------------------------
        try:
            changed = patch_fn(provisioned.worktree_path) or []
        except Exception as exc:  # patch failure is a normal mission outcome
            result.failure_reason = f"patch-fn-raised: {type(exc).__name__}: {exc}"
            return _abort_mission(result, repo)

        result.changed_files = [Path(p) for p in changed]
        if not result.changed_files:
            # Use the diff fallback so a forgetful patch_fn still works.
            result.changed_files = list_changed_py_files(provisioned.worktree_path)

        if not result.changed_files:
            result.failure_reason = "no-op: patch_fn produced no changes"
            return _abort_mission(result, repo)

        # --- Step 4: syntax check ---------------------------------------
        syntax: SyntaxCheckResult = syntax_check_changed_py(
            provisioned.worktree_path, result.changed_files,
        )
        result.syntax_failures = list(syntax.failures)
        if not syntax.ok:
            result.failure_reason = "syntax-check-failed"
            revert_changed_files(provisioned.worktree_path, result.changed_files)
            result.reverted = True
            return _abort_mission(result, repo)

        # --- Artifact tripwire (matches pr-validate.yml) -----------------
        try:
            assert_no_artifacts(provisioned.worktree_path)
        except ArtifactLeakError as exc:
            result.failure_reason = f"artifact-leak: {exc}"
            revert_changed_files(provisioned.worktree_path, result.changed_files)
            result.reverted = True
            return _abort_mission(result, repo)

        # --- Step 5: run tests ------------------------------------------
        ok, output = runner(provisioned.worktree_path, result.changed_files)
        result.test_output = output
        if not ok:
            result.failure_reason = "tests-failed"
            revert_changed_files(provisioned.worktree_path, result.changed_files)
            result.reverted = True
            return _abort_mission(result, repo)

        # --- Step 6: commit + push --------------------------------------
        msg = commit_message or f"{bot_name}: {_extract_task_summary(task)}"
        committed, commit_out = _commit_changes(provisioned.worktree_path, msg)
        if not committed:
            result.failure_reason = f"commit-failed: {commit_out}"
            revert_changed_files(provisioned.worktree_path, result.changed_files)
            result.reverted = True
            return _abort_mission(result, repo)

        # --- Step 7: open PR --------------------------------------------
        title = msg.splitlines()[0]
        body = pr_body or _default_pr_body(task=task, result=result)
        pr_url = creator(
            worktree_path=provisioned.worktree_path,
            branch=provisioned.branch,
            base_branch=base_branch,
            title=title,
            body=body,
        )
        if not pr_url:
            result.failure_reason = "pr-creation-failed"
            return _abort_mission(result, repo)

        result.pushed = True
        result.pr_url = pr_url
        result.succeeded = True
        return _finalize_mission(result, repo)

    except WorktreeError as exc:
        result.failure_reason = f"worktree-error: {exc}"
        return _abort_mission(result, repo)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("execute_autonomous_mission: unexpected failure")
        result.failure_reason = f"unexpected: {type(exc).__name__}: {exc}"
        return _abort_mission(result, repo)


def _default_pr_body(*, task: Mapping[str, Any], result: MissionResult) -> str:
    """Render a PR body summarising the mission."""

    task_id = task.get("task_id") or task.get("id") or "?"
    title = str(task.get("title") or "(no title)").strip().splitlines()[0]
    bullets = []
    for path in result.changed_files[:30]:
        try:
            rel = path.relative_to(result.worktree_path)  # type: ignore[arg-type]
        except (ValueError, TypeError):
            rel = path
        bullets.append(f"- `{rel}`")
    if len(result.changed_files) > 30:
        bullets.append(f"- _(+{len(result.changed_files) - 30} more)_")

    return (
        f"Automated tier-2 mission ({result.bot}).\n\n"
        f"**Task Hub item:** `{task_id}` — {title}\n\n"
        f"**Changed files:**\n" + ("\n".join(bullets) if bullets else "(none)")
        + "\n\nGenerated by `vp.autonomous_mission_executor`. "
        "Contract: docs/deployment/ai_coder_instructions.md."
    )


def _abort_mission(result: MissionResult, repo: Path) -> MissionResult:
    """Tear down the worktree (best effort) and return the failure result."""

    if result.worktree_path is not None:
        result.teardown_ok = teardown_worktree(result.worktree_path, repo_root=repo)
    return result


def _finalize_mission(result: MissionResult, repo: Path) -> MissionResult:
    """Tear down the worktree on a successful run.

    The worktree is no longer needed after the PR is open: the branch
    lives on the remote and reviewers work in their own checkouts.
    """

    if result.worktree_path is not None:
        result.teardown_ok = teardown_worktree(result.worktree_path, repo_root=repo)
    return result
