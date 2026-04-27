"""Calendar → Task Bridge: materializes Google Calendar events as trackable tasks.

Converts upcoming calendar events into Task Hub entries so they appear on the
To-Do List dashboard and get picked up by the dispatch pipeline (heartbeat,
scheduled, or immediate).

Design principles:
  - LLM-powered classification for intelligent priority + description generation
  - Deterministic heuristics as synchronous fallback when LLM unavailable
  - Python plumbing for deduplication, scheduling, sanitization, DB ops
  - Content sanitization as a security boundary (untrusted external input)

Key behaviors:
  - Event-level deduplication: one task per calendar event ID; re-syncs
    update the existing task's title/time.
  - Task Hub entry → source_kind='calendar', labels=['calendar-task','agent-ready'].
  - due_at is derived from the event's start time, shifted backward by
    a configurable lead time (default: 30 min) to ensure prep work is
    dispatched before the meeting starts.
  - Priority classification (LLM or fallback heuristic):
      P1 = urgent, deadline, critical meetings
      P2 = default meeting / event
      P3 = informational / optional / FYI
  - All operations are idempotent (Task Hub UPSERT semantics).
  - Feature-gated by UA_CALENDAR_BRIDGE_ENABLED.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
import re
import sqlite3
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_CALENDAR_TASK_SOURCE_KIND = "calendar"
_CALENDAR_TASK_PROJECT_KEY = "immediate"
_CALENDAR_TASK_DEFAULT_LABELS = ["calendar-task", "agent-ready"]

# Default lead time: tasks are due this many minutes before the event starts.
_DEFAULT_LEAD_MINUTES = 30

# Priority keyword lists (case-insensitive)
_P1_KEYWORDS = re.compile(
    r"\b(urgent|deadline|asap|critical|blocker|emergency|p1)\b",
    re.IGNORECASE,
)
_P3_KEYWORDS = re.compile(
    r"\b(optional|fyi|informational|tentative|social|lunch|coffee)\b",
    re.IGNORECASE,
)

# ── Content Sanitization ─────────────────────────────────────────────────────
# Calendar event descriptions from shared invites are untrusted external input.
# Like the email triage agent, we treat content as DATA, not INSTRUCTIONS.
# We strip known prompt-injection patterns before passing to task descriptions.

_INJECTION_PATTERNS = re.compile(
    r"(?:"
    r"ignore\s+(?:previous|all|above|prior)\s+instructions"
    r"|you\s+are\s+now\b"
    r"|system\s*prompt\s*:"
    r"|as\s+an?\s+ai\s+assistant"
    r"|act\s+as\s+(?:a\s+)?(?:helpful|friendly)"
    r"|\bexecute\s*\("
    r"|\bos\.(?:system|popen)"
    r"|\bsubprocess\."
    r"|\$\(.*\)"
    r"|`[^`]*`"
    r")",
    re.IGNORECASE,
)

# Kevin's known email addresses for organizer trust classification
_TRUSTED_ORGANIZER_EMAILS = {
    "kevin.dragan@outlook.com",
    "kevinjdragan@gmail.com",
    "kevin@clearspringcg.com",
}


def _sanitize_event_content(text: str) -> tuple[str, list[str]]:
    """Sanitize calendar event text, returning (cleaned_text, threats_detected).

    Follows the email triage agent's principle: content is DATA, not INSTRUCTIONS.
    We paraphrase/strip rather than blindly passing external input to agents.
    """
    threats: list[str] = []
    if not text:
        return text, threats

    # Detect injection attempts
    matches = _INJECTION_PATTERNS.findall(text)
    if matches:
        threats.append("prompt_injection")
        # Strip matched patterns, replacing with [REDACTED]
        text = _INJECTION_PATTERNS.sub("[REDACTED]", text)

    # Truncate excessively long descriptions (> 2000 chars is suspicious for a
    # calendar event — real invites are seldom that long)
    if len(text) > 2000:
        threats.append("excessive_length")
        text = text[:2000] + "\n[truncated]"

    return text.strip(), threats


def _is_trusted_organizer(email: str) -> bool:
    """Check if the event organizer is a trusted (Kevin) address."""
    return email.strip().lower() in _TRUSTED_ORGANIZER_EMAILS

# ── Helpers ──────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _deterministic_task_id(event_id: str) -> str:
    """Generate a stable, deterministic task_id from a calendar event_id."""
    h = hashlib.sha256(f"calendar_event:{event_id}".encode()).hexdigest()[:16]
    return f"cal:{h}"


def _parse_event_time(raw: str | None) -> datetime | None:
    """Parse an ISO datetime string from Google Calendar."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _classify_priority(title: str, description: str = "") -> int:
    """Deterministic priority classification for calendar events.

    Returns: 1 (high/urgent), 2 (normal), or 3 (low/informational).
    """
    combined = f"{title} {description}"
    if _P1_KEYWORDS.search(combined):
        return 1
    if _P3_KEYWORDS.search(combined):
        return 3
    return 2


def calendar_bridge_enabled() -> bool:
    """Check if the calendar task bridge is enabled."""
    explicit = os.getenv("UA_CALENDAR_BRIDGE_ENABLED", "").strip().lower()
    if explicit in ("1", "true", "yes"):
        return True
    if explicit in ("0", "false", "no"):
        return False
    # Default: disabled (opt-in Phase 4)
    return False


# ── Database Schema ──────────────────────────────────────────────────────────

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS calendar_task_mappings (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL DEFAULT '',
    calendar_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    event_start TEXT NOT NULL DEFAULT '',
    event_end TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    organizer_email TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    priority INTEGER NOT NULL DEFAULT 2,
    last_sync_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calendar_task_mappings_status
    ON calendar_task_mappings(status, event_start);
CREATE INDEX IF NOT EXISTS idx_calendar_task_mappings_task_id
    ON calendar_task_mappings(task_id);
"""


def ensure_calendar_task_schema(conn: sqlite3.Connection) -> None:
    """Idempotently create the calendar_task_mappings table."""
    conn.executescript(_SCHEMA_SQL)
    conn.commit()


# ── CalendarTaskBridge ───────────────────────────────────────────────────────


class CalendarTaskBridge:
    """Materializes upcoming calendar events as trackable Task Hub tasks.

    Usage::

        bridge = CalendarTaskBridge(db_conn=conn)
        result = bridge.materialize_event(
            event_id="evt_abc123",
            calendar_id="primary",
            title="Sprint Review",
            description="Demo new features to stakeholders",
            event_start="2026-03-27T15:00:00-05:00",
            event_end="2026-03-27T16:00:00-05:00",
        )
    """

    def __init__(
        self,
        *,
        db_conn: sqlite3.Connection,
        lead_minutes: int | None = None,
    ) -> None:
        self._conn = db_conn
        self._lead_minutes = lead_minutes if lead_minutes is not None else _DEFAULT_LEAD_MINUTES
        ensure_calendar_task_schema(self._conn)

    # ── Public API ────────────────────────────────────────────────────────

    def materialize_event(
        self,
        *,
        event_id: str,
        calendar_id: str = "primary",
        title: str,
        description: str = "",
        event_start: str,
        event_end: str = "",
        location: str = "",
        organizer_email: str = "",
        attendees: list[str] | None = None,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Convert a calendar event into a tracked task.

        Creates or updates a Task Hub entry for the calendar event.

        Returns a dict with ``task_id``, ``is_update``, ``priority``,
        ``due_at``, and ``status``.
        """
        event_id = str(event_id or "").strip()
        if not event_id:
            raise ValueError("event_id is required")

        task_id = _deterministic_task_id(event_id)

        # ── Content sanitization (security boundary) ──────────────────────
        # Calendar descriptions from shared invites are untrusted external
        # input.  Sanitize before materializing into task descriptions that
        # agents will later process.
        organizer_trusted = _is_trusted_organizer(organizer_email)
        sanitized_title, title_threats = _sanitize_event_content(title)
        sanitized_desc, desc_threats = _sanitize_event_content(description)
        all_threats = list(set(title_threats + desc_threats))

        if all_threats:
            logger.warning(
                "📅🛡️ Content threats detected in calendar event %s from %s: %s",
                event_id, organizer_email, all_threats,
            )

        # Use sanitized content from here on
        title = sanitized_title or title  # fallback to original if sanitize empties it
        description = sanitized_desc

        priority = _classify_priority(title, description)
        existing = self._get_mapping(event_id)
        is_update = existing is not None

        # Compute due_at: event start minus lead time
        start_dt = _parse_event_time(event_start)
        due_at_iso = ""
        if start_dt:
            due_dt = start_dt - timedelta(minutes=self._lead_minutes)
            due_at_iso = due_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        # ① Upsert the mapping row
        now = _now_iso()
        if is_update:
            self._conn.execute(
                """
                UPDATE calendar_task_mappings
                SET title = ?, description = ?, event_start = ?, event_end = ?,
                    location = ?, organizer_email = ?, priority = ?,
                    last_sync_at = ?, updated_at = ?
                WHERE event_id = ?
                """,
                (title, description, event_start, event_end,
                 location, organizer_email, priority,
                 now, now, event_id),
            )
        else:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO calendar_task_mappings
                    (event_id, task_id, calendar_id, title, description,
                     event_start, event_end, location, organizer_email,
                     status, priority, last_sync_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (event_id, task_id, calendar_id, title, description,
                 event_start, event_end, location, organizer_email,
                 priority, now, now, now),
            )
        self._conn.commit()

        # ② Create/update Task Hub entry
        task_labels = list(labels or _CALENDAR_TASK_DEFAULT_LABELS)
        self._upsert_task_hub(
            task_id=task_id,
            title=title,
            description=description,
            event_id=event_id,
            calendar_id=calendar_id,
            event_start=event_start,
            event_end=event_end,
            location=location,
            organizer_email=organizer_email,
            attendees=attendees or [],
            priority=priority,
            due_at=due_at_iso,
            labels=task_labels,
        )

        result = {
            "task_id": task_id,
            "event_id": event_id,
            "is_update": is_update,
            "priority": priority,
            "due_at": due_at_iso,
            "status": "active",
        }
        logger.info(
            "📅→📋 Calendar task materialized: task_id=%s event=%s title='%s' "
            "is_update=%s priority=%d due_at=%s",
            task_id, event_id, title[:50], is_update, priority, due_at_iso,
        )
        return result

    async def materialize_event_llm(
        self,
        *,
        event_id: str,
        calendar_id: str = "primary",
        title: str,
        description: str = "",
        event_start: str,
        event_end: str = "",
        location: str = "",
        organizer_email: str = "",
        attendees: list[str] | None = None,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Convert a calendar event into a tracked task using LLM classification.

        Uses the LLM classifier for:
          - Priority classification (understanding implied urgency)
          - Actionable task description generation

        Falls back to deterministic heuristics if LLM is unavailable.
        Returns the same dict shape as ``materialize_event()``.
        """
        event_id = str(event_id or "").strip()
        if not event_id:
            raise ValueError("event_id is required")

        task_id = _deterministic_task_id(event_id)

        # ── Content sanitization (security boundary) ──────────────────────
        # This ALWAYS runs first, before any LLM sees the content.
        # Sanitization is a security gate, not a classification step.
        organizer_trusted = _is_trusted_organizer(organizer_email)
        sanitized_title, title_threats = _sanitize_event_content(title)
        sanitized_desc, desc_threats = _sanitize_event_content(description)
        all_threats = list(set(title_threats + desc_threats))

        if all_threats:
            logger.warning(
                "📅🛡️ Content threats detected in calendar event %s from %s: %s",
                event_id, organizer_email, all_threats,
            )

        title = sanitized_title or title
        description = sanitized_desc

        # ── LLM-powered priority classification ──────────────────────────
        heuristic_priority = _classify_priority(title, description)
        try:
            from universal_agent.services.llm_classifier import classify_priority

            priority_result = await classify_priority(
                title=title,
                description=description,
                source="calendar",
                sender_trusted=organizer_trusted,
                context=f"Location: {location}. Attendees: {', '.join(attendees or [])}",
                fallback_priority=heuristic_priority,
            )
            priority = priority_result["priority"]
            priority_method = priority_result["method"]
            priority_reasoning = priority_result.get("reasoning", "")
        except Exception:
            priority = heuristic_priority
            priority_method = "heuristic_fallback"
            priority_reasoning = "LLM unavailable"

        # ── LLM-powered task description ─────────────────────────────────
        # Build fallback description first (always available)
        fallback_desc_parts = []
        if description:
            fallback_desc_parts.append(description[:1500])
        if location:
            fallback_desc_parts.append(f"📍 Location: {location}")
        if organizer_email:
            fallback_desc_parts.append(f"👤 Organizer: {organizer_email}")
        if attendees:
            fallback_desc_parts.append(f"👥 Attendees: {', '.join(attendees[:10])}")
        if event_start:
            fallback_desc_parts.append(f"🕐 Starts: {event_start}")
        if event_end:
            fallback_desc_parts.append(f"🕐 Ends: {event_end}")
        fallback_desc = "\n".join(fallback_desc_parts)

        start_dt = _parse_event_time(event_start)
        end_dt = _parse_event_time(event_end)
        duration = None
        if start_dt and end_dt:
            duration = int((end_dt - start_dt).total_seconds() / 60)

        try:
            from universal_agent.services.llm_classifier import (
                generate_calendar_task_description,
            )

            desc_result = await generate_calendar_task_description(
                title=title,
                description=description,
                location=location,
                attendees=attendees,
                duration_minutes=duration,
                organizer=organizer_email,
                fallback_description=fallback_desc,
            )
            task_description = desc_result["task_description"]
            extra_labels = desc_result.get("suggested_labels", [])
            desc_method = desc_result["method"]
        except Exception:
            task_description = fallback_desc
            extra_labels = []
            desc_method = "heuristic_fallback"

        existing = self._get_mapping(event_id)
        is_update = existing is not None

        # Compute due_at: event start minus lead time
        due_at_iso = ""
        if start_dt:
            due_dt = start_dt - timedelta(minutes=self._lead_minutes)
            due_at_iso = due_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        # ① Upsert the mapping row
        now = _now_iso()
        if is_update:
            self._conn.execute(
                """
                UPDATE calendar_task_mappings
                SET title = ?, description = ?, event_start = ?, event_end = ?,
                    location = ?, organizer_email = ?, priority = ?,
                    last_sync_at = ?, updated_at = ?
                WHERE event_id = ?
                """,
                (title, description, event_start, event_end,
                 location, organizer_email, priority,
                 now, now, event_id),
            )
        else:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO calendar_task_mappings
                    (event_id, task_id, calendar_id, title, description,
                     event_start, event_end, location, organizer_email,
                     status, priority, last_sync_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (event_id, task_id, calendar_id, title, description,
                 event_start, event_end, location, organizer_email,
                 priority, now, now, now),
            )
        self._conn.commit()

        # ② Create/update Task Hub entry with LLM-enriched content
        task_labels = list(labels or _CALENDAR_TASK_DEFAULT_LABELS)
        # Merge any suggested labels from the LLM
        for lbl in extra_labels:
            if lbl and lbl not in task_labels:
                task_labels.append(lbl)

        # Append event details to the LLM-generated description
        full_description = task_description
        if desc_method == "llm":
            # Add factual event details after the LLM prep description
            event_details = []
            if location:
                event_details.append(f"📍 Location: {location}")
            if event_start:
                event_details.append(f"🕐 Starts: {event_start}")
            if event_end:
                event_details.append(f"🕐 Ends: {event_end}")
            if attendees:
                event_details.append(f"👥 Attendees: {', '.join(attendees[:10])}")
            if event_details:
                full_description += "\n\n---\n" + "\n".join(event_details)

        metadata: dict[str, Any] = {
            "calendar_event_id": event_id,
            "calendar_id": calendar_id,
            "event_start": event_start,
            "event_end": event_end,
            "location": location,
            "organizer_email": organizer_email,
            "organizer_trusted": organizer_trusted,
            "attendees": (attendees or [])[:10],
            "source_system": "google_calendar",
            "content_sanitized": True,
            "priority_method": priority_method,
            "priority_reasoning": priority_reasoning,
            "description_method": desc_method,
        }

        try:
            from universal_agent.task_hub import ensure_schema, upsert_item
            ensure_schema(self._conn)

            item = {
                "task_id": task_id,
                "source_kind": _CALENDAR_TASK_SOURCE_KIND,
                "source_ref": f"gcal_event:{event_id}",
                "title": f"📅 {title}" if title else "📅 Calendar Event",
                "description": full_description,
                "project_key": _CALENDAR_TASK_PROJECT_KEY,
                "priority": priority,
                "due_at": due_at_iso or None,
                "labels": task_labels,
                "status": "open",
                "trigger_type": "scheduled",
                "agent_ready": "agent-ready" in [l.lower() for l in task_labels],
                "must_complete": False,
                "metadata": metadata,
            }
            upsert_item(self._conn, item)
        except Exception as exc:
            logger.warning("📅→📋 Task Hub upsert failed for task_id=%s: %s", task_id, exc)

        result = {
            "task_id": task_id,
            "event_id": event_id,
            "is_update": is_update,
            "priority": priority,
            "priority_method": priority_method,
            "due_at": due_at_iso,
            "status": "active",
            "description_method": desc_method,
        }
        logger.info(
            "📅→📋🤖 Calendar task materialized (LLM): task_id=%s event=%s "
            "priority=%d(%s) desc=%s due_at=%s",
            task_id, event_id, priority, priority_method, desc_method, due_at_iso,
        )
        return result

    async def materialize_events_llm(
        self,
        events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Batch-materialize a list of calendar events using LLM classification.

        Each event dict should have at least: event_id, title, event_start.
        Returns a list of materialization results.
        """
        results = []
        for event in events:
            try:
                result = await self.materialize_event_llm(
                    event_id=event.get("event_id", event.get("id", "")),
                    calendar_id=event.get("calendar_id", "primary"),
                    title=event.get("title", event.get("summary", "")),
                    description=event.get("description", ""),
                    event_start=event.get("event_start", event.get("start", "")),
                    event_end=event.get("event_end", event.get("end", "")),
                    location=event.get("location", ""),
                    organizer_email=event.get("organizer_email", event.get("organizer", "")),
                    attendees=event.get("attendees"),
                    labels=event.get("labels"),
                )
                results.append(result)
            except Exception as exc:
                logger.warning(
                    "📅→📋 Failed to materialize event %s (LLM): %s",
                    event.get("event_id", "?"), exc,
                )
        return results

    def materialize_events(
        self,
        events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Batch-materialize a list of calendar events.

        Each event dict should have at least: event_id, title, event_start.
        Returns a list of materialization results.
        """
        results = []
        for event in events:
            try:
                result = self.materialize_event(
                    event_id=event.get("event_id", event.get("id", "")),
                    calendar_id=event.get("calendar_id", "primary"),
                    title=event.get("title", event.get("summary", "")),
                    description=event.get("description", ""),
                    event_start=event.get("event_start", event.get("start", "")),
                    event_end=event.get("event_end", event.get("end", "")),
                    location=event.get("location", ""),
                    organizer_email=event.get("organizer_email", event.get("organizer", "")),
                    attendees=event.get("attendees"),
                    labels=event.get("labels"),
                )
                results.append(result)
            except Exception as exc:
                logger.warning(
                    "📅→📋 Failed to materialize event %s: %s",
                    event.get("event_id", "?"), exc,
                )
        return results

    def get_active_calendar_tasks(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return all active calendar-driven task mappings."""
        ensure_calendar_task_schema(self._conn)
        rows = self._conn.execute(
            """
            SELECT * FROM calendar_task_mappings
            WHERE status = 'active'
            ORDER BY event_start ASC
            LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_upcoming_tasks(
        self,
        *,
        within_hours: int = 24,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return calendar tasks with events starting within the given window."""
        ensure_calendar_task_schema(self._conn)
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=within_hours)
        rows = self._conn.execute(
            """
            SELECT * FROM calendar_task_mappings
            WHERE status = 'active'
              AND event_start >= ?
              AND event_start <= ?
            ORDER BY event_start ASC
            LIMIT ?
            """,
            (now.isoformat(), cutoff.isoformat(), max(1, limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_completed(self, event_id: str) -> bool:
        """Mark a calendar task as completed (event passed or handled)."""
        now = _now_iso()
        self._conn.execute(
            "UPDATE calendar_task_mappings SET status = 'completed', updated_at = ? WHERE event_id = ?",
            (now, event_id),
        )
        self._conn.commit()
        return True

    def mark_cancelled(self, event_id: str) -> bool:
        """Mark a calendar task as cancelled (event was deleted/cancelled)."""
        now = _now_iso()
        self._conn.execute(
            "UPDATE calendar_task_mappings SET status = 'cancelled', updated_at = ? WHERE event_id = ?",
            (now, event_id),
        )
        self._conn.commit()
        return True

    def expire_past_events(self, *, hours_past: int = 2) -> int:
        """Auto-complete tasks whose events have already ended.

        Returns the number of tasks expired.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_past)).isoformat()
        now = _now_iso()
        cursor = self._conn.execute(
            """
            UPDATE calendar_task_mappings
            SET status = 'completed', updated_at = ?
            WHERE status = 'active'
              AND event_end != ''
              AND event_end < ?
            """,
            (now, cutoff),
        )
        count = cursor.rowcount
        self._conn.commit()
        if count:
            logger.info("📅→📋 Expired %d past calendar tasks (cutoff=%s)", count, cutoff)
        return count

    # ── Internal Methods ──────────────────────────────────────────────────

    def _get_mapping(self, event_id: str) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM calendar_task_mappings WHERE event_id = ? LIMIT 1",
            (event_id,),
        ).fetchone()
        return dict(row) if row else None

    def _upsert_task_hub(
        self,
        *,
        task_id: str,
        title: str,
        description: str,
        event_id: str,
        calendar_id: str,
        event_start: str,
        event_end: str,
        location: str,
        organizer_email: str,
        attendees: list[str],
        priority: int,
        due_at: str,
        labels: list[str],
    ) -> dict[str, Any]:
        """Create or update a Task Hub entry for this calendar event."""
        try:
            from universal_agent.task_hub import ensure_schema, upsert_item

            ensure_schema(self._conn)

            # Build rich description
            desc_parts = []
            if description:
                desc_parts.append(description[:1500])
            if location:
                desc_parts.append(f"📍 Location: {location}")
            if organizer_email:
                desc_parts.append(f"👤 Organizer: {organizer_email}")
            if attendees:
                desc_parts.append(f"👥 Attendees: {', '.join(attendees[:10])}")
            if event_start:
                desc_parts.append(f"🕐 Starts: {event_start}")
            if event_end:
                desc_parts.append(f"🕐 Ends: {event_end}")

            full_description = "\n".join(desc_parts)

            metadata: dict[str, Any] = {
                "calendar_event_id": event_id,
                "calendar_id": calendar_id,
                "event_start": event_start,
                "event_end": event_end,
                "location": location,
                "organizer_email": organizer_email,
                "organizer_trusted": _is_trusted_organizer(organizer_email),
                "attendees": attendees[:10],
                "source_system": "google_calendar",
                "content_sanitized": True,
            }

            item = {
                "task_id": task_id,
                "source_kind": _CALENDAR_TASK_SOURCE_KIND,
                "source_ref": f"gcal_event:{event_id}",
                "title": f"📅 {title}" if title else "📅 Calendar Event",
                "description": full_description,
                "project_key": _CALENDAR_TASK_PROJECT_KEY,
                "priority": priority,
                "due_at": due_at or None,
                "labels": labels,
                "status": "open",
                "trigger_type": "scheduled",
                "agent_ready": "agent-ready" in [l.lower() for l in labels],
                "must_complete": False,
                "metadata": metadata,
            }
            return upsert_item(self._conn, item)
        except Exception as exc:
            logger.warning("📅→📋 Task Hub upsert failed for task_id=%s: %s", task_id, exc)
            return {}
