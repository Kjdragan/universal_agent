"""Email → Task Bridge: materializes inbound emails as trackable tasks.

Pure-Python service (no LLM calls) that converts trusted inbound AgentMail
emails into Task Hub entries so they appear on the To-Do List dashboard
and get picked up by the heartbeat scheduler.

Key design decisions:
  - Thread-level deduplication: one task per email thread; subsequent emails
    on the same thread UPDATE the existing task.
  - Task Hub entry → source_kind='email', labels=['email-task','agent-ready'].
  - HEARTBEAT.md auto-append: active email tasks are written into the
    heartbeat file so the heartbeat cron job picks them up.
  - All operations are idempotent and crash-safe (SQLite UPSERT semantics).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_HEARTBEAT_SECTION_HEADER = "## Email-Driven Active Tasks"
_HEARTBEAT_SECTION_FOOTER = "<!-- end email-driven-tasks -->"
_EMAIL_TASK_SOURCE_KIND = "email"
_EMAIL_TASK_PROJECT_KEY = "immediate"
_EMAIL_TASK_DEFAULT_LABELS = ["email-task", "agent-ready"]

# Subject prefixes to strip when computing the master key
_REPLY_PREFIX_RE = re.compile(r"^(Re|Fwd|Fw):\s*", re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _deterministic_task_id(thread_id: str) -> str:
    """Generate a stable, deterministic task_id from an email thread_id."""
    h = hashlib.sha256(f"email_thread:{thread_id}".encode()).hexdigest()[:16]
    return f"email:{h}"


def _workflow_lineage_lines(
    *,
    workflow_run_id: str = "",
    workflow_attempt_id: str = "",
    provider_session_id: str = "",
) -> list[str]:
    lines: list[str] = []
    if workflow_run_id:
        lines.append(f"Workflow Run: {workflow_run_id}")
    if workflow_attempt_id:
        lines.append(f"Workflow Attempt: {workflow_attempt_id}")
    if provider_session_id:
        lines.append(f"Provider Session: {provider_session_id}")
    return lines


def _workflow_lineage_comment(
    *,
    workflow_run_id: str = "",
    workflow_attempt_id: str = "",
    provider_session_id: str = "",
) -> str:
    parts: list[str] = []
    if workflow_run_id:
        parts.append(f"run={workflow_run_id}")
    if workflow_attempt_id:
        parts.append(f"attempt={workflow_attempt_id}")
    if provider_session_id:
        parts.append(f"provider_session={provider_session_id}")
    if not parts:
        return ""
    return f"**Workflow linkage:** {' | '.join(parts)}"


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
    ]:
        try:
            conn.execute(
                f"ALTER TABLE email_task_mappings ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}"
            )
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()


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
        priority: int | None = None,
        due_at: str | None = None,
        workflow_run_id: str = "",
        workflow_attempt_id: str = "",
        provider_session_id: str = "",
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
        if not sender_trusted:
            email_labels = ["email-task", "external-untriaged"]
            if security_classification:
                email_labels.append(f"security-{security_classification}")

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
                    workflow_run_id = ?, workflow_attempt_id = ?, provider_session_id = ?
                WHERE thread_id = ?
                """,
                (subject, sender_email, message_id, message_count, now,
                 master_key, int(sender_trusted), security_classification,
                 resolved_workflow_run_id, resolved_workflow_attempt_id, resolved_provider_session_id,
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
                     message_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, 1, ?, ?)
                """,
                (thread_id, task_id, master_key, subject, sender_email,
                 int(sender_trusted), security_classification,
                 resolved_workflow_run_id, resolved_workflow_attempt_id, resolved_provider_session_id,
                 message_id, now, now),
            )
        self._conn.commit()

        # ② Create/update Task Hub entry
        # Status starts as 'in_progress' to prevent double-claiming by the
        # heartbeat while the hook session is actively running.  If the
        # session crashes, the task will be reverted to 'open' by the
        # hook finalization logic.
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
            initial_status="in_progress",
        )

        # ③ Update HEARTBEAT.md
        self._update_heartbeat(subject=subject, thread_id=thread_id, task_id=task_id)

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
            from universal_agent.task_hub import upsert_item, ensure_schema

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
        try:
            from universal_agent.task_hub import update_item_status
            task_id = _deterministic_task_id(thread_id)
            update_item_status(
                self._conn, task_id,
                status="waiting-on-reply",
                labels=["email-task", "waiting-on-reply"],
            )
        except Exception:
            pass

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
        try:
            from universal_agent.task_hub import update_item_status
            task_id = _deterministic_task_id(thread_id)
            update_item_status(
                self._conn, task_id,
                status="open",
                labels=["email-task", "agent-ready"],
            )
        except Exception:
            pass

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
    ) -> dict[str, Any]:
        """Create or update a Task Hub entry for this email task.

        Parameters
        ----------
        priority : int | None
            Task Hub numeric priority (0-3). ``None`` falls through to
            a default of 2 (medium).
        initial_status : str
            Starting status — defaults to ``open`` but callers may pass
            ``in_progress`` to prevent the heartbeat from double-claiming
            while the hook session is already running.
        """
        try:
            from universal_agent.task_hub import upsert_item, ensure_schema

            ensure_schema(self._conn)

            # Truncate reply for description (keep it concise in the task hub)
            description = reply_text[:2000] if reply_text else ""
            if message_count > 1:
                description = f"[Email thread update #{message_count}]\n{description}"

            task_labels = labels or list(_EMAIL_TASK_DEFAULT_LABELS)

            metadata: dict[str, Any] = {
                "email_thread_id": thread_id,
                "sender_email": sender_email,
                "message_count": message_count,
                "session_key": session_key,
                "source_system": "agentmail",
            }
            if workflow_run_id:
                metadata["workflow_run_id"] = workflow_run_id
            if workflow_attempt_id:
                metadata["workflow_attempt_id"] = workflow_attempt_id
            if provider_session_id:
                metadata["provider_session_id"] = provider_session_id

            resolved_priority = priority if priority is not None else 2

            item = {
                "task_id": task_id,
                "source_kind": _EMAIL_TASK_SOURCE_KIND,
                "source_ref": f"agentmail_thread:{thread_id}",
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


    def _update_heartbeat(
        self,
        *,
        subject: str,
        thread_id: str,
        task_id: str,
    ) -> None:
        """Append or update email task entry in HEARTBEAT.md."""
        try:
            hb_path = Path(self._heartbeat_path)
            if not hb_path.exists():
                logger.debug("📧→📋 HEARTBEAT.md not found at %s, skipping", hb_path)
                return

            content = hb_path.read_text(encoding="utf-8")
            now_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            entry_marker = f"<!-- email-task:{thread_id} -->"

            # Build the task entry line
            safe_subject = subject.replace("\n", " ")[:80]
            entry_line = (
                f"- [ ] Email Task: {safe_subject} "
                f"(thread: {thread_id[:20]}, task: {task_id}) "
                f"{entry_marker}\n"
                f"  - Last update: {now_date}. Follow up on email conversation.\n"
            )

            if entry_marker in content:
                # Update existing entry — replace the old lines
                pattern = re.compile(
                    rf"- \[ \] Email Task:.*{re.escape(entry_marker)}.*\n"
                    rf"(?:  - .*\n)*",
                    re.MULTILINE,
                )
                content = pattern.sub(entry_line, content)
            elif _HEARTBEAT_SECTION_HEADER in content:
                # Section exists, append before footer
                if _HEARTBEAT_SECTION_FOOTER in content:
                    content = content.replace(
                        _HEARTBEAT_SECTION_FOOTER,
                        f"{entry_line}{_HEARTBEAT_SECTION_FOOTER}",
                    )
                else:
                    # Section exists but no footer — append at end of section
                    idx = content.index(_HEARTBEAT_SECTION_HEADER) + len(_HEARTBEAT_SECTION_HEADER)
                    # Find end of section header line
                    nl_idx = content.find("\n", idx)
                    if nl_idx >= 0:
                        content = (
                            content[: nl_idx + 1]
                            + entry_line
                            + content[nl_idx + 1 :]
                        )
            else:
                # Section doesn't exist — create it before Response Policy
                insert_before = "## Response Policy"
                if insert_before in content:
                    content = content.replace(
                        insert_before,
                        (
                            f"{_HEARTBEAT_SECTION_HEADER}\n"
                            f"{entry_line}"
                            f"{_HEARTBEAT_SECTION_FOOTER}\n\n"
                            f"{insert_before}"
                        ),
                    )
                else:
                    # Append at end
                    content += (
                        f"\n{_HEARTBEAT_SECTION_HEADER}\n"
                        f"{entry_line}"
                        f"{_HEARTBEAT_SECTION_FOOTER}\n"
                    )

            hb_path.write_text(content, encoding="utf-8")
            logger.debug("📧→📋 HEARTBEAT.md updated for thread=%s", thread_id)
        except Exception as exc:
            logger.warning("📧→📋 HEARTBEAT.md update failed: %s", exc)
