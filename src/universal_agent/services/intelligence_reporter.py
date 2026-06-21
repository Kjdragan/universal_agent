"""Compose and send proactive intelligence review emails."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import html
import os
import re
import sqlite3
from typing import Any

from universal_agent import task_hub
from universal_agent.services import proactive_artifacts
from universal_agent.services.email_tags import ActionTag, KindTag
from universal_agent.services.proactive_preferences import (
    build_weekly_preference_report,
    score_artifact_for_review,
)


@dataclass(frozen=True)
class ReviewEmailPayload:
    """Rendered email ready for delivery via a mail service."""

    artifact_id: str
    to: str
    subject: str
    text: str
    html: str


class IntelligenceReporter:
    """Composes review-oriented emails for proactive artifacts."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Initialize the intelligence reporter."""
        self._conn = conn
        proactive_artifacts.ensure_schema(conn)

    def compose_review_email(self, *, artifact_id: str, recipient: str) -> ReviewEmailPayload:
        """Build a review email payload for a single proactive artifact.

        Raises KeyError if the artifact_id does not exist.
        """
        artifact = proactive_artifacts.get_artifact(self._conn, artifact_id)
        if artifact is None:
            raise KeyError(artifact_id)
        subject = f"{self._subject_prefix(artifact)} {artifact['title']} [{artifact_id}]"
        text = self._compose_text(artifact)
        html_body = self._compose_html(artifact, text)
        return ReviewEmailPayload(
            artifact_id=artifact_id,
            to=recipient,
            subject=subject,
            text=text,
            html=html_body,
        )

    def compose_daily_digest(
        self,
        *,
        recipient: str,
        limit: int = 12,
        calendar_events: list[dict[str, Any]] | None = None,
    ) -> ReviewEmailPayload:
        """Build a daily digest email from the top-ranked review candidates.

        Syncs proactive signal cards and work items, ranks candidates by
        preference score, and renders a digest payload with optional
        calendar context.
        """
        artifacts = self._rank_digest_artifacts(limit=max(1, int(limit or 12)))
        today = datetime.now(timezone.utc).date().isoformat()
        title = f"Daily proactive review digest - {today}"
        digest = proactive_artifacts.upsert_artifact(
            self._conn,
            artifact_id=proactive_artifacts.make_artifact_id(
                source_kind="runtime",
                source_ref=today,
                artifact_type="daily_digest",
                title=title,
            ),
            artifact_type="daily_digest",
            source_kind="runtime",
            source_ref=today,
            title=title,
            summary=f"{len(artifacts)} proactive review candidate(s) are ready.",
            status=proactive_artifacts.ARTIFACT_STATUS_CANDIDATE,
            priority=5,
            metadata={"included_artifact_ids": [artifact["artifact_id"] for artifact in artifacts]},
        )
        subject = f"[UA Digest] Proactive review candidates - {today} [{digest['artifact_id']}]"
        text = self._compose_digest_text(artifacts, calendar_events=calendar_events or [])
        html_body = self._compose_html(digest, text)
        return ReviewEmailPayload(
            artifact_id=str(digest["artifact_id"]),
            to=recipient,
            subject=subject,
            text=text,
            html=html_body,
        )

    def compose_weekly_preference_report(self, *, recipient: str) -> ReviewEmailPayload:
        """Build a weekly preference model summary email.

        Aggregates the week's positive and negative feedback signals into
        a human-readable report and wraps it in a delivery payload.
        """
        report = build_weekly_preference_report(self._conn)
        today = datetime.now(timezone.utc).date().isoformat()
        title = f"Weekly preference model update - {today}"
        artifact = proactive_artifacts.upsert_artifact(
            self._conn,
            artifact_id=proactive_artifacts.make_artifact_id(
                source_kind="preference_model",
                source_ref=today,
                artifact_type="weekly_preference_report",
                title=title,
            ),
            artifact_type="weekly_preference_report",
            source_kind="preference_model",
            source_ref=today,
            title=title,
            summary="Weekly summary of learned proactive intelligence preferences.",
            status=proactive_artifacts.ARTIFACT_STATUS_CANDIDATE,
            priority=4,
            topic_tags=["preferences", "weekly-report"],
            metadata={"positive": report["positive"], "negative": report["negative"]},
        )
        subject = f"[UA Weekly] Preference Model Update - {today} [{artifact['artifact_id']}]"
        text = str(report["report_text"])
        html_body = self._compose_html(artifact, text)
        return ReviewEmailPayload(
            artifact_id=str(artifact["artifact_id"]),
            to=recipient,
            subject=subject,
            text=text,
            html=html_body,
        )

    async def send_review_email(
        self,
        *,
        artifact_id: str,
        recipient: str,
        mail_service: Any,
    ) -> dict[str, Any]:
        """Compose and send a review email, then record the delivery.

        Delegates to ``compose_review_email`` for rendering, sends via
        *mail_service*, and persists the delivery metadata.
        """
        payload = self.compose_review_email(artifact_id=artifact_id, recipient=recipient)
        result = await mail_service.send_email(
            to=payload.to,
            subject=payload.subject,
            text=payload.text,
            html=payload.html,
            force_send=True,
            require_approval=False,
            action=ActionTag.DECISION,
            kind=KindTag.PROACTIVE,
            source="intelligence_reporter.send_review_email",
            related=[f"artifact_id={artifact_id}"],
        )
        proactive_artifacts.record_email_delivery(
            self._conn,
            artifact_id=artifact_id,
            message_id=str((result or {}).get("message_id") or ""),
            thread_id=str((result or {}).get("thread_id") or ""),
            subject=payload.subject,
            recipient=recipient,
            metadata={"mail_status": str((result or {}).get("status") or "")},
        )
        return dict(result or {})

    async def send_daily_digest(
        self,
        *,
        recipient: str,
        mail_service: Any,
        limit: int = 12,
        calendar_events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Compose and send the daily digest, then record the delivery."""
        payload = self.compose_daily_digest(
            recipient=recipient,
            limit=limit,
            calendar_events=calendar_events or [],
        )
        result = await mail_service.send_email(
            to=payload.to,
            subject=payload.subject,
            text=payload.text,
            html=payload.html,
            force_send=True,
            require_approval=False,
            action=ActionTag.FYI,
            kind=KindTag.DIGEST,
            source="intelligence_reporter.send_daily_digest",
            related=[f"artifact_id={payload.artifact_id}"],
        )
        proactive_artifacts.record_email_delivery(
            self._conn,
            artifact_id=payload.artifact_id,
            message_id=str((result or {}).get("message_id") or ""),
            thread_id=str((result or {}).get("thread_id") or ""),
            subject=payload.subject,
            recipient=recipient,
            metadata={"mail_status": str((result or {}).get("status") or ""), "digest": True},
        )
        return dict(result or {})

    async def send_weekly_preference_report(
        self,
        *,
        recipient: str,
        mail_service: Any,
    ) -> dict[str, Any]:
        """Compose and send the weekly preference report, then record the delivery."""
        payload = self.compose_weekly_preference_report(recipient=recipient)
        result = await mail_service.send_email(
            to=payload.to,
            subject=payload.subject,
            text=payload.text,
            html=payload.html,
            force_send=True,
            require_approval=False,
            action=ActionTag.FYI,
            kind=KindTag.DIGEST,
            source="intelligence_reporter.send_weekly_preference_report",
            related=[f"artifact_id={payload.artifact_id}"],
        )
        proactive_artifacts.record_email_delivery(
            self._conn,
            artifact_id=payload.artifact_id,
            message_id=str((result or {}).get("message_id") or ""),
            thread_id=str((result or {}).get("thread_id") or ""),
            subject=payload.subject,
            recipient=recipient,
            metadata={"mail_status": str((result or {}).get("status") or ""), "weekly_preference_report": True},
        )
        return dict(result or {})

    def _subject_prefix(self, artifact: dict[str, Any]) -> str:
        artifact_type = str(artifact.get("artifact_type") or "").strip().lower()
        if artifact_type in {"tutorial_build", "private_repo"}:
            return "[UA Build Review]"
        if artifact_type in {"codie_pr", "pull_request"}:
            return "[UA PR Review]"
        if artifact_type in {"daily_digest", "digest"}:
            return "[UA Digest]"
        if artifact_type == "weekly_preference_report":
            return "[UA Weekly]"
        return "[Simone Review]"

    def _compose_text(self, artifact: dict[str, Any]) -> str:
        title = str(artifact.get("title") or "Proactive artifact").strip()
        summary = str(artifact.get("summary") or "").strip()
        artifact_link = _artifact_link(artifact)
        source_url = str(artifact.get("source_url") or "").strip()
        task_context = self._artifact_task_context(artifact)
        lines = [
            "I made this proactively because I think it may be useful. Please review when convenient.",
            "",
            f"Title: {title}",
        ]
        if summary:
            lines.extend(["", summary])
        if task_context:
            lines.extend(_task_context_lines(task_context))
        if artifact_link:
            lines.extend(["", f"Final work product: {artifact_link}"])
        if source_url:
            lines.append(f"Source: {source_url}")
        lines.extend(
            [
                "",
                "Quick feedback: reply with one number plus optional comments.",
                "1 useful",
                "2 interesting but not now",
                "3 not relevant",
                "4 wrong direction",
                "5 more like this",
                "",
                "No reply is fine; I will keep producing review candidates.",
            ]
        )
        return "\n".join(lines)

    def _compose_html(self, artifact: dict[str, Any], text: str) -> str:
        escaped = html.escape(text).replace("\n", "<br>")
        artifact_link = _artifact_link(artifact)
        if artifact_link:
            safe_link = html.escape(artifact_link, quote=True)
            escaped = escaped.replace(
                html.escape(f"Final work product: {artifact_link}"),
                f'Final work product: <a href="{safe_link}">{safe_link}</a>',
            )
        return f"<html><body><p>{escaped}</p></body></html>"

    def _rank_digest_artifacts(self, *, limit: int) -> list[dict[str, Any]]:
        proactive_artifacts.sync_from_proactive_signal_cards(self._conn)
        self._sync_from_proactive_work_items()
        artifacts = proactive_artifacts.list_artifacts(self._conn, limit=250)
        candidates = [
            artifact
            for artifact in artifacts
            if artifact.get("artifact_type") != "daily_digest"
            and artifact.get("status") not in {proactive_artifacts.ARTIFACT_STATUS_ARCHIVED}
            # Staleness backstop: drop days-old candidates (e.g. already
            # merged/closed PRs) without a synchronous per-item gh lookup.
            and not _is_stale_candidate(artifact)
        ]
        scored = [
            (artifact, float(score_artifact_for_review(self._conn, artifact)))
            for artifact in candidates
        ]
        scored.sort(
            key=lambda pair: (pair[1], str(pair[0].get("updated_at") or "")),
            reverse=True,
        )
        # Collapse duplicates that point at the same underlying work target
        # (e.g. a demo mirrored as both cody_demo_task and proactive_work_item).
        deduped = _dedup_candidates(scored)
        return [artifact for artifact, _score in deduped][:limit]

    def _compose_digest_text(
        self,
        artifacts: list[dict[str, Any]],
        *,
        calendar_events: list[dict[str, Any]],
    ) -> str:
        lines = [
            "I made these proactively because I think some may be useful. Please review when convenient.",
            "",
            f"Review candidates: {len(artifacts)}",
        ]
        if calendar_events:
            lines.extend(["", "Calendar context:"])
            for event in calendar_events[:8]:
                title = str(event.get("summary") or event.get("title") or "(untitled)").strip()
                start = str(event.get("start") or event.get("event_start") or "").strip()
                lines.append(f"- {start} {title}".strip())
        if not artifacts:
            lines.extend(["", "No proactive work products are waiting for review right now."])
        for index, artifact in enumerate(artifacts, start=1):
            link = _artifact_link(artifact)
            task_context = self._artifact_task_context(artifact)
            # Prefer the recap's success_assessment over the raw description, then
            # scrub any BRIEF/ACCEPTANCE boilerplate and conversational tails.
            recap_for_summary = (
                task_context.get("recap") if isinstance(task_context.get("recap"), dict) else {}
            )
            summary = _sanitize_summary(
                str(recap_for_summary.get("success_assessment") or "").strip()
                or str(artifact.get("summary") or "").strip()
            )
            lines.extend(
                [
                    "",
                    f"{index}. {artifact.get('title') or '(untitled)'}",
                    f"   Type: {artifact.get('artifact_type') or 'artifact'}",
                ]
            )
            if summary:
                lines.append(f"   Summary: {summary}")
            if task_context:
                recap = task_context.get("recap") if isinstance(task_context.get("recap"), dict) else {}
                assessment = str(recap.get("success_assessment") or "").strip()
                next_action = str(recap.get("recommended_next_action") or "").strip()
                audit_url = str(task_context.get("dashboard_url") or "").strip()
                if assessment:
                    lines.append(f"   Assessment: {assessment}")
                if next_action:
                    lines.append(f"   Next: {next_action}")
                if audit_url:
                    lines.append(f"   Audit: {audit_url}")
            if link:
                lines.append(f"   Final work product: {link}")
        lines.extend(
            [
                "",
                "Quick feedback: reply with one number plus optional comments.",
                "1 useful",
                "2 interesting but not now",
                "3 not relevant",
                "4 wrong direction",
                "5 more like this",
                "",
                "No reply is fine; I will keep producing review candidates.",
            ]
        )
        return "\n".join(lines)

    def _sync_from_proactive_work_items(self, *, limit: int = 250) -> dict[str, int]:
        try:
            tasks = task_hub.list_proactive_work_tasks(self._conn, limit=limit)
        except Exception:
            return {"seen": 0, "upserted": 0}
        upserted = 0
        for item in tasks:
            status = str(item.get("status") or "").strip().lower()
            if status not in {
                task_hub.TASK_STATUS_COMPLETED,
                task_hub.TASK_STATUS_BLOCKED,
                task_hub.TASK_STATUS_REVIEW,
                task_hub.TASK_STATUS_PENDING_REVIEW,
                task_hub.TASK_STATUS_PARKED,
            }:
                continue
            task_id = str(item.get("task_id") or "").strip()
            if not task_id:
                continue
            recap = self._task_recap(task_id)
            summary = (
                str(recap.get("success_assessment") or "").strip()
                or str(item.get("description") or "").strip()
                or str((item.get("last_assignment") or {}).get("result_summary") or "").strip()
            )
            metadata = {
                "task_id": task_id,
                "source_kind": item.get("source_kind"),
                "status": status,
                "stage": _task_stage(item),
                "recap": recap,
                "session_id": str((item.get("last_assignment") or {}).get("session_id") or ""),
                "workspace_dir": str((item.get("last_assignment") or {}).get("workspace_dir") or ""),
                "dashboard_url": _dashboard_task_url(task_id),
            }
            artifact_id = proactive_artifacts.make_artifact_id(
                source_kind=str(item.get("source_kind") or "proactive_task"),
                source_ref=task_id,
                artifact_type="proactive_work_item",
                title=str(item.get("title") or ""),
            )
            existing = proactive_artifacts.get_artifact(self._conn, artifact_id)
            artifact_status = proactive_artifacts.ARTIFACT_STATUS_CANDIDATE
            delivery_state = proactive_artifacts.DELIVERY_NOT_SURFACED
            if existing:
                existing_status = str(existing.get("status") or "").strip()
                if existing_status in proactive_artifacts.VALID_ARTIFACT_STATUSES:
                    artifact_status = existing_status
                existing_delivery = str(existing.get("delivery_state") or "").strip()
                if existing_delivery in proactive_artifacts.VALID_DELIVERY_STATES:
                    delivery_state = existing_delivery
            proactive_artifacts.upsert_artifact(
                self._conn,
                artifact_id=artifact_id,
                artifact_type="proactive_work_item",
                source_kind=str(item.get("source_kind") or "proactive_task"),
                source_ref=task_id,
                title=str(item.get("title") or "Proactive work item"),
                summary=summary[:1200],
                status=artifact_status,
                delivery_state=delivery_state,
                priority=max(1, int(item.get("priority") or 2)),
                source_url=_dashboard_task_url(task_id),
                topic_tags=["proactive-work", str(item.get("source_kind") or "")],
                metadata=metadata,
            )
            upserted += 1
        return {"seen": len(tasks), "upserted": upserted}

    def _artifact_task_context(self, artifact: dict[str, Any]) -> dict[str, Any]:
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
        task_id = str(metadata.get("task_id") or "").strip()
        if not task_id:
            source_ref = str(artifact.get("source_ref") or "").strip()
            task = task_hub.get_item(self._conn, source_ref) if source_ref else None
            task_id = str((task or {}).get("task_id") or "").strip()
        if not task_id:
            return {}
        task = task_hub.get_item(self._conn, task_id) or {}
        if not task:
            return {}
        recap = self._task_recap(task_id)
        return {
            "task_id": task_id,
            "title": str(task.get("title") or ""),
            "status": str(task.get("status") or ""),
            "source_kind": str(task.get("source_kind") or ""),
            "dashboard_url": str(metadata.get("dashboard_url") or _dashboard_task_url(task_id)),
            "recap": recap,
        }

    def _task_recap(self, task_id: str) -> dict[str, Any]:
        try:
            from universal_agent.services.proactive_work_recap import get_recap_for_task

            recap = get_recap_for_task(self._conn, task_id)
        except Exception:
            recap = None
        return dict(recap or {})


def _artifact_link(artifact: dict[str, Any]) -> str:
    for key in ("artifact_uri", "source_url", "artifact_path"):
        value = str(artifact.get(key) or "").strip()
        if value:
            return value
    return ""


def _dashboard_task_url(task_id: str) -> str:
    base = str(os.getenv("FRONTEND_URL") or "https://app.clearspringcg.com").rstrip("/")
    clean_task_id = str(task_id or "").strip()
    if not clean_task_id:
        return f"{base}/dashboard/proactive-task-history"
    return f"{base}/dashboard/proactive-task-history?task_id={clean_task_id}"


def _task_stage(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "").strip().lower()
    if status == task_hub.TASK_STATUS_COMPLETED:
        return "completed"
    if status in {task_hub.TASK_STATUS_IN_PROGRESS, task_hub.TASK_STATUS_DELEGATED}:
        return "running"
    if status in {
        task_hub.TASK_STATUS_BLOCKED,
        task_hub.TASK_STATUS_REVIEW,
        task_hub.TASK_STATUS_PENDING_REVIEW,
        task_hub.TASK_STATUS_PARKED,
        task_hub.TASK_STATUS_CANCELLED,
    }:
        return "needs_attention"
    return "queued"


def _task_context_lines(context: dict[str, Any]) -> list[str]:
    recap = context.get("recap") if isinstance(context.get("recap"), dict) else {}
    lines = ["", "Task audit:"]
    lines.append(f"- Task: {context.get('title') or context.get('task_id')}")
    lines.append(f"- Status: {context.get('status') or 'unknown'}")
    audit_url = str(context.get("dashboard_url") or "").strip()
    if audit_url:
        lines.append(f"- Proactive history: {audit_url}")
    implemented = str(recap.get("implemented") or "").strip()
    issues = str(recap.get("known_issues") or "").strip()
    assessment = str(recap.get("success_assessment") or "").strip()
    next_action = str(recap.get("recommended_next_action") or "").strip()
    if implemented:
        lines.append(f"- Implemented: {implemented}")
    if issues:
        lines.append(f"- Known issues: {issues}")
    if assessment:
        lines.append(f"- Assessment: {assessment}")
    if next_action:
        lines.append(f"- Recommended next action: {next_action}")
    return lines


# ── Digest hygiene: dedup, staleness, chatter strip ─────────────────────────
# The daily proactive-review digest historically built candidates with a single
# filter (drop daily_digest + archived). That let the same underlying work
# surface twice (a demo mirrored as both ``cody_demo_task`` and
# ``proactive_work_item`` — distinct make_artifact_id seeds → two rows), let
# days-old already-merged/closed PRs ride along as "fresh" candidates, and
# leaked raw chatter (BRIEF.md instruction blocks, conversational tails) into
# the summary. These helpers are deterministic and DB-free so they are unit
# testable in isolation.

# Demo workspace slugs look like ``opus-port-rust-to-ts__demo-1`` — the part
# before the trailing ``__demo-N`` (or ``__iter-N``) identifies the work target
# across iterations / sync paths.
_DEMO_SLUG_SUFFIX_RE = re.compile(r"__(?:demo|iter|iteration)-\d+$", re.IGNORECASE)

# Per-type preference order when collapsing duplicates: the task-typed demo
# artifact is richer than the generic proactive_work_item mirror of the same
# task, so prefer it on a score tie.
_DEDUP_TYPE_PREFERENCE = {
    "cody_demo_task": 2,
}

_STALE_MAX_AGE_DAYS = 7

# Conversational / instructional lines that must never leak into the digest.
_CHATTER_LINE_RE = re.compile(
    r"^\s*(?:want me to .*\?|should i .*\?|let me know\b.*|shall i .*\?|"
    r"do you want me to .*\?|would you like me to .*\?)\s*$",
    re.IGNORECASE,
)

# Leading instruction-boilerplate headers emitted by BRIEF.md-style prompts.
_BRIEF_BOILERPLATE_RE = re.compile(
    r"^\s*(?:#+\s*)?(?:brief|acceptance(?:\s+criteria)?|task|objective|"
    r"instructions?|context|deliverables?)\s*:?\s*$",
    re.IGNORECASE,
)


def _dedup_key_for_artifact(artifact: dict[str, Any]) -> str:
    """Stable key identifying the underlying work target.

    Prefers ``metadata.task_id`` (shared across the cody_demo_task mirror and the
    proactive_work_item sync of the same Task Hub item). Falls back to the demo
    workspace slug parsed from ``source_ref`` (collapsing iterations of the same
    demo), then to the artifact_id (no collapse).
    """
    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
    task_id = str(metadata.get("task_id") or "").strip()
    if task_id:
        return f"task:{task_id}"
    source_ref = str(artifact.get("source_ref") or "").strip()
    if source_ref:
        slug = _DEMO_SLUG_SUFFIX_RE.sub("", source_ref).strip()
        if slug:
            return f"ref:{slug}"
    return f"id:{artifact.get('artifact_id') or id(artifact)}"


def _dedup_preference(artifact: dict[str, Any], score: float) -> tuple[float, int]:
    """Rank a duplicate: higher score wins; on a tie prefer the richer type."""
    type_rank = _DEDUP_TYPE_PREFERENCE.get(str(artifact.get("artifact_type") or "").strip(), 0)
    return (float(score), type_rank)


def _dedup_candidates(
    scored: list[tuple[dict[str, Any], float]],
) -> list[tuple[dict[str, Any], float]]:
    """Collapse candidates sharing a work-target key, keeping the best one.

    ``scored`` is a list of ``(artifact, score)``. Returns the kept subset in the
    same order they first appeared (preserving the caller's sort)."""
    best: dict[str, tuple[dict[str, Any], float]] = {}
    order: list[str] = []
    for artifact, score in scored:
        key = _dedup_key_for_artifact(artifact)
        if key not in best:
            best[key] = (artifact, score)
            order.append(key)
            continue
        kept_artifact, kept_score = best[key]
        if _dedup_preference(artifact, score) > _dedup_preference(kept_artifact, kept_score):
            best[key] = (artifact, score)
    return [best[key] for key in order]


def _parse_iso(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _pr_state_is_resolved(metadata: dict[str, Any]) -> bool:
    """True if metadata already carries a closed/merged PR state (no live lookup)."""
    if not isinstance(metadata, dict):
        return False
    for key in ("pr_state", "state", "pull_request_state", "merge_state", "status"):
        value = str(metadata.get(key) or "").strip().lower()
        if value in {"merged", "closed"}:
            return True
    for flag in ("merged", "is_merged", "closed", "is_closed"):
        if bool(metadata.get(flag)):
            return True
    return False


def _is_stale_candidate(
    artifact: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_days: int = _STALE_MAX_AGE_DAYS,
) -> bool:
    """Drop candidates that are stale by age, or carry a resolved PR state.

    Staleness is a backstop for already-merged/closed PRs (which are days old by
    the time the digest runs) — we deliberately avoid a synchronous gh lookup in
    this cron path. Uses the most recent of created_at/updated_at."""
    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
    if _pr_state_is_resolved(metadata):
        return True
    now = now or datetime.now(timezone.utc)
    stamps = [
        ts
        for ts in (_parse_iso(artifact.get("updated_at")), _parse_iso(artifact.get("created_at")))
        if ts is not None
    ]
    if not stamps:
        return False
    newest = max(stamps)
    return (now - newest).total_seconds() > max_age_days * 86400


def _sanitize_summary(summary: str) -> str:
    """Strip leading BRIEF/ACCEPTANCE boilerplate and trailing conversational tails."""
    text = str(summary or "").strip()
    if not text:
        return ""
    lines = text.splitlines()
    # Drop leading instruction-boilerplate header lines (and any blank lines).
    start = 0
    while start < len(lines):
        line = lines[start].strip()
        if not line or _BRIEF_BOILERPLATE_RE.match(line):
            start += 1
            continue
        break
    # Drop trailing conversational / blank lines.
    end = len(lines)
    while end > start:
        line = lines[end - 1].strip()
        if not line or _CHATTER_LINE_RE.match(line):
            end -= 1
            continue
        break
    return "\n".join(lines[start:end]).strip()
