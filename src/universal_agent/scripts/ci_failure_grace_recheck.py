"""Grace-period re-check + autonomous action for a single CI failure.

Invoked as a **durable one-shot cron job** (`run_at` + `delete_after_run`)
~15 minutes after a CI workflow run fails. The timer is scheduled by the
``POST /api/v1/hooks/ci-failure`` gateway endpoint, which is in turn called by
``.github/workflows/ci-failure-issue.yml`` on the ``workflow_run: completed``
(``conclusion == failure``) event.

Why a grace period: the session that owns a failure almost always fix-forwards
it within minutes (this exact class of failure bit us repeatedly). So by the
time this fires we **RE-VERIFY everything via the `gh` CLI** and only act on a
genuinely *orphaned, still-red* failure. This is the coordination mechanism —
no collision with whoever already owns the failure.

Decision flow (all reads via the VPS `gh` CLI, which is authed with repo scope):

  1. ci-failure issue still OPEN?            no  -> stand down (already resolved)
  2. PR merged / closed?                     yes -> stand down (owner shipped)
  3. Newer push on the branch (head moved)?  yes -> stand down (owner pushed a fix)
  4. The failing run still red?              no  -> stand down (already green)
  5. Issue already labelled
     ci-autofix-dispatched / needs-operator? yes -> stand down (another claim won)
  6. Classify the still-orphaned failure:
       code/test/lint/doc + PR present  -> dispatch a Cody fix mission
                                           (+ claim with label ci-autofix-dispatched)
       deploy / infra / secret / oauth /
       no-PR (push to main)             -> Telegram-escalate to operator
                                           (+ label needs-operator)

The Cody mission brief itself begins with a re-verify-or-noop step (TOCTOU guard):
even inside the grace window, Cody re-checks the failure is still red before
touching anything, then fixes, pushes to the PR branch, and self-labels the PR
``ci-autofix`` so ``pr-auto-merge.yml`` lands it once green.

This module is intentionally **synchronous** — no asyncio at import or call time
— to avoid the "no running event loop" failure mode seen in lightweight cron
scripts. GitHub access is subprocess `gh`; Telegram is ``telegram_send_sync``;
Cody dispatch is ``queue_proactive_task`` against the activity DB.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
import json
import os
import subprocess
from typing import Any, Callable, Optional

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DEFAULT_REPO = "Kjdragan/universal_agent"

# Workflows whose failures are plausibly fixable by an autonomous code change on
# the PR branch (py_compile / ruff / pytest / doc-accuracy). Everything else
# (Deploy, PR Auto-Merge, PR Rebase Watchdog, ...) escalates to a human because
# the remedy is infra/secret/merge-state, not a code edit.
CODY_ELIGIBLE_WORKFLOWS = frozenset(
    {
        "PR Validate",
        "Documentation Audit",
        "Nightly Documentation Health",
    }
)

LABEL_DISPATCHED = "ci-autofix-dispatched"
LABEL_NEEDS_OPERATOR = "needs-operator"
LABEL_AUTOFIX = "ci-autofix"
LABEL_CI_FAILURE = "ci-failure"

# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #


@dataclass
class FailureContext:
    """The failure as reported by the GHA workflow_run event (via the hook)."""

    workflow: str
    head_sha: str
    run_id: str
    conclusion: str = "failure"
    head_branch: str = ""
    pr_number: Optional[int] = None
    run_url: str = ""
    issue_number: Optional[int] = None
    failing_check: str = ""
    repo: str = DEFAULT_REPO

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "FailureContext":
        def _int(v: Any) -> Optional[int]:
            try:
                return int(v) if v not in (None, "", "null") else None
            except (TypeError, ValueError):
                return None

        return cls(
            workflow=str(payload.get("workflow") or payload.get("workflow_name") or "").strip(),
            head_sha=str(payload.get("head_sha") or "").strip(),
            run_id=str(payload.get("run_id") or "").strip(),
            conclusion=str(payload.get("conclusion") or "failure").strip(),
            head_branch=str(payload.get("head_branch") or "").strip(),
            pr_number=_int(payload.get("pr_number")),
            run_url=str(payload.get("run_url") or "").strip(),
            issue_number=_int(payload.get("issue_number")),
            failing_check=str(payload.get("failing_check") or "").strip(),
            repo=str(payload.get("repo") or DEFAULT_REPO).strip() or DEFAULT_REPO,
        )


@dataclass
class RepoState:
    """Current GitHub state, gathered at grace-fire time via `gh`."""

    issue_open: bool = False
    issue_number: Optional[int] = None
    issue_labels: list[str] = field(default_factory=list)
    pr_state: Optional[str] = None  # "OPEN" | "MERGED" | "CLOSED" | None
    pr_merged: bool = False
    pr_head_sha: Optional[str] = None
    run_conclusion: Optional[str] = None  # latest conclusion of the failing run


# --------------------------------------------------------------------------- #
# Pure decision logic (unit-tested without touching the network)
# --------------------------------------------------------------------------- #


def classify_failure(workflow: str, pr_number: Optional[int]) -> str:
    """Return ``"cody"`` (autofixable code change on a PR branch) or
    ``"escalate"`` (needs a human: infra/secret/oauth/deploy or no PR)."""
    if pr_number and workflow in CODY_ELIGIBLE_WORKFLOWS:
        return "cody"
    return "escalate"


def decide_action(ctx: FailureContext, state: RepoState) -> tuple[str, str]:
    """Pure re-verify gate. Returns ``(action, reason)`` where action is one of
    ``stand_down`` | ``dispatch_cody`` | ``escalate``.

    Every short-circuit below is a coordination win: it means the owner (or a
    prior dispatcher) already handled the failure during the grace window.
    """
    if not state.issue_open:
        return "stand_down", "issue_closed_or_missing"
    if state.pr_merged:
        return "stand_down", "pr_merged"
    if state.pr_state == "CLOSED":
        return "stand_down", "pr_closed"
    if state.pr_head_sha and ctx.head_sha and state.pr_head_sha != ctx.head_sha:
        return "stand_down", "newer_push"
    if state.run_conclusion is not None and state.run_conclusion != "failure":
        return "stand_down", "run_no_longer_failing"
    if LABEL_DISPATCHED in state.issue_labels:
        return "stand_down", "already_dispatched"
    if LABEL_NEEDS_OPERATOR in state.issue_labels:
        return "stand_down", "already_escalated"

    # Genuinely orphaned and still red -> act.
    if classify_failure(ctx.workflow, ctx.pr_number) == "cody":
        return "dispatch_cody", "orphaned_autofixable"
    return "escalate", "orphaned_needs_operator"


# --------------------------------------------------------------------------- #
# GitHub access (subprocess `gh`) — injectable for tests
# --------------------------------------------------------------------------- #

# A gh runner takes the argv AFTER the `gh` token and returns (rc, stdout, stderr).
GhRunner = Callable[[list[str]], "tuple[int, str, str]"]


def _real_gh(args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", "gh CLI not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"gh timed out after {timeout}s: {' '.join(args)}"


def _find_issue_number(ctx: FailureContext, gh: GhRunner) -> Optional[int]:
    """Locate the open ci-failure issue for this run. The issue title from
    ci-failure-issue.yml ends with ``run_id=<RUN_ID>``."""
    if ctx.issue_number:
        return ctx.issue_number
    rc, out, _ = gh(
        [
            "issue",
            "list",
            "--repo",
            ctx.repo,
            "--label",
            LABEL_CI_FAILURE,
            "--state",
            "open",
            "--search",
            f'in:title "run_id={ctx.run_id}"',
            "--json",
            "number",
            "--limit",
            "10",
        ]
    )
    if rc != 0:
        return None
    try:
        rows = json.loads(out or "[]")
    except json.JSONDecodeError:
        return None
    if rows:
        return int(rows[0]["number"])
    return None


def gather_repo_state(ctx: FailureContext, gh: GhRunner = _real_gh) -> RepoState:
    state = RepoState()

    issue_number = _find_issue_number(ctx, gh)
    if issue_number is None:
        # No open issue -> resolved/closed. Nothing else to gather.
        return state
    state.issue_number = issue_number

    rc, out, _ = gh(
        [
            "issue",
            "view",
            str(issue_number),
            "--repo",
            ctx.repo,
            "--json",
            "state,labels",
        ]
    )
    if rc == 0:
        try:
            data = json.loads(out or "{}")
            state.issue_open = str(data.get("state", "")).upper() == "OPEN"
            state.issue_labels = [
                str(lbl.get("name", "")) for lbl in (data.get("labels") or [])
            ]
        except json.JSONDecodeError:
            pass

    if ctx.pr_number:
        rc, out, _ = gh(
            [
                "pr",
                "view",
                str(ctx.pr_number),
                "--repo",
                ctx.repo,
                "--json",
                "state,mergedAt,headRefOid",
            ]
        )
        if rc == 0:
            try:
                data = json.loads(out or "{}")
                state.pr_state = str(data.get("state") or "").upper() or None
                state.pr_merged = bool(data.get("mergedAt"))
                state.pr_head_sha = str(data.get("headRefOid") or "") or None
            except json.JSONDecodeError:
                pass

    if ctx.run_id:
        rc, out, _ = gh(
            [
                "run",
                "view",
                ctx.run_id,
                "--repo",
                ctx.repo,
                "--json",
                "conclusion",
            ]
        )
        if rc == 0:
            try:
                data = json.loads(out or "{}")
                # An empty/None conclusion means in-progress; treat as still-failing
                # only if the original event said failure (it did).
                conclusion = data.get("conclusion")
                state.run_conclusion = str(conclusion).strip() if conclusion else None
            except json.JSONDecodeError:
                pass

    return state


def _add_label(ctx: FailureContext, issue_number: int, label: str, gh: GhRunner) -> bool:
    rc, _, _ = gh(
        ["issue", "edit", str(issue_number), "--repo", ctx.repo, "--add-label", label]
    )
    return rc == 0


# --------------------------------------------------------------------------- #
# Cody fix-mission brief
# --------------------------------------------------------------------------- #


def build_cody_brief(ctx: FailureContext) -> str:
    """The mission brief. MUST begin with a re-verify-or-noop step so Cody never
    redoes work that was already addressed inside the grace window."""
    failing = ctx.failing_check or ctx.workflow
    lines = [
        f"Autonomously fix the CI failure on PR #{ctx.pr_number} and push the fix to its branch.",
        "",
        "## STEP 0 — RE-VERIFY OR NO-OP (do this FIRST, before any edit)",
        f"- Run: `gh pr view {ctx.pr_number} --repo {ctx.repo} --json state,mergedAt,headRefOid`.",
        f"  If the PR is merged or closed, OR headRefOid is no longer `{ctx.head_sha}`",
        "  (someone pushed after the failure), STOP immediately: write COMPLETION.md noting"
        " 'no-op: failure already resolved/superseded' and exit without changing anything.",
        f"- Run `gh run view {ctx.run_id} --repo {ctx.repo} --json conclusion`. If it is no",
        "  longer `failure` (re-run went green), STOP and no-op as above.",
        "",
        "## STEP 1 — Reproduce",
        f"- Check out the PR branch: `gh pr checkout {ctx.pr_number} --repo {ctx.repo}`.",
        f"- The failing workflow was **{ctx.workflow}** (check: {failing}).",
        "  Reproduce locally with the matching gate command:",
        "    - PR Validate -> `uv run ruff check .` ; `uv run python -m py_compile <changed.py>` ;"
        " `uv run pytest tests/unit`",
        "    - Documentation Audit / Nightly Documentation Health ->"
        " `uv run python scripts/doc_accuracy_sweep.py` (or the command the run log shows).",
        f"- If you cannot reproduce, read the failed run log: `gh run view {ctx.run_id}"
        f" --repo {ctx.repo} --log-failed`.",
        "",
        "## STEP 2 — Fix (minimal, surgical)",
        "- Implement the smallest change that turns the gate green. Do NOT refactor unrelated"
        " code, redesign subsystems, or change product behavior.",
        "- For behavior-touching changes use red-green TDD: add/adjust a focused regression test,"
        " confirm the fix makes it pass.",
        "",
        "## STEP 3 — Verify locally",
        "- Re-run the exact failing gate command and confirm it now passes.",
        "",
        "## STEP 4 — Push to the SAME PR branch (never main, never a new PR)",
        f"- Commit and `git push` to the existing PR #{ctx.pr_number} branch"
        f" (`{ctx.head_branch}`).",
        f"- Then run: `gh pr edit {ctx.pr_number} --repo {ctx.repo} --add-label {LABEL_AUTOFIX}`.",
        f"  The `{LABEL_AUTOFIX}` label tells pr-auto-merge.yml to land the PR once CI is green.",
        "",
        "## Hard constraints",
        "- Do NOT merge, do NOT push to main, do NOT deploy, do NOT touch secrets/config.",
        "- Do NOT reintroduce `actions/checkout` to any GitHub Actions job.",
        "- If the fix is non-trivial or risky, STOP and instead comment on the PR explaining what"
        " a human needs to do, then exit (do not push a speculative change).",
        "",
        "## Work product",
        f"- A commit pushed to PR #{ctx.pr_number}'s branch plus the `{LABEL_AUTOFIX}` label, OR a"
        " clear no-op/explanation in COMPLETION.md if STEP 0 short-circuited.",
    ]
    return "\n".join(lines)


def _resolve_codebase_root() -> str:
    explicit = (os.getenv("UA_PROACTIVE_CODIE_CODEBASE_ROOT") or "").strip()
    if explicit:
        return explicit
    try:
        from universal_agent.codebase_policy import (
            DEFAULT_APPROVED_CODEBASE_ROOT,
            approved_codebase_roots_from_env,
        )

        approved = approved_codebase_roots_from_env()
        if approved:
            return approved[0]
        return DEFAULT_APPROVED_CODEBASE_ROOT
    except Exception:
        return "/opt/universal_agent"


def _cody_task_id(ctx: FailureContext) -> str:
    sha = (ctx.head_sha or "")[:12]
    return f"ci-autofix:pr{ctx.pr_number}:{sha}"


# --------------------------------------------------------------------------- #
# Action executors (impure)
# --------------------------------------------------------------------------- #


def dispatch_cody_fix(
    ctx: FailureContext,
    state: RepoState,
    *,
    gh: GhRunner = _real_gh,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    """Claim the issue (label ``ci-autofix-dispatched``) then queue a Cody fix
    mission. Claim-before-enqueue is the dedup guard against a double timer."""
    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
    from universal_agent.services.proactive_codie import CODIE_TARGET_AGENT
    from universal_agent.services.proactive_task_builder import queue_proactive_task

    claimed = False
    if state.issue_number:
        claimed = _add_label(ctx, state.issue_number, LABEL_DISPATCHED, gh)

    root = _resolve_codebase_root()
    task_id = _cody_task_id(ctx)
    brief = build_cody_brief(ctx)

    conn = connect_runtime_db(db_path or get_activity_db_path())
    try:
        item = queue_proactive_task(
            conn,
            task_id=task_id,
            source_kind="proactive_codie",
            source_ref=f"pr-{ctx.pr_number}",
            title=f"CI auto-fix: {ctx.workflow} on PR #{ctx.pr_number}",
            description=brief,
            priority=3,
            labels=["agent-ready", "proactive-codie", "ci-autofix", "code"],
            metadata={
                "source": "ci_autofix_hook",
                "pr_number": ctx.pr_number,
                "pr_branch": ctx.head_branch,
                "head_sha": ctx.head_sha,
                "run_id": ctx.run_id,
                "run_url": ctx.run_url,
                "workflow": ctx.workflow,
                "issue_number": state.issue_number,
                "review_gate": "ci_autofix_label_then_auto_merge",
                "complexity_target": "low_to_medium",
                "target_agent": CODIE_TARGET_AGENT,
                "codebase_root": root,
                "external_effect_policy": {
                    "allow_pr": True,  # push to the existing PR branch + edit PR labels
                    "allow_merge": False,
                    "allow_main_push": False,
                    "allow_deploy": False,
                    "allow_payments": False,
                    "allow_public_communications": False,
                    "allow_destructive_ops": False,
                    "allow_secret_mutation": False,
                    "allow_major_dep_bump": False,
                    "allow_control_plane_edits": False,
                },
                "workflow_manifest": {
                    "workflow_kind": "code_change",
                    "delivery_mode": "interactive_chat",
                    "final_channel": "chat",
                    "canonical_executor": "simone_first",
                    "target_agent": CODIE_TARGET_AGENT,
                    "codebase_root": root,
                    "repo_mutation_allowed": True,
                },
            },
        )
        conn.commit()
    finally:
        conn.close()

    nudge = "skipped"
    try:
        from universal_agent.services.idle_dispatch_loop import nudge_dispatch

        nudge_dispatch(reason=f"ci_autofix_dispatched:{task_id}")
        nudge = "requested"
    except Exception as exc:  # pragma: no cover - best effort
        nudge = f"failed:{type(exc).__name__}"

    return {
        "action": "dispatch_cody",
        "claimed_label": claimed,
        "task_id": task_id,
        "task": item,
        "dispatch_nudge": nudge,
    }


def escalate_to_operator(
    ctx: FailureContext, state: RepoState, *, gh: GhRunner = _real_gh
) -> dict[str, Any]:
    """Label the issue ``needs-operator`` and Telegram-ping the operator."""
    from universal_agent.services.telegram_send import telegram_send_sync

    labelled = False
    if state.issue_number:
        labelled = _add_label(ctx, state.issue_number, LABEL_NEEDS_OPERATOR, gh)

    chat_id = (os.getenv("UA_OPERATOR_TELEGRAM_CHAT_ID") or "").strip()
    bot_token = (os.getenv("UA_OPERATOR_TELEGRAM_BOT_TOKEN") or "").strip() or None

    issue_ref = f"#{state.issue_number}" if state.issue_number else "(no issue)"
    msg = (
        f"🚨 CI failure needs you — {ctx.workflow}\n"
        f"branch={ctx.head_branch} @ {ctx.head_sha[:7]}\n"
        f"PR={('#' + str(ctx.pr_number)) if ctx.pr_number else 'none (push to main)'}"
        f" | issue {issue_ref}\n"
        f"Not auto-fixable (infra/secret/oauth/deploy or no PR). Still red after grace.\n"
        f"{ctx.run_url}"
    )

    sent, detail = (False, "no_chat_id")
    if chat_id:
        try:
            sent, detail = telegram_send_sync(chat_id=chat_id, text=msg, bot_token=bot_token)
        except Exception as exc:  # pragma: no cover - best effort
            sent, detail = False, f"{type(exc).__name__}:{exc}"

    return {
        "action": "escalate",
        "labelled_needs_operator": labelled,
        "telegram_sent": sent,
        "telegram_detail": detail,
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def run_grace_recheck(
    ctx: FailureContext,
    *,
    gh: GhRunner = _real_gh,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    """Gather state, decide, and execute. Returns a JSON-serializable result."""
    state = gather_repo_state(ctx, gh)
    action, reason = decide_action(ctx, state)

    result: dict[str, Any] = {
        "ok": True,
        "action": action,
        "reason": reason,
        "workflow": ctx.workflow,
        "pr_number": ctx.pr_number,
        "run_id": ctx.run_id,
        "issue_number": state.issue_number,
    }

    if action == "stand_down":
        return result
    if action == "dispatch_cody":
        result.update(dispatch_cody_fix(ctx, state, gh=gh, db_path=db_path))
        return result
    if action == "escalate":
        result.update(escalate_to_operator(ctx, state, gh=gh))
        return result
    return result


def _decode_payload(payload_b64: str) -> dict[str, Any]:
    raw = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("payload is not a JSON object")
    return data


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--payload-b64",
        default="",
        help="urlsafe-base64 JSON failure context (preferred; cron passes this).",
    )
    parser.add_argument("--payload-json", default="", help="Raw JSON failure context.")
    parser.add_argument("--db-path", default="", help="Override activity DB path.")
    args = parser.parse_args(argv)

    # Cron subprocesses inherit the gateway's Infisical-loaded env, but bootstrap
    # defensively in case this is run standalone.
    try:
        from universal_agent.infisical_loader import initialize_runtime_secrets

        initialize_runtime_secrets()
    except Exception:
        pass

    try:
        if args.payload_b64:
            payload = _decode_payload(args.payload_b64)
        elif args.payload_json:
            payload = json.loads(args.payload_json)
        else:
            raise ValueError("one of --payload-b64 / --payload-json is required")
        ctx = FailureContext.from_payload(payload)
        if not ctx.workflow or not ctx.run_id:
            raise ValueError("payload missing required fields: workflow, run_id")
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}:{exc}"}, indent=2))
        return 1

    try:
        result = run_grace_recheck(ctx, db_path=args.db_path or None)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}:{exc}"}, indent=2))
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
