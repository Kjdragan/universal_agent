"""Email → Task Bridge: materializes inbound emails as trackable tasks.

Pure-Python service (no LLM calls) that converts trusted inbound AgentMail
emails into Task Hub entries so they appear on the To-Do List dashboard
and get picked up by the dedicated ToDo dispatcher.

Key design decisions:
  - Thread-level deduplication: one task per email thread; subsequent emails
    on the same thread UPDATE the existing task.
  - Task Hub entry → source_kind='email', labels=['email-task','agent-ready'].
  - Delivery mode is inferred once at materialization and stored with the task.
  - All operations are idempotent and crash-safe (SQLite UPSERT semantics).
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_EMAIL_TASK_SOURCE_KIND = "email"
_EMAIL_TASK_PROJECT_KEY = "immediate"
_EMAIL_TASK_DEFAULT_LABELS = ["email-task", "agent-ready"]
_EMAIL_TASK_TRIAGE_PENDING_LABELS = ["email-task", "triage-pending"]
_EMAIL_TASK_EXTERNAL_REVIEW_LABELS = ["email-task", "external-untriaged"]
_EMAIL_TASK_QUARANTINED_LABEL = "quarantined"
_EMAIL_TASK_REVIEW_REQUIRED_LABEL = "review-required"
# Mapping from target_agent identifiers to agent labels.
# Default fallback is "agent-atlas" for any unmapped agent key.
_AGENT_LABEL_MAP = {
    "vp.coder.primary": "agent-codie",
    "vp.general.primary": "agent-atlas",
    "vp.coder": "agent-codie",
    "vp.general": "agent-atlas",
    "coder": "agent-codie",
    "general": "agent-atlas",
}

# Subject prefixes to strip when computing the master key
_REPLY_PREFIX_RE = re.compile(r"^(Re|Fwd|Fw):\s*", re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _deterministic_task_id(thread_id: str) -> str:
    """Generate a stable, deterministic task_id from an email thread_id."""
    h = hashlib.sha256(f"email_thread:{thread_id}".encode()).hexdigest()[:16]
    return f"email:{h}"


def infer_delivery_mode(*, subject: str = "", body: str = "") -> str:
    """Infer the preferred outbound delivery mode for an email task."""
    candidate = f"{subject}\n{body}".strip().lower()
    if not candidate:
        return "standard_report"
    if any(token in candidate for token in ("infographic", "audio", "video", "notebooklm", "podcast", "slide deck")):
        return "enhanced_report"
    if any(token in candidate for token in ("quick", "short answer", "brief summary", "right away", "asap")):
        return "fast_summary"
    if any(token in candidate for token in ("comprehensive report", "intelligence brief", "full report", "detailed analysis")):
        return "standard_report"
    return "standard_report"


def _build_email_execution_manifest(*, subject: str, body: str) -> dict[str, Any]:
    from universal_agent.services.todo_dispatch_service import build_execution_manifest

    delivery_mode = infer_delivery_mode(subject=subject, body=body)
    return build_execution_manifest(
        user_input=f"{subject}\n{body}".strip(),
        delivery_mode=delivery_mode,
        final_channel="email",
        canonical_executor="simone_first",
    )


_TRIAGE_FIELD_RE = {
    "safety_status": re.compile(r"^\s*safety_status\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE),
    "routing_decision": re.compile(r"^\s*routing_decision\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE),
    "classification": re.compile(r"^\s*classification\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE),
    "priority": re.compile(r"^\s*priority\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE),
    "subject_summary": re.compile(r"^\s*subject_summary\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE),
}


def parse_email_triage_brief(raw: Any, *, sender_trusted: bool) -> dict[str, Any]:
    text = str(raw or "").strip()
    parsed: dict[str, Any] = {
        "raw_text": text,
        "safety_status": "",
        "routing_decision": "",
        "classification": "",
        "priority": "",
        "subject_summary": "",
    }
    if not text:
        parsed["routing_decision"] = "trusted_execute" if sender_trusted else "review_required"
        return parsed

    for field, pattern in _TRIAGE_FIELD_RE.items():
        match = pattern.search(text)
        if not match:
            continue
        parsed[field] = str(match.group(1) or "").strip()

    safety_status = str(parsed.get("safety_status") or "").strip().lower()
    routing_decision = str(parsed.get("routing_decision") or "").strip().lower()
    if not safety_status:
        if "quarantine" in text.lower():
            safety_status = "quarantine"
        else:
            safety_status = "clean"
    if safety_status not in {"clean", "quarantine"}:
        safety_status = "quarantine" if "quarantine" in safety_status else "clean"

    if not routing_decision:
        routing_decision = "quarantine" if safety_status == "quarantine" else (
            "trusted_execute" if sender_trusted else "review_required"
        )
    if routing_decision not in {"trusted_execute", "review_required", "quarantine"}:
        if "quarantine" in routing_decision:
            routing_decision = "quarantine"
        elif "review" in routing_decision:
            routing_decision = "review_required"
        else:
            routing_decision = "trusted_execute" if sender_trusted else "review_required"

    parsed["safety_status"] = safety_status
    parsed["routing_decision"] = routing_decision
    return parsed



# ── Database Schema ──────────────────────────────────────────────────────────

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS email_task_mappings (
    thread_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL DEFAULT '',
    master_key TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    sender_email TEXT NOT NULL DEFAULT '',
    sender_trusted INTEGER NOT NULL DEFAULT 1,
    security_classification TEXT NOT NULL DEFAULT '',
    workflow_run_id TEXT NOT NULL DEFAULT '',
    workflow_attempt_id TEXT NOT NULL DEFAULT '',
    provider_session_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    last_message_id TEXT NOT NULL DEFAULT '',
    real_thread_id TEXT NOT NULL DEFAULT '',
    real_message_id TEXT NOT NULL DEFAULT '',
    email_sent_at TEXT NOT NULL DEFAULT '',
    ack_sent_at TEXT NOT NULL DEFAULT '',
    ack_message_id TEXT NOT NULL DEFAULT '',
    ack_draft_id TEXT NOT NULL DEFAULT '',
    final_email_sent_at TEXT NOT NULL DEFAULT '',
    final_message_id TEXT NOT NULL DEFAULT '',
    final_draft_id TEXT NOT NULL DEFAULT '',
    message_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_email_task_mappings_status
    ON email_task_mappings(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_task_mappings_task_id
    ON email_task_mappings(task_id);
CREATE INDEX IF NOT EXISTS idx_email_task_mappings_master_key
    ON email_task_mappings(master_key);
"""


def ensure_email_task_schema(conn: sqlite3.Connection) -> None:
    """Idempotently create the email_task_mappings table."""
    conn.executescript(_SCHEMA_SQL)
    # Migrate existing tables: add new columns if they don't exist yet
    for col, default in [
        ("master_key", "''"),
        ("sender_trusted", "1"),
        ("security_classification", "''"),
        ("workflow_run_id", "''"),
        ("workflow_attempt_id", "''"),
        ("provider_session_id", "''"),
        ("real_thread_id", "''"),
        ("real_message_id", "''"),
        # Idempotency and delivery lifecycle tracking.
        ("email_sent_at", "''"),
        ("ack_sent_at", "''"),
        ("ack_message_id", "''"),
        ("ack_draft_id", "''"),
        ("final_email_sent_at", "''"),
        ("final_message_id", "''"),
        ("final_draft_id", "''"),
    ]:
        try:
            conn.execute(
                f"ALTER TABLE email_task_mappings ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}"
            )
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()


def reconcile_terminal_email_task_mappings(conn: sqlite3.Connection) -> dict[str, int]:
    """Repair stale email thread rows after Task Hub has already reached a terminal state."""
    ensure_email_task_schema(conn)
    now_iso = _now_iso()
    completed_backfilled = 0
    completed_status_synced = 0
    reviewed_backfilled = 0
    reviewed_status_synced = 0

    rows = conn.execute(
        """
        SELECT
            m.thread_id,
            m.status AS mapping_status,
            m.email_sent_at,
            m.final_email_sent_at,
            m.final_message_id,
            m.final_draft_id,
            i.status AS task_status,
            i.updated_at AS task_updated_at
        FROM email_task_mappings m
        JOIN task_hub_items i ON i.task_id = m.task_id
        WHERE i.status IN ('completed', 'needs_review')
        """
    ).fetchall()

    for row in rows:
        thread_id = str(row["thread_id"] or "").strip()
        if not thread_id:
            continue
        task_status = str(row["task_status"] or "").strip().lower()
        mapping_status = str(row["mapping_status"] or "").strip().lower()
        final_seen = any(
            str(row[field] or "").strip()
            for field in ("final_email_sent_at", "final_message_id", "final_draft_id", "email_sent_at")
        )
        task_updated_at = str(row["task_updated_at"] or "").strip() or now_iso
        if not final_seen:
            conn.execute(
                """
                UPDATE email_task_mappings
                SET final_email_sent_at = CASE WHEN final_email_sent_at = '' THEN ? ELSE final_email_sent_at END,
                    email_sent_at = CASE WHEN email_sent_at = '' THEN ? ELSE email_sent_at END,
                    updated_at = ?
                WHERE thread_id = ?
                """,
                (task_updated_at, task_updated_at, now_iso, thread_id),
            )
            if task_status == "completed":
                completed_backfilled += 1
            elif task_status == "needs_review":
                reviewed_backfilled += 1

        if task_status == "completed" and mapping_status != "completed":
            conn.execute(
                "UPDATE email_task_mappings SET status = 'completed', updated_at = ? WHERE thread_id = ?",
                (now_iso, thread_id),
            )
            completed_status_synced += 1
        elif task_status == "needs_review" and mapping_status == "active":
            conn.execute(
                "UPDATE email_task_mappings SET status = 'waiting-on-reply', updated_at = ? WHERE thread_id = ?",
                (now_iso, thread_id),
            )
            reviewed_status_synced += 1

    conn.commit()
    return {
        "completed_backfilled": completed_backfilled,
        "completed_status_synced": completed_status_synced,
        "reviewed_backfilled": reviewed_backfilled,
        "reviewed_status_synced": reviewed_status_synced,
    }


# ── EmailTaskBridge ──────────────────────────────────────────────────────────

class EmailTaskBridge:
    """Materializes inbound trusted emails as trackable tasks.

    Usage::

        bridge = EmailTaskBridge(db_conn=conn)
        result = bridge.materialize(
            thread_id="thd_abc",
            message_id="msg_123",
            sender_email="kevin@example.com",
            subject="Re: N8N Pipeline",
            reply_text="Please do X, Y, Z",
            session_key="agentmail_thd_abc",
        )
    """

    def __init__(
        self,
        *,
        db_conn: sqlite3.Connection,
        heartbeat_path: Optional[str] = None,
    ) -> None:
        self._conn = db_conn
        self._heartbeat_path = heartbeat_path or self._default_heartbeat_path()
        ensure_email_task_schema(self._conn)

    @staticmethod
    def _default_heartbeat_path() -> str:
        """Resolve the default HEARTBEAT.md path from env or well-known location."""
        env_path = os.getenv("UA_HEARTBEAT_MD_PATH", "").strip()
        if env_path:
            return env_path
        # Well-known heartbeat cron workspace
        base = os.getenv("UA_REPO_ROOT", "").strip()
        if not base:
            base = str(Path(__file__).resolve().parents[3])
        return str(
            Path(base) / "AGENT_RUN_WORKSPACES" / "cron_6eb03023c0" / "memory" / "HEARTBEAT.md"
        )

    # ── Public API ────────────────────────────────────────────────────────

    def materialize(
        self,
        *,
        thread_id: str,
        message_id: str,
        sender_email: str,
        subject: str,
        reply_text: str,
        session_key: str = "",
        sender_trusted: bool = True,
        security_classification: str = "",
        triage_pending: bool = False,
        priority: int | None = None,
        due_at: str | None = None,
        workflow_run_id: str = "",
        workflow_attempt_id: str = "",
        provider_session_id: str = "",
        real_thread_id: str = "",
        real_message_id: str = "",
        target_agent: str | None = None,
    ) -> dict[str, Any]:
        """Convert an inbound email into a tracked task.

        Creates or updates a Task Hub entry for the email thread.
        For untrusted senders, creates a task with ``external-untriaged`` label
        that Simone must review before marking ``agent-ready``.

        Returns a dict with ``task_id``, ``is_update``, ``message_count``,
        ``master_key``, and ``status``.
        """
        thread_id = str(thread_id or "").strip()
        message_id = str(message_id or "").strip()
        if not thread_id:
            thread_id = message_id or f"orphan_{int(time.time())}"
            
        real_thread_id = str(real_thread_id or thread_id).strip()
        real_message_id = str(real_message_id or message_id).strip()

        if sender_trusted:
            try:
                from universal_agent.services.proactive_feedback import (
                    handle_proactive_feedback_reply,
                )

                feedback_result = handle_proactive_feedback_reply(
                    self._conn,
                    subject=subject,
                    reply_text=reply_text,
                    thread_id=real_thread_id or thread_id,
                    message_id=real_message_id or message_id,
                    actor=sender_email or "trusted_operator",
                )
            except Exception as exc:
                logger.warning(
                    "📧→🧠 Proactive feedback interception failed thread=%s: %s",
                    thread_id,
                    exc,
                )
                feedback_result = None
            if feedback_result is not None:
                logger.info(
                    "📧→🧠 Recorded proactive artifact feedback artifact_id=%s score=%s",
                    feedback_result.get("artifact_id"),
                    feedback_result.get("score"),
                )
                return {
                    **feedback_result,
                    "task_id": "",
                    "is_update": False,
                    "message_count": 0,
                    "thread_id": thread_id,
                    "sender_trusted": sender_trusted,
                    "delivery_mode": "proactive_feedback",
                }

        task_id = _deterministic_task_id(thread_id)
        master_key = self._classify_master_key(subject)
        existing = self._get_mapping(thread_id)
        is_update = existing is not None
        existing_mapping = existing or {}
        resolved_workflow_run_id = str(workflow_run_id or existing_mapping.get("workflow_run_id") or "").strip()
        resolved_workflow_attempt_id = str(workflow_attempt_id or existing_mapping.get("workflow_attempt_id") or "").strip()
        resolved_provider_session_id = str(provider_session_id or existing_mapping.get("provider_session_id") or "").strip()

        # Determine labels based on trust level
        email_labels = list(_EMAIL_TASK_DEFAULT_LABELS)
        if triage_pending and sender_trusted:
            email_labels = list(_EMAIL_TASK_TRIAGE_PENDING_LABELS)
        elif not sender_trusted:
            email_labels = list(_EMAIL_TASK_EXTERNAL_REVIEW_LABELS)
            if security_classification:
                email_labels.append(f"security-{security_classification}")

        # Add VP agent-specific label when the email targets a specific VP
        if target_agent and target_agent not in ("simone", "simone_first"):
            agent_label = _AGENT_LABEL_MAP.get(str(target_agent).strip().lower(), "agent-atlas")
            if agent_label not in email_labels:
                email_labels.append(agent_label)

        # Auto-reactivate if this thread was waiting-on-reply
        if is_update and existing and str(existing.get("status", "")) == "waiting-on-reply":
            self._reactivate_waiting_thread(thread_id)
            logger.info(
                "📧→📋 Auto-reactivated waiting thread=%s because new inbound arrived",
                thread_id,
            )

        # ① Upsert the mapping row
        now = _now_iso()
        if is_update:
            message_count = int(existing.get("message_count") or 1) + 1
            self._conn.execute(
                """
                UPDATE email_task_mappings
                SET subject = ?, sender_email = ?, last_message_id = ?,
                    message_count = ?, updated_at = ?, master_key = ?,
                    sender_trusted = ?, security_classification = ?,
                    workflow_run_id = ?, workflow_attempt_id = ?, provider_session_id = ?,
                    real_thread_id = ?, real_message_id = ?
                WHERE thread_id = ?
                """,
                (subject, sender_email, message_id, message_count, now,
                 master_key, int(sender_trusted), security_classification,
                 resolved_workflow_run_id, resolved_workflow_attempt_id, resolved_provider_session_id,
                 real_thread_id, real_message_id,
                 thread_id),
            )
        else:
            message_count = 1
            self._conn.execute(
                """
                INSERT OR IGNORE INTO email_task_mappings
                    (thread_id, task_id,
                     master_key, subject, sender_email, sender_trusted,
                     security_classification, workflow_run_id, workflow_attempt_id,
                     provider_session_id, status, last_message_id,
                     message_count, created_at, updated_at, real_thread_id, real_message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, 1, ?, ?, ?, ?)
                """,
                (thread_id, task_id, master_key, subject, sender_email,
                 int(sender_trusted), security_classification,
                 resolved_workflow_run_id, resolved_workflow_attempt_id, resolved_provider_session_id,
                 message_id, now, now, real_thread_id, real_message_id),
            )
        self._conn.commit()

        # ② Create/update Task Hub entry
        # Status starts as 'open' so the heartbeat dispatch sweep can
        # discover and claim it through the normal pipeline.  The
        # claim_next_dispatch_tasks function atomically transitions the
        # task to 'in_progress' when seized, which prevents double-claiming.
        self._upsert_task_hub(
            task_id=task_id,
            subject=subject,
            sender_email=sender_email,
            reply_text=reply_text,
            thread_id=thread_id,
            message_count=message_count,
            session_key=session_key,
            workflow_run_id=resolved_workflow_run_id,
            workflow_attempt_id=resolved_workflow_attempt_id,
            provider_session_id=resolved_provider_session_id,
            labels=email_labels,
            priority=priority,
            due_at=due_at,
            initial_status="open",
            real_thread_id=real_thread_id,
            real_message_id=real_message_id,
            target_agent=target_agent,
        )

        result = {
            "task_id": task_id,
            "master_key": master_key,
            "is_update": is_update,
            "message_count": message_count,
            "status": "active",
            "thread_id": thread_id,
            "sender_trusted": sender_trusted,
            "workflow_run_id": resolved_workflow_run_id,
            "workflow_attempt_id": resolved_workflow_attempt_id,
            "provider_session_id": resolved_provider_session_id,
            "delivery_mode": infer_delivery_mode(subject=subject, body=reply_text),
        }
        logger.info(
            "📧→📋 Email task materialized: task_id=%s thread=%s master=%s is_update=%s messages=%d trusted=%s",
            task_id, thread_id, master_key, is_update, message_count, sender_trusted,
        )

        # ④ Phase 3: Nudge the idle dispatch loop so Simone wakes within
        # seconds instead of waiting for the next poll interval.
        try:
            from universal_agent.services.idle_dispatch_loop import nudge_dispatch
            nudge_dispatch(reason=f"email_inbound:{thread_id[:16]}")
        except Exception as _nudge_exc:
            logger.debug("Nudge dispatch unavailable: %s", _nudge_exc)

        return result

    def link_workflow(
        self,
        *,
        thread_id: str,
        workflow_run_id: str = "",
        workflow_attempt_id: str = "",
        provider_session_id: str = "",
    ) -> dict[str, Any]:
        """Backfill durable workflow lineage onto an already materialized email task."""
        mapping = self._get_mapping(thread_id)
        if not mapping:
            return {}

        resolved_workflow_run_id = str(workflow_run_id or mapping.get("workflow_run_id") or "").strip()
        resolved_workflow_attempt_id = str(workflow_attempt_id or mapping.get("workflow_attempt_id") or "").strip()
        resolved_provider_session_id = str(provider_session_id or mapping.get("provider_session_id") or "").strip()
        if not any((resolved_workflow_run_id, resolved_workflow_attempt_id, resolved_provider_session_id)):
            return mapping

        now = _now_iso()
        self._conn.execute(
            """
            UPDATE email_task_mappings
            SET workflow_run_id = ?, workflow_attempt_id = ?, provider_session_id = ?, updated_at = ?
            WHERE thread_id = ?
            """,
            (
                resolved_workflow_run_id,
                resolved_workflow_attempt_id,
                resolved_provider_session_id,
                now,
                str(thread_id or "").strip(),
            ),
        )
        self._conn.commit()

        try:
            from universal_agent.task_hub import ensure_schema, upsert_item

            ensure_schema(self._conn)
            metadata: dict[str, Any] = {}
            if resolved_workflow_run_id:
                metadata["workflow_run_id"] = resolved_workflow_run_id
            if resolved_workflow_attempt_id:
                metadata["workflow_attempt_id"] = resolved_workflow_attempt_id
            if resolved_provider_session_id:
                metadata["provider_session_id"] = resolved_provider_session_id
            if metadata:
                upsert_item(
                    self._conn,
                    {
                        "task_id": str(mapping.get("task_id") or ""),
                        "metadata": metadata,
                    },
                )
        except Exception as exc:
            logger.warning(
                "📧→📋 Email task workflow linkage update failed thread=%s: %s",
                thread_id,
                exc,
            )

        updated = self._get_mapping(thread_id)
        return updated or mapping

    def get_active_email_tasks(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return all active email-driven task mappings."""
        ensure_email_task_schema(self._conn)
        rows = self._conn.execute(
            """
            SELECT * FROM email_task_mappings
            WHERE status = 'active'
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_completed(self, thread_id: str) -> bool:
        """Mark an email task as completed."""
        now = _now_iso()
        self._conn.execute(
            "UPDATE email_task_mappings SET status = 'completed', updated_at = ? WHERE thread_id = ?",
            (now, thread_id),
        )
        self._conn.commit()
        return True

    def mark_email_sent(self, thread_id: str) -> bool:
        """Record that an email response was sent for this thread.

        This provides an idempotency signal: if the task is re-claimed
        and re-executed, the agent prompt will include a warning that an
        email has already been sent.
        """
        now = _now_iso()
        self._conn.execute(
            "UPDATE email_task_mappings SET email_sent_at = ?, updated_at = ? WHERE thread_id = ?",
            (now, now, thread_id),
        )
        self._conn.commit()
        logger.info("📧 Marked email_sent_at=%s for thread=%s", now, thread_id)
        return True

    def get_mapping_for_task_id(self, task_id: str) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM email_task_mappings WHERE task_id = ? ORDER BY updated_at DESC LIMIT 1",
            (str(task_id or "").strip(),),
        ).fetchone()
        return dict(row) if row else None

    def get_mapping_for_session_key(self, session_key: str) -> Optional[dict[str, Any]]:
        key = str(session_key or "").strip()
        if not key:
            return None
        task_id = ""
        try:
            row = self._conn.execute(
                """
                SELECT task_id
                FROM task_hub_items
                WHERE json_extract(metadata_json, '$.session_key') = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (key,),
            ).fetchone()
            if row:
                task_id = str(row["task_id"] or "").strip()
        except Exception:
            row = self._conn.execute(
                """
                SELECT task_id
                FROM task_hub_items
                WHERE metadata_json LIKE ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (f'%"session_key": "{key}"%',),
            ).fetchone()
            if row:
                task_id = str(row["task_id"] or "").strip()
        if not task_id:
            return None
        return self.get_mapping_for_task_id(task_id)

    def has_ack_outbound(self, thread_id: str) -> bool:
        mapping = self._get_mapping(thread_id)
        if not mapping:
            return False
        return any(
            str(mapping.get(field) or "").strip()
            for field in ("ack_sent_at", "ack_message_id", "ack_draft_id")
        )

    def has_final_outbound(self, thread_id: str) -> bool:
        mapping = self._get_mapping(thread_id)
        if not mapping:
            return False
        return any(
            str(mapping.get(field) or "").strip()
            for field in ("final_email_sent_at", "final_message_id", "final_draft_id", "email_sent_at")
        )

    def record_ack_outbound(self, thread_id: str, *, message_id: str = "", draft_id: str = "") -> bool:
        now = _now_iso()
        self._conn.execute(
            """
            UPDATE email_task_mappings
            SET ack_sent_at = CASE WHEN ack_sent_at = '' THEN ? ELSE ack_sent_at END,
                ack_message_id = CASE WHEN ? <> '' THEN ? ELSE ack_message_id END,
                ack_draft_id = CASE WHEN ? <> '' THEN ? ELSE ack_draft_id END,
                updated_at = ?
            WHERE thread_id = ?
            """,
            (
                now,
                str(message_id or "").strip(),
                str(message_id or "").strip(),
                str(draft_id or "").strip(),
                str(draft_id or "").strip(),
                now,
                str(thread_id or "").strip(),
            ),
        )
        self._conn.commit()
        return True

    def record_final_outbound(self, thread_id: str, *, message_id: str = "", draft_id: str = "") -> bool:
        now = _now_iso()
        self._conn.execute(
            """
            UPDATE email_task_mappings
            SET final_email_sent_at = CASE WHEN final_email_sent_at = '' THEN ? ELSE final_email_sent_at END,
                final_message_id = CASE WHEN ? <> '' THEN ? ELSE final_message_id END,
                final_draft_id = CASE WHEN ? <> '' THEN ? ELSE final_draft_id END,
                email_sent_at = CASE WHEN email_sent_at = '' THEN ? ELSE email_sent_at END,
                updated_at = ?
            WHERE thread_id = ?
            """,
            (
                now,
                str(message_id or "").strip(),
                str(message_id or "").strip(),
                str(draft_id or "").strip(),
                str(draft_id or "").strip(),
                now,
                now,
                str(thread_id or "").strip(),
            ),
        )
        self._conn.commit()
        return True

    def was_email_sent(self, thread_id: str) -> bool:
        """Check whether an email response was already sent for this thread."""
        row = self._conn.execute(
            "SELECT email_sent_at FROM email_task_mappings WHERE thread_id = ? LIMIT 1",
            (str(thread_id or "").strip(),),
        ).fetchone()
        if row is None:
            return False
        return bool(row["email_sent_at"])

    def mark_waiting_on_reply(self, thread_id: str) -> bool:
        """Mark an email task as waiting for the user's reply.

        Called after Simone sends a response and is awaiting user feedback.
        """
        now = _now_iso()
        self._conn.execute(
            "UPDATE email_task_mappings SET status = 'waiting-on-reply', updated_at = ? WHERE thread_id = ?",
            (now, thread_id),
        )
        self._conn.commit()

        # Update Task Hub status
        self._upsert_thread_task_state(
            thread_id=thread_id,
            status="waiting-on-reply",
            labels=["email-task", "waiting-on-reply"],
        )

        return True

    def _reactivate_waiting_thread(self, thread_id: str) -> None:
        """Reactivate a thread that was waiting-on-reply because a new inbound arrived.

        Called automatically by materialize() when an update arrives for a waiting thread.
        """
        now = _now_iso()
        self._conn.execute(
            "UPDATE email_task_mappings SET status = 'active', updated_at = ? WHERE thread_id = ?",
            (now, thread_id),
        )
        self._conn.commit()

        # Update Task Hub
        self._upsert_thread_task_state(
            thread_id=thread_id,
            status="open",
            labels=list(_EMAIL_TASK_DEFAULT_LABELS),
        )

    def promote_to_agent_ready(self, thread_id: str) -> bool:
        thread = str(thread_id or "").strip()
        if not thread:
            return False
        now = _now_iso()
        self._conn.execute(
            """
            UPDATE email_task_mappings
            SET status = 'active', security_classification = CASE
                WHEN security_classification = 'quarantine' THEN ''
                ELSE security_classification
            END, updated_at = ?
            WHERE thread_id = ?
            """,
            (now, thread),
        )
        self._conn.commit()
        self._upsert_thread_task_state(
            thread_id=thread,
            status="open",
            labels=list(_EMAIL_TASK_DEFAULT_LABELS),
        )
        return True

    def mark_review_required(
        self,
        thread_id: str,
        *,
        security_classification: str = "clean",
        note: str = "",
    ) -> bool:
        thread = str(thread_id or "").strip()
        if not thread:
            return False
        now = _now_iso()
        self._conn.execute(
            """
            UPDATE email_task_mappings
            SET status = 'review_required',
                security_classification = ?,
                updated_at = ?
            WHERE thread_id = ?
            """,
            (str(security_classification or "clean").strip() or "clean", now, thread),
        )
        self._conn.commit()
        labels = list(_EMAIL_TASK_EXTERNAL_REVIEW_LABELS)
        labels.append(_EMAIL_TASK_REVIEW_REQUIRED_LABEL)
        self._upsert_thread_task_state(
            thread_id=thread,
            status="needs_review",
            labels=labels,
            metadata_patch={
                "email_triage_routing": "review_required",
                "email_triage_note": str(note or "").strip(),
            },
        )
        return True

    def mark_quarantined(
        self,
        thread_id: str,
        *,
        note: str = "",
        sender_trusted: bool = False,
    ) -> bool:
        thread = str(thread_id or "").strip()
        if not thread:
            return False
        now = _now_iso()
        self._conn.execute(
            """
            UPDATE email_task_mappings
            SET status = 'quarantined',
                security_classification = 'quarantine',
                updated_at = ?
            WHERE thread_id = ?
            """,
            (now, thread),
        )
        self._conn.commit()
        labels = list(_EMAIL_TASK_DEFAULT_LABELS if sender_trusted else _EMAIL_TASK_EXTERNAL_REVIEW_LABELS)
        labels = [label for label in labels if label != "agent-ready" and label != "triage-pending"]
        labels.append(_EMAIL_TASK_QUARANTINED_LABEL)
        self._upsert_thread_task_state(
            thread_id=thread,
            status="blocked",
            labels=labels,
            metadata_patch={
                "email_triage_routing": "quarantine",
                "email_triage_note": str(note or "").strip(),
            },
        )
        return True

    def _upsert_thread_task_state(
        self,
        *,
        thread_id: str,
        status: str,
        labels: list[str],
        metadata_patch: Optional[dict[str, Any]] = None,
    ) -> bool:
        try:
            from universal_agent.task_hub import ensure_schema, get_item, upsert_item

            ensure_schema(self._conn)
            task_id = _deterministic_task_id(thread_id)
            current = get_item(self._conn, task_id) or {}
            metadata = dict(current.get("metadata") or {})
            if metadata_patch:
                metadata.update(metadata_patch)

            # Ensure a human-readable title is always set.  When the
            # task_hub entry doesn't have a proper title yet (or it
            # equals the raw task_id), look up the email subject from
            # email_task_mappings so the dashboard card is readable.
            existing_title = str(current.get("title") or "").strip()
            title: Optional[str] = None
            if not existing_title or existing_title == task_id:
                mapping = self._get_mapping(thread_id)
                subject = str((mapping or {}).get("subject") or "").strip() if mapping else ""
                title = f"📧 {subject}" if subject else "📧 Email Task (pending classification)"

            item: dict[str, Any] = {
                "task_id": task_id,
                "source_kind": _EMAIL_TASK_SOURCE_KIND,
                "status": status,
                "labels": labels,
                "agent_ready": "agent-ready" in labels,
                "metadata": metadata,
            }
            if title is not None:
                item["title"] = title
            upsert_item(self._conn, item)
            return True
        except Exception as exc:
            logger.warning("📧→📋 Thread task state update failed thread=%s: %s", thread_id, exc)
            return False

    # ── Internal Methods ──────────────────────────────────────────────────

    def _get_mapping(self, thread_id: str) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM email_task_mappings WHERE thread_id = ? LIMIT 1",
            (thread_id,),
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _classify_master_key(subject: str) -> str:
        """Derive a grouping key from an email subject.

        Strips Re:/Fwd: prefixes and normalizes to a lowercase slug.
        Emails with the same cleaned subject get the same master key,
        grouping them under one parent task in the Task Hub.
        """
        clean = _REPLY_PREFIX_RE.sub("", (subject or "")).strip()
        # Iteratively strip nested Re:/Fwd: prefixes
        while True:
            stripped = _REPLY_PREFIX_RE.sub("", clean).strip()
            if stripped == clean:
                break
            clean = stripped
        if not clean:
            return "general-email"
        # Create URL-safe slug: lowercase, collapse whitespace, truncate
        slug = re.sub(r"[^a-z0-9]+", "-", clean.lower()).strip("-")[:60]
        return slug or "general-email"

    def _upsert_task_hub(
        self,
        *,
        task_id: str,
        subject: str,
        sender_email: str,
        reply_text: str,
        thread_id: str,
        message_count: int,
        session_key: str,
        workflow_run_id: str = "",
        workflow_attempt_id: str = "",
        provider_session_id: str = "",
        labels: list[str] | None = None,
        priority: int | None = None,
        due_at: str | None = None,
        initial_status: str = "open",
        real_thread_id: str = "",
        real_message_id: str = "",
        target_agent: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a Task Hub entry for this email task.

        Parameters
        ----------
        priority : int | None
            Task Hub numeric priority (0-3). ``None`` falls through to
            a default of 2 (medium).
        initial_status : str
            Starting status — defaults to ``open``.  The heartbeat's
            ``claim_next_dispatch_tasks`` atomically transitions to
            ``in_progress`` when it seizes the task for execution.
        """
        try:
            from universal_agent.task_hub import ensure_schema, upsert_item

            ensure_schema(self._conn)

            # Truncate reply for description (keep it concise in the task hub)
            description = reply_text[:2000] if reply_text else ""
            if message_count > 1:
                description = f"[Email thread update #{message_count}]\n{description}"

            task_labels = labels or list(_EMAIL_TASK_DEFAULT_LABELS)

            metadata: dict[str, Any] = {
                "email_thread_id": str(real_thread_id or thread_id),
                "email_message_id": str(real_message_id),
                "sender_email": sender_email,
                "message_count": message_count,
                "session_key": session_key,
                "source_system": "agentmail",
                "delivery_mode": infer_delivery_mode(subject=subject, body=reply_text),
                "canonical_execution_owner": "todo_dispatcher",
                "workflow_manifest": _build_email_execution_manifest(subject=subject, body=reply_text),
            }

            # ── Security: neutralize manifest for untrusted senders ──────────
            # External/untriaged emails must NEVER carry a manifest that
            # permits code mutation, even if the content *looks* like an
            # instruction.  This is a defense-in-depth measure.
            _is_untrusted = "external-untriaged" in (labels or [])
            if _is_untrusted:
                metadata["workflow_manifest"] = {
                    "workflow_kind": "data_only",
                    "delivery_mode": "fast_summary",
                    "requires_pdf": False,
                    "final_channel": "email",
                    "canonical_executor": "simone_first",
                    "codebase_root": "",
                    "repo_mutation_allowed": False,
                }

            # Inject target_agent into the workflow manifest so the ToDo
            # dispatcher can route this task directly to the named VP.
            if target_agent and target_agent not in ("simone", "simone_first"):
                metadata["workflow_manifest"]["target_agent"] = target_agent
            if workflow_run_id:
                metadata["workflow_run_id"] = workflow_run_id
            if workflow_attempt_id:
                metadata["workflow_attempt_id"] = workflow_attempt_id
            if provider_session_id:
                metadata["provider_session_id"] = provider_session_id

            resolved_priority = priority if priority is not None else 2
            resolved_source_ref = str(real_thread_id or thread_id)

            item = {
                "task_id": task_id,
                "source_kind": _EMAIL_TASK_SOURCE_KIND,
                "source_ref": f"agentmail_thread:{resolved_source_ref}",
                "title": f"📧 {subject}" if subject else "📧 Email Task",
                "description": description,
                "project_key": _EMAIL_TASK_PROJECT_KEY,
                "priority": resolved_priority,
                "due_at": due_at,
                "labels": task_labels,
                "status": initial_status,
                "agent_ready": "agent-ready" in task_labels,
                "must_complete": False,
                "metadata": metadata,
            }
            return upsert_item(self._conn, item)
        except Exception as exc:
            logger.warning("📧→📋 Task Hub upsert failed for task_id=%s: %s", task_id, exc)
            return {}
