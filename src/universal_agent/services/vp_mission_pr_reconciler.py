"""VP mission ↔ PR auto-reconciliation.

Closes the gap between "a VP coding mission shipped a PR" and "the mission
task closed in Task Hub." Today, when a CODIE mission successfully opens a
PR and that PR merges to main, nothing automatically updates the task_hub
row — so the task lingers in `blocked` / `needs_review` until the retry
budget exhausts. Incident: `vp-mission-95e1a15a3b0ec8dbf58db662` (PR #142,
merged 2026-05-02) sat blocked for 10 days.

Architecture
============
Two halves:

  1. **Writer** (`record_mission_pr`) — called immediately after a PR is
     created (by `worker_loop._post_mission_push_pr_merge` for the legacy
     doc-maintenance flow, and by `claude_code_client.run_mission` for the
     modern CODIE flow after regex-extracting the PR URL from the
     mission's final_text). Stamps `task_hub_items.metadata.dispatch.pr`
     with `{number, url, head_branch, recorded_at}`.

  2. **Reader / reconciler** (`reconcile_vp_missions_with_prs`) — runs
     every 15 minutes during active hours via system cron. For every
     vp_mission task in a non-terminal state, if `metadata.dispatch.pr` is
     present, queries the GitHub API for the PR's current merge status.
     If merged, updates the task metadata with `pr.merged_at` +
     `pr.merge_commit_sha` and flips the task to `completed`.

Failure modes
=============
- GitHub rate-limit / 5xx → log and skip THIS mission; never raise.
- PR 404 (deleted) → mark `pr.deleted = true` in metadata, leave task
  open. Operator decides via the Mark Complete card button.
- Missing GitHub token → reconciler short-circuits with a log; no DB
  writes. Production has the token; dev may not.

Why polling instead of a webhook
================================
A webhook needs auth-signature validation, a public endpoint exposure,
and operational handling for replay/missed events. A 15-minute poll is
operationally cheap (one HTTP call per active mission, maybe 0-5 per
tick) and recovers gracefully on its own from any transient failure.
The latency cost (≤15 min from merge to mission-closed) is invisible
operationally — the operator already isn't watching individual task
states in real time.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import re
import sqlite3
from typing import Any, Iterable, Optional
import urllib.error
import urllib.request

from universal_agent import task_hub

logger = logging.getLogger(__name__)


# ── Configuration ────────────────────────────────────────────────────────


# Repo to query — same env var the worker_loop's PR creation path uses.
def _gh_repo() -> str:
    return os.getenv("UA_GH_REPO", "Kjdragan/universal_agent")


def _gh_token() -> str:
    """Resolve the GitHub token. Reads `GITHUB_TOKEN` directly — this is
    populated at service-startup by `initialize_runtime_secrets()` from
    Infisical. Never reads from `.env` directly per CLAUDE.md secrets
    policy.
    """
    return (os.getenv("GITHUB_TOKEN") or "").strip()


# Bound the scan window so we don't poll forever for dead missions.
_SCAN_WINDOW_DAYS = int(os.getenv("UA_VP_PR_RECONCILE_WINDOW_DAYS", "30") or 30)


# Statuses we consider "still trying to complete." Anything else (already
# completed/parked/cancelled) is terminal and out of scope.
_NON_TERMINAL_STATUSES = (
    task_hub.TASK_STATUS_OPEN,
    task_hub.TASK_STATUS_IN_PROGRESS,
    task_hub.TASK_STATUS_BLOCKED,
    task_hub.TASK_STATUS_REVIEW,
    task_hub.TASK_STATUS_DELEGATED,
    task_hub.TASK_STATUS_PENDING_REVIEW,
)


# Regex for PR URL detection in CODIE's final response text.
_PR_URL_PATTERN = re.compile(
    r"https?://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/pull/(?P<number>\d+)"
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Writer ──────────────────────────────────────────────────────────────


def extract_pr_from_text(text: str) -> Optional[dict[str, Any]]:
    """Pull the first GitHub PR URL out of a free-form text block.

    Returns `{number, url, owner, repo}` on match, or None. Used by
    `claude_code_client.run_mission` to scan CODIE's final response for a
    PR URL it just opened.

    Multiple matches are not flagged — we take the first occurrence. In
    practice the URL appears once, in the agent's wrap-up paragraph.
    """
    if not text:
        return None
    m = _PR_URL_PATTERN.search(text)
    if not m:
        return None
    return {
        "url": m.group(0),
        "owner": m.group("owner"),
        "repo": m.group("repo"),
        "number": int(m.group("number")),
    }


def record_mission_pr(
    conn: sqlite3.Connection,
    *,
    mission_id: str,
    pr_number: int,
    pr_url: Optional[str] = None,
    head_branch: Optional[str] = None,
) -> None:
    """Stamp the mission's task_hub row with PR linkage info.

    Deep-merges `dispatch.pr = {...}` into existing metadata. Safe to
    call multiple times for the same mission (idempotent).

    Caller is responsible for committing the connection — we leave the
    transaction open so the caller can bundle this with other writes.

    `mission_id` is the task_hub task_id for VP missions (the
    `task_id = mission_id` convention is enforced by `worker_loop.py:464`
    via `task_hub.upsert_item({"task_id": mission_id, ...})`).
    """
    if not mission_id:
        logger.debug("record_mission_pr: empty mission_id, skipping")
        return
    try:
        pr_number_int = int(pr_number)
    except (TypeError, ValueError):
        logger.warning("record_mission_pr: invalid pr_number=%r for %s", pr_number, mission_id)
        return

    existing = task_hub.get_item(conn, mission_id)
    if not existing:
        # No task_hub row to attach the PR to. Common for very recent
        # missions where the upsert hasn't fired yet, or for missions
        # never tracked in task_hub at all. Log at debug — not actionable.
        logger.debug("record_mission_pr: no task_hub row for %s, skipping", mission_id)
        return

    existing_metadata = dict(existing.get("metadata") or {})
    existing_dispatch = dict(existing_metadata.get("dispatch") or {})
    existing_pr = dict(existing_dispatch.get("pr") or {})

    # Deep-merge: only write fields the caller actually provided. Keeps
    # later writes (e.g. the reconciler stamping `merged_at`) from
    # accidentally clearing fields the original recording set.
    pr_block: dict[str, Any] = {**existing_pr, "number": pr_number_int}
    if pr_url:
        pr_block["url"] = pr_url
    if head_branch:
        pr_block["head_branch"] = head_branch
    pr_block.setdefault("recorded_at", _utc_now_iso())

    # Reassemble the merged metadata. `upsert_item` does a SHALLOW merge
    # of top-level metadata keys, so we have to pass the fully merged
    # dispatch dict to avoid clobbering sibling keys.
    new_dispatch = {**existing_dispatch, "pr": pr_block}
    new_metadata = {**existing_metadata, "dispatch": new_dispatch}

    task_hub.upsert_item(conn, {"task_id": mission_id, "metadata": new_metadata})
    logger.info("record_mission_pr: linked mission %s to PR #%d", mission_id, pr_number_int)


# ── Reader / reconciler ────────────────────────────────────────────────


def _list_candidates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Find vp_mission tasks that are non-terminal and have a recorded
    PR. Bounded by `_SCAN_WINDOW_DAYS` to keep the scan O(small) even as
    the task_hub grows.
    """
    placeholders = ",".join("?" * len(_NON_TERMINAL_STATUSES))
    rows = conn.execute(
        f"""
        SELECT task_id, status, metadata_json
        FROM task_hub_items
        WHERE source_kind = 'vp_mission'
          AND status IN ({placeholders})
          AND created_at > datetime('now', '-{_SCAN_WINDOW_DAYS} days')
        """,
        _NON_TERMINAL_STATUSES,
    ).fetchall()

    candidates: list[dict[str, Any]] = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}") if row["metadata_json"] else {}
        except json.JSONDecodeError:
            metadata = {}
        pr = (metadata.get("dispatch") or {}).get("pr") or {}
        if not pr.get("number"):
            continue
        if pr.get("merged_at"):
            # Already reconciled; the reconciler closes the task below
            # if it isn't already terminal, but no need to re-query GH.
            pass
        candidates.append(
            {
                "task_id": row["task_id"],
                "status": row["status"],
                "metadata": metadata,
                "pr": pr,
            }
        )
    return candidates


def _query_github_pr(pr_number: int) -> Optional[dict[str, Any]]:
    """Fetch the current state of a PR from GitHub.

    Returns the parsed JSON body on success, or None on any error
    (rate-limit, 5xx, network). 404 returns `{"_status": 404}` so the
    caller can distinguish "PR deleted" from "transient failure."
    """
    token = _gh_token()
    if not token:
        logger.warning("_query_github_pr: GITHUB_TOKEN unset, cannot query #%d", pr_number)
        return None

    url = f"https://api.github.com/repos/{_gh_repo()}/pulls/{pr_number}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "universal-agent-vp-mission-reconciler/1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"_status": 404}
        # 403 typically = rate-limit; 5xx = transient. Log + skip.
        logger.warning("_query_github_pr: HTTP %d for PR #%d", exc.code, pr_number)
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning("_query_github_pr: PR #%d query failed: %s", pr_number, exc)
        return None


def _close_mission_as_merged(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    metadata: dict[str, Any],
    pr_response: dict[str, Any],
) -> None:
    """Stamp the PR merge info into metadata and flip the task to
    completed. Bypasses `perform_task_action`'s email-delivery
    verification gate — the PR merge IS the completion evidence here,
    not an email send.

    We use `upsert_item` directly for this reason: it does NOT enforce
    the verification gate, and the same pathway already covers happy-path
    completion via `worker_loop.py:464`.
    """
    merged_at = pr_response.get("merged_at")
    merge_commit_sha = pr_response.get("merge_commit_sha")
    head_branch = (pr_response.get("head") or {}).get("ref")

    dispatch = dict(metadata.get("dispatch") or {})
    pr_meta = dict(dispatch.get("pr") or {})
    pr_meta.update(
        {
            "merged_at": merged_at,
            "merge_commit_sha": merge_commit_sha,
            "reconciled_at": _utc_now_iso(),
        }
    )
    if head_branch and not pr_meta.get("head_branch"):
        pr_meta["head_branch"] = head_branch
    dispatch["pr"] = pr_meta
    # Last-disposition trail so operators can see WHY this auto-closed.
    dispatch["last_disposition"] = "completed"
    dispatch["last_disposition_reason"] = f"pr_reconciler:pr_{pr_meta['number']}_merged"
    new_metadata = {**metadata, "dispatch": dispatch}

    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "status": task_hub.TASK_STATUS_COMPLETED,
            "seizure_state": "completed",
            "metadata": new_metadata,
        },
    )
    logger.info(
        "vp_mission_pr_reconciler: closed %s as completed (PR #%d merged at %s)",
        task_id,
        pr_meta["number"],
        merged_at,
    )


def _mark_pr_deleted(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    metadata: dict[str, Any],
) -> None:
    """A previously-recorded PR is no longer fetchable (404). Mark the
    record so we don't keep querying it, but leave the task open — the
    operator decides via the Mark Complete card button whether the work
    really shipped or was abandoned.
    """
    dispatch = dict(metadata.get("dispatch") or {})
    pr_meta = dict(dispatch.get("pr") or {})
    pr_meta.update({"deleted": True, "deleted_observed_at": _utc_now_iso()})
    dispatch["pr"] = pr_meta
    new_metadata = {**metadata, "dispatch": dispatch}
    task_hub.upsert_item(conn, {"task_id": task_id, "metadata": new_metadata})
    logger.info(
        "vp_mission_pr_reconciler: PR #%d for %s returned 404; flagged as deleted",
        pr_meta.get("number"),
        task_id,
    )


def reconcile_vp_missions_with_prs(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Main entrypoint — run a single reconciliation pass.

    Returns a counter dict for observability: keys `scanned`, `closed`,
    `still_open`, `pr_deleted`, `errors`.

    `dry_run=True` logs candidates and decisions without writing
    anything. Used by the CLI for one-shot operator audits.
    """
    candidates = _list_candidates(conn)
    counters = {
        "scanned": len(candidates),
        "closed": 0,
        "still_open": 0,
        "pr_deleted": 0,
        "errors": 0,
        "skipped_no_token": 0,
    }
    if not candidates:
        logger.info("vp_mission_pr_reconciler: no candidates in last %d days", _SCAN_WINDOW_DAYS)
        return counters

    for cand in candidates:
        task_id = cand["task_id"]
        pr = cand["pr"]
        try:
            pr_response = _query_github_pr(int(pr["number"]))
        except Exception:
            counters["errors"] += 1
            logger.exception("reconciler: unexpected error querying PR #%s", pr.get("number"))
            continue

        if pr_response is None:
            # Transient (rate-limit / 5xx / no token). Skip; try again next tick.
            counters["skipped_no_token" if not _gh_token() else "errors"] += 1
            continue

        if pr_response.get("_status") == 404:
            if not dry_run:
                _mark_pr_deleted(conn, task_id=task_id, metadata=cand["metadata"])
                conn.commit()
            counters["pr_deleted"] += 1
            continue

        merged_at = pr_response.get("merged_at")
        if not merged_at:
            counters["still_open"] += 1
            logger.debug(
                "reconciler: PR #%s for %s not merged yet (state=%s)",
                pr["number"], task_id, pr_response.get("state"),
            )
            continue

        # Merged — close the mission.
        if dry_run:
            logger.info(
                "reconciler[dry-run]: would close %s (PR #%s merged at %s)",
                task_id, pr["number"], merged_at,
            )
        else:
            _close_mission_as_merged(
                conn,
                task_id=task_id,
                metadata=cand["metadata"],
                pr_response=pr_response,
            )
            conn.commit()
        counters["closed"] += 1

    return counters
