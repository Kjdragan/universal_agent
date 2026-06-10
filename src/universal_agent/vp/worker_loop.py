from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from typing import Any, Optional
import uuid

from universal_agent.durable.state import (
    acquire_vp_session_lease,
    append_vp_event,
    claim_next_vp_mission,
    finalize_vp_mission,
    get_vp_mission,
    heartbeat_vp_mission_claim,
    heartbeat_vp_session_lease,
    release_vp_session_lease,
    update_vp_session_status,
    upsert_vp_session,
)
from universal_agent.feature_flags import (
    vp_lease_ttl_seconds,
    vp_max_concurrent_missions,
    vp_poll_interval_seconds,
    vp_worker_max_uptime_seconds,
)
from universal_agent.services.dag_governor import DagConcurrencyGovernor
from universal_agent.vp.clients.base import MissionOutcome, VpClient
from universal_agent.vp.clients.claude_code_client import ClaudeCodeClient
from universal_agent.vp.clients.claude_generalist_client import ClaudeGeneralistClient
from universal_agent.vp.profiles import get_vp_profile

logger = logging.getLogger(__name__)

# Path constraint key aliases — must stay in sync with
# claude_code_client._PATH_CONSTRAINT_KEYS and dispatcher._extract_target_paths.
_PATH_CONSTRAINT_KEYS = (
    "target_path", "path", "repo_path", "workspace_dir", "project_path",
    "output_path", "working_directory", "dest_path", "destination",
)


_AUTH_FAILURE_MARKERS_LOWER = (
    "invalid authentication credentials",
    "failed to authenticate",
    "invalid x-api-key",
    "authentication_error",
    # ZAI/glm + proxy lane (Cody runs glm in-process via the ZAI proxy): these
    # auth/proxy failures must classify as auth_failure (operator re-auth), not
    # fall through to vp_self_reported / "unspecified". Phrasings kept specific to
    # avoid false-positives from substring matching (no bare "407"/"unauthorized").
    "proxy authentication required",
    "invalid api key",
    "invalid_api_key",
    "api key is invalid",
)
_GOAL_CAP_MARKERS_LOWER = (
    "stop after",
    "goal evaluator",
    "max turns",
    "turn limit",
)
_WORKSPACE_GUARD_MARKERS_LOWER = (
    "workspaceguarderror",
    "workspace guard",
    "outside approved",
)


def _resolve_source_task_id_from_payload(mission_payload: Any) -> str:
    """Return the originating Task Hub task_id from a VP mission payload.

    The linkage between an operator-dispatched ``qa-*`` task and the
    spawned VP mission can live in three places on the mission_payload,
    depending on which dispatch path created it:

      1. ``payload.task_id`` (top-level) — PR #490's ``_build_payload``
         lifts ``request.metadata.linked_task_id`` here. Highest
         priority because it's the explicit contract field.
      2. ``payload.metadata.linked_task_id`` — PR #491's
         ``_vp_dispatch_mission_impl`` auto-discovery writes here when
         the caller's args didn't include ``task_id`` but a current
         seized assignment exists.
      3. ``payload.metadata.task_id`` — legacy callers that stuffed
         it directly under metadata.

    Pre-PR-#493 the worker_loop only checked the third path — and
    nobody writes that key — so the source-task closure
    silently no-op'd for every operator-dispatched Cody mission, leaving
    `qa-*` rows stuck in ``status=delegated`` indefinitely (the long-
    standing "delegated zombie" pattern).

    Returns ``""`` when no linkage is found — callers skip the closure
    silently in that case (it's expected for ad-hoc tool-call dispatches
    without a Task Hub parent).
    """
    if not isinstance(mission_payload, dict):
        return ""
    top_level = str(mission_payload.get("task_id") or "").strip()
    if top_level:
        return top_level
    metadata = mission_payload.get("metadata")
    if isinstance(metadata, dict):
        linked = str(metadata.get("linked_task_id") or "").strip()
        if linked:
            return linked
        legacy = str(metadata.get("task_id") or "").strip()
        if legacy:
            return legacy
    return ""


def _classify_outcome_failure_mode(outcome) -> Optional[str]:
    """Best-effort failure-mode classifier for the rescue hook.

    Returns a stable string from the set documented in
    ``durable/state.finalize_vp_mission`` or None when classification
    can't pick a confident category (caller substitutes a default).
    """
    if outcome is None:
        return None
    status = str(getattr(outcome, "status", "") or "").lower()
    msg = str(getattr(outcome, "message", "") or "")
    payload = getattr(outcome, "payload", {}) or {}
    final_text = str(payload.get("final_text") or "")
    haystack = (msg + " " + final_text).lower()

    if not haystack:
        return None
    # Check protocol-violation markers FIRST so they take precedence over
    # the generic vp_self_reported fallback. 2026-05-26 fix: previously
    # the smoke-test failure (where worker_loop demoted a completed
    # mission to failed for missing COMPLETION.md) was mislabeled as
    # vp_self_reported because the classifier returned the generic
    # `status == failed` fallback before checking for protocol markers.
    # That was confusing because the failure card said the VP self-
    # reported when in fact our own attestation guard fired.
    if "missing_completion_attestation" in haystack:
        return "missing_completion_attestation"
    if any(marker in haystack for marker in _AUTH_FAILURE_MARKERS_LOWER):
        return "auth_failure"
    if any(marker in haystack for marker in _WORKSPACE_GUARD_MARKERS_LOWER):
        return "workspace_guard"
    if "sigterm" in haystack or "sigill" in haystack or "killed by signal" in haystack:
        return "subprocess_crash"
    if "timeout" in haystack or "timed out" in haystack:
        return "timeout"
    if any(marker in haystack for marker in _GOAL_CAP_MARKERS_LOWER):
        return "goal_cap_hit"
    if status == "cancelled":
        return "operator_cancel"
    if status == "failed":
        return "vp_self_reported"
    return None


def _extract_first_target_path(constraints: dict[str, Any]) -> str:
    """Extract the first non-empty path from recognized constraint keys."""
    for key in _PATH_CONSTRAINT_KEYS:
        value = str(constraints.get(key) or "").strip()
        if value:
            return value
    return ""


def _stamp_demo_manifest_build_session(
    *,
    workspace_dir: str,
    mission_id: str,
    vp_id: str,
    cody_session_id: str = "",
) -> bool:
    """P5 (15_demo_tutorial_pipeline_adr.md "Cross-cutting requirement"):
    stamp the building VP mission onto the demo's manifest.json so dashboard
    surfaces can deep-link the 3-panel session viewer.

    ``build_session_id`` is the ``vp-mission-<id>`` — the id the viewer's
    VP-mission special case (web-ui/app/page.tsx) and
    ``viewer/resolver.py::mission_log_rel`` both key on; the demo workspace
    itself is the ``workspace`` hint (mirrors the Kanban completed-card
    enrichment in ``gateway_server.py::dashboard_todolist_completed``).

    Additive merge via ``resolve_demo_artifacts_dir`` (handles the
    ``vp-mission-<id>/`` subdir layout). Returns True when a manifest was
    stamped. Best-effort: never raises, never creates a manifest.
    """
    try:
        from universal_agent.services.cody_implementation import (
            resolve_demo_artifacts_dir,
        )

        ws = Path(str(workspace_dir or "")).expanduser()
        if not ws.is_dir():
            return False
        manifest_path = resolve_demo_artifacts_dir(ws) / "manifest.json"
        if not manifest_path.is_file():
            return False
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False
        payload["build_mission_id"] = str(mission_id or "")
        payload["build_session_id"] = str(mission_id or "")
        payload["build_vp_id"] = str(vp_id or "")
        if cody_session_id:
            payload["build_cli_session_id"] = str(cody_session_id)
        manifest_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return True
    except Exception:
        logger.warning(
            "Demo manifest build-session stamp failed for %s (mission %s)",
            workspace_dir,
            mission_id,
            exc_info=True,
        )
        return False

# ── GitHub repo for doc-maintenance PRs ──────────────────────────────────────
_GH_REPO = os.getenv("UA_GH_REPO", "Kjdragan/universal_agent")

# Default PR base for the post-mission doc-maintenance hook. ``develop`` was
# retired 2026-05-10 in favor of a main-only branching model — see
# docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md.
# Operator-overridable for staging environments via UA_GH_PR_BASE_BRANCH.
_PR_BASE_BRANCH = os.getenv("UA_GH_PR_BASE_BRANCH", "main")


def _post_mission_push_pr_merge(*, workspace_root: str, mission_id: str) -> None:
    """Push the agent's doc branch, create a PR, and squash-merge it.

    Runs OUTSIDE the Claude CLI sandbox (in the VP worker process) so it has
    full network access.  The agent inside the sandbox creates a branch and
    commits but cannot push (sandbox blocks outbound HTTPS auth).
    """
    import re
    import urllib.error
    import urllib.request

    cwd = workspace_root

    # 1. Determine current branch (agent should have checked out a docs/* branch)
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, cwd=cwd,
    )
    branch = result.stdout.strip()
    if not branch or not branch.startswith("docs/"):
        logger.info("Post-mission hook: not on a docs/ branch (%s), skipping push", branch)
        return

    # 2. Check if there are commits on this branch that aren't on the
    # PR base. ``develop`` retired 2026-05-10; default base is ``main``
    # (see module-level ``_PR_BASE_BRANCH``).
    result = subprocess.run(
        ["git", "log", f"{_PR_BASE_BRANCH}..HEAD", "--oneline"],
        capture_output=True, text=True, cwd=cwd,
    )
    commits = result.stdout.strip()
    if not commits:
        logger.info(
            "Post-mission hook: no new commits on %s vs %s, skipping",
            branch, _PR_BASE_BRANCH,
        )
        return

    logger.info("Post-mission hook [%s]: pushing branch %s (%d commits)",
                mission_id, branch, len(commits.splitlines()))

    # 3. Extract GitHub PAT from remote URL
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True, text=True, cwd=cwd,
    )
    remote_url = result.stdout.strip()
    token_match = re.search(r"x-access-token:([^@]+)@", remote_url)
    if not token_match:
        # Try GITHUB_TOKEN env var as fallback
        gh_token = os.getenv("GITHUB_TOKEN", "").strip()
        if not gh_token:
            logger.warning("Post-mission hook: no GitHub token found in remote URL or env, cannot push")
            return
    else:
        gh_token = token_match.group(1)

    # 4. Push the branch
    result = subprocess.run(
        ["git", "push", "-u", "origin", branch],
        capture_output=True, text=True, cwd=cwd, timeout=60,
    )
    if result.returncode != 0:
        logger.warning("Post-mission hook: git push failed: %s", result.stderr.strip())
        return
    logger.info("Post-mission hook: pushed %s successfully", branch)

    # 5. Create PR via GitHub API
    commit_title = commits.splitlines()[0].split(" ", 1)[-1] if commits else f"docs: {branch}"
    pr_body = json.dumps({
        "title": commit_title,
        "head": branch,
        "base": _PR_BASE_BRANCH,
        "body": f"Automated doc maintenance (mission {mission_id}).",
    }).encode()

    headers = {
        "Authorization": f"token {gh_token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }

    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{_GH_REPO}/pulls",
            data=pr_body, headers=headers, method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            pr_data = json.loads(resp.read())
            pr_number = pr_data.get("number")
            pr_html_url = pr_data.get("html_url")
            logger.info("Post-mission hook: created PR #%s — %s", pr_number, commit_title)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        logger.warning("Post-mission hook: PR creation failed (HTTP %d): %s", exc.code, body)
        return
    except Exception as exc:
        logger.warning("Post-mission hook: PR creation failed: %s", exc)
        return

    if not pr_number:
        logger.warning("Post-mission hook: PR created but no number returned")
        return

    # Record the PR linkage on the mission's task_hub row so the
    # `vp_mission_pr_reconciler` cron can later close the mission when
    # the PR merges. Best-effort — a failure here is non-fatal because
    # the reconciler also fallback-handles missions whose final_text
    # mentioned a PR URL (see `claude_code_client.run_mission`).
    try:
        from universal_agent import task_hub as _th
        from universal_agent.durable.db import (
            connect_runtime_db as _connect,
            get_activity_db_path as _activity_path,
        )
        from universal_agent.services.vp_mission_pr_reconciler import record_mission_pr
        _th_conn = _connect(_activity_path())
        try:
            record_mission_pr(
                _th_conn,
                mission_id=mission_id,
                pr_number=pr_number,
                pr_url=pr_html_url,
                head_branch=branch,
            )
            _th_conn.commit()
        finally:
            _th_conn.close()
    except Exception as exc:
        logger.warning("Post-mission hook: record_mission_pr failed: %s", exc)

    # 6. Squash-merge the PR
    import time as _time
    _time.sleep(3)  # brief pause for GitHub to process the PR

    merge_body = json.dumps({
        "merge_method": "squash",
        "commit_title": commit_title,
    }).encode()

    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{_GH_REPO}/pulls/{pr_number}/merge",
            data=merge_body, headers=headers, method="PUT",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            merge_data = json.loads(resp.read())
            logger.info("Post-mission hook: PR #%s merged — %s", pr_number, merge_data.get("message", "ok"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        logger.warning("Post-mission hook: merge failed (HTTP %d): %s", exc.code, body)
    except Exception as exc:
        logger.warning("Post-mission hook: merge failed: %s", exc)

    # 7. Switch back to the PR base so the next mission starts clean
    subprocess.run(["git", "checkout", _PR_BASE_BRANCH], capture_output=True, cwd=cwd)
    subprocess.run(["git", "pull", "origin", _PR_BASE_BRANCH], capture_output=True, cwd=cwd)
    logger.info(
        "Post-mission hook: switched back to %s, ready for next mission",
        _PR_BASE_BRANCH,
    )


# How often the worker re-checks the deployed git SHA (subprocess spawn) while
# polling. Cheap throttle so an idle worker isn't spawning `git` every tick.
_CODE_VERSION_CHECK_INTERVAL_SECONDS = 30


def _deployed_code_version() -> str:
    """Return the deployed code version (git HEAD SHA) for the running tree.

    A VP worker uses this to self-restart when idle after a deploy advances the
    code — picking up the new code WITHOUT interrupting an in-flight mission.
    This replaces the old behaviour where the deploy restarted the worker
    directly (which killed whatever mission was running). Returns ``""`` when
    the version can't be determined (e.g. no git); callers treat ``""`` as
    "version unknown" and fall back to the uptime backstop.
    """
    try:
        repo_root = Path(__file__).resolve().parents[3]
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


class VpWorkerLoop:
    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        vp_id: str,
        worker_id: Optional[str] = None,
        workspace_base: Optional[Path | str] = None,
        poll_interval_seconds: Optional[int] = None,
        lease_ttl_seconds: Optional[int] = None,
        max_concurrent_missions: Optional[int] = None,
    ) -> None:
        profile = get_vp_profile(vp_id, workspace_base=workspace_base)
        if profile is None:
            raise ValueError(f"VP profile not configured/enabled: {vp_id}")

        self.conn = conn
        self.profile = profile
        self.vp_id = vp_id
        self.worker_id = worker_id or f"{vp_id}.worker.{uuid.uuid4().hex[:8]}"
        self.poll_interval_seconds = int(poll_interval_seconds or vp_poll_interval_seconds(default=5))
        self.lease_ttl_seconds = int(lease_ttl_seconds or vp_lease_ttl_seconds(default=120))
        self.max_concurrent_missions = int(
            max_concurrent_missions or vp_max_concurrent_missions(default=1)
        )
        self._stopped = asyncio.Event()
        self._client: Optional[VpClient] = None
        self._default_client = self._create_client()
        # Code-currency self-restart state. Deploys no longer restart VP
        # workers directly (that killed in-flight missions); instead the worker
        # exits cleanly BETWEEN missions when it sees the deployed SHA change,
        # and systemd (Restart=always) relaunches it on the new code.
        self._start_code_version = _deployed_code_version()
        self._started_monotonic = time.monotonic()
        self._last_code_version_check_monotonic = 0.0
        self._max_uptime_seconds = vp_worker_max_uptime_seconds()

    def stop(self) -> None:
        self._stopped.set()

    def _should_restart_for_code_currency(self) -> bool:
        """True when the worker should exit (BETWEEN missions) so systemd
        relaunches it on freshly-deployed code.

        Primary signal: the deployed git SHA changed since startup (throttled
        so an idle worker isn't spawning ``git`` every tick). Backstop: uptime
        exceeded the configured max (covers the case where the SHA can't be
        read). NEVER called mid-mission — only at the top of ``_tick``, between
        missions — so a deploy can't interrupt in-flight work. A gateway
        restart during a mission is harmless: this worker keeps heartbeating
        the claim lease from its own process, so the gateway's startup
        reconciler sees a live claim and leaves the mission alone.
        """
        now = time.monotonic()
        if self._start_code_version and (
            now - self._last_code_version_check_monotonic
            >= _CODE_VERSION_CHECK_INTERVAL_SECONDS
        ):
            self._last_code_version_check_monotonic = now
            current = _deployed_code_version()
            if current and current != self._start_code_version:
                logger.info(
                    "VP worker code version changed (%s → %s) — restarting "
                    "between missions to pick up new code (vp_id=%s)",
                    self._start_code_version[:8], current[:8], self.vp_id,
                )
                return True
        if (
            self._max_uptime_seconds > 0
            and (now - self._started_monotonic) > self._max_uptime_seconds
        ):
            logger.info(
                "VP worker uptime exceeded %ds — restarting between missions "
                "for code currency (vp_id=%s)",
                self._max_uptime_seconds, self.vp_id,
            )
            return True
        return False

    async def run_forever(self) -> None:
        logger.info("VP worker starting: vp_id=%s worker_id=%s", self.vp_id, self.worker_id)
        self.profile.workspace_root.mkdir(parents=True, exist_ok=True)
        self._upsert_session(status="idle")
        lease_ok = acquire_vp_session_lease(
            self.conn,
            vp_id=self.vp_id,
            lease_owner=self.worker_id,
            lease_ttl_seconds=self.lease_ttl_seconds,
        )
        if not lease_ok:
            logger.warning("VP worker lease acquisition failed: vp_id=%s worker_id=%s", self.vp_id, self.worker_id)
            update_vp_session_status(
                self.conn,
                vp_id=self.vp_id,
                status="degraded",
                last_error="worker lease acquisition failed",
            )

        while not self._stopped.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error_str = str(exc).lower()
                is_rate_limit = "429" in error_str or "too many requests" in error_str or "overloaded" in error_str
                if is_rate_limit:
                    from universal_agent.services.capacity_governor import (
                        CapacityGovernor,
                    )
                    asyncio.ensure_future(
                        CapacityGovernor.get_instance().report_rate_limit(
                            f"vp_{self.vp_id}", error=exc
                        )
                    )

                logger.exception("VP worker tick failed: vp_id=%s err=%s", self.vp_id, exc)
                update_vp_session_status(
                    self.conn,
                    vp_id=self.vp_id,
                    status="degraded",
                    last_error=str(exc),
                )
                await asyncio.sleep(self.poll_interval_seconds)

        release_vp_session_lease(self.conn, vp_id=self.vp_id, lease_owner=self.worker_id)
        self._upsert_session(status="idle")
        logger.info("VP worker stopped: vp_id=%s worker_id=%s", self.vp_id, self.worker_id)

    async def _tick(self) -> None:
        # Code-currency self-restart (between missions only). If a deploy
        # advanced the code while we were idle or running the previous mission,
        # exit cleanly so systemd (Restart=always) relaunches us on the new
        # code. This is why deploys no longer restart VP workers directly —
        # doing so killed in-flight missions (the worker process died mid-run,
        # its claim lease lapsed, and the reconciler reaped the orphan as
        # "failed"). See deployment/systemd/universal-agent-vp-worker@.service
        # and scripts/deploy/remote_deploy.sh.
        if self._should_restart_for_code_currency():
            self.stop()
            return
        heartbeat_vp_session_lease(
            self.conn,
            vp_id=self.vp_id,
            lease_owner=self.worker_id,
            lease_ttl_seconds=self.lease_ttl_seconds,
        )
        # Fix: reset degraded → idle after successful heartbeat so the
        # dashboard correctly reports the worker as available.
        try:
            from universal_agent.durable.state import get_vp_session
            session = get_vp_session(self.conn, self.vp_id)
            if session and str(session["status"] or "") == "degraded":
                self._upsert_session(status="idle")
                logger.info("VP worker recovered from degraded: vp_id=%s", self.vp_id)
        except Exception:
            pass  # Non-critical; don't let recovery check block the tick
        claimed = claim_next_vp_mission(
            self.conn,
            vp_id=self.vp_id,
            worker_id=self.worker_id,
            lease_ttl_seconds=self.lease_ttl_seconds,
        )
        if claimed is None:
            await asyncio.sleep(self.poll_interval_seconds)
            return

        mission_id = str(claimed["mission_id"])
        started_context = _mission_source_context(claimed)
        self._upsert_session(status="active")
        append_vp_event(
            self.conn,
            event_id=f"vp-event-{uuid.uuid4().hex}",
            mission_id=mission_id,
            vp_id=self.vp_id,
            event_type="vp.mission.started",
            payload={**started_context, "worker_id": self.worker_id},
        )

        mission = get_vp_mission(self.conn, mission_id)
        if mission is None:
            return
        # Convert sqlite3.Row → dict so .get() works in soul seeding, briefing, etc.
        mission = dict(mission)
        source_context = _mission_source_context(mission)
        if int(mission["cancel_requested"] or 0) == 1:
            finalize_vp_mission(self.conn, mission_id, "cancelled")
            append_vp_event(
                self.conn,
                event_id=f"vp-event-{uuid.uuid4().hex}",
                mission_id=mission_id,
                vp_id=self.vp_id,
                event_type="vp.mission.cancelled",
                payload={
                    **source_context,
                    "worker_id": self.worker_id,
                    "reason": "cancel_requested_before_start",
                },
            )
            self._upsert_session(status="idle")
            return

        await self._execute_mission_logic(mission, mission_id, source_context)

    async def _execute_mission_logic(self, mission: dict[str, Any], mission_id: str, source_context: dict[str, Any]) -> None:
        """Internal logic to execute a mission after it has been claimed."""
        heartbeat_vp_mission_claim(
            self.conn,
            mission_id=mission_id,
            vp_id=self.vp_id,
            worker_id=self.worker_id,
            lease_ttl_seconds=self.lease_ttl_seconds,
        )

        # ── VP identity & mission briefing ────────────────────────────
        self._seed_vp_soul(mission)
        self._write_mission_briefing(mission)
        
        workspace_path = await self._provision_workspace(mission)

        logger.info(
            "VP mission starting: vp_id=%s soul=%s mission_id=%s mission_type=%s",
            self.vp_id,
            self.profile.soul_file,
            mission_id,
            str(mission.get("mission_type") or ""),
        )

        client = self._select_client_for_mission(mission)

        heartbeat_stop = asyncio.Event()

        async def _heartbeat_mission_claim() -> None:
            interval = max(5, self.lease_ttl_seconds // 3)
            while not heartbeat_stop.is_set():
                try:
                    await asyncio.wait_for(heartbeat_stop.wait(), timeout=interval)
                    break
                except asyncio.TimeoutError:
                    pass
                try:
                    heartbeat_vp_mission_claim(
                        self.conn,
                        mission_id=mission_id,
                        vp_id=self.vp_id,
                        worker_id=self.worker_id,
                        lease_ttl_seconds=self.lease_ttl_seconds,
                    )
                    heartbeat_vp_session_lease(
                        self.conn,
                        vp_id=self.vp_id,
                        lease_owner=self.worker_id,
                        lease_ttl_seconds=self.lease_ttl_seconds,
                    )
                except Exception as hb_exc:
                    logger.warning(
                        "VP mission heartbeat failed: mission_id=%s err=%s",
                        mission_id, hb_exc,
                    )

        heartbeat_task = asyncio.create_task(_heartbeat_mission_claim())

        # Mark Task Hub item as in_progress when VP worker claims execution.
        #
        # ALSO persist a live dispatch handle onto the mirror row. The daemon
        # claim path (claim_next_dispatch_tasks / claim_task_for_agent) is the
        # only writer of dispatch handles, and todo_dispatch_service explicitly
        # excludes vp_mission source kinds — so without this write the mirror
        # row carries STATUS ONLY and no liveness anchor. The startup
        # reconciler then false-orphans the (alive, heartbeating) mission.
        # Authoritative liveness still comes from the vp_missions lease (see
        # task_hub.reconcile_task_lifecycle); these handles are the durable
        # breadcrumb that lets the reconciler recognise the row as a VP mission.
        try:
            from universal_agent import task_hub
            from universal_agent.durable.db import (
                connect_runtime_db,
                get_activity_db_path,
            )
            th_conn = connect_runtime_db(get_activity_db_path())
            task_hub.ensure_schema(th_conn)
            # Merge the dispatch sub-dict ourselves so we don't clobber other
            # dispatch keys (upsert_item merges metadata only one level deep).
            _existing_item = task_hub.get_item(th_conn, mission_id) or {}
            _existing_dispatch = dict(
                (_existing_item.get("metadata") or {}).get("dispatch") or {}
            )
            _existing_dispatch.update({
                "vp_mission_id": mission_id,
                "active_agent_id": self.vp_id,
                # Any stable non-empty id — liveness comes from the vp_missions
                # lease, not from running_session_ids.
                "active_provider_session_id": self.worker_id,
                "active_workspace_dir": str(workspace_path),
                "last_assignment_started_at": datetime.now(timezone.utc).isoformat(),
            })
            task_hub.upsert_item(th_conn, {
                "task_id": mission_id,
                "status": task_hub.TASK_STATUS_IN_PROGRESS,
                "metadata": {"dispatch": _existing_dispatch},
            })
            th_conn.close()
        except Exception:
            pass  # Task Hub status sync is best-effort

        try:
            # Phase 2: ZAI Concurrency Management (Global DAG Execution Limiter)
            async with DagConcurrencyGovernor.get_instance().acquire_slot():
                outcome = await client.run_mission(
                    mission=dict(mission),
                    workspace_root=workspace_path,
                )
        finally:
            heartbeat_stop.set()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        # Completion attestation guard (PRD § 5.5) — when THIS MISSION is
        # /goal-eligible (use_goal_loop=True in payload metadata OR an
        # eligible source_kind), the VP must have written COMPLETION.md
        # per the self-brief-and-attest skill before we accept "completed".
        # Missing the file demotes to failed with a stable failure_mode
        # that Simone can recognize as a protocol violation (not a work
        # failure). The demoted outcome then flows through the failure_mode
        # / transcript_tail derivation below, surfacing to Simone via the
        # vp_mission_failure lane just like any other failure.
        #
        # 2026-05-26 fix: previously this gated on the GLOBAL flag
        # `vp_goal_enabled()` instead of the per-mission eligibility,
        # which caused successful Cody missions WITHOUT use_goal_loop to
        # be spuriously demoted for missing a file they were never told
        # to write. The smoke test (vp-mission-24b75861...) hit exactly
        # this — Cody completed the task correctly, but the global flag
        # was on, COMPLETION.md was absent (skill wasn't even invokable
        # — see issue #4 in PR description), and the guard demoted it.
        # Now we check `is_goal_eligible_mission(mission)` so the guard
        # only fires for missions that actually opted into /goal flow.
        if outcome.status == "completed":
            try:
                from universal_agent.services.self_briefing import (
                    check_completion_attestation,
                    is_goal_eligible_mission,
                )
                if is_goal_eligible_mission(mission):
                    # PR #492 dual-path — also check Cody's actual cwd
                    # (captured by PR #490 in MissionOutcome.payload
                    # when CLI capture fires) before declaring missing.
                    # Cody may have written COMPLETION.md to his cwd
                    # rather than the canonical mission_workspace when
                    # the BRIEF scoped his work to a /tmp dir.
                    _fallback_dirs: list[Path] = []
                    _payload_cwd = str((outcome.payload or {}).get("cli_workspace_dir") or "").strip()
                    if _payload_cwd:
                        _fallback_dirs.append(Path(_payload_cwd))
                    ok, reason = check_completion_attestation(
                        workspace_path,
                        fallback_dirs=_fallback_dirs or None,
                    )
                    if not ok:
                        logger.warning(
                            "VP mission %s missing COMPLETION.md: %s — demoting to failed",
                            mission_id, reason,
                        )
                        outcome = MissionOutcome(
                            status="failed",
                            result_ref=outcome.result_ref,
                            message=f"missing_completion_attestation: {reason}",
                            payload={
                                **(outcome.payload or {}),
                                "demoted_from_completed": True,
                                "completion_attestation_reason": reason,
                            },
                        )
            except Exception as exc:
                logger.warning(
                    "VP mission %s: completion-attestation check failed (%s); "
                    "treating original outcome as authoritative",
                    mission_id, exc,
                )

        # Derive failure_mode + transcript_tail for the rescue hook in
        # finalize_vp_mission. transcript_tail comes from outcome.payload
        # (Claude CLI stream-json client populates this on failure paths).
        # If COMPLETION-attestation demotion happened above, the classifier
        # will see "missing_completion_attestation" in the message and
        # return that string — preserving the protocol-violation signal
        # all the way to Simone's rescue surface.
        _payload = outcome.payload or {}
        _transcript_tail = (
            str(_payload.get("final_text") or "")
            or str(outcome.message or "")
            or None
        )
        _failure_mode = _classify_outcome_failure_mode(outcome)
        if not _failure_mode and "missing_completion_attestation" in str(outcome.message or ""):
            _failure_mode = "missing_completion_attestation"

        if outcome.status == "cancelled":
            finalize_vp_mission(
                self.conn, mission_id, "cancelled",
                result_ref=outcome.result_ref,
                failure_mode=_failure_mode or "operator_cancel",
                transcript_tail=_transcript_tail,
            )
            event_type = "vp.mission.cancelled"
        elif outcome.status == "failed":
            finalize_vp_mission(
                self.conn, mission_id, "failed",
                result_ref=outcome.result_ref,
                failure_mode=_failure_mode or "vp_self_reported",
                transcript_tail=_transcript_tail,
            )
            event_type = "vp.mission.failed"
        else:
            finalize_vp_mission(self.conn, mission_id, "completed", result_ref=outcome.result_ref)
            event_type = "vp.mission.completed"

        # Teardown worktree AFTER finalization so result_ref is persisted.
        # Skip teardown if the agent's result_ref points at the worktree itself
        # (meaning the agent wrote output there and we'd lose artifacts).
        _result_dir = ""
        if outcome.result_ref:
            _result_dir = str(outcome.result_ref).removeprefix("workspace://").strip()
        worktree_str = str(workspace_path).rstrip("/")
        result_str = _result_dir.rstrip("/")
        if not result_str or result_str != worktree_str:
            await self._teardown_workspace(workspace_path)
        else:
            logger.info(
                "Skipping worktree teardown — agent output is at worktree path: %s",
                workspace_path,
            )

        # Sync terminal status to Task Hub for Kanban board visibility.
        #
        # Two task hub rows may need closing on VP terminal:
        #
        #   (1) The mirror row keyed by ``task_id == mission_id`` (created
        #       by upsert_item at mission start). Always closed.
        #
        #   (2) The ORIGINAL source task that triggered this dispatch
        #       (the row Simone called ``task_redirect_to`` on, now in
        #       ``status=delegated`` waiting to be closed). Identified
        #       via ``payload_json.metadata.task_id``. Closing this is
        #       what prevents the "delegated zombie" pattern documented
        #       in the 2026-05-26 cleanup (188 rows accumulated this
        #       way over ~3 weeks). Skips silently when the mission
        #       carries no source task_id (e.g., direct vp_dispatch
        #       from a non-task-hub caller).
        try:
            from universal_agent import task_hub
            from universal_agent.durable.db import (
                connect_runtime_db,
                get_activity_db_path,
            )
            _th_status_map = {
                "vp.mission.completed": task_hub.TASK_STATUS_COMPLETED,
                "vp.mission.failed": task_hub.TASK_STATUS_OPEN,
                "vp.mission.cancelled": task_hub.TASK_STATUS_CANCELLED,
            }
            th_status = _th_status_map.get(event_type, task_hub.TASK_STATUS_COMPLETED)
            th_conn = connect_runtime_db(get_activity_db_path())
            task_hub.ensure_schema(th_conn)

            # Machine-legible terminal disposition so downstream readers
            # (Simone's digest) can distinguish a shipped-PR completion from a
            # legitimate no-op ("inspected, nothing worth a PR") instead of
            # inferring a stalled backlog from the bare status. Only meaningful
            # for completed missions; failed/cancelled keep their own status.
            _terminal_meta: dict[str, Any] = {
                "vp_terminal_status": event_type.replace("vp.mission.", ""),
                "result_ref": outcome.result_ref or "",
            }
            if event_type == "vp.mission.completed":
                _pr_url = self._detect_pr_url(outcome)
                _terminal_meta["terminal_disposition"] = (
                    "completed_with_pr" if _pr_url else "completed_without_pr"
                )
                if _pr_url:
                    _terminal_meta["pr_url"] = _pr_url

            # (1) Close the mirror row.
            task_hub.upsert_item(th_conn, {
                "task_id": mission_id,
                "status": th_status,
                "metadata": dict(_terminal_meta),
            })

            # (2) Close the original source task, if linked.
            try:
                _mission_payload = json.loads(mission.get("payload_json") or "{}")
            except Exception:
                _mission_payload = {}
            _source_task_id = _resolve_source_task_id_from_payload(_mission_payload)
            if _source_task_id and _source_task_id != mission_id:
                try:
                    _src_item = task_hub.get_item(th_conn, _source_task_id)
                    _src_kind = str((_src_item or {}).get("source_kind") or "")
                    _src_meta = dict((_src_item or {}).get("metadata") or {})

                    # P6 — deterministic tutorial_build finalize: synthesize
                    # manifest.json (so the P5 stamp below stops no-op'ing),
                    # run existence-only mechanical checks, and register the
                    # demo on the dashboard demo surface (UA_DEMOS_ROOT
                    # symlink the _claude_code_intel_demos walker picks up).
                    _tutorial_finalize: dict[str, Any] = {}
                    if event_type == "vp.mission.completed" and _src_kind == "tutorial_build":
                        from universal_agent.services.tutorial_demo_finalize import (
                            finalize_tutorial_build_demo,
                        )
                        _fin_result_ws = ""
                        if str(outcome.result_ref or "").startswith("workspace://"):
                            _fin_result_ws = str(outcome.result_ref).removeprefix("workspace://").strip()
                        _tutorial_finalize = finalize_tutorial_build_demo(
                            task_id=_source_task_id,
                            task_meta=_src_meta,
                            mission=dict(mission),
                            mission_id=mission_id,
                            workspace_candidates=[
                                str(_src_meta.get("workspace_dir") or "").strip(),
                                str((outcome.payload or {}).get("cli_workspace_dir") or "").strip(),
                                _fin_result_ws,
                            ],
                        )

                    # P5 (15_demo_tutorial_pipeline_adr.md "Cross-cutting
                    # requirement"): stamp the building mission's identity onto
                    # the demo manifest BEFORE terminal routing so
                    # finalize_direct_demo / the evaluator / the dashboard demo
                    # walker all see the session link. Deterministic code
                    # stamping (never trusts the LLM to copy ids); execution-
                    # mode-agnostic (CLI and ZAI/SDK clients both land here).
                    if event_type == "vp.mission.completed" and _src_kind in (
                        "cody_demo_task",
                        "tutorial_build",
                    ):
                        _dispatch_meta = (
                            _src_meta.get("dispatch")
                            if isinstance(_src_meta.get("dispatch"), dict)
                            else {}
                        )
                        _stamp_result_ws = ""
                        if str(outcome.result_ref or "").startswith("workspace://"):
                            _stamp_result_ws = str(outcome.result_ref).removeprefix("workspace://").strip()
                        for _demo_ws in (
                            str(_src_meta.get("workspace_dir") or "").strip(),
                            str((outcome.payload or {}).get("cli_workspace_dir") or "").strip(),
                            _stamp_result_ws,
                        ):
                            if _demo_ws and _stamp_demo_manifest_build_session(
                                workspace_dir=_demo_ws,
                                mission_id=mission_id,
                                vp_id=self.vp_id,
                                cody_session_id=str(
                                    (_dispatch_meta or {}).get("cody_session_id") or ""
                                ).strip(),
                            ):
                                break

                    if _src_kind == "cody_demo_task" and event_type == "vp.mission.completed":
                        # Consolidated, SINGLE owner of cody_demo_task terminal routing.
                        # Previously this blind "→ completed" raced the gateway VP-event
                        # bridge (which routed demos to pending_review / finalize) and
                        # always won synchronously, pre-empting the review + endpoint
                        # gates. Doing the routing HERE — in-worker, synchronous,
                        # restart-safe — removes that race and the duplicate bridge path.
                        if _src_meta.get("review_required") is False:
                            # Direct/ungated demo → enforce the mechanical endpoint check
                            # and complete on pass; on a miss leave it in pending_review
                            # (visible, not a delegated zombie).
                            from universal_agent.services.cody_evaluation import (
                                finalize_direct_demo,
                            )
                            _fin = finalize_direct_demo(th_conn, task_id=_source_task_id)
                            if _fin.get("status") != "completed":
                                task_hub.upsert_item(th_conn, {
                                    "task_id": _source_task_id,
                                    "status": task_hub.TASK_STATUS_PENDING_REVIEW,
                                    "metadata": {**_terminal_meta, "linked_mission_id": mission_id},
                                })
                            logger.info(
                                "Direct demo terminal-routed: source_task_id=%s mission=%s → %s",
                                _source_task_id, mission_id, _fin.get("status"),
                            )
                        else:
                            # Curated demo → pending_review for Simone's evaluator (do
                            # NOT auto-complete; the review gate owns the outcome).
                            task_hub.upsert_item(th_conn, {
                                "task_id": _source_task_id,
                                "status": task_hub.TASK_STATUS_PENDING_REVIEW,
                                "metadata": {**_terminal_meta, "linked_mission_id": mission_id},
                            })
                            logger.info(
                                "Curated demo → pending_review: source_task_id=%s mission=%s",
                                _source_task_id, mission_id,
                            )
                    else:
                        # Default: close the source task. Zombie prevention for every
                        # non-demo delegation, plus demo failed/cancelled missions.
                        task_hub.upsert_item(th_conn, {
                            "task_id": _source_task_id,
                            "status": th_status,
                            "metadata": {
                                **_terminal_meta,
                                "linked_mission_id": mission_id,
                                **({"demo_finalize": _tutorial_finalize} if _tutorial_finalize else {}),
                            },
                        })
                        logger.info(
                            "Source task closed after VP terminal: source_task_id=%s mission_id=%s status=%s",
                            _source_task_id, mission_id, th_status,
                        )
                except Exception as src_exc:
                    logger.warning(
                        "Source-task close failed for %s ← mission %s: %s",
                        _source_task_id, mission_id, src_exc,
                    )

            th_conn.close()
        except Exception as th_exc:
            logger.warning("Task Hub finalize sync failed for %s: %s", mission_id, th_exc)

        if event_type == "vp.mission.completed":
            try:
                mission_type = (dict(mission)["mission_type"] or "").strip()
            except (KeyError, TypeError):
                mission_type = ""
            if mission_type.startswith("doc-maintenance"):
                try:
                    _post_mission_push_pr_merge(
                        workspace_root=str(self.profile.workspace_root),
                        mission_id=mission_id,
                    )
                except Exception as exc:
                    logger.warning("Post-mission push/PR hook failed for %s: %s", mission_id, exc)

        payload = dict(outcome.payload or {})
        if outcome.message:
            payload["message"] = outcome.message
        if outcome.result_ref:
            payload["result_ref"] = outcome.result_ref
        payload["worker_id"] = self.worker_id
        payload.update(
            _write_vp_finalize_artifacts(
                mission_id=mission_id,
                mission_row=mission,
                vp_id=self.vp_id,
                worker_id=self.worker_id,
                outcome=outcome,
                terminal_status=event_type.replace("vp.mission.", "", 1),
                source_context=source_context,
                workspace_root=self.profile.workspace_root,
            )
        )
        if self.vp_id == "vp.coder.primary" and event_type == "vp.mission.completed":
            self._register_proactive_pr_artifact(mission=mission, payload=payload)

        append_vp_event(
            self.conn,
            event_id=f"vp-event-{uuid.uuid4().hex}",
            mission_id=mission_id,
            vp_id=self.vp_id,
            event_type=event_type,
            payload={**source_context, **payload},
        )
        self._upsert_session(status="idle")

    @staticmethod
    def _detect_pr_url(outcome: Any) -> str:
        """Return the first GitHub PR URL found in a mission outcome, else "".

        Reuses the single canonical PR-URL pattern from ``proactive_codie`` so
        detection here can't drift from the artifact-registration path.
        """
        try:
            from universal_agent.services.proactive_codie import _GITHUB_PR_RE

            text = "\n".join(
                [
                    str(getattr(outcome, "message", "") or ""),
                    str(getattr(outcome, "result_ref", "") or ""),
                    json.dumps(dict(getattr(outcome, "payload", {}) or {}), ensure_ascii=True, sort_keys=True),
                ]
            )
            match = _GITHUB_PR_RE.search(text)
            return match.group(0) if match else ""
        except Exception:
            return ""

    def _register_proactive_pr_artifact(self, *, mission: sqlite3.Row, payload: dict[str, Any]) -> None:
        try:
            from universal_agent.durable.db import (
                connect_runtime_db,
                get_activity_db_path,
            )
            from universal_agent.services.proactive_codie import (
                register_pr_artifact_from_text,
            )

            text = "\n".join(
                [
                    str(payload.get("message") or ""),
                    str(payload.get("final_text") or ""),
                    str(payload.get("result_ref") or ""),
                    json.dumps(payload, ensure_ascii=True, sort_keys=True),
                ]
            )
            mission_payload = _parse_mission_payload(mission)
            context = mission_payload.get("context") if isinstance(mission_payload.get("context"), dict) else {}
            with connect_runtime_db(get_activity_db_path()) as conn:
                register_pr_artifact_from_text(
                    conn,
                    text=text,
                    title=f"CODIE proactive PR: {str(mission['objective'] or '')[:120]}",
                    summary=str(payload.get("message") or payload.get("final_text") or "")[:1000],
                    theme=str(context.get("theme") or ""),
                )
        except Exception as exc:
            logger.debug("Failed registering proactive PR artifact for mission %s: %s", mission["mission_id"], exc)

    def _upsert_session(self, *, status: str) -> None:
        upsert_vp_session(
            self.conn,
            vp_id=self.vp_id,
            runtime_id=self.profile.runtime_id,
            status=status,
            session_id=f"{self.vp_id}.external",
            workspace_dir=str(self.profile.workspace_root),
            lease_owner=self.worker_id,
            metadata={"client_kind": self.profile.client_kind, "display_name": self.profile.display_name},
        )

    def _seed_vp_soul(self, mission: Any) -> None:
        """Seed the VP's soul file into the mission workspace.

        agent_setup.py._load_soul_context() checks {workspace_dir}/SOUL.md first,
        so placing the VP-specific soul there ensures the VP agent gets its own
        identity instead of Simone's.
        """
        try:
            # Resolve the mission workspace directory (same logic as the client)
            workspace_dir = self._resolve_mission_workspace(mission)
            workspace_dir.mkdir(parents=True, exist_ok=True)

            soul_dest = workspace_dir / "SOUL.md"
            if soul_dest.exists():
                return  # Don't overwrite an existing soul (may be from a previous run)

            soul_src = Path(__file__).resolve().parents[1] / "prompt_assets" / self.profile.soul_file
            if soul_src.exists() and soul_src.is_file():
                content = soul_src.read_text(encoding="utf-8").rstrip()
                if content:
                    soul_dest.write_text(content + "\n", encoding="utf-8")
                    logger.info("Seeded VP soul %s → %s", self.profile.soul_file, soul_dest)
            else:
                logger.warning("VP soul file not found: %s", soul_src)
        except Exception as exc:
            logger.warning("Failed to seed VP soul for mission: %s", exc)

    def _write_mission_briefing(self, mission: Any) -> None:
        """Write a mission-specific briefing into the workspace if provided.

        Dispatchers may include a `system_prompt_injection` field in the mission
        payload to provide task-specific context that gets loaded into the VP's
        system prompt as a MISSION BRIEFING section.
        """
        try:
            payload_json = mission.get("payload_json") if hasattr(mission, "get") else mission["payload_json"]
            if isinstance(payload_json, str) and payload_json.strip():
                payload = json.loads(payload_json)
            elif isinstance(payload_json, dict):
                payload = payload_json
            else:
                return

            injection = str(payload.get("system_prompt_injection") or "").strip()
            if not injection:
                return

            workspace_dir = self._resolve_mission_workspace(mission)
            workspace_dir.mkdir(parents=True, exist_ok=True)
            briefing_path = workspace_dir / "MISSION_BRIEFING.md"
            briefing_path.write_text(injection + "\n", encoding="utf-8")
            logger.info("Wrote mission briefing (%d chars) → %s", len(injection), briefing_path)
        except Exception as exc:
            logger.warning("Failed to write mission briefing: %s", exc)

    def _resolve_mission_workspace(self, mission: Any) -> Path:
        """Resolve the workspace directory for a mission.

        Must match the workspace resolution logic in the VP client (ClaudeCodeClient
        or ClaudeGeneralistClient) so the soul/briefing land in the same directory
        that the agent will run in.
        """
        mission_id = str(mission.get("mission_id") or mission["mission_id"]).replace("/", "_").replace("..", "_")
        payload_json = mission.get("payload_json") if hasattr(mission, "get") else mission["payload_json"]
        if isinstance(payload_json, str) and payload_json.strip():
            try:
                payload = json.loads(payload_json)
                constraints = payload.get("constraints") if isinstance(payload, dict) else {}
                if isinstance(constraints, dict):
                    target_path = _extract_first_target_path(constraints)
                    if target_path:
                        return Path(target_path).expanduser().resolve()
            except Exception:
                pass
        return (self.profile.workspace_root / (mission_id or "mission")).resolve()

    async def _provision_workspace(self, mission: Any) -> Path:
        """Provision the workspace for the mission.
        
        If this is vp.coder.primary and the target path is a git repository,
        this provisions an ephemeral git worktree for the mission.
        Otherwise, it returns the standard workspace path.
        """
        mission_payload = _parse_mission_payload(mission)
        constraints = mission_payload.get("constraints", {})
        if not isinstance(constraints, dict):
            constraints = {}
        target_path_str = _extract_first_target_path(constraints)
        
        # Default fallback if not a coder mission or no target path
        if self.vp_id != "vp.coder.primary" or not target_path_str:
            ws = self._resolve_mission_workspace(mission)
            ws.mkdir(parents=True, exist_ok=True)
            return ws

        target_path = Path(target_path_str).expanduser().resolve()
        
        # If the target path doesn't have a .git dir, we can't make a worktree
        if not (target_path / ".git").exists() and not (target_path.parent / ".git").exists():
            ws = self._resolve_mission_workspace(mission)
            ws.mkdir(parents=True, exist_ok=True)
            return ws

        mission_id = str(mission.get("mission_id") or mission["mission_id"]).replace("/", "_").replace("..", "_")
        worktree_path = (self.profile.workspace_root / mission_id).resolve()
        branch_name = f"vp-task-{mission_id[:8]}"

        # Create the worktree on a branch based on a freshly-fetched
        # origin/<base> (main), NOT the deploy tree's local HEAD. The live
        # /opt/universal_agent checkout can sit on a diverged/renamed local
        # branch; branching off that HEAD yields a worktree with no merge-base
        # to origin/main → disjoint-history PRs (e.g. #646). The fetch is
        # best-effort so offline/dev runs gracefully fall back to HEAD below.
        logger.info("Provisioning git worktree at %s (branch: %s, base: origin/%s)", worktree_path, branch_name, _PR_BASE_BRANCH)
        subprocess.run(
            ["git", "fetch", "origin", _PR_BASE_BRANCH],
            cwd=str(target_path),
            capture_output=True,
            text=True,
            timeout=180,
        )
        result = subprocess.run(
            ["git", "worktree", "add", str(worktree_path), "-b", branch_name, f"origin/{_PR_BASE_BRANCH}"],
            cwd=str(target_path),
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            if "already exists" in (result.stderr or ""):
                # Branch already exists (re-run of the same mission) — reuse it.
                result = subprocess.run(
                    ["git", "worktree", "add", str(worktree_path), branch_name],
                    cwd=str(target_path),
                    capture_output=True,
                    text=True
                )
            else:
                # origin/<base> unavailable (offline / fresh clone without the
                # remote ref) — fall back to branching off the current HEAD.
                result = subprocess.run(
                    ["git", "worktree", "add", str(worktree_path), "-b", branch_name],
                    cwd=str(target_path),
                    capture_output=True,
                    text=True
                )
            if result.returncode != 0:
                logger.warning("Failed to provision git worktree: %s", result.stderr.strip())
                worktree_path.mkdir(parents=True, exist_ok=True)

        return worktree_path

    async def _teardown_workspace(self, workspace_path: Path) -> None:
        """Tear down the workspace if it is an ephemeral git worktree."""
        if self.vp_id != "vp.coder.primary":
            return
            
        # A git worktree has a .git file (not directory) pointing to the main repo
        git_marker = workspace_path / ".git"
        if not git_marker.exists() or not git_marker.is_file():
            return
            
        logger.info("Tearing down git worktree at %s", workspace_path)
        result = subprocess.run(
            ["git", "worktree", "remove", "--force", str(workspace_path)],
            cwd=str(workspace_path.parent),
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            logger.warning("Failed to teardown git worktree: %s", result.stderr.strip())

    def _select_client_for_mission(self, mission: Any) -> VpClient:
        """Select SDK, CLI, or DAG client based on mission payload's execution_mode."""
        if self._client is not None:
            return self._client
        payload_json = mission["payload_json"] if "payload_json" in mission.keys() else None
        if isinstance(payload_json, str) and payload_json.strip():
            try:
                payload = json.loads(payload_json)
                execution_mode = str(payload.get("execution_mode") or "").strip().lower()
                if execution_mode == "cli":
                    from universal_agent.vp.clients.claude_cli_client import (
                        ClaudeCodeCLIClient,
                    )
                    logger.info(
                        "Mission %s using CLI execution mode (vp=%s)",
                        mission.get("mission_id", "?"), self.vp_id,
                    )
                    return ClaudeCodeCLIClient()
                if execution_mode == "dag":
                    from universal_agent.vp.clients.dag_client import DagClient
                    logger.info(
                        "Mission %s using DAG execution mode (vp=%s)",
                        mission.get("mission_id", "?"), self.vp_id,
                    )
                    return DagClient()
            except (json.JSONDecodeError, Exception):
                pass
        return self._default_client

    def _create_client(self) -> VpClient:
        if self.profile.client_kind == "claude_code":
            return ClaudeCodeClient()
        if self.profile.client_kind == "claude_generalist":
            return ClaudeGeneralistClient()
        raise ValueError(f"Unsupported VP client_kind: {self.profile.client_kind}")


def _mission_source_context(mission_row: Any) -> dict[str, Any]:
    payload_json = mission_row["payload_json"] if "payload_json" in mission_row.keys() else None
    if not isinstance(payload_json, str) or not payload_json.strip():
        return {}
    try:
        payload = json.loads(payload_json)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    source_session_id = str(payload.get("source_session_id") or "").strip()
    source_turn_id = str(payload.get("source_turn_id") or "").strip()
    reply_mode = str(payload.get("reply_mode") or "").strip()

    context: dict[str, Any] = {}
    if source_session_id:
        context["source_session_id"] = source_session_id
    if source_turn_id:
        context["source_turn_id"] = source_turn_id
    if reply_mode:
        context["reply_mode"] = reply_mode
    return context


def _env_true(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _mission_workspace_dir(*, mission_id: str, result_ref: str, workspace_root: Path) -> Path:
    root_resolved = workspace_root.resolve()
    if result_ref.startswith("workspace://"):
        candidate = Path(result_ref.replace("workspace://", "", 1)).expanduser()
        try:
            resolved = candidate.resolve()
            if resolved == root_resolved or root_resolved in resolved.parents:
                return resolved
        except Exception:
            pass
    return (workspace_root / mission_id).resolve()


def _write_json_file(path: Path, payload: dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return True
    except Exception as exc:
        logger.warning("VP worker failed writing file path=%s err=%s", path, exc)
        return False


def _collect_user_artifact_relpaths(
    mission_workspace: Path,
    *,
    ignore_names: set[str],
    max_items: int = 8,
) -> list[str]:
    if not mission_workspace.exists() or max_items <= 0:
        return []
    relpaths: list[str] = []
    for file_path in sorted(path for path in mission_workspace.rglob("*") if path.is_file()):
        relpath = str(file_path.relative_to(mission_workspace))
        normalized_name = file_path.name.strip().lower()
        if normalized_name in ignore_names:
            continue
        if relpath.startswith("."):
            continue
        relpaths.append(relpath)
        if len(relpaths) >= max_items:
            break
    return relpaths


def _write_vp_finalize_artifacts(
    *,
    mission_id: str,
    mission_row: Any,
    vp_id: str,
    worker_id: str,
    outcome: Any,
    terminal_status: str,
    source_context: dict[str, Any],
    workspace_root: Path,
) -> dict[str, Any]:
    result_ref = str(getattr(outcome, "result_ref", "") or "").strip()
    mission_workspace = _mission_workspace_dir(
        mission_id=mission_id,
        result_ref=result_ref,
        workspace_root=workspace_root,
    )
    completed_epoch = time.time()
    created_at = str(mission_row["created_at"] or "")
    started_at = str(mission_row["started_at"] or "")
    updated_at = str(mission_row["updated_at"] or "")
    objective = str(mission_row["objective"] or "")
    mission_type = str(mission_row["mission_type"] or "")
    mission_payload_raw = mission_row["payload_json"] if "payload_json" in mission_row.keys() else None
    mission_payload = {}
    if isinstance(mission_payload_raw, str) and mission_payload_raw.strip():
        try:
            parsed = json.loads(mission_payload_raw)
            if isinstance(parsed, dict):
                mission_payload = parsed
        except Exception:
            mission_payload = {}

    artifact_refs: dict[str, Any] = {}
    receipt_filename = "mission_receipt.json"
    marker_name = (os.getenv("UA_VP_SYNC_READY_MARKER_FILENAME") or "").strip() or "sync_ready.json"

    if _env_true("UA_VP_MISSION_RECEIPT_ENABLED", True):
        receipt_payload = {
            "version": 1,
            "mission_id": mission_id,
            "vp_id": vp_id,
            "status": terminal_status,
            "worker_id": worker_id,
            "objective": objective,
            "mission_type": mission_type or None,
            "result_ref": result_ref or None,
            "source_session_id": source_context.get("source_session_id"),
            "source_turn_id": source_context.get("source_turn_id"),
            "reply_mode": source_context.get("reply_mode"),
            "created_at": created_at or None,
            "started_at": started_at or None,
            "updated_at": updated_at or None,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "completed_at_epoch": completed_epoch,
            "mission_payload": mission_payload,
            "outcome": {
                "status": str(getattr(outcome, "status", "") or "").strip() or terminal_status,
                "message": str(getattr(outcome, "message", "") or "").strip() or None,
                "payload": dict(getattr(outcome, "payload", {}) or {}),
            },
        }
        receipt_path = mission_workspace / receipt_filename
        if _write_json_file(receipt_path, receipt_payload):
            artifact_refs["mission_receipt_relpath"] = receipt_filename
            artifact_refs["mission_receipt_path"] = str(receipt_path)

    if _env_true("UA_VP_SYNC_READY_MARKER_ENABLED", True):
        marker_payload = {
            "version": 1,
            "mission_id": mission_id,
            "vp_id": vp_id,
            "state": terminal_status,
            "ready": True,
            "worker_id": worker_id,
            "result_ref": result_ref or None,
            "source_session_id": source_context.get("source_session_id"),
            "source_turn_id": source_context.get("source_turn_id"),
            "reply_mode": source_context.get("reply_mode"),
            "updated_at_epoch": completed_epoch,
            "completed_at_epoch": completed_epoch,
        }
        marker_path = mission_workspace / marker_name
        if _write_json_file(marker_path, marker_payload):
            artifact_refs["sync_ready_marker_relpath"] = marker_name
            artifact_refs["sync_ready_marker_path"] = str(marker_path)

    user_artifact_relpaths = _collect_user_artifact_relpaths(
        mission_workspace,
        ignore_names={receipt_filename.lower(), marker_name.lower()},
    )
    if user_artifact_relpaths:
        artifact_refs["artifact_relpath"] = user_artifact_relpaths[0]
        artifact_refs["artifact_relpaths"] = user_artifact_relpaths

    return artifact_refs


def _parse_mission_payload(mission_row: Any) -> dict[str, Any]:
    try:
        raw = mission_row["payload_json"] if "payload_json" in mission_row.keys() else None
    except Exception:
        raw = None
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}
