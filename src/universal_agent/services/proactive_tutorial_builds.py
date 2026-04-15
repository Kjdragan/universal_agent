"""Tutorial build automation helpers for proactive intelligence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from universal_agent import task_hub
from universal_agent.services.proactive_artifacts import ARTIFACT_STATUS_CANDIDATE, make_artifact_id, upsert_artifact


def queue_tutorial_build_task(
    conn: sqlite3.Connection,
    *,
    video_id: str,
    video_title: str,
    video_url: str = "",
    channel_name: str = "",
    source: str = "csi_auto_route",
    extraction_plan: dict[str, Any] | None = None,
    priority: int = 3,
) -> dict[str, Any]:
    """Queue CODIE to build a private working repo from a tutorial video."""
    clean_video_id = str(video_id or "").strip()
    if not clean_video_id:
        raise ValueError("video_id is required")
    clean_title = str(video_title or "").strip() or clean_video_id
    plan = extraction_plan if isinstance(extraction_plan, dict) else {}
    task_id = f"tutorial-build:{hashlib.sha256(clean_video_id.encode()).hexdigest()[:16]}"
    preference_context = _preference_context(conn, task_type="tutorial_build", topic_tags=["tutorial", "codie", clean_title])
    description = _build_task_description(
        video_title=clean_title,
        video_url=video_url,
        channel_name=channel_name,
        extraction_plan=plan,
        preference_context=preference_context,
    )
    task = task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "tutorial_build",
            "source_ref": clean_video_id,
            "title": f"Build private tutorial repo: {clean_title}",
            "description": description,
            "project_key": "proactive",
            "priority": max(1, min(int(priority or 3), 4)),
            "labels": ["agent-ready", "tutorial-build", "codie", "code"],
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": True,
            "trigger_type": "heartbeat_poll",
            "metadata": {
                "source": source,
                "video_id": clean_video_id,
                "video_title": clean_title,
                "video_url": str(video_url or "").strip(),
                "channel_name": str(channel_name or "").strip(),
                "extraction_plan": plan,
                "repo_visibility": "private",
                "public_publication_allowed": False,
                "workflow_manifest": {
                    "workflow_kind": "code_change",
                    "delivery_mode": "interactive_chat",
                    "requires_pdf": False,
                    "final_channel": "chat",
                    "canonical_executor": "simone_first",
                    "repo_mutation_allowed": True,
                },
            },
        },
    )
    artifact = upsert_artifact(
        conn,
        artifact_type="tutorial_build_task",
        source_kind="tutorial_build",
        source_ref=clean_video_id,
        title=str(task.get("title") or ""),
        summary=f"Queued CODIE to build a private tutorial repo from {clean_title}.",
        status=ARTIFACT_STATUS_CANDIDATE,
        priority=max(1, min(int(priority or 3), 4)),
        source_url=str(video_url or "").strip(),
        topic_tags=["tutorial", "codie", "private-repo"],
        metadata={"task_id": task_id, "video_id": clean_video_id, "source": source},
    )
    return {"task": task, "artifact": artifact}


def register_tutorial_build_artifact(
    conn: sqlite3.Connection,
    *,
    video_id: str,
    title: str,
    repo_url: str = "",
    artifact_path: str = "",
    video_url: str = "",
    channel_name: str = "",
    run_commands: str = "",
    tests: str = "",
    status: str = "success",
) -> dict[str, Any]:
    """Register a completed tutorial build repo or local fallback artifact."""
    clean_video_id = str(video_id or "").strip()
    if not clean_video_id:
        raise ValueError("video_id is required")
    uri = str(repo_url or "").strip()
    path = str(artifact_path or "").strip()
    if not uri and not path:
        raise ValueError("repo_url or artifact_path is required")
    metadata = {
        "video_id": clean_video_id,
        "video_url": str(video_url or "").strip(),
        "channel_name": str(channel_name or "").strip(),
        "repo_url": uri,
        "artifact_path": path,
        "repo_visibility": "private" if uri else "",
        "run_commands": str(run_commands or "").strip(),
        "tests": str(tests or "").strip(),
        "build_status": str(status or "success").strip(),
    }
    return upsert_artifact(
        conn,
        artifact_id=make_artifact_id(
            source_kind="tutorial_build",
            source_ref=clean_video_id,
            artifact_type="tutorial_build",
            title=title,
        ),
        artifact_type="tutorial_build",
        source_kind="tutorial_build",
        source_ref=clean_video_id,
        title=str(title or "").strip() or "Tutorial build artifact",
        summary=_build_artifact_summary(metadata),
        status=ARTIFACT_STATUS_CANDIDATE,
        priority=4,
        artifact_uri=uri,
        artifact_path=path,
        source_url=str(video_url or uri or "").strip(),
        topic_tags=["tutorial", "codie", "private-repo"],
        metadata=metadata,
    )


def register_tutorial_bootstrap_job_artifact(conn: sqlite3.Connection, job: dict[str, Any]) -> dict[str, Any] | None:
    """Register a completed tutorial bootstrap job as a review artifact."""
    if str((job or {}).get("status") or "").strip().lower() != "completed":
        return None
    video_id = str(job.get("video_id") or job.get("tutorial_run_path") or job.get("job_id") or "").strip()
    title = str(job.get("tutorial_title") or job.get("repo_name") or job.get("tutorial_run_path") or "Tutorial build").strip()
    repo_dir = str(job.get("repo_dir") or "").strip()
    repo_url = str(job.get("repo_url") or "").strip()
    if not repo_url and not repo_dir:
        return None
    return register_tutorial_build_artifact(
        conn,
        video_id=video_id,
        title=title,
        repo_url=repo_url,
        artifact_path=repo_dir,
        video_url=str(job.get("video_url") or "").strip(),
        channel_name=str(job.get("channel_name") or "").strip(),
        run_commands=str(job.get("run_commands") or "").strip(),
        tests=str(job.get("tests") or "").strip(),
        status=str(job.get("status") or "completed").strip(),
    )


def _build_task_description(
    *,
    video_title: str,
    video_url: str,
    channel_name: str,
    extraction_plan: dict[str, Any],
    preference_context: str = "",
) -> str:
    plan_json = json.dumps(extraction_plan or {}, indent=2, ensure_ascii=True)
    base = "\n".join(
        [
            "CODIE should build a working private repository from this tutorial.",
            "",
            f"Source video: {video_title}",
            f"Channel: {channel_name or '(unknown)'}",
            f"URL: {video_url or '(none)'}",
            "",
            "Implementation extraction plan:",
            plan_json,
            "",
            "Instructions:",
            "1. Create a complete working implementation in a clean repo/workspace.",
            "2. The GitHub repo must be private by default if pushed.",
            "3. Public publication is not allowed without explicit Kevin approval.",
            "4. Include README run commands, source video attribution, and any adaptations.",
            "5. Use environment variables or mock modes for API keys.",
            "6. Run the implementation or the most relevant tests before declaring success.",
            "7. If GitHub is unavailable, preserve a complete local git repo artifact and report the fallback.",
        ]
    )
    if preference_context:
        base = f"{base}\n\nPreference context:\n{preference_context}"
    return base


def _preference_context(conn: sqlite3.Connection, *, task_type: str, topic_tags: list[str]) -> str:
    try:
        from universal_agent.services.proactive_preferences import get_delegation_context

        return get_delegation_context(conn, task_type=task_type, topic_tags=topic_tags)
    except Exception:
        return ""


def _build_artifact_summary(metadata: dict[str, Any]) -> str:
    location = metadata.get("repo_url") or metadata.get("artifact_path") or "artifact"
    status = metadata.get("build_status") or "success"
    return f"Tutorial build {status}; final work product: {location}"
