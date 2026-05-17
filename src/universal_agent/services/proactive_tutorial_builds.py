"""Tutorial build automation helpers for proactive intelligence."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

import yaml

from universal_agent.services.proactive_artifacts import (
    ARTIFACT_STATUS_CANDIDATE,
    make_artifact_id,
    upsert_artifact,
)
from universal_agent.services.proactive_task_builder import queue_proactive_task

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DENYLIST_FILE = _REPO_ROOT / "state" / "tutorial_build_denylist.yaml"

# Categories CSI assigns that should never auto-route into tutorial-build.
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

# Word-boundary tokens. Substring matching previously tripped on "api" inside
# "rapid" / "Apia", letting news clips through.
_POSITIVE_TITLE_TOKENS = frozenset(
    {
        "tutorial",
        "walkthrough",
        "build",
        "building",
        "hands-on",
        "code",
        "coding",
        "python",
        "typescript",
        "javascript",
        "github",
        "repo",
        "mcp",
        "sdk",
        "api",
        "docker",
        "agent",
        "agents",
    }
)

_NEGATIVE_TOKENS = frozenset(
    {
        "reaction",
        "drama",
        "podcast",
        "vlog",
        "news",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+\-]*")


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
    task = queue_proactive_task(
        conn,
        task_id=task_id,
        source_kind="tutorial_build",
        source_ref=clean_video_id,
        title=f"Build private tutorial repo: {clean_title}",
        description=description,
        priority=priority or 3,
        labels=["agent-ready", "tutorial-build", "codie", "code"],
        metadata={
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


def sync_build_oriented_csi_videos(
    conn: sqlite3.Connection,
    *,
    csi_db_path: Path | None,
    limit: int = 200,
) -> dict[str, int]:
    """Queue CODIE tutorial build tasks for build-oriented CSI RSS videos."""
    if _auto_route_disabled() or csi_db_path is None or not csi_db_path.exists():
        return {"seen": 0, "queued": 0}
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
        return {"seen": 0, "queued": 0}
    finally:
        db.close()

    queued = 0
    for row in rows:
        subject = _json_loads_obj(row["subject_json"])
        analysis = _json_loads_obj(row["analysis_json"])
        if not _looks_build_oriented(subject=subject, analysis=analysis, category=str(row["category"] or ""), summary=str(row["summary_text"] or "")):
            continue
        video_id = str(subject.get("video_id") or row["event_id"] or "").strip()
        if not video_id:
            continue
        queue_tutorial_build_task(
            conn,
            video_id=video_id,
            video_title=str(subject.get("title") or subject.get("media_title") or video_id),
            video_url=str(subject.get("url") or ""),
            channel_name=str(subject.get("channel_name") or subject.get("author_name") or ""),
            source="csi_auto_route",
            extraction_plan=_extraction_plan_from_analysis(analysis=analysis, row=row),
            priority=3 if str(row["transcript_status"] or "").lower() == "ok" else 2,
        )
        queued += 1
    return {"seen": len(rows), "queued": queued}


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
    """Decide whether a CSI video should auto-route into tutorial-build.

    Multi-gate filter (any gate failing rejects):
      1. Category denylist (news/politics/etc).
      2. Channel denylist (self-learned from prior misroute parks).
      3. Extraction plan must not be structurally empty (nothing to build).
      4. No negative tokens (word-boundary) in title/description.
      5. Positive token must appear in the TITLE — description noise alone
         is too weak a signal and was the source of false positives.
    """
    cat = str(category or analysis.get("category") or "").strip().lower().replace(" ", "_")
    if cat in _BLOCKED_CATEGORIES:
        return False

    channel = str(subject.get("channel_name") or subject.get("author_name") or "").strip()
    if channel and _channel_in_denylist(channel):
        return False

    if _extraction_plan_is_empty(analysis):
        return False

    title_tokens = _tokenize(str(subject.get("title") or ""))
    description_tokens = _tokenize(str(subject.get("description") or ""))
    summary_tokens = _tokenize(str(summary or ""))
    all_tokens = title_tokens | description_tokens | summary_tokens

    if all_tokens & _NEGATIVE_TOKENS:
        return False

    return bool(title_tokens & _POSITIVE_TITLE_TOKENS)


def _tokenize(text: str) -> set[str]:
    """Return word-boundary lowercase tokens — avoids substring false positives."""
    return set(_TOKEN_RE.findall((text or "").lower()))


def _extraction_plan_is_empty(analysis: dict[str, Any]) -> bool:
    """True when CSI tried to extract a build plan and got nothing.

    Only fires when ``language`` / ``primary_language`` is *explicitly* present
    and set to a degenerate value (e.g. ``"unknown"``) AND there are no
    dependencies AND no implementation steps. If those keys are simply absent
    from the analysis (e.g. the analyzer hasn't been run on this video, or a
    different analysis schema is in use), we don't penalize — other gates
    (category, channel, title tokens) carry the load.
    """
    if not isinstance(analysis, dict):
        return False
    has_language_key = "language" in analysis or "primary_language" in analysis
    if not has_language_key:
        return False
    language = str(analysis.get("language") or analysis.get("primary_language") or "").strip().lower()
    deps = analysis.get("dependencies")
    steps = analysis.get("implementation_steps")
    has_deps = isinstance(deps, list) and len(deps) > 0
    has_steps = isinstance(steps, list) and len(steps) > 0
    return language in {"", "unknown", "n/a", "none"} and not has_deps and not has_steps


def _load_denylist_channels() -> list[str]:
    """Load the channel denylist YAML, swallowing IO/parse errors."""
    if not _DENYLIST_FILE.exists():
        return []
    try:
        data = yaml.safe_load(_DENYLIST_FILE.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("tutorial-build denylist load failed: %s", exc)
        return []
    if not isinstance(data, dict):
        return []
    channels = data.get("channels")
    if not isinstance(channels, list):
        return []
    return [str(c).strip() for c in channels if str(c or "").strip()]


def _channel_in_denylist(channel: str) -> bool:
    """Case-insensitive substring match against the denylist."""
    if not channel:
        return False
    needle = channel.lower()
    for entry in _load_denylist_channels():
        e = entry.lower()
        if e and (e in needle or needle in e):
            return True
    return False


def record_channel_denylist_entry(channel_name: str, *, reason: str = "") -> bool:
    """Append a channel to the tutorial-build denylist. Idempotent.

    Call this when parking a tutorial-build task as a non-code misroute so the
    same channel never re-routes. Returns True if the entry was added (or
    already present), False on IO error.
    """
    clean = str(channel_name or "").strip()
    if not clean:
        return False
    try:
        _DENYLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing = _load_denylist_channels()
        if any(clean.lower() == e.lower() for e in existing):
            return True
        existing.append(clean)
        header = (
            "# Channels that have produced misrouted tutorial-build tasks.\n"
            "# Appended automatically when Simone parks a tutorial_build task as\n"
            "# a non-code misroute. Match is case-insensitive substring.\n"
        )
        body = "channels:\n" + "".join(f"  - {json.dumps(c)}\n" for c in existing)
        _DENYLIST_FILE.write_text(header + body, encoding="utf-8")
        logger.info("Added %s to tutorial-build denylist (reason=%s)", clean, reason or "unspecified")
        return True
    except OSError as exc:
        logger.warning("Failed to write tutorial-build denylist: %s", exc)
        return False


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
