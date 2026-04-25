"""Compose and send proactive intelligence review emails."""

from __future__ import annotations

import html
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from universal_agent.services import proactive_artifacts
from universal_agent.services.proactive_preferences import build_weekly_preference_report, score_artifact_for_review


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
        lines = [
            "I made this proactively because I think it may be useful. Please review when convenient.",
            "",
            f"Title: {title}",
        ]
        if summary:
            lines.extend(["", summary])
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


def _artifact_link(artifact: dict[str, Any]) -> str:
    for key in ("artifact_uri", "source_url", "artifact_path"):
        value = str(artifact.get(key) or "").strip()
        if value:
            return value
    return ""
