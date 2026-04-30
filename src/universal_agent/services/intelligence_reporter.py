"""Compose and send proactive intelligence review emails."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import html
import os
import sqlite3
from typing import Any

from universal_agent import task_hub
from universal_agent.services import proactive_artifacts
from universal_agent.services.proactive_preferences import (
    build_weekly_preference_report,
    score_artifact_for_review,
)


@dataclass(frozen=True)
class ReviewEmailPayload:
    artifact_id: str
    to: str
    subject: str
    text: str
    html: str


class IntelligenceReporter:
    """Composes review-oriented emails for proactive artifacts."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        proactive_artifacts.ensure_schema(conn)

    def compose_review_email(self, *, artifact_id: str, recipient: str) -> ReviewEmailPayload:
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
        payload = self.compose_review_email(artifact_id=artifact_id, recipient=recipient)
        result = await mail_service.send_email(
            to=payload.to,
            subject=payload.subject,
            text=payload.text,
            html=payload.html,
            force_send=True,
            require_approval=False,
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
        payload = self.compose_weekly_preference_report(recipient=recipient)
        result = await mail_service.send_email(
            to=payload.to,
            subject=payload.subject,
            text=payload.text,
            html=payload.html,
            force_send=True,
            require_approval=False,
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
        ]
        candidates.sort(
            key=lambda artifact: (
                score_artifact_for_review(self._conn, artifact),
                str(artifact.get("updated_at") or ""),
            ),
            reverse=True,
        )
        return candidates[:limit]

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
            summary = str(artifact.get("summary") or "").strip()
            lines.extend(
                [
                    "",
                    f"{index}. {artifact.get('title') or '(untitled)'}",
                    f"   Type: {artifact.get('artifact_type') or 'artifact'}",
                ]
            )
            if summary:
                lines.append(f"   Summary: {summary}")
            task_context = self._artifact_task_context(artifact)
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
