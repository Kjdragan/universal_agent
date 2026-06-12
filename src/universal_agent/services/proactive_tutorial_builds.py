"""Tutorial build automation helpers for proactive intelligence."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

from universal_agent import task_hub
from universal_agent.services.proactive_artifacts import (
    ARTIFACT_STATUS_CANDIDATE,
    make_artifact_id,
    upsert_artifact,
)
from universal_agent.services.proactive_task_builder import queue_proactive_task

logger = logging.getLogger(__name__)

# Cheap, surgical deterministic prefilter — only meant to skip obvious-no
# cases before invoking the LLM judge. The real decision is made by reading
# the Claude-distilled transcript summary, not by string matching.
_BLOCKED_CATEGORIES = frozenset(
    {
        "news",
        "politics",
        "geopolitics",
        "current_events",
        "current-events",
        "sports",
        "music",
        "gaming",
        "vlog",
        "comedy",
        "entertainment",
        "reaction",
        "podcast",
    }
)

_NEGATIVE_TOKENS = frozenset(
    {
        "reaction",
        "drama",
        "podcast",
        "vlog",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+\-]*")

# ── P4 Demo build contract ───────────────────────────────────────────────────
# Embedded verbatim in every `tutorial_build` task description (the BRIEF that
# reaches Cody via Simone's vp_dispatch_mission objective) and mirrored in
# .claude/skills/cody-implements-from-brief/SKILL.md. Canonical spec:
# project_docs/04_intelligence/15_demo_tutorial_pipeline_adr.md
# section "Demo build contract". Guard: tests/unit/test_demo_build_contract.py.
DEMO_BUILD_CONTRACT = """\
Demo build contract (binding):

Framework selection — pick the demo's stack from what the VIDEO is about:
1. A specific SDK/stack (Google ADK, Gemini, LangGraph, ...) -> build the
   demo in THAT native stack, first-class. Hands-on learning of that stack
   is the point — it is not a fallback.
2. A Claude Code / Anthropic feature (e.g. /goal) -> build with the
   Claude Agent SDK.
3. A stack-agnostic concept (e.g. "memory pipelines") -> default to the
   Claude Agent SDK (the north star).
4. Cross-framework integration (e.g. Claude Agent SDK + ADK) ->
   ONLY on explicit operator direction — never by default.
If "how to build this one" is ambiguous, PAUSE for operator input:
disposition the task for review with your specific question in the note.
Ambiguity never blocks demo-worthiness — it only pauses the build.

Acceptance bar — functional completeness, not looks:
Keep the UI simple (zero design-polish effort) but make the demo
functionally sophisticated enough to FULLY exercise the capability.
This demo is the operator's personal learning/reference library entry;
acceptance = it demonstrates the capability end-to-end, not how it looks.

Inference wiring (Claude Agent SDK demos): the demo MUST run against LIVE
inference — never mock the capability it is meant to demonstrate. Any demo
built on the Claude Agent SDK MUST read ANTHROPIC_BASE_URL and
ANTHROPIC_AUTH_TOKEN from the environment (the UA runtime injects both;
ANTHROPIC_BASE_URL routes inference to the ZAI/GLM endpoint) so the demo
actually runs against a real endpoint. Never hardcode an endpoint or token;
never commit a token; document the two env var names in the demo README.

Model & API currency — verify, never recall:
Your training data lags live APIs. For any external model id, API endpoint,
SDK method, or version the demo calls, treat your memory as STALE and confirm
the CURRENT identifier before hardcoding it. The source material names the
PRODUCT (e.g. "Nano Banana"), not the wire model id — resolve product -> current
id from an authoritative source, not recall: the gemini-api-dev skill or
Context7 docs for the SDK, the provider's current docs, or a minimal
authenticated live probe that returns 200. If you cannot confirm the current
id, PAUSE for operator input rather than guess. A demo that ships a
deprecated/invalid model id (e.g. a 404 on generate) is a FAILED demo even if
every other line is correct.

Runnable + manifest requirements (binding):
- The demo MUST be runnable end-to-end from the workspace with a
  uv-managed environment (`pyproject.toml` + `uv sync`, or a committed
  `uv.lock`); the exact run command (e.g. `uv run python main.py`) MUST
  appear in a "Run" section of README.md.
- Author `manifest.json` at the workspace root, schema-compatible with
  services/cody_implementation.py::DemoManifest (keys: demo_id, feature,
  endpoint_required, endpoint_hit, model_used, acceptance_passed,
  iteration, started_at, finished_at, notes). Record endpoint_hit
  truthfully (zai vs anthropic_native). endpoint_hit="mock" is NOT an
  acceptable pass state: if the capability requires a live API/key the
  build does not have, the demo is NOT done — report the missing credential
  and stop, rather than mocking it.
- Author BRIEF.md + ACCEPTANCE.md from this card via the
  self-brief-and-attest skill's "tutorial_build card mode" before
  building; the /goal loop runs against your own ACCEPTANCE criteria.
"""


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
    agent_ready: bool = True,
) -> dict[str, Any]:
    """Queue CODIE to build a private working repo from a tutorial video.

    When ``agent_ready`` is True (default) the row is immediately
    dispatch-eligible — unchanged behavior. When False the row is queued as a
    *pending-approval* build: ``status=open`` and visible on the dashboard, but
    ``agent_ready=False`` removes it from the eligible set so CODIE never claims
    it. The ``agent-ready`` label is also dropped (replaced with
    ``pending-approval``) so ``task_hub.upsert_item``'s label OR-fallback does
    not re-derive ``agent_ready=True``. A later one-field flip
    (``agent_ready 0→1``) promotes it without churning the rest of the queue.
    """
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
    dispatchable = bool(agent_ready)
    # Dispatchable rows keep the task_hub.TASK_LABEL_AGENT_READY label (and the explicit
    # agent_ready=True). Pending-approval rows MUST drop task_hub.TASK_LABEL_AGENT_READY so the
    # upsert_item OR-fallback (task_hub.TASK_LABEL_AGENT_READY in label_set) doesn't silently
    # re-flip agent_ready back to True.
    labels = (
        [task_hub.TASK_LABEL_AGENT_READY, "tutorial-build", "codie", "code"]
        if dispatchable
        else ["pending-approval", "tutorial-build", "codie", "code"]
    )
    task = queue_proactive_task(
        conn,
        task_id=task_id,
        source_kind="tutorial_build",
        source_ref=clean_video_id,
        title=f"Build private tutorial repo: {clean_title}",
        description=description,
        priority=priority or 3,
        labels=labels,
        agent_ready=dispatchable,
        metadata={
            "source": source,
            # P6: tutorial_build builds run the /goal loop. The per-task
            # override path in services/self_briefing.is_goal_eligible_mission
            # (payload metadata.use_goal_loop) is flag-independent, so the
            # lane is goal-driven without flipping UA_VP_GOAL_ENABLED
            # globally. Inherited onto the vp_missions row by
            # tools/vp_orchestration._vp_dispatch_mission_impl's
            # use_goal_loop inheritance block.
            "use_goal_loop": True,
            "video_id": clean_video_id,
            "video_title": clean_title,
            "video_url": str(video_url or "").strip(),
            "channel_name": str(channel_name or "").strip(),
            "extraction_plan": plan,
            "repo_visibility": "private",
            "public_publication_allowed": False,
            "approval_state": "dispatchable" if dispatchable else "pending_approval",
            "workflow_manifest": {
                "workflow_kind": "code_change",
                "delivery_mode": "interactive_chat",
                "requires_pdf": False,
                "final_channel": "chat",
                "canonical_executor": "simone_first",
                "repo_mutation_allowed": True,
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


def remaining_daily_build_budget(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """Return ``(remaining, ceiling, today_count)`` for the demo-build daily ceiling.

    Single home for the boundary math P2a introduced: ceiling from
    ``UA_DEMO_BUILD_DAILY_CEILING`` (default 10), ``today_count`` over
    America/Chicago local-midnight (``_count_today_tutorial_builds``).
    """
    ceiling = _daily_build_ceiling()
    today_count = _count_today_tutorial_builds(conn)
    return max(0, ceiling - today_count), ceiling, today_count


def queue_tutorial_builds_with_ceiling(
    conn: sqlite3.Connection,
    candidates: list[dict[str, Any]],
    *,
    source: str,
) -> dict[str, Any]:
    """Queue pre-ranked tutorial-build candidates through the shared daily ceiling.

    The ONE boundary-math implementation used by BOTH demo-lane sources (P3):
    the broad CSI RSS sweep (``sync_build_oriented_csi_videos``,
    ``source="csi_auto_route"``) and the curated Daily Digest
    (``youtube_daily_digest._queue_demo_builds``, ``source="youtube_daily_digest"``).

    ``candidates`` must be ranked best-first; each dict carries the
    ``queue_tutorial_build_task`` kwargs: ``video_id``, ``video_title``,
    ``video_url``, ``channel_name``, ``extraction_plan``, ``priority``.

    Behavior (identical to the inlined P2a loop this replaces):
      - the top ``remaining = max(0, ceiling - today_count)`` queue dispatchable
        (``agent_ready=True``); the rest queue pending-approval for the P2b button;
      - no-churn invariant: an already-dispatchable row is never demoted
        (``_existing_build_is_dispatchable``);
      - cross-source dedupe is structural — ``queue_tutorial_build_task`` derives
        ``task_id = tutorial-build:sha256(video_id)[:16]``, so the same video
        from both sources upserts ONE Task Hub row;
      - honors the ``UA_PROACTIVE_TUTORIAL_AUTO_ROUTE`` kill switch (queues
        nothing, returns ``disabled=True``).

    Returns ``{auto_queued, auto_new, auto_reaffirmed, pending_approval,
    ceiling, today_count, remaining}``. ``auto_queued`` is the legacy total
    (``auto_new + auto_reaffirmed``); the split exists because the total alone
    misreads as a ceiling violation — ``auto_reaffirmed`` rows were ALREADY
    dispatchable from a prior run (the no-churn invariant re-confirms them, they
    consume no new budget), so only ``auto_new`` (always ``<= remaining``) is
    this run's genuinely-new auto-dispatch count against the daily ceiling.
    """
    if _auto_route_disabled():
        return {
            "auto_queued": 0,
            "auto_new": 0,
            "auto_reaffirmed": 0,
            "pending_approval": 0,
            "ceiling": _daily_build_ceiling(),
            "today_count": 0,
            "remaining": 0,
            "disabled": True,
        }
    remaining, ceiling, today_count = remaining_daily_build_budget(conn)
    auto_new = 0
    auto_reaffirmed = 0
    pending_approval = 0
    for idx, candidate in enumerate(candidates):
        video_id = str(candidate.get("video_id") or "").strip()
        # A row already dispatchable from a PRIOR run is a re-confirmation, not
        # new work — the no-churn invariant keeps it dispatchable regardless of
        # this run's remaining budget, and it must not be counted against the
        # ceiling (checked for every candidate so the split is accurate even
        # within budget). Net dispatch decision is unchanged:
        # ``idx < remaining OR already_dispatchable``.
        already_dispatchable = _existing_build_is_dispatchable(conn, video_id)
        dispatchable = idx < remaining or already_dispatchable
        queue_tutorial_build_task(
            conn,
            video_id=video_id,
            video_title=str(candidate.get("video_title") or ""),
            video_url=str(candidate.get("video_url") or ""),
            channel_name=str(candidate.get("channel_name") or ""),
            source=source,
            extraction_plan=candidate.get("extraction_plan") if isinstance(candidate.get("extraction_plan"), dict) else {},
            priority=int(candidate.get("priority") or 3),
            agent_ready=dispatchable,
        )
        if dispatchable:
            if already_dispatchable:
                auto_reaffirmed += 1
            else:
                auto_new += 1
        else:
            pending_approval += 1
    return {
        # Legacy total (= auto_new + auto_reaffirmed) kept for back-compat.
        "auto_queued": auto_new + auto_reaffirmed,
        # The honest split: auto_new is this run's NEW auto-dispatches (always
        # <= remaining); auto_reaffirmed are prior-run rows re-confirmed by the
        # no-churn invariant (consume no new budget).
        "auto_new": auto_new,
        "auto_reaffirmed": auto_reaffirmed,
        "pending_approval": pending_approval,
        "ceiling": ceiling,
        "today_count": today_count,
        "remaining": remaining,
    }


def sync_build_oriented_csi_videos(
    conn: sqlite3.Connection,
    *,
    csi_db_path: Path | None,
    limit: int = 200,
) -> dict[str, int]:
    """Queue CODIE tutorial build tasks for build-oriented CSI RSS videos.

    A daily ceiling (``UA_DEMO_BUILD_DAILY_CEILING``, default 10) caps how many
    builds are auto-dispatched per America/Chicago day. Buildable candidates are
    ranked (transcript-ok first, then newest upload); the top ``remaining`` are
    queued dispatch-eligible (``agent_ready=True``), and the rest are queued as
    pending-approval rows (``agent_ready=False``) that a one-field flip can
    later promote. An already-dispatchable row is never demoted on a re-run, so
    the ceiling can't churn or seize an in-flight build.

    Returns ``{seen, queued, auto_queued, auto_new, auto_reaffirmed,
    pending_approval, ceiling, today_count, remaining}`` — ``auto_new`` is this
    run's genuinely-new auto-dispatches (against the ceiling); ``auto_reaffirmed``
    are no-churn re-confirmations of prior-run rows (see
    ``queue_tutorial_builds_with_ceiling``).
    """
    empty = {"seen": 0, "queued": 0, "auto_queued": 0, "auto_new": 0, "auto_reaffirmed": 0, "pending_approval": 0, "ceiling": _daily_build_ceiling(), "today_count": 0, "remaining": 0}
    if _auto_route_disabled() or csi_db_path is None or not csi_db_path.exists():
        return empty
    db = sqlite3.connect(str(csi_db_path))
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            """
            SELECT
                e.event_id, e.occurred_at, e.subject_json,
                a.category, a.summary_text, a.analysis_json, a.transcript_status
            FROM events e
            LEFT JOIN rss_event_analysis a ON a.event_id = e.event_id
            WHERE e.source = 'youtube_channel_rss'
            ORDER BY e.id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 1000)),),
        ).fetchall()
    except sqlite3.Error:
        return empty
    finally:
        db.close()

    # ── Collect buildable candidates (don't queue inside the loop anymore) ──
    candidates: list[dict[str, Any]] = []
    for row in rows:
        subject = _json_loads_obj(row["subject_json"])
        analysis = _json_loads_obj(row["analysis_json"])
        summary = str(row["summary_text"] or "")
        if not _looks_build_oriented(subject=subject, analysis=analysis, category=str(row["category"] or ""), summary=summary):
            continue
        video_id = str(subject.get("video_id") or row["event_id"] or "").strip()
        if not video_id:
            continue
        title = str(subject.get("title") or subject.get("media_title") or video_id)
        channel = str(subject.get("channel_name") or subject.get("author_name") or "")
        if not is_video_buildable_with_judge(
            conn,
            video_id=video_id,
            title=title,
            channel_name=channel,
            summary_text=summary,
        ):
            continue
        transcript_ok = str(row["transcript_status"] or "").lower() == "ok"
        candidates.append(
            {
                "video_id": video_id,
                "title": title,
                "channel": channel,
                "url": str(subject.get("url") or ""),
                "extraction_plan": _extraction_plan_from_analysis(analysis=analysis, row=row),
                "transcript_ok": transcript_ok,
                "occurred_at": str(row["occurred_at"] or ""),
            }
        )

    # ── Rank: transcript-ok first, then newest upload (no numeric score exists) ──
    candidates.sort(key=lambda c: (c["transcript_ok"], c["occurred_at"]), reverse=True)

    # ── Queue through the SHARED daily-ceiling boundary (P3: one ladder for
    # both sources — boundary math lives in queue_tutorial_builds_with_ceiling) ──
    outcome = queue_tutorial_builds_with_ceiling(
        conn,
        [
            {
                "video_id": c["video_id"],
                "video_title": c["title"],
                "video_url": c["url"],
                "channel_name": c["channel"],
                "extraction_plan": c["extraction_plan"],
                "priority": 3 if c["transcript_ok"] else 2,
            }
            for c in candidates
        ],
        source="csi_auto_route",
    )

    return {
        "seen": len(rows),
        "queued": outcome["auto_queued"] + outcome["pending_approval"],
        "auto_queued": outcome["auto_queued"],
        "auto_new": outcome.get("auto_new", outcome["auto_queued"]),
        "auto_reaffirmed": outcome.get("auto_reaffirmed", 0),
        "pending_approval": outcome["pending_approval"],
        "ceiling": outcome["ceiling"],
        "today_count": outcome["today_count"],
        "remaining": outcome.get("remaining", 0),
    }


def list_pending_approval_builds(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List tutorial_build rows queued as pending-approval (P2a overflow).

    The pending representation is ``source_kind='tutorial_build'``,
    ``status=open``, ``agent_ready=0`` with a ``pending-approval`` label
    (see ``queue_tutorial_build_task``). Returns dashboard-renderable
    summaries, newest first.
    """
    task_hub.ensure_schema(conn)
    clamped = max(1, min(int(limit or 50), 200))
    rows = conn.execute(
        """
        SELECT * FROM task_hub_items
        WHERE source_kind = 'tutorial_build'
          AND status = ?
          AND agent_ready = 0
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (task_hub.TASK_STATUS_OPEN, clamped),
    ).fetchall()
    builds: list[dict[str, Any]] = []
    for raw in rows:
        item = task_hub.hydrate_item(dict(raw))
        label_set = {str(v).lower() for v in item.get("labels") or []}
        if "pending-approval" not in label_set:
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        builds.append(
            {
                "task_id": str(item.get("task_id") or ""),
                "title": str(item.get("title") or ""),
                "video_id": str(metadata.get("video_id") or item.get("source_ref") or ""),
                "video_title": str(metadata.get("video_title") or ""),
                "video_url": str(metadata.get("video_url") or ""),
                "channel_name": str(metadata.get("channel_name") or ""),
                "approval_state": str(metadata.get("approval_state") or "pending_approval"),
                "priority": int(item.get("priority") or 0),
                "score": float(item.get("score") or 0.0),
                "created_at": str(item.get("created_at") or ""),
            }
        )
    return builds


def approve_pending_tutorial_build(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    agent_id: str = "dashboard_operator",
) -> dict[str, Any]:
    """Operator approve path for a pending-approval tutorial build (P2b).

    Validates the row is a P2a pending-approval tutorial_build row, swaps the
    ``pending-approval`` label for ``agent-ready`` (keeping the
    ``upsert_item`` label OR-fallback consistent with ``agent_ready=True``),
    stamps the approval audit trail in metadata, then promotes + claims via
    the canonical ``dispatch_service.dispatch_on_approval`` one-field flip
    (``agent_ready`` 0 -> 1).

    Manual approvals are deliberately UNCAPPED: this path never consults
    ``_daily_build_ceiling`` — the ceiling only throttles *auto*-dispatch in
    ``sync_build_oriented_csi_videos``.

    Raises ``DispatchError`` when the row is missing, not a tutorial build,
    terminal, or not pending approval.
    """
    from universal_agent.services.dispatch_service import (
        DispatchError,
        dispatch_on_approval,
    )

    clean_task_id = str(task_id or "").strip()
    if not clean_task_id:
        raise DispatchError("task_id is required")
    item = task_hub.get_item(conn, clean_task_id)
    if not item:
        raise DispatchError(f"Task {clean_task_id!r} not found")
    if str(item.get("source_kind") or "") != "tutorial_build":
        raise DispatchError(f"Task {clean_task_id!r} is not a tutorial build")
    current_status = str(item.get("status") or "").lower()
    if current_status in task_hub.TERMINAL_STATUSES:
        raise DispatchError(
            f"Task {clean_task_id!r} is in terminal status={current_status!r}, cannot approve"
        )
    label_set = {str(v).lower() for v in item.get("labels") or []}
    if bool(item.get("agent_ready")) or "pending-approval" not in label_set:
        raise DispatchError(f"Task {clean_task_id!r} is not pending approval")

    promoted_labels = [
        v for v in (item.get("labels") or []) if str(v).lower() != "pending-approval"
    ]
    if task_hub.TASK_LABEL_AGENT_READY not in {str(v).lower() for v in promoted_labels}:
        promoted_labels.insert(0, task_hub.TASK_LABEL_AGENT_READY)
    task_hub.upsert_item(
        conn,
        {
            "task_id": clean_task_id,
            "labels": promoted_labels,
            "metadata": {
                "approval_state": "approved",
                "approved_by": agent_id,
                "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        },
    )
    return dispatch_on_approval(conn, clean_task_id, agent_id=agent_id)


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
    """Build the full task description for a CODIE tutorial build."""
    plan_json = json.dumps(extraction_plan or {}, indent=2, ensure_ascii=True)
    base = "\n".join(
        [
            "Cody should build a runnable demo of this video's capability — a standalone mini-app, not a line-by-line reproduction of the video's tutorial.",
            "",
            f"Source video: {video_title}",
            f"Channel: {channel_name or '(unknown)'}",
            f"URL: {video_url or '(none)'}",
            "",
            "Implementation extraction plan:",
            plan_json,
            "",
            DEMO_BUILD_CONTRACT.rstrip(),
            "",
            "Instructions:",
            "1. Create a complete working implementation in a clean repo/workspace.",
            "2. The GitHub repo must be private by default if pushed.",
            "3. Public publication is not allowed without explicit Kevin approval.",
            "4. Include README run commands, source video attribution, and any adaptations.",
            "5. Use the API credentials the runtime provides (e.g. "
            "ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN for Claude-SDK demos "
            "route to live inference). Do NOT mock the capability the demo "
            "is meant to demonstrate — if a required key is unavailable the "
            "demo is NOT done; report the missing credential rather than "
            "mocking it.",
            "6. Run the implementation or the most relevant tests before declaring success.",
            "7. If GitHub is unavailable, preserve a complete local git repo artifact and report the fallback.",
            "8. Author manifest.json + a README 'Run' section per the Demo build contract above — the parent worker mechanically checks both at finalize.",
            "",
            "Dispatcher note (Simone): when delegating this task to Cody via vp_dispatch_mission, include this description VERBATIM in the mission objective — the Demo build contract above is binding for the build.",
        ]
    )
    if preference_context:
        base = f"{base}\n\nPreference context:\n{preference_context}"
    return base


def _preference_context(conn: sqlite3.Connection, *, task_type: str, topic_tags: list[str]) -> str:
    """Fetch preference delegation context, returning empty string on failure."""
    try:
        from universal_agent.services.proactive_preferences import (
            get_delegation_context,
        )

        return get_delegation_context(conn, task_type=task_type, topic_tags=topic_tags)
    except Exception:
        return ""


def _build_artifact_summary(metadata: dict[str, Any]) -> str:
    """Create a one-line artifact summary from build metadata."""
    location = metadata.get("repo_url") or metadata.get("artifact_path") or "artifact"
    status = metadata.get("build_status") or "success"
    return f"Tutorial build {status}; final work product: {location}"


def _looks_build_oriented(*, subject: dict[str, Any], analysis: dict[str, Any], category: str, summary: str) -> bool:
    """Cheap, surgical PREFILTER for the tutorial-build auto-route.

    This is intentionally narrow — it only rejects categories and tokens that
    are clearly non-code (news, drama, podcast, vlog, …). For anything that
    survives this gate, the real decision is made by
    ``is_video_buildable_with_judge`` which reads CSI's Claude-distilled
    transcript summary and asks the LLM whether this is something CODIE could
    actually build a working code demo from.

    Returning ``True`` here is necessary but not sufficient — the caller must
    still consult the LLM judge before queueing.
    """
    cat = str(category or analysis.get("category") or "").strip().lower().replace(" ", "_")
    if cat in _BLOCKED_CATEGORIES:
        return False

    title_tokens = _tokenize(str(subject.get("title") or ""))
    description_tokens = _tokenize(str(subject.get("description") or ""))
    summary_tokens = _tokenize(str(summary or ""))
    if (title_tokens | description_tokens | summary_tokens) & _NEGATIVE_TOKENS:
        return False

    return True


def _tokenize(text: str) -> set[str]:
    """Return word-boundary lowercase tokens — avoids substring false positives."""
    return set(_TOKEN_RE.findall((text or "").lower()))


def _ensure_judge_table(conn: sqlite3.Connection) -> None:
    """Idempotently create the per-video buildability verdict cache."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tutorial_build_judge (
            video_id TEXT PRIMARY KEY,
            buildable INTEGER NOT NULL,
            reasoning TEXT,
            method TEXT,
            judged_at TEXT NOT NULL
        )
        """
    )


def _get_cached_judge_verdict(conn: sqlite3.Connection, video_id: str) -> dict[str, Any] | None:
    """Return cached verdict for a video_id, or None on miss."""
    _ensure_judge_table(conn)
    row = conn.execute(
        "SELECT buildable, reasoning, method, judged_at FROM tutorial_build_judge WHERE video_id = ?",
        (video_id,),
    ).fetchone()
    if row is None:
        return None
    # ``no_summary`` is NOT a real verdict — it means the transcript summary
    # hadn't been analyzed yet when we first saw this video (the normal
    # ingestion->analysis lag). Treat such rows as a cache MISS so the video is
    # re-judged once the summary is backfilled, instead of being permanently
    # locked out. Without this, a verdict written during the race short-circuits
    # the LLM judge on every later sweep even after the summary lands — which is
    # exactly how the production cache ended up 534/534 ``no_summary`` with zero
    # buildable candidates ever produced.
    if str(row[2] or "") == "no_summary":
        return None
    return {
        "buildable": bool(row[0]),
        "reasoning": row[1] or "",
        "method": row[2] or "",
        "judged_at": row[3] or "",
    }


def _cache_judge_verdict(
    conn: sqlite3.Connection,
    *,
    video_id: str,
    buildable: bool,
    reasoning: str,
    method: str,
) -> None:
    """Write a verdict to the cache. Last-write wins for re-judged videos."""
    _ensure_judge_table(conn)
    conn.execute(
        """
        INSERT INTO tutorial_build_judge (video_id, buildable, reasoning, method, judged_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            buildable = excluded.buildable,
            reasoning = excluded.reasoning,
            method = excluded.method,
            judged_at = excluded.judged_at
        """,
        (
            video_id,
            1 if buildable else 0,
            reasoning,
            method,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()


def is_video_buildable_with_judge(
    conn: sqlite3.Connection,
    *,
    video_id: str,
    title: str,
    channel_name: str,
    summary_text: str,
) -> bool:
    """Return True iff the LLM judge confirms this video is a code-build candidate.

    Caches verdicts per video_id. Falls back to ``False`` when the LLM is
    unavailable — better to skip than to misroute. When the summary is empty
    (no transcript signal), also returns ``False`` without an LLM call.
    """
    clean_summary = str(summary_text or "").strip()
    if not clean_summary:
        # No transcript summary yet — almost always the normal ingestion->analysis
        # lag (the sweep saw this CSI event before its summary was written). Skip
        # WITHOUT caching a terminal verdict so the next sweep re-judges once the
        # summary lands. (Mirrors the non-caching ``_judge_disabled`` path below;
        # caching here was what permanently starved the tutorial-build lane.)
        logger.debug(
            "tutorial-build judge: no summary yet for %s; skipping without caching (will re-judge later)",
            video_id,
        )
        return False

    cached = _get_cached_judge_verdict(conn, video_id)
    if cached is not None:
        return cached["buildable"]

    if _judge_disabled():
        # Without an LLM available we'd rather skip than misroute. Don't cache
        # this verdict — re-judge once the LLM is back online.
        logger.info("tutorial-build LLM judge disabled; skipping %s", video_id)
        return False

    try:
        from universal_agent.services.llm_classifier import (
            classify_tutorial_buildability,
        )

        verdict = asyncio.run(
            classify_tutorial_buildability(
                title=title,
                channel_name=channel_name,
                summary_text=clean_summary,
            )
        )
    except Exception as exc:  # noqa: BLE001 — any failure means skip, never misroute
        logger.warning("tutorial-build LLM judge failed for %s: %s", video_id, exc)
        return False

    buildable = bool(verdict.get("buildable"))
    method = str(verdict.get("method") or "llm")
    reasoning = str(verdict.get("reasoning") or "")
    if method == "fallback":
        # Fallback means the LLM call itself failed inside the classifier;
        # don't cache so we'll retry next sync.
        return False
    _cache_judge_verdict(
        conn,
        video_id=video_id,
        buildable=buildable,
        reasoning=reasoning,
        method=method,
    )
    return buildable


def _judge_disabled() -> bool:
    """Allow tests / ops to bypass the LLM judge entirely."""
    raw = str(os.getenv("UA_TUTORIAL_BUILD_JUDGE_ENABLED", "1") or "1").strip().lower()
    return raw in {"0", "false", "no", "off"}


def _extraction_plan_from_analysis(*, analysis: dict[str, Any], row: Any) -> dict[str, Any]:
    """Derive an implementation extraction plan from CSI analysis fields."""
    return {
        "language": str(analysis.get("language") or analysis.get("primary_language") or "unknown"),
        "estimated_complexity": str(analysis.get("estimated_complexity") or "unknown"),
        "dependencies": analysis.get("dependencies") if isinstance(analysis.get("dependencies"), list) else [],
        "implementation_steps": analysis.get("implementation_steps") if isinstance(analysis.get("implementation_steps"), list) else [],
        "summary": str(row["summary_text"] or ""),
        "category": str(row["category"] or analysis.get("category") or ""),
    }


def _auto_route_disabled() -> bool:
    """Return True when tutorial auto-routing is explicitly disabled via env var."""
    raw = str(os.getenv("UA_PROACTIVE_TUTORIAL_AUTO_ROUTE", "1") or "1").strip().lower()
    return raw in {"0", "false", "no", "off"}


_DEFAULT_DAILY_BUILD_CEILING = 10


def _daily_build_ceiling() -> int:
    """Max tutorial builds auto-dispatched per America/Chicago day.

    Reads ``UA_DEMO_BUILD_DAILY_CEILING`` (default 10), clamped to >= 0. A
    ceiling of 0 means every buildable candidate is queued as pending-approval
    (nothing auto-dispatches) until an operator promotes it.
    """
    raw = str(os.getenv("UA_DEMO_BUILD_DAILY_CEILING", "") or "").strip()
    if not raw:
        return _DEFAULT_DAILY_BUILD_CEILING
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return _DEFAULT_DAILY_BUILD_CEILING


def _count_today_tutorial_builds(conn: sqlite3.Connection) -> int:
    """Count tutorial_build rows created since America/Chicago local midnight.

    ``task_hub_items.created_at`` is always ``task_hub._now_iso()`` — a
    fixed-width UTC ISO-8601 string with a ``+00:00`` offset — so the day
    boundary is computed in local (Houston) time, converted to the same UTC
    ``+00:00`` form, and compared lexicographically (valid because both strings
    are zero-padded ISO UTC).
    """
    local_now = datetime.now(ZoneInfo("America/Chicago"))
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_utc_iso = local_midnight.astimezone(timezone.utc).isoformat()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM task_hub_items WHERE source_kind = ? AND created_at >= ?",
            ("tutorial_build", day_start_utc_iso),
        ).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0] or 0) if row else 0


def _existing_build_is_dispatchable(conn: sqlite3.Connection, video_id: str) -> bool:
    """True if this video already has a dispatchable (agent_ready) build row.

    Guards the no-churn invariant: an already-approved/dispatched build must not
    be demoted back to pending when a later over-ceiling sweep re-encounters it.
    """
    clean = str(video_id or "").strip()
    if not clean:
        return False
    task_id = f"tutorial-build:{hashlib.sha256(clean.encode()).hexdigest()[:16]}"
    try:
        existing = task_hub.get_item(conn, task_id)
    except Exception:  # noqa: BLE001 — a lookup failure must not block queueing
        return False
    return bool(existing and existing.get("agent_ready"))


def _json_loads_obj(raw: Any) -> dict[str, Any]:
    """Parse a JSON object from raw text or return the input if already a dict."""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}
