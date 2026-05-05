"""Cody task dispatcher — enqueues a `cody_demo_task` Task Hub item.

Once the scaffold builder (services/cody_scaffold.py) has populated a demo
workspace under `/opt/ua_demos/<demo-id>/` with BRIEF/ACCEPTANCE/SOURCES,
this module enqueues a Task Hub item pointing Cody at the workspace.

Per the v2 design (§7.4), the queue policy is **wait_indefinitely** —
Cody picks up the task whenever she's available. No retries-then-give-up.

This module performs no LLM calls. The Task Hub upsert is mechanical.

See docs/proactive_signals/claudedevs_intel_v2_design.md §7.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
from pathlib import Path
import sqlite3
from typing import Any

from universal_agent import task_hub
from universal_agent.services.proactive_artifacts import upsert_artifact

logger = logging.getLogger(__name__)


SOURCE_KIND_CODY_DEMO_TASK = "cody_demo_task"
DEFAULT_WALL_TIME_MAX_MINUTES = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_task_id(*, demo_id: str, entity_slug: str) -> str:
    """Deterministic per-(demo,entity) ID so re-dispatch is idempotent."""
    payload = f"{entity_slug}::{demo_id}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:16]
    return f"{SOURCE_KIND_CODY_DEMO_TASK}:{digest}"


def dispatch_cody_demo_task(
    conn: sqlite3.Connection,
    *,
    workspace_dir: Path,
    entity_slug: str,
    entity_path: Path,
    demo_id: str,
    title: str | None = None,
    endpoint_required: str = "anthropic_native",
    wall_time_max_minutes: int = DEFAULT_WALL_TIME_MAX_MINUTES,
    iteration: int = 1,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Queue a `cody_demo_task` for Cody. Returns the upserted task dict.

    Idempotent across re-dispatches with the same (entity_slug, demo_id).
    Persistent queue policy means Task Hub will hold the task until Cody
    is available; no retries-then-give-up.
    """
    task_hub.ensure_schema(conn)

    task_id = _stable_task_id(demo_id=demo_id, entity_slug=entity_slug)
    resolved_title = title or f"Build demo for {entity_slug} ({demo_id})"
    description = (
        f"Cody Phase 3 demo task. Workspace: {workspace_dir}\n\n"
        f"- Read BRIEF.md, ACCEPTANCE.md, business_relevance.md FIRST.\n"
        f"- Read at least the primary doc in SOURCES/ before writing code.\n"
        f"- Hard rule: NO INVENTION. If docs don't show how to do X, document the gap in BUILD_NOTES.md.\n"
        f"- Invoke `claude` from inside the workspace dir so vanilla settings take effect.\n"
        f"- Endpoint required: {endpoint_required}.\n"
        f"- Iteration: {iteration} (re-queues with new FEEDBACK.md if Simone iterates).\n"
        f"- Wall time max: {wall_time_max_minutes} minutes per attempt.\n"
        f"- Source entity: {entity_path}"
    )

    metadata: dict[str, Any] = {
        "source": "claude_code_intel_v2",
        "task_kind": SOURCE_KIND_CODY_DEMO_TASK,
        "demo_id": demo_id,
        "entity_slug": entity_slug,
        "entity_path": str(entity_path),
        "workspace_dir": str(workspace_dir),
        "endpoint_required": endpoint_required,
        "iteration": int(iteration),
        "wall_time_max_minutes": int(wall_time_max_minutes),
        "queue_policy": "wait_indefinitely",
        "preferred_vp": "vp.coder.primary",
        "knowledge_base_slug": "claude-code-intelligence",
        "workflow_manifest": {
            "workflow_kind": "code_change",
            "delivery_mode": "interactive_chat",
            "requires_pdf": False,
            "final_channel": "chat",
            "canonical_executor": "simone_first",
            "repo_mutation_allowed": False,  # demo workspaces are NOT in repo
        },
    }
    if extra_metadata:
        metadata.update({k: v for k, v in extra_metadata.items() if k not in metadata})

    item = {
        "task_id": task_id,
        "source_kind": SOURCE_KIND_CODY_DEMO_TASK,
        "source_ref": demo_id,
        "title": resolved_title,
        "description": description,
        "project_key": "proactive",
        # Demo tasks rank high — they're the headline value of v2.
        "priority": 4,
        "labels": [
            "agent-ready",
            "claude-code-intel",
            "cody-demo",
            "v2-phase3",
            f"endpoint:{endpoint_required}",
        ],
        "status": task_hub.TASK_STATUS_OPEN,
        "agent_ready": True,
        "trigger_type": "scaffold_dispatch",
        "metadata": metadata,
    }

    upserted = task_hub.upsert_item(conn, item)

    # Mirror as a proactive artifact so it surfaces in dashboards.
    try:
        upsert_artifact(
            conn,
            artifact_type="cody_demo_task",
            source_kind=SOURCE_KIND_CODY_DEMO_TASK,
            source_ref=demo_id,
            title=resolved_title,
            summary=f"Demo task for {entity_slug} → {workspace_dir.name}",
            status="surfaced",
            priority=4,
            artifact_path=str(workspace_dir / "BRIEF.md"),
            topic_tags=["claude-code", "cody-demo", entity_slug],
            metadata={
                "task_id": task_id,
                "demo_id": demo_id,
                "entity_slug": entity_slug,
                "iteration": int(iteration),
            },
        )
    except Exception:
        logger.exception("failed to mirror cody_demo_task as proactive artifact")

    return upserted


def reissue_cody_demo_task_with_feedback(
    conn: sqlite3.Connection,
    *,
    workspace_dir: Path,
    entity_slug: str,
    entity_path: Path,
    demo_id: str,
    feedback_path: Path,
    iteration: int,
    title: str | None = None,
    endpoint_required: str = "anthropic_native",
) -> dict[str, Any]:
    """Re-queue a Cody demo task after Simone wrote FEEDBACK.md.

    Same task_id (idempotent) — Task Hub treats this as a status reset
    plus a new iteration count. Cody's skill (PR 9) reads FEEDBACK.md
    on iteration > 1.
    """
    return dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace_dir,
        entity_slug=entity_slug,
        entity_path=entity_path,
        demo_id=demo_id,
        title=title,
        endpoint_required=endpoint_required,
        iteration=iteration,
        extra_metadata={"feedback_path": str(feedback_path)},
    )
