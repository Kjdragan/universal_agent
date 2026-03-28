from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import sqlite3

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
)
from universal_agent.vp.clients.base import VpClient
from universal_agent.vp.clients.claude_code_client import ClaudeCodeClient
from universal_agent.vp.clients.claude_generalist_client import ClaudeGeneralistClient
from universal_agent.vp.profiles import get_vp_profile

logger = logging.getLogger(__name__)

# ── GitHub repo for doc-maintenance PRs ──────────────────────────────────────
_GH_REPO = os.getenv("UA_GH_REPO", "Kjdragan/universal_agent")


def _post_mission_push_pr_merge(*, workspace_root: str, mission_id: str) -> None:
    """Push the agent's doc branch, create a PR, and squash-merge it.

    Runs OUTSIDE the Claude CLI sandbox (in the VP worker process) so it has
    full network access.  The agent inside the sandbox creates a branch and
    commits but cannot push (sandbox blocks outbound HTTPS auth).
    """
    import re
    import urllib.request
    import urllib.error

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

    # 2. Check if there are commits on this branch that aren't on develop
    result = subprocess.run(
        ["git", "log", "develop..HEAD", "--oneline"],
        capture_output=True, text=True, cwd=cwd,
    )
    commits = result.stdout.strip()
    if not commits:
        logger.info("Post-mission hook: no new commits on %s vs develop, skipping", branch)
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
        "base": "develop",
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

    # 7. Switch back to develop so the next mission starts clean
    subprocess.run(["git", "checkout", "develop"], capture_output=True, cwd=cwd)
    subprocess.run(["git", "pull", "origin", "develop"], capture_output=True, cwd=cwd)
    logger.info("Post-mission hook: switched back to develop, ready for next mission")


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
        self._default_client = self._create_client()

    def stop(self) -> None:
        self._stopped.set()

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
                    from universal_agent.services.capacity_governor import CapacityGovernor
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

        logger.info(
            "VP mission starting: vp_id=%s soul=%s mission_id=%s mission_type=%s",
            self.vp_id,
            self.profile.soul_file,
            mission_id,
            str(mission.get("mission_type") or ""),
        )

        client = self._select_client_for_mission(mission)

        # Fix: run a background heartbeat task that extends the mission claim
        # lease while client.run_mission() is executing.  Without this, the
        # lease expires after lease_ttl_seconds and the mission is re-claimed
        # and restarted by the next _tick(), causing an infinite restart loop.
        heartbeat_stop = asyncio.Event()

        async def _heartbeat_mission_claim() -> None:
            interval = max(5, self.lease_ttl_seconds // 3)  # heartbeat at 1/3 of TTL
            while not heartbeat_stop.is_set():
                try:
                    await asyncio.wait_for(heartbeat_stop.wait(), timeout=interval)
                    break  # stop event was set
                except asyncio.TimeoutError:
                    pass  # interval elapsed, heartbeat now
                try:
                    heartbeat_vp_mission_claim(
                        self.conn,
                        mission_id=mission_id,
                        vp_id=self.vp_id,
                        worker_id=self.worker_id,
                        lease_ttl_seconds=self.lease_ttl_seconds,
                    )
                    # Also heartbeat the session lease
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
        try:
            outcome = await client.run_mission(
                mission=dict(mission),
                workspace_root=self.profile.workspace_root,
            )
        finally:
            heartbeat_stop.set()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        if outcome.status == "cancelled":
            finalize_vp_mission(self.conn, mission_id, "cancelled", result_ref=outcome.result_ref)
            event_type = "vp.mission.cancelled"
        elif outcome.status == "failed":
            finalize_vp_mission(self.conn, mission_id, "failed", result_ref=outcome.result_ref)
            event_type = "vp.mission.failed"
        else:
            finalize_vp_mission(self.conn, mission_id, "completed", result_ref=outcome.result_ref)
            event_type = "vp.mission.completed"
            # Post-mission hook: push branch, create PR, merge for doc-maintenance missions
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

        append_vp_event(
            self.conn,
            event_id=f"vp-event-{uuid.uuid4().hex}",
            mission_id=mission_id,
            vp_id=self.vp_id,
            event_type=event_type,
            payload={**source_context, **payload},
        )
        self._upsert_session(status="idle")

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
                    target_path = str(constraints.get("target_path") or "").strip()
                    if target_path:
                        return Path(target_path).expanduser().resolve()
            except Exception:
                pass
        return (self.profile.workspace_root / (mission_id or "mission")).resolve()

    def _select_client_for_mission(self, mission: Any) -> VpClient:
        """Select SDK or CLI client based on mission payload's execution_mode."""
        payload_json = mission["payload_json"] if "payload_json" in mission.keys() else None
        if isinstance(payload_json, str) and payload_json.strip():
            try:
                payload = json.loads(payload_json)
                execution_mode = str(payload.get("execution_mode") or "").strip().lower()
                if execution_mode == "cli":
                    from universal_agent.vp.clients.claude_cli_client import ClaudeCodeCLIClient
                    logger.info(
                        "Mission %s using CLI execution mode (vp=%s)",
                        mission.get("mission_id", "?"), self.vp_id,
                    )
                    return ClaudeCodeCLIClient()
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
