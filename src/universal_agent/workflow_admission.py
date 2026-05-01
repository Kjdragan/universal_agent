from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import sqlite3
from typing import Any, Callable, Literal, Optional, TypeVar
import uuid

from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import (
    create_run_attempt,
    get_run,
    get_run_attempt,
    update_run_attempt,
    update_run_provider_session,
    upsert_run,
)
from universal_agent.run_workspace import ensure_run_workspace_scaffold

logger = logging.getLogger(__name__)

# ── Run status constants ──
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_BLOCKED = "blocked"
STATUS_PAUSED = "paused"
STATUS_WAITING_ON_HUMAN = "waiting_on_human"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_SUCCEEDED = "succeeded"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_NEEDS_REVIEW = "needs_review"

_ACTIVE_RUN_STATUSES = {
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_BLOCKED,
    STATUS_PAUSED,
    STATUS_WAITING_ON_HUMAN,
    STATUS_IN_PROGRESS,
}
_SUCCESS_RUN_STATUSES = {STATUS_COMPLETED, STATUS_SUCCEEDED, STATUS_SUCCESS}
_FAILED_RUN_STATUSES = {STATUS_FAILED}

# ── Admission decision action constants ──
ACTION_START_NEW_RUN = "start_new_run"
ACTION_ATTACH_TO_EXISTING = "attach_to_existing_run"
ACTION_START_NEW_ATTEMPT = "start_new_attempt"
ACTION_SKIP_DUPLICATE = "skip_duplicate"
ACTION_DEFER = "defer"
ACTION_ESCALATE_REVIEW = "escalate_review"

# ── Admission reason constants ──
REASON_EXISTING_COMPLETED = "existing_completed_run"
REASON_ACTIVE_RUN_DEFERRED = "active_run_deferred"
REASON_ACTIVE_RUN_EXISTS = "active_run_exists"
REASON_RETRY_EXHAUSTED = "retry_exhausted"
REASON_RETRYABLE_FAILURE = "retryable_failure"
REASON_NEW_RUN_CREATED = "new_run_created"
REASON_UNKNOWN_RUN = "unknown_run"
REASON_RETRY_QUEUED = "retry_queued"

# ── Default failure class ──
DEFAULT_FAILURE_CLASS = "dispatch_failed"
_T = TypeVar("_T")


def _is_sqlite_lock_error(exc: sqlite3.OperationalError) -> bool:
    detail = str(exc or "").strip().lower()
    return "database is locked" in detail or "database table is locked" in detail


@dataclass(frozen=True)
class WorkflowTrigger:
    run_kind: str
    trigger_source: str
    dedup_key: str
    payload_json: str | None
    priority: int | None
    run_policy: str
    interrupt_policy: str
    external_origin: str | None = None
    external_origin_id: str | None = None
    external_correlation_id: str | None = None


@dataclass(frozen=True)
class WorkflowAdmissionDecision:
    action: Literal[
        "start_new_run",
        "attach_to_existing_run",
        "start_new_attempt",
        "skip_duplicate",
        "defer",
        "escalate_review",
    ]
    run_id: str | None
    attempt_id: str | None
    reason: str


class WorkflowAdmissionService:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or get_runtime_db_path()

    def _connect(self) -> sqlite3.Connection:
        conn = connect_runtime_db(self.db_path)
        ensure_schema(conn)
        return conn

    def _run_with_retry(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        """Execute a DB operation with a single attempt.

        Retry logic is handled at the async caller level (hooks_service)
        using proper ``await asyncio.sleep()`` to avoid blocking the event loop.
        The SQLite busy_timeout (15s) provides initial wait-for-lock at the
        connection level.
        """
        conn = self._connect()
        try:
            return operation(conn)
        except sqlite3.OperationalError as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            if _is_sqlite_lock_error(exc):
                logger.warning("Workflow admission DB locked: %s", exc)
            raise
        finally:
            conn.close()

    @staticmethod
    def _parse_run_spec(row: sqlite3.Row | None) -> dict:
        if row is None:
            return {}
        raw = row["run_spec_json"]
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _latest_matching_run(self, conn: sqlite3.Connection, trigger: WorkflowTrigger) -> Optional[sqlite3.Row]:
        dedup_key = str(trigger.dedup_key or "").strip()
        if not dedup_key:
            return None
        return conn.execute(
            """
            SELECT *
            FROM runs
            WHERE run_kind = ?
              AND dedup_key = ?
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            (trigger.run_kind, dedup_key),
        ).fetchone()

    @staticmethod
    def _generate_run_id(trigger: WorkflowTrigger) -> str:
        prefix = "".join(ch if ch.isalnum() else "_" for ch in trigger.run_kind.lower()).strip("_") or "workflow"
        return f"run_{prefix}_{uuid.uuid4().hex[:12]}"

    def admit(
        self,
        trigger: WorkflowTrigger,
        *,
        entrypoint: str,
        workspace_dir: Optional[str] = None,
        retryable_failure: bool = False,
        max_attempts: int = 3,
    ) -> WorkflowAdmissionDecision:
        payload_json = str(trigger.payload_json or "").strip() or None
        run_spec = {
            "payload_json": payload_json,
            "workspace_dir": workspace_dir,
            "priority": trigger.priority,
        }
        def _operation(conn: sqlite3.Connection) -> WorkflowAdmissionDecision:
            existing = self._latest_matching_run(conn, trigger)
            if existing is not None:
                status = str(existing["status"] or "").strip().lower()
                run_id = str(existing["run_id"] or "").strip() or None
                latest_attempt_id = str(existing["latest_attempt_id"] or "").strip() or None
                attempt_count = int(existing["attempt_count"] or 0)

                if status in _SUCCESS_RUN_STATUSES:
                    return WorkflowAdmissionDecision(ACTION_SKIP_DUPLICATE, run_id, latest_attempt_id, REASON_EXISTING_COMPLETED)
                if status in _ACTIVE_RUN_STATUSES:
                    if trigger.interrupt_policy == "defer_if_foreground":
                        return WorkflowAdmissionDecision(ACTION_DEFER, run_id, latest_attempt_id, REASON_ACTIVE_RUN_DEFERRED)
                    return WorkflowAdmissionDecision(ACTION_ATTACH_TO_EXISTING, run_id, latest_attempt_id, REASON_ACTIVE_RUN_EXISTS)
                if retryable_failure and status in _FAILED_RUN_STATUSES:
                    if attempt_count >= max(1, int(max_attempts)):
                        upsert_run(
                            conn,
                            run_id=run_id or "",
                            entrypoint=entrypoint,
                            run_spec=self._parse_run_spec(existing) or run_spec,
                            status=STATUS_NEEDS_REVIEW,
                            workspace_dir=workspace_dir,
                            run_kind=trigger.run_kind,
                            trigger_source=trigger.trigger_source,
                            dedup_key=trigger.dedup_key,
                            run_policy=trigger.run_policy,
                            interrupt_policy=trigger.interrupt_policy,
                            terminal_reason=REASON_RETRY_EXHAUSTED,
                            external_origin=trigger.external_origin,
                            external_origin_id=trigger.external_origin_id,
                            external_correlation_id=trigger.external_correlation_id,
                        )
                        conn.commit()
                        return WorkflowAdmissionDecision(ACTION_ESCALATE_REVIEW, run_id, latest_attempt_id, REASON_RETRY_EXHAUSTED)
                    attempt_id = create_run_attempt(
                        conn,
                        run_id or "",
                        status=STATUS_QUEUED,
                        retry_reason=REASON_RETRYABLE_FAILURE,
                    )
                    attempt_row = get_run_attempt(conn, attempt_id)
                    effective_workspace_dir = workspace_dir or str(existing["workspace_dir"] or "").strip() or None
                    if effective_workspace_dir and attempt_row is not None:
                        ensure_run_workspace_scaffold(
                            workspace_dir=effective_workspace_dir,
                            run_id=run_id or "",
                            attempt_id=attempt_id,
                            attempt_number=int(attempt_row["attempt_number"] or 0),
                            status=STATUS_QUEUED,
                            run_kind=trigger.run_kind,
                            trigger_source=trigger.trigger_source,
                        )
                    upsert_run(
                        conn,
                        run_id=run_id or "",
                        entrypoint=entrypoint,
                        run_spec=self._parse_run_spec(existing) or run_spec,
                        status=STATUS_QUEUED,
                        workspace_dir=workspace_dir,
                        run_kind=trigger.run_kind,
                        trigger_source=trigger.trigger_source,
                        dedup_key=trigger.dedup_key,
                        run_policy=trigger.run_policy,
                        interrupt_policy=trigger.interrupt_policy,
                        external_origin=trigger.external_origin,
                        external_origin_id=trigger.external_origin_id,
                        external_correlation_id=trigger.external_correlation_id,
                    )
                    conn.commit()
                    return WorkflowAdmissionDecision(ACTION_START_NEW_ATTEMPT, run_id, attempt_id, REASON_RETRYABLE_FAILURE)

            run_id = self._generate_run_id(trigger)
            upsert_run(
                conn,
                run_id=run_id,
                entrypoint=entrypoint,
                run_spec=run_spec,
                status=STATUS_QUEUED,
                workspace_dir=workspace_dir,
                run_kind=trigger.run_kind,
                trigger_source=trigger.trigger_source,
                dedup_key=trigger.dedup_key,
                run_policy=trigger.run_policy,
                interrupt_policy=trigger.interrupt_policy,
                external_origin=trigger.external_origin,
                external_origin_id=trigger.external_origin_id,
                external_correlation_id=trigger.external_correlation_id,
            )
            attempt_id = create_run_attempt(conn, run_id, status=STATUS_QUEUED)
            attempt_row = get_run_attempt(conn, attempt_id)
            if workspace_dir and attempt_row is not None:
                ensure_run_workspace_scaffold(
                    workspace_dir=workspace_dir,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    attempt_number=int(attempt_row["attempt_number"] or 0),
                    status=STATUS_QUEUED,
                    run_kind=trigger.run_kind,
                    trigger_source=trigger.trigger_source,
                )
            conn.commit()
            return WorkflowAdmissionDecision(ACTION_START_NEW_RUN, run_id, attempt_id, REASON_NEW_RUN_CREATED)
        return self._run_with_retry(_operation)

    def mark_completed(
        self,
        run_id: str,
        *,
        attempt_id: Optional[str],
        summary: Optional[dict] = None,
    ) -> None:
        def _operation(conn: sqlite3.Connection) -> None:
            row = get_run(conn, run_id)
            if row is None:
                return
            upsert_run(
                conn,
                run_id=run_id,
                entrypoint=str(row["entrypoint"] or "workflow_admission"),
                run_spec=self._parse_run_spec(row),
                status=STATUS_COMPLETED,
                workspace_dir=row["workspace_dir"],
                run_kind=row["run_kind"],
                trigger_source=row["trigger_source"],
                dedup_key=row["dedup_key"],
                run_policy=row["run_policy"],
                interrupt_policy=row["interrupt_policy"],
                external_origin=row["external_origin"],
                external_origin_id=row["external_origin_id"],
                external_correlation_id=row["external_correlation_id"],
            )
            if attempt_id:
                update_run_attempt(
                    conn,
                    attempt_id,
                    status=STATUS_COMPLETED,
                    summary=summary,
                    promote_to_canonical=False,
                )
                attempt_row = get_run_attempt(conn, attempt_id)
                if row["workspace_dir"] and attempt_row is not None:
                    ensure_run_workspace_scaffold(
                        workspace_dir=row["workspace_dir"],
                        run_id=run_id,
                        attempt_id=attempt_id,
                        attempt_number=int(attempt_row["attempt_number"] or 0),
                        status=STATUS_COMPLETED,
                        run_kind=str(row["run_kind"] or "") or None,
                        trigger_source=str(row["trigger_source"] or "") or None,
                    )
            conn.commit()
        self._run_with_retry(_operation)

    def mark_running(
        self,
        run_id: str,
        *,
        attempt_id: Optional[str],
        provider_session_id: Optional[str] = None,
        summary: Optional[dict[str, Any]] = None,
    ) -> None:
        def _operation(conn: sqlite3.Connection) -> None:
            row = get_run(conn, run_id)
            if row is None:
                return
            upsert_run(
                conn,
                run_id=run_id,
                entrypoint=str(row["entrypoint"] or "workflow_admission"),
                run_spec=self._parse_run_spec(row),
                status=STATUS_RUNNING,
                workspace_dir=row["workspace_dir"],
                run_kind=row["run_kind"],
                trigger_source=row["trigger_source"],
                dedup_key=row["dedup_key"],
                run_policy=row["run_policy"],
                interrupt_policy=row["interrupt_policy"],
                external_origin=row["external_origin"],
                external_origin_id=row["external_origin_id"],
                external_correlation_id=row["external_correlation_id"],
            )
            if provider_session_id is not None:
                update_run_provider_session(conn, run_id, provider_session_id)
            if attempt_id:
                update_run_attempt(
                    conn,
                    attempt_id,
                    status=STATUS_RUNNING,
                    provider_session_id=provider_session_id,
                    summary=summary,
                )
                attempt_row = get_run_attempt(conn, attempt_id)
                if row["workspace_dir"] and attempt_row is not None:
                    ensure_run_workspace_scaffold(
                        workspace_dir=row["workspace_dir"],
                        run_id=run_id,
                        attempt_id=attempt_id,
                        attempt_number=int(attempt_row["attempt_number"] or 0),
                        status=STATUS_RUNNING,
                        run_kind=str(row["run_kind"] or "") or None,
                        trigger_source=str(row["trigger_source"] or "") or None,
                    )
            conn.commit()
        self._run_with_retry(_operation)

    def mark_blocked(
        self,
        run_id: str,
        *,
        attempt_id: Optional[str],
        reason: str,
        summary: Optional[dict[str, Any]] = None,
    ) -> None:
        def _operation(conn: sqlite3.Connection) -> None:
            row = get_run(conn, run_id)
            if row is None:
                return
            upsert_run(
                conn,
                run_id=run_id,
                entrypoint=str(row["entrypoint"] or "workflow_admission"),
                run_spec=self._parse_run_spec(row),
                status=STATUS_BLOCKED,
                workspace_dir=row["workspace_dir"],
                run_kind=row["run_kind"],
                trigger_source=row["trigger_source"],
                dedup_key=row["dedup_key"],
                run_policy=row["run_policy"],
                interrupt_policy=row["interrupt_policy"],
                terminal_reason=reason,
                external_origin=row["external_origin"],
                external_origin_id=row["external_origin_id"],
                external_correlation_id=row["external_correlation_id"],
            )
            if attempt_id:
                update_run_attempt(
                    conn,
                    attempt_id,
                    status=STATUS_BLOCKED,
                    failure_reason=reason,
                    summary=summary,
                )
                attempt_row = get_run_attempt(conn, attempt_id)
                if row["workspace_dir"] and attempt_row is not None:
                    ensure_run_workspace_scaffold(
                        workspace_dir=row["workspace_dir"],
                        run_id=run_id,
                        attempt_id=attempt_id,
                        attempt_number=int(attempt_row["attempt_number"] or 0),
                        status=STATUS_BLOCKED,
                        run_kind=str(row["run_kind"] or "") or None,
                        trigger_source=str(row["trigger_source"] or "") or None,
                    )
            conn.commit()
        self._run_with_retry(_operation)

    def mark_needs_review(
        self,
        run_id: str,
        *,
        attempt_id: Optional[str],
        reason: str,
        failure_class: str,
        summary: Optional[dict[str, Any]] = None,
    ) -> None:
        def _operation(conn: sqlite3.Connection) -> None:
            row = get_run(conn, run_id)
            if row is None:
                return
            upsert_run(
                conn,
                run_id=run_id,
                entrypoint=str(row["entrypoint"] or "workflow_admission"),
                run_spec=self._parse_run_spec(row),
                status=STATUS_NEEDS_REVIEW,
                workspace_dir=row["workspace_dir"],
                run_kind=row["run_kind"],
                trigger_source=row["trigger_source"],
                dedup_key=row["dedup_key"],
                run_policy=row["run_policy"],
                interrupt_policy=row["interrupt_policy"],
                terminal_reason=reason,
                external_origin=row["external_origin"],
                external_origin_id=row["external_origin_id"],
                external_correlation_id=row["external_correlation_id"],
            )
            if attempt_id:
                update_run_attempt(
                    conn,
                    attempt_id,
                    status=STATUS_FAILED,
                    failure_class=failure_class,
                    failure_reason=reason,
                    terminal_reason=reason,
                    summary=summary,
                )
                attempt_row = get_run_attempt(conn, attempt_id)
                if row["workspace_dir"] and attempt_row is not None:
                    ensure_run_workspace_scaffold(
                        workspace_dir=row["workspace_dir"],
                        run_id=run_id,
                        attempt_id=attempt_id,
                        attempt_number=int(attempt_row["attempt_number"] or 0),
                        status=STATUS_FAILED,
                        run_kind=str(row["run_kind"] or "") or None,
                        trigger_source=str(row["trigger_source"] or "") or None,
                    )
            conn.commit()
        self._run_with_retry(_operation)

    def queue_retry(
        self,
        trigger: WorkflowTrigger,
        *,
        entrypoint: str,
        run_id: str,
        attempt_id: Optional[str],
        workspace_dir: Optional[str],
        failure_reason: str,
        failure_class: str,
        max_attempts: int,
    ) -> WorkflowAdmissionDecision:
        def _operation(conn: sqlite3.Connection) -> WorkflowAdmissionDecision:
            row = get_run(conn, run_id)
            if row is None:
                return WorkflowAdmissionDecision(ACTION_ESCALATE_REVIEW, run_id, attempt_id, REASON_UNKNOWN_RUN)
            attempt_row = get_run_attempt(conn, attempt_id) if attempt_id else None
            existing_attempt_count = int(row["attempt_count"] or 0)
            current_attempt_number = int(attempt_row["attempt_number"] or 0) if attempt_row is not None else existing_attempt_count
            effective_attempt_count = max(existing_attempt_count, current_attempt_number)
            effective_workspace_dir = workspace_dir or str(row["workspace_dir"] or "").strip() or None

            if attempt_id and attempt_row is not None:
                update_run_attempt(
                    conn,
                    attempt_id,
                    status=STATUS_FAILED,
                    failure_class=failure_class,
                    failure_reason=failure_reason,
                    terminal_reason=failure_reason,
                )
                if effective_workspace_dir:
                    ensure_run_workspace_scaffold(
                        workspace_dir=effective_workspace_dir,
                        run_id=run_id,
                        attempt_id=attempt_id,
                        attempt_number=int(attempt_row["attempt_number"] or 0),
                        status=STATUS_FAILED,
                        run_kind=str(row["run_kind"] or "") or None,
                        trigger_source=str(row["trigger_source"] or "") or None,
                    )

            if effective_attempt_count >= max(1, int(max_attempts)):
                upsert_run(
                    conn,
                    run_id=run_id,
                    entrypoint=str(row["entrypoint"] or entrypoint or "workflow_admission"),
                    run_spec=self._parse_run_spec(row),
                    status=STATUS_NEEDS_REVIEW,
                    workspace_dir=effective_workspace_dir,
                    run_kind=trigger.run_kind,
                    trigger_source=trigger.trigger_source,
                    dedup_key=trigger.dedup_key,
                    run_policy=trigger.run_policy,
                    interrupt_policy=trigger.interrupt_policy,
                    terminal_reason=failure_reason,
                    external_origin=trigger.external_origin,
                    external_origin_id=trigger.external_origin_id,
                    external_correlation_id=trigger.external_correlation_id,
                )
                conn.commit()
                return WorkflowAdmissionDecision(ACTION_ESCALATE_REVIEW, run_id, attempt_id, REASON_RETRY_EXHAUSTED)

            next_attempt_id = create_run_attempt(
                conn,
                run_id,
                status=STATUS_QUEUED,
                retry_reason=failure_reason,
            )
            next_attempt_row = get_run_attempt(conn, next_attempt_id)
            upsert_run(
                conn,
                run_id=run_id,
                entrypoint=str(row["entrypoint"] or entrypoint or "workflow_admission"),
                run_spec=self._parse_run_spec(row),
                status=STATUS_QUEUED,
                workspace_dir=effective_workspace_dir,
                run_kind=trigger.run_kind,
                trigger_source=trigger.trigger_source,
                dedup_key=trigger.dedup_key,
                run_policy=trigger.run_policy,
                interrupt_policy=trigger.interrupt_policy,
                external_origin=trigger.external_origin,
                external_origin_id=trigger.external_origin_id,
                external_correlation_id=trigger.external_correlation_id,
            )
            if effective_workspace_dir and next_attempt_row is not None:
                ensure_run_workspace_scaffold(
                    workspace_dir=effective_workspace_dir,
                    run_id=run_id,
                    attempt_id=next_attempt_id,
                    attempt_number=int(next_attempt_row["attempt_number"] or 0),
                    status=STATUS_QUEUED,
                    run_kind=str(row["run_kind"] or "") or None,
                    trigger_source=str(row["trigger_source"] or "") or None,
                )
            conn.commit()
            return WorkflowAdmissionDecision(ACTION_START_NEW_ATTEMPT, run_id, next_attempt_id, REASON_RETRY_QUEUED)
        return self._run_with_retry(_operation)

    def mark_failed(
        self,
        run_id: str,
        *,
        attempt_id: Optional[str],
        failure_reason: str,
        failure_class: str = DEFAULT_FAILURE_CLASS,
    ) -> None:
        def _operation(conn: sqlite3.Connection) -> None:
            row = get_run(conn, run_id)
            if row is None:
                return
            upsert_run(
                conn,
                run_id=run_id,
                entrypoint=str(row["entrypoint"] or "workflow_admission"),
                run_spec=self._parse_run_spec(row),
                status=STATUS_FAILED,
                workspace_dir=row["workspace_dir"],
                run_kind=row["run_kind"],
                trigger_source=row["trigger_source"],
                dedup_key=row["dedup_key"],
                run_policy=row["run_policy"],
                interrupt_policy=row["interrupt_policy"],
                terminal_reason=failure_reason,
                external_origin=row["external_origin"],
                external_origin_id=row["external_origin_id"],
                external_correlation_id=row["external_correlation_id"],
            )
            if attempt_id:
                update_run_attempt(
                    conn,
                    attempt_id,
                    status=STATUS_FAILED,
                    failure_class=failure_class,
                    failure_reason=failure_reason,
                    terminal_reason=failure_reason,
                )
                attempt_row = get_run_attempt(conn, attempt_id)
                if row["workspace_dir"] and attempt_row is not None:
                    ensure_run_workspace_scaffold(
                        workspace_dir=row["workspace_dir"],
                        run_id=run_id,
                        attempt_id=attempt_id,
                        attempt_number=int(attempt_row["attempt_number"] or 0),
                        status=STATUS_FAILED,
                        run_kind=str(row["run_kind"] or "") or None,
                        trigger_source=str(row["trigger_source"] or "") or None,
                    )
            conn.commit()
        self._run_with_retry(_operation)
