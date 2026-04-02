from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


TASK_STATUS_OPEN = "open"
TASK_STATUS_IN_PROGRESS = "in_progress"
TASK_STATUS_BLOCKED = "blocked"
TASK_STATUS_REVIEW = "needs_review"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_PARKED = "parked"
TASK_STATUS_DELEGATED = "delegated"           # VP is actively working this
TASK_STATUS_PENDING_REVIEW = "pending_review"  # VP done, Simone sign-off needed
TASK_STATUS_SCHEDULED = "scheduled"            # Time-bound: cron trigger will execute at due_at

TERMINAL_STATUSES = {TASK_STATUS_COMPLETED, TASK_STATUS_PARKED}
ACTIVE_STATUSES = {
    TASK_STATUS_OPEN, TASK_STATUS_IN_PROGRESS, TASK_STATUS_BLOCKED,
    TASK_STATUS_REVIEW, TASK_STATUS_DELEGATED, TASK_STATUS_PENDING_REVIEW,
    TASK_STATUS_SCHEDULED,
}
VALID_ACTIONS = {"seize", "reject", "block", "unblock", "review", "complete", "park", "snooze", "delegate", "approve"}

TRIGGER_TYPES = {"immediate", "scheduled", "event_triggered", "human_approved", "brainstorm", "heartbeat_poll"}
DEFAULT_TRIGGER_TYPE = "heartbeat_poll"

REFINEMENT_STAGES = {"raw_idea", "interviewing", "exploring", "crystallizing", "decomposing", "actionable"}
DEFAULT_REFINEMENT_STAGE = "raw_idea"
CSI_ROUTING_INCUBATING = "incubating"
CSI_ROUTING_AGENT_ACTIONABLE = "agent_actionable"
CSI_ROUTING_HUMAN_INTERVENTION_REQUIRED = "human_intervention_required"
CSI_ROUTING_STATES = {
    CSI_ROUTING_INCUBATING,
    CSI_ROUTING_AGENT_ACTIONABLE,
    CSI_ROUTING_HUMAN_INTERVENTION_REQUIRED,
}

_CSI_INCIDENT_TEMPORAL_SEGMENT_RE = re.compile(
    r"^(?:\d{6,}|\d{4}-\d{2}-\d{2}(?:t[0-9:\-+.z]+)?|\d{8,14})$",
    re.IGNORECASE,
)
_CSI_INCIDENT_NORMALIZED_EVENT_TYPES = {
    "opportunity_bundle_ready",
    "global_trend_brief_ready",
    "csi_global_brief_review_due",
}

logger = logging.getLogger(__name__)


@dataclass
class TaskHubPolicy:
    agent_threshold: int
    stale_enabled: bool
    stale_min_cycles: int
    stale_min_age_minutes: int




def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _json_loads_obj(raw: Any, *, default: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    if default is None:
        default = {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return dict(default)
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return dict(default)
    return dict(default)


def _json_loads_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            return []
    return []


def _json_dumps(raw: Any) -> str:
    return json.dumps(raw, ensure_ascii=True, separators=(",", ":"))


def _looks_temporal_incident_segment(raw: Any) -> bool:
    token = str(raw or "").strip().lower()
    if not token:
        return False
    if _CSI_INCIDENT_TEMPORAL_SEGMENT_RE.match(token):
        return True
    # Defensive fallback for ISO-like fragments that include delimiters.
    if "t" in token and "-" in token and any(ch.isdigit() for ch in token):
        return True
    return False


def normalize_csi_incident_key(*, incident_key: Any, event_type: Any = None) -> str:
    """Collapse timestamp/version suffixes for selected CSI report-style events."""
    key = str(incident_key or "").strip()
    if not key:
        return ""
    normalized_event_type = str(event_type or "").strip().lower()
    if normalized_event_type not in _CSI_INCIDENT_NORMALIZED_EVENT_TYPES:
        return key
    parts = [segment.strip() for segment in key.split(":")]
    if len(parts) < 3:
        return key
    tail = parts[-1]
    if not _looks_temporal_incident_segment(tail):
        return key
    collapsed = ":".join(parts[:-1]).strip(":").strip()
    return collapsed or key


def _parse_iso(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def current_policy() -> TaskHubPolicy:
    return TaskHubPolicy(
        agent_threshold=max(1, min(10, _safe_int(os.getenv("UA_TASK_AGENT_THRESHOLD"), 3))),
        stale_enabled=str(os.getenv("UA_TASK_STALE_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"},
        stale_min_cycles=max(1, _safe_int(os.getenv("UA_TASK_STALE_MIN_CYCLES"), 4)),
        stale_min_age_minutes=max(10, _safe_int(os.getenv("UA_TASK_STALE_MIN_AGE_MINUTES"), 180)),
    )


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS task_hub_items (
            task_id TEXT PRIMARY KEY,
            source_kind TEXT NOT NULL,
            source_ref TEXT,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            project_key TEXT NOT NULL DEFAULT 'immediate',
            priority INTEGER NOT NULL DEFAULT 1,
            due_at TEXT,
            labels_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'open',
            must_complete INTEGER NOT NULL DEFAULT 0,
            incident_key TEXT,
            workstream_id TEXT,
            subtask_role TEXT,
            parent_task_id TEXT,
            agent_ready INTEGER NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0.0,
            score_confidence REAL NOT NULL DEFAULT 0.0,
            stale_state TEXT NOT NULL DEFAULT 'fresh',
            seizure_state TEXT NOT NULL DEFAULT 'unseized',
            mirror_status TEXT NOT NULL DEFAULT 'internal',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_task_hub_items_project_status ON task_hub_items(project_key, status, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_task_hub_items_agent_ready ON task_hub_items(agent_ready, status, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_task_hub_items_incident ON task_hub_items(incident_key, status, updated_at DESC);

        CREATE TABLE IF NOT EXISTS task_hub_evaluations (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            evaluated_at TEXT NOT NULL,
            agent_id TEXT,
            decision TEXT NOT NULL,
            reason TEXT,
            score REAL,
            score_confidence REAL,
            judge_payload_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_task_hub_eval_task_time ON task_hub_evaluations(task_id, evaluated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_task_hub_eval_decision ON task_hub_evaluations(decision, evaluated_at DESC);

        CREATE TABLE IF NOT EXISTS task_hub_assignments (
            assignment_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            workflow_run_id TEXT,
            workflow_attempt_id TEXT,
            provider_session_id TEXT,
            state TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            result_summary TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_task_hub_assign_task_state ON task_hub_assignments(task_id, state, started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_task_hub_assign_agent_state ON task_hub_assignments(agent_id, state, started_at DESC);

        CREATE TABLE IF NOT EXISTS task_hub_dispatch_queue (
            queue_build_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            rank INTEGER NOT NULL,
            eligible INTEGER NOT NULL DEFAULT 0,
            skip_reason TEXT,
            built_at TEXT NOT NULL,
            PRIMARY KEY (queue_build_id, task_id)
        );
        CREATE INDEX IF NOT EXISTS idx_task_hub_dispatch_rank ON task_hub_dispatch_queue(queue_build_id, eligible, rank);

        CREATE TABLE IF NOT EXISTS task_hub_comments (
            comment_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            author TEXT NOT NULL DEFAULT 'system',
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_task_hub_comments_task ON task_hub_comments(task_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS task_hub_question_queue (
            question_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            question_text TEXT NOT NULL,
            asked_at TEXT NOT NULL,
            answered INTEGER NOT NULL DEFAULT 0,
            answer_text TEXT,
            channel TEXT NOT NULL DEFAULT 'dashboard',
            expires_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_task_hub_questions_task ON task_hub_question_queue(task_id, answered, asked_at DESC);

        CREATE TABLE IF NOT EXISTS task_hub_workstreams (
            workstream_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'active',
            agent_threshold INTEGER NOT NULL DEFAULT 8,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS task_hub_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS task_hub_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            event_key TEXT NOT NULL,
            channel TEXT NOT NULL DEFAULT 'dashboard',
            sent_at TEXT NOT NULL,
            UNIQUE(task_id, event_key)
        );
        CREATE INDEX IF NOT EXISTS idx_task_hub_notifications_task ON task_hub_notifications(task_id, sent_at DESC);
        """
    )
    for ddl in (
        "ALTER TABLE task_hub_assignments ADD COLUMN workflow_run_id TEXT",
        "ALTER TABLE task_hub_assignments ADD COLUMN workflow_attempt_id TEXT",
        "ALTER TABLE task_hub_assignments ADD COLUMN provider_session_id TEXT",
        "ALTER TABLE task_hub_assignments ADD COLUMN workspace_dir TEXT",
        "ALTER TABLE task_hub_items ADD COLUMN trigger_type TEXT NOT NULL DEFAULT 'heartbeat_poll'",
        "ALTER TABLE task_hub_items ADD COLUMN refinement_stage TEXT",
        "ALTER TABLE task_hub_items ADD COLUMN refinement_history_json TEXT NOT NULL DEFAULT '{}'",
        # Idempotency token: prevents re-claim of completed tasks until explicitly cleared
        "ALTER TABLE task_hub_items ADD COLUMN completion_token TEXT DEFAULT NULL",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass
    conn.commit()


def _get_setting(conn: sqlite3.Connection, key: str, *, default: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    ensure_schema(conn)
    row = conn.execute("SELECT value_json FROM task_hub_settings WHERE key = ? LIMIT 1", (key,)).fetchone()
    if row is None:
        return dict(default or {})
    return _json_loads_obj(row["value_json"], default=default or {})


def _set_setting(conn: sqlite3.Connection, key: str, value: dict[str, Any]) -> None:
    ensure_schema(conn)
    conn.execute(
        """
        INSERT INTO task_hub_settings (key, value_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value_json=excluded.value_json,
            updated_at=excluded.updated_at
        """,
        (key, _json_dumps(value), _now_iso()),
    )
    conn.commit()




def hydrate_item(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["labels"] = [str(v) for v in _json_loads_list(item.get("labels_json")) if str(v)]
    item["metadata"] = _json_loads_obj(item.get("metadata_json"), default={})
    item["must_complete"] = bool(item.get("must_complete"))
    item["agent_ready"] = bool(item.get("agent_ready"))
    item["score"] = _safe_float(item.get("score"), 0.0)
    item["score_confidence"] = _safe_float(item.get("score_confidence"), 0.0)
    return item


def _csi_metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    csi = metadata.get("csi")
    return dict(csi) if isinstance(csi, dict) else {}


def _infer_csi_routing_state(item: dict[str, Any], *, threshold: Optional[float] = None) -> str:
    source_kind = str(item.get("source_kind") or "").strip().lower()
    if source_kind != "csi":
        return ""
    csi = _csi_metadata(item)
    state = str(csi.get("routing_state") or "").strip().lower()
    if state in CSI_ROUTING_STATES:
        return state
    score = _safe_float(item.get("score"), 0.0)
    threshold_value = float(threshold if threshold is not None else current_policy().agent_threshold)
    if bool(item.get("agent_ready")) and score >= threshold_value:
        return CSI_ROUTING_AGENT_ACTIONABLE
    return CSI_ROUTING_INCUBATING


def _decorate_csi_routing(item: dict[str, Any], *, threshold: Optional[float] = None) -> dict[str, Any]:
    source_kind = str(item.get("source_kind") or "").strip().lower()
    if source_kind != "csi":
        return item
    metadata = dict(item.get("metadata") or {})
    csi = _csi_metadata(item)
    state = _infer_csi_routing_state(item, threshold=threshold)
    csi["routing_state"] = state
    if "last_maturation_at" not in csi:
        csi["last_maturation_at"] = str(item.get("updated_at") or item.get("created_at") or "")
    metadata["csi"] = csi
    item["metadata"] = metadata
    item["csi_routing_state"] = state
    item["csi_human_intervention_reason"] = str(csi.get("human_intervention_reason") or "").strip() or None
    return item


def get_item(conn: sqlite3.Connection, task_id: str) -> Optional[dict[str, Any]]:
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM task_hub_items WHERE task_id = ? LIMIT 1", (task_id,)).fetchone()
    if row is None:
        return None
    return hydrate_item(dict(row))


def upsert_item(conn: sqlite3.Connection, item: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(conn)
    task_id = str(item.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("task_id is required")

    now_iso = _now_iso()
    existing = get_item(conn, task_id) or {}

    labels = item.get("labels")
    if labels is None:
        labels = list(existing.get("labels") or [])
    labels = [str(v).strip() for v in labels if str(v).strip()]
    label_set = {v.lower() for v in labels}

    metadata = dict(existing.get("metadata") or {})
    metadata.update(_json_loads_obj(item.get("metadata"), default={}))

    must_complete = bool(item.get("must_complete", existing.get("must_complete", False)))
    if not must_complete:
        must_complete = bool({"must-complete", "safety-critical"} & label_set)

    agent_ready = bool(item.get("agent_ready", existing.get("agent_ready", False)))
    if not agent_ready:
        agent_ready = "agent-ready" in label_set

    existing_status = str(existing.get("status") or "").strip().lower()
    status = str(item.get("status") or existing_status or TASK_STATUS_OPEN).strip().lower()
    if status not in ACTIVE_STATUSES | TERMINAL_STATUSES:
        status = TASK_STATUS_OPEN
    # Preserve active non-open states when a source refresh blindly re-upserts as open.
    # This avoids clobbering a live claim back to open while the assignment remains seized.
    if (
        "status" in item
        and status == TASK_STATUS_OPEN
        and existing_status in {TASK_STATUS_IN_PROGRESS, TASK_STATUS_BLOCKED, TASK_STATUS_REVIEW}
    ):
        status = existing_status

    seizure_state = str(item.get("seizure_state") or existing.get("seizure_state") or "unseized")
    if status == TASK_STATUS_OPEN and seizure_state == "seized":
        seizure_state = "unseized"

    trigger_type = str(item.get("trigger_type") or existing.get("trigger_type") or DEFAULT_TRIGGER_TYPE).strip()
    if trigger_type not in TRIGGER_TYPES:
        trigger_type = DEFAULT_TRIGGER_TYPE

    payload = {
        "task_id": task_id,
        "source_kind": str(item.get("source_kind") or existing.get("source_kind") or "internal"),
        "source_ref": str(item.get("source_ref") or existing.get("source_ref") or ""),
        "title": str(item.get("title") or existing.get("title") or task_id),
        "description": str(item.get("description") or existing.get("description") or ""),
        "project_key": str(item.get("project_key") or existing.get("project_key") or "immediate"),
        "priority": max(1, min(4, _safe_int(item.get("priority"), _safe_int(existing.get("priority"), 1)))),
        "due_at": str(item.get("due_at") or existing.get("due_at") or "").strip() or None,
        "labels_json": _json_dumps(labels),
        "status": status,
        "must_complete": 1 if must_complete else 0,
        "incident_key": str(item.get("incident_key") or existing.get("incident_key") or "").strip() or None,
        "workstream_id": str(item.get("workstream_id") or existing.get("workstream_id") or "").strip() or None,
        "subtask_role": str(item.get("subtask_role") or existing.get("subtask_role") or "").strip() or None,
        "parent_task_id": str(item.get("parent_task_id") or existing.get("parent_task_id") or "").strip() or None,
        "agent_ready": 1 if agent_ready else 0,
        "score": _safe_float(item.get("score"), _safe_float(existing.get("score"), 0.0)),
        "score_confidence": _safe_float(item.get("score_confidence"), _safe_float(existing.get("score_confidence"), 0.0)),
        "stale_state": str(item.get("stale_state") or existing.get("stale_state") or "fresh"),
        "seizure_state": seizure_state,
        "mirror_status": str(item.get("mirror_status") or existing.get("mirror_status") or "internal"),
        "trigger_type": trigger_type,
        "metadata_json": _json_dumps(metadata),
        "created_at": str(existing.get("created_at") or item.get("created_at") or now_iso),
        "updated_at": now_iso,
    }

    conn.execute(
        """
        INSERT INTO task_hub_items (
            task_id, source_kind, source_ref, title, description, project_key, priority, due_at,
            labels_json, status, must_complete, incident_key, workstream_id, subtask_role,
            parent_task_id, agent_ready, score, score_confidence, stale_state, seizure_state,
            mirror_status, trigger_type, metadata_json, created_at, updated_at
        ) VALUES (
            :task_id, :source_kind, :source_ref, :title, :description, :project_key, :priority, :due_at,
            :labels_json, :status, :must_complete, :incident_key, :workstream_id, :subtask_role,
            :parent_task_id, :agent_ready, :score, :score_confidence, :stale_state, :seizure_state,
            :mirror_status, :trigger_type, :metadata_json, :created_at, :updated_at
        )
        ON CONFLICT(task_id) DO UPDATE SET
            source_kind=excluded.source_kind,
            source_ref=excluded.source_ref,
            title=excluded.title,
            description=excluded.description,
            project_key=excluded.project_key,
            priority=excluded.priority,
            due_at=excluded.due_at,
            labels_json=excluded.labels_json,
            status=excluded.status,
            must_complete=excluded.must_complete,
            incident_key=excluded.incident_key,
            workstream_id=excluded.workstream_id,
            subtask_role=excluded.subtask_role,
            parent_task_id=excluded.parent_task_id,
            agent_ready=excluded.agent_ready,
            score=excluded.score,
            score_confidence=excluded.score_confidence,
            stale_state=excluded.stale_state,
            seizure_state=excluded.seizure_state,
            mirror_status=excluded.mirror_status,
            trigger_type=excluded.trigger_type,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        payload,
    )
    conn.commit()
    return get_item(conn, task_id) or payload


def _priority_weight(priority: int) -> float:
    return max(0.0, min(3.0, float(priority - 1)))


def _due_urgency_score(due_at: Optional[str]) -> float:
    dt = _parse_iso(due_at)
    if dt is None:
        raw = str(due_at or "").strip()
        if not raw:
            return 0.0
        if len(raw) == 10 and raw[4] == "-":
            dt = _parse_iso(f"{raw}T23:59:59+00:00")
    if dt is None:
        return 0.0
    delta_hours = (dt - datetime.now(timezone.utc)).total_seconds() / 3600.0
    if delta_hours <= 0:
        return 2.2
    if delta_hours <= 4:
        return 1.8
    if delta_hours <= 24:
        return 1.2
    if delta_hours <= 72:
        return 0.5
    return 0.0


def _historical_completion_bonus(conn: sqlite3.Connection, task: dict[str, Any]) -> float:
    rows = conn.execute(
        """
        SELECT a.state, COUNT(*) AS c
        FROM task_hub_assignments a
        JOIN task_hub_items i ON i.task_id = a.task_id
        WHERE i.source_kind = ? AND i.project_key = ?
        GROUP BY a.state
        """,
        (str(task.get("source_kind") or ""), str(task.get("project_key") or "")),
    ).fetchall()
    total = 0
    completed = 0
    for row in rows:
        c = int(row["c"] or 0)
        total += c
        if str(row["state"] or "") == "completed":
            completed += c
    if total <= 0:
        return 0.0
    ratio = completed / float(total)
    if ratio >= 0.8:
        return 1.2
    if ratio >= 0.6:
        return 0.8
    if ratio >= 0.4:
        return 0.3
    return 0.0


def _task_intent(task: dict[str, Any]) -> str:
    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("intent") or "").strip().lower()


def _is_system_schedule_task(task: dict[str, Any]) -> bool:
    source_kind = str(task.get("source_kind") or "").strip().lower()
    return source_kind == "system_command" and _task_intent(task) == "schedule_task"


def _dispatch_skip_reason(item: dict[str, Any], *, eligible: bool, threshold: float) -> Optional[str]:
    if eligible:
        return None
    status = str(item.get("status") or TASK_STATUS_OPEN).strip().lower()
    if status == TASK_STATUS_BLOCKED:
        return "blocked"
    if status == TASK_STATUS_IN_PROGRESS:
        return "in_progress"
    if status == TASK_STATUS_DELEGATED:
        return "delegated_to_vp"
    if status == TASK_STATUS_PENDING_REVIEW:
        return "pending_review"
    if status == TASK_STATUS_REVIEW and not _is_system_schedule_task(item):
        return "needs_review"
    if not bool(item.get("agent_ready")):
        return "agent_not_ready"
    score = _safe_float(item.get("score"), 0.0)
    if score < float(threshold):
        return "below_threshold"
    return "not_eligible"


def _memory_relevance_bonus(task: dict[str, Any]) -> float:
    """Boost score if memory has relevant context for this task.

    Searches the memory orchestrator for past work related to the task's
    title.  Returns +0.4 when relevant institutional memory exists so that
    tasks the agent has context for are prioritised.  Never raises — memory
    is advisory, not a hard dependency.
    """
    try:
        title = str(task.get("title") or "").strip()
        if len(title) < 5:
            return 0.0
        from universal_agent.memory.orchestrator import get_memory_orchestrator

        broker = get_memory_orchestrator()
        hits = broker.search(query=title, limit=2, direct_context=True)
        if hits:
            return 0.4
    except Exception:
        pass
    return 0.0


def score_task(conn: sqlite3.Connection, task: dict[str, Any]) -> tuple[float, float, dict[str, Any]]:
    labels = {str(v).strip().lower() for v in (task.get("labels") or [])}
    must_complete = bool(task.get("must_complete"))
    priority = _safe_int(task.get("priority"), 1)
    project_key = str(task.get("project_key") or "")

    score = 4.2
    details: dict[str, Any] = {"base": score}

    if must_complete:
        score += 2.8
        details["must_complete"] = 2.8
    if "safety-critical" in labels:
        score += 1.2
        details["safety_critical"] = 1.2

    p_bonus = _priority_weight(priority)
    score += p_bonus
    details["priority_bonus"] = p_bonus

    d_bonus = _due_urgency_score(task.get("due_at"))
    score += d_bonus
    details["due_bonus"] = d_bonus

    if project_key in {"mission", "immediate"}:
        score += 0.6
        details["project_bonus"] = 0.6
    elif project_key in {"memory", "proactive", "approval"}:
        score += 0.3
        details["project_bonus"] = 0.3

    if _is_system_schedule_task(task):
        score += 1.0
        details["system_schedule_bonus"] = 1.0

    h_bonus = _historical_completion_bonus(conn, task)
    score += h_bonus
    details["history_bonus"] = h_bonus

    if "blocked" in labels or str(task.get("status") or "") == TASK_STATUS_BLOCKED:
        score -= 3.0
        details["blocked_penalty"] = -3.0
    if "needs-review" in labels or str(task.get("status") or "") == TASK_STATUS_REVIEW:
        score -= 0.8
        details["review_penalty"] = -0.8
    if not bool(task.get("agent_ready")):
        score -= 2.0
        details["not_agent_ready_penalty"] = -2.0

    # Staleness bonus — tasks waiting longer naturally rise in priority.
    # This ensures no task languishes indefinitely at the bottom of the queue.
    created_dt = _parse_iso(task.get("created_at"))
    if created_dt is not None:
        age_hours = max(0.0, (datetime.now(timezone.utc) - created_dt).total_seconds() / 3600.0)
        if age_hours >= 24:
            s_bonus = 2.0
        elif age_hours >= 6:
            s_bonus = 1.0
        elif age_hours >= 2:
            s_bonus = 0.5
        else:
            s_bonus = 0.0
        if s_bonus > 0:
            score += s_bonus
            details["staleness_bonus"] = s_bonus

    # Memory relevance bonus — tasks with institutional context get a confidence boost.
    m_bonus = _memory_relevance_bonus(task)
    if m_bonus > 0:
        score += m_bonus
        details["memory_relevance_bonus"] = m_bonus

    final_score = max(1.0, min(10.0, round(score, 2)))

    confidence = 0.58
    if h_bonus > 0:
        confidence += 0.12
    if must_complete:
        confidence += 0.1
    if "blocked" in labels:
        confidence += 0.08
    if m_bonus > 0:
        confidence += 0.06
    confidence = max(0.35, min(0.95, round(confidence, 2)))

    judge_payload = {
        "method": "heuristic_hybrid",
        "threshold": current_policy().agent_threshold,
        "details": details,
    }
    return final_score, confidence, judge_payload


def _record_evaluation(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    agent_id: str,
    decision: str,
    reason: str,
    score: Optional[float] = None,
    score_confidence: Optional[float] = None,
    judge_payload: Optional[dict[str, Any]] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO task_hub_evaluations (
            id, task_id, evaluated_at, agent_id, decision, reason, score, score_confidence, judge_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"eval_{uuid.uuid4().hex[:16]}",
            task_id,
            _now_iso(),
            agent_id,
            decision,
            reason,
            score,
            score_confidence,
            _json_dumps(judge_payload or {}),
        ),
    )


def _apply_stale_policy(conn: sqlite3.Connection, task: dict[str, Any], policy: TaskHubPolicy) -> tuple[str, dict[str, Any], Optional[str]]:
    status = str(task.get("status") or TASK_STATUS_OPEN)
    metadata = dict(task.get("metadata") or {})

    if status in TERMINAL_STATUSES:
        metadata["stale_missed_cycles"] = 0
        return "terminal", metadata, None

    if not policy.stale_enabled:
        return str(task.get("stale_state") or "fresh"), metadata, None

    if bool(task.get("must_complete")):
        metadata["stale_missed_cycles"] = 0
        return "must_complete", metadata, None

    eval_row = conn.execute(
        "SELECT COUNT(*) AS c FROM task_hub_evaluations WHERE task_id = ?",
        (str(task.get("task_id") or ""),),
    ).fetchone()
    eval_count = int((eval_row["c"] if eval_row else 0) or 0)
    if eval_count <= 0:
        metadata["stale_missed_cycles"] = 0
        return "awaiting_evaluation", metadata, None

    cycles = _safe_int(metadata.get("stale_missed_cycles"), 0) + 1
    metadata["stale_missed_cycles"] = cycles

    age_minutes = 0.0
    created_dt = _parse_iso(task.get("created_at"))
    if created_dt is not None:
        age_minutes = max(0.0, (datetime.now(timezone.utc) - created_dt).total_seconds() / 60.0)

    if cycles >= policy.stale_min_cycles and age_minutes >= float(policy.stale_min_age_minutes):
        return "stale_parked", metadata, TASK_STATUS_PARKED
    return "aging", metadata, None


def rebuild_dispatch_queue(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_schema(conn)
    policy = current_policy()

    rows = conn.execute(
        "SELECT * FROM task_hub_items WHERE status NOT IN (?, ?)",
        (TASK_STATUS_COMPLETED, TASK_STATUS_PARKED),
    ).fetchall()
    items = [hydrate_item(dict(row)) for row in rows]

    queue_build_id = f"q_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    built_at = _now_iso()

    scored: list[dict[str, Any]] = []
    for item in items:
        task_id = str(item.get("task_id") or "")
        score, confidence, judge_payload = score_task(conn, item)
        stale_state, metadata, force_status = _apply_stale_policy(conn, item, policy)

        if force_status == TASK_STATUS_PARKED:
            conn.execute(
                "UPDATE task_hub_items SET status=?, stale_state=?, seizure_state=?, metadata_json=?, updated_at=? WHERE task_id=?",
                (TASK_STATUS_PARKED, stale_state, "unseized", _json_dumps(metadata), _now_iso(), task_id),
            )
            continue

        # Keep `updated_at` for lifecycle transitions only (status/seizure/manual action).
        # Re-scoring on every queue rebuild should not make tasks look "freshly updated".
        conn.execute(
            "UPDATE task_hub_items SET score=?, score_confidence=?, stale_state=?, metadata_json=? WHERE task_id=?",
            (score, confidence, stale_state, _json_dumps(metadata), task_id),
        )

        status = str(item.get("status") or TASK_STATUS_OPEN).strip().lower()
        is_system_schedule = _is_system_schedule_task(item)
        eligible = bool(item.get("agent_ready")) and score >= float(policy.agent_threshold)
        if status in {TASK_STATUS_BLOCKED, TASK_STATUS_IN_PROGRESS, TASK_STATUS_DELEGATED, TASK_STATUS_PENDING_REVIEW, TASK_STATUS_SCHEDULED}:
            eligible = False
        elif status == TASK_STATUS_REVIEW:
            # System command schedule instructions are explicit operator directives
            # and should not be trapped behind manual review, unless explicitly blocked.
            eligible = bool(item.get("agent_ready")) and is_system_schedule
            
        if bool(item.get("must_complete")) and status in {TASK_STATUS_OPEN, TASK_STATUS_REVIEW}:
            if status != TASK_STATUS_REVIEW or is_system_schedule:
                eligible = bool(item.get("agent_ready"))
                
        if is_system_schedule and status in {TASK_STATUS_OPEN, TASK_STATUS_REVIEW}:
            eligible = bool(item.get("agent_ready"))

        # Finally, prevent infinite starvation loops for items that auto-landed in review
        # due to successive heartbeat failures or unhandled completion states.
        if status == TASK_STATUS_REVIEW:
            dispatch_meta = dict(dict(item.get("metadata") or {}).get("dispatch") or {})
            reason = str(dispatch_meta.get("last_disposition_reason") or "")
            if reason.startswith("heartbeat_") or reason.startswith("todo_retry"):
                eligible = False

        # Only record evaluations for tasks in potentially-dispatchable states.
        # Tasks in blocked/in_progress/delegated/scheduled always defer — recording
        # that on every rebuild just creates noise (120+ identical rows per task).
        _status_skip_eval = {TASK_STATUS_BLOCKED, TASK_STATUS_IN_PROGRESS, TASK_STATUS_DELEGATED, TASK_STATUS_PENDING_REVIEW, TASK_STATUS_SCHEDULED}
        if status not in _status_skip_eval:
            _record_evaluation(
                conn,
                task_id=task_id,
                agent_id="scorer",
                decision="defer",
                reason="dispatch_rebuild",
                score=score,
                score_confidence=confidence,
                judge_payload=judge_payload,
            )

        item["score"] = score
        item["score_confidence"] = confidence
        item = _decorate_csi_routing(item, threshold=policy.agent_threshold)
        item["eligible"] = eligible
        if str(item.get("source_kind") or "").strip().lower() == "csi":
            item["eligible"] = bool(item.get("eligible")) and (
                str(item.get("csi_routing_state") or "") == CSI_ROUTING_AGENT_ACTIONABLE
            )
        item["stale_state"] = stale_state
        scored.append(item)

    def _sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        trigger = str(row.get("trigger_type") or DEFAULT_TRIGGER_TYPE)
        is_immediate = 1 if trigger == "immediate" else 0
        system_schedule = 1 if _is_system_schedule_task(row) else 0
        must_complete = 1 if bool(row.get("must_complete")) else 0
        approval = 1 if str(row.get("project_key") or "") == "approval" else 0
        score = _safe_float(row.get("score"), 0.0)
        priority = _safe_int(row.get("priority"), 1)
        due_sort = str(row.get("due_at") or "9999-12-31T23:59:59+00:00")
        updated_sort = str(row.get("updated_at") or "")
        return (-is_immediate, -system_schedule, -must_complete, -approval, -score, -priority, due_sort, updated_sort)

    scored.sort(key=_sort_key)

    conn.execute("DELETE FROM task_hub_dispatch_queue")
    inserted = 0
    eligible_total = 0
    for rank, row in enumerate(scored, start=1):
        task_id = str(row.get("task_id") or "")
        eligible = bool(row.get("eligible"))
        if eligible:
            eligible_total += 1
        conn.execute(
            """
            INSERT INTO task_hub_dispatch_queue (queue_build_id, task_id, rank, eligible, skip_reason, built_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                queue_build_id,
                task_id,
                rank,
                1 if eligible else 0,
                _dispatch_skip_reason(row, eligible=eligible, threshold=policy.agent_threshold),
                built_at,
            ),
        )
        inserted += 1

    conn.commit()
    return {
        "queue_build_id": queue_build_id,
        "built_at": built_at,
        "items_total": inserted,
        "eligible_total": eligible_total,
        "threshold": policy.agent_threshold,
    }


def get_dispatch_queue(conn: sqlite3.Connection, *, limit: int = 100) -> dict[str, Any]:
    ensure_schema(conn)
    policy = current_policy()
    row = conn.execute(
        "SELECT queue_build_id, built_at FROM task_hub_dispatch_queue ORDER BY built_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        summary = rebuild_dispatch_queue(conn)
        queue_build_id = str(summary.get("queue_build_id") or "")
    else:
        queue_build_id = str(row["queue_build_id"] or "")

    rows = conn.execute(
        """
        SELECT q.queue_build_id, q.task_id, q.rank, q.eligible, q.skip_reason, q.built_at,
               i.*
        FROM task_hub_dispatch_queue q
        JOIN task_hub_items i ON i.task_id = q.task_id
        WHERE q.queue_build_id = ?
        ORDER BY q.rank ASC
        LIMIT ?
        """,
        (queue_build_id, max(1, int(limit))),
    ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        merged = dict(row)
        item = hydrate_item(merged)
        item = _decorate_csi_routing(item, threshold=policy.agent_threshold)
        item["rank"] = _safe_int(merged.get("rank"), 0)
        item["eligible"] = bool(_safe_int(merged.get("eligible"), 0))
        item["skip_reason"] = str(merged.get("skip_reason") or "").strip() or None
        items.append(item)

    eligible_row = conn.execute(
        "SELECT COUNT(*) AS c FROM task_hub_dispatch_queue WHERE queue_build_id = ? AND eligible = 1",
        (queue_build_id,),
    ).fetchone()

    return {
        "queue_build_id": queue_build_id,
        "items": items,
        "eligible_total": int((eligible_row["c"] if eligible_row else 0) or 0),
    }


def claim_next_dispatch_tasks(
    conn: sqlite3.Connection,
    *,
    limit: int = 1,
    agent_id: str = "heartbeat",
    workflow_run_id: Optional[str] = None,
    workflow_attempt_id: Optional[str] = None,
    provider_session_id: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    trigger_types: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    ensure_schema(conn)
    rebuild_summary = rebuild_dispatch_queue(conn)
    queue_build_id = str(rebuild_summary.get("queue_build_id") or "")
    if not queue_build_id:
        latest = conn.execute(
            "SELECT queue_build_id FROM task_hub_dispatch_queue ORDER BY built_at DESC LIMIT 1"
        ).fetchone()
        queue_build_id = str((latest["queue_build_id"] if latest else "") or "")
    if not queue_build_id:
        return []
    claim_limit = max(1, int(limit))

    # Build trigger_type filter clause
    if trigger_types:
        placeholders = ",".join("?" for _ in trigger_types)
        trigger_filter = f"AND i.trigger_type IN ({placeholders})"
        params: tuple = (queue_build_id, TASK_STATUS_OPEN, TASK_STATUS_REVIEW) + tuple(trigger_types) + (claim_limit,)
    else:
        trigger_filter = ""
        params = (queue_build_id, TASK_STATUS_OPEN, TASK_STATUS_REVIEW, claim_limit)

    rows = conn.execute(
        f"""
        SELECT q.task_id, q.rank, q.skip_reason, i.*
        FROM task_hub_dispatch_queue q
        JOIN task_hub_items i ON i.task_id = q.task_id
        WHERE q.queue_build_id = ?
          AND q.eligible = 1
          AND i.status IN (?, ?)
          {trigger_filter}
        ORDER BY q.rank ASC
        LIMIT ?
        """,
        params,
    ).fetchall()
    queue_items = [hydrate_item(dict(row)) for row in rows]
    claimed: list[dict[str, Any]] = []

    for item in queue_items:
        if len(claimed) >= claim_limit:
            break
        task_id = str(item.get("task_id") or "")
        if not task_id:
            continue

        current = get_item(conn, task_id)
        if not current:
            continue
        if str(current.get("status") or "") not in {TASK_STATUS_OPEN, TASK_STATUS_REVIEW}:
            continue

        # ── completion_token guard: if set, task was finalized and should
        # not be re-claimed without explicit operator reset. ──
        if current.get("completion_token"):
            logger.warning(
                "⛔ Skipping claim of completion-locked task %s (token=%s)",
                task_id, str(current["completion_token"])[:16],
            )
            continue

        assignment_id = f"asg_{uuid.uuid4().hex[:16]}"
        resolved_provider_session_id = str(provider_session_id or "").strip() or _session_id_from_agent_id(agent_id)
        resolved_workflow_run_id = str(workflow_run_id or "").strip() or None
        resolved_workflow_attempt_id = str(workflow_attempt_id or "").strip() or None
        resolved_workspace_dir = str(workspace_dir or "").strip() or None
        conn.execute(
            """
            INSERT INTO task_hub_assignments (
                assignment_id, task_id, agent_id, workflow_run_id, workflow_attempt_id, provider_session_id, workspace_dir, state, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assignment_id,
                task_id,
                agent_id,
                resolved_workflow_run_id,
                resolved_workflow_attempt_id,
                resolved_provider_session_id,
                resolved_workspace_dir,
                "seized",
                _now_iso(),
            ),
        )
        metadata = dict(current.get("metadata") or {})
        dispatch_meta = dict(metadata.get("dispatch") or {})
        dispatch_meta.update(
            {
                "active_assignment_id": assignment_id,
                "active_agent_id": agent_id,
                "active_provider_session_id": resolved_provider_session_id,
                "active_workspace_dir": resolved_workspace_dir,
                "active_workflow_run_id": resolved_workflow_run_id,
                "active_workflow_attempt_id": resolved_workflow_attempt_id,
                "last_assignment_started_at": _now_iso(),
            }
        )
        metadata["dispatch"] = dispatch_meta
        conn.execute(
            "UPDATE task_hub_items SET status=?, seizure_state=?, metadata_json=?, updated_at=? WHERE task_id=?",
            (TASK_STATUS_IN_PROGRESS, "seized", _json_dumps(metadata), _now_iso(), task_id),
        )
        _record_evaluation(
            conn,
            task_id=task_id,
            agent_id=agent_id,
            decision="seize",
            reason="dispatch_claim",
            score=_safe_float(item.get("score"), 0.0),
            score_confidence=_safe_float(item.get("score_confidence"), 0.0),
            judge_payload={"source": "dispatch_claim"},
        )
        item["assignment_id"] = assignment_id
        item["status"] = TASK_STATUS_IN_PROGRESS
        item["seizure_state"] = "seized"
        item["metadata"] = metadata
        item["rank"] = _safe_int(item.get("rank"), 0)
        item["eligible"] = True
        item["skip_reason"] = None
        claimed.append(item)

    conn.commit()
    return claimed


def claim_task_for_agent(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    agent_id: str,
    workflow_run_id: Optional[str] = None,
    workflow_attempt_id: Optional[str] = None,
    provider_session_id: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    claim_reason: str = "direct_claim",
) -> dict[str, Any]:
    """Claim a specific task for a specific agent/session.

    This is the targeted companion to ``claim_next_dispatch_tasks`` for
    interactive or explicit intake paths that should enter the same durable
    Task Hub lifecycle without waiting for a background sweep.
    """
    current = get_item(conn, task_id)
    if not current:
        raise ValueError(f"task not found: {task_id}")
    if str(current.get("status") or "") not in {TASK_STATUS_OPEN, TASK_STATUS_REVIEW}:
        raise ValueError(f"task {task_id!r} cannot be claimed from status={current.get('status')!r}")
    if current.get("completion_token"):
        raise ValueError(f"task {task_id!r} is completion-locked")

    assignment_id = f"asg_{uuid.uuid4().hex[:16]}"
    resolved_provider_session_id = (
        str(provider_session_id or "").strip() or _session_id_from_agent_id(agent_id)
    )
    resolved_workflow_run_id = str(workflow_run_id or "").strip() or None
    resolved_workflow_attempt_id = str(workflow_attempt_id or "").strip() or None
    resolved_workspace_dir = str(workspace_dir or "").strip() or None

    conn.execute(
        """
        INSERT INTO task_hub_assignments (
            assignment_id, task_id, agent_id, workflow_run_id, workflow_attempt_id, provider_session_id, workspace_dir, state, started_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            assignment_id,
            task_id,
            agent_id,
            resolved_workflow_run_id,
            resolved_workflow_attempt_id,
            resolved_provider_session_id,
            resolved_workspace_dir,
            "seized",
            _now_iso(),
        ),
    )

    metadata = dict(current.get("metadata") or {})
    dispatch_meta = dict(metadata.get("dispatch") or {})
    dispatch_meta.update(
        {
            "active_assignment_id": assignment_id,
            "active_agent_id": agent_id,
            "active_provider_session_id": resolved_provider_session_id,
            "active_workspace_dir": resolved_workspace_dir,
            "active_workflow_run_id": resolved_workflow_run_id,
            "active_workflow_attempt_id": resolved_workflow_attempt_id,
            "last_assignment_started_at": _now_iso(),
        }
    )
    metadata["dispatch"] = dispatch_meta
    conn.execute(
        "UPDATE task_hub_items SET status=?, seizure_state=?, metadata_json=?, updated_at=? WHERE task_id=?",
        (TASK_STATUS_IN_PROGRESS, "seized", _json_dumps(metadata), _now_iso(), task_id),
    )
    _record_evaluation(
        conn,
        task_id=task_id,
        agent_id=agent_id,
        decision="seize",
        reason=claim_reason or "direct_claim",
        score=_safe_float(current.get("score"), 0.0),
        score_confidence=_safe_float(current.get("score_confidence"), 0.0),
        judge_payload={"source": "direct_claim"},
    )
    rebuild_dispatch_queue(conn)
    conn.commit()

    updated = get_item(conn, task_id) or dict(current)
    updated["assignment_id"] = assignment_id
    updated["status"] = TASK_STATUS_IN_PROGRESS
    updated["seizure_state"] = "seized"
    updated["metadata"] = metadata
    return updated


def _session_id_from_agent_id(agent_id: Any) -> str:
    raw = str(agent_id or "").strip()
    if not raw:
        return ""
    if raw.startswith("heartbeat:"):
        return raw.split(":", 1)[1].strip()
    if raw.startswith("todo:"):
        return raw.split(":", 1)[1].strip()
    if raw.startswith("daemon_"):
        return raw
    return ""


def _resolve_dispatch_metadata(
    metadata: dict[str, Any],
    *,
    assignment_state: str,
    now_iso: str,
) -> dict[str, Any]:
    """Roll active dispatch lineage into the last-* fields and clear ownership."""
    dispatch_meta = dict(metadata.get("dispatch") or {})
    dispatch_meta.update(
        {
            "last_assignment_state": assignment_state,
            "last_assignment_ended_at": now_iso,
            "last_provider_session_id": dispatch_meta.get("active_provider_session_id"),
            "last_workspace_dir": dispatch_meta.get("active_workspace_dir"),
            "last_workflow_run_id": dispatch_meta.get("active_workflow_run_id"),
            "last_workflow_attempt_id": dispatch_meta.get("active_workflow_attempt_id"),
        }
    )
    dispatch_meta.pop("active_assignment_id", None)
    dispatch_meta.pop("active_agent_id", None)
    dispatch_meta.pop("active_provider_session_id", None)
    dispatch_meta.pop("active_workspace_dir", None)
    dispatch_meta.pop("active_workflow_run_id", None)
    dispatch_meta.pop("active_workflow_attempt_id", None)
    metadata["dispatch"] = dispatch_meta
    return metadata


def _complete_active_assignments_for_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    result_summary: str,
    ended_at: str,
) -> None:
    conn.execute(
        """
        UPDATE task_hub_assignments
        SET state='completed', ended_at=?, result_summary=?
        WHERE task_id=? AND state IN ('seized', 'running')
        """,
        (ended_at, result_summary.strip() or None, task_id),
    )


def list_due_scheduled_tasks(
    conn: sqlite3.Connection,
    *,
    as_of_iso: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return open tasks with trigger_type='scheduled' whose due_at has arrived."""
    ensure_schema(conn)
    cutoff = as_of_iso or _now_iso()
    rows = conn.execute(
        """
        SELECT * FROM task_hub_items
        WHERE trigger_type = 'scheduled'
          AND status = ?
          AND due_at IS NOT NULL
          AND due_at <= ?
        ORDER BY due_at ASC
        """,
        (TASK_STATUS_OPEN, cutoff),
    ).fetchall()
    return [hydrate_item(dict(row)) for row in rows]


def _assignment_lineage(
    item: dict[str, Any],
    *,
    agent_id: str,
    workflow_run_id: Any = None,
    workflow_attempt_id: Any = None,
    provider_session_id: Any = None,
    workspace_dir: Any = None,
) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    dispatch_meta = metadata.get("dispatch") if isinstance(metadata.get("dispatch"), dict) else {}
    return {
        "session_id": str(provider_session_id or dispatch_meta.get("last_provider_session_id") or dispatch_meta.get("active_provider_session_id") or _session_id_from_agent_id(agent_id)),
        "workflow_run_id": str(workflow_run_id or dispatch_meta.get("last_workflow_run_id") or dispatch_meta.get("active_workflow_run_id") or "").strip() or None,
        "workflow_attempt_id": str(workflow_attempt_id or dispatch_meta.get("last_workflow_attempt_id") or dispatch_meta.get("active_workflow_attempt_id") or "").strip() or None,
        "provider_session_id": str(provider_session_id or dispatch_meta.get("last_provider_session_id") or dispatch_meta.get("active_provider_session_id") or "").strip() or None,
        "workspace_dir": str(workspace_dir or dispatch_meta.get("last_workspace_dir") or dispatch_meta.get("active_workspace_dir") or "").strip() or None,
    }


def update_assignment_lineage(
    conn: sqlite3.Connection,
    *,
    assignment_id: str,
    workflow_run_id: Optional[str] = None,
    workflow_attempt_id: Optional[str] = None,
    workspace_dir: Optional[str] = None,
) -> None:
    """Stamp an existing assignment with run-scoped lineage.

    Called by the dispatcher after allocating a fresh ExecutionRunContext
    so the assignment record carries the correct artifact root.
    """
    ensure_schema(conn)
    sets: list[str] = []
    params: list[Any] = []
    if workflow_run_id is not None:
        sets.append("workflow_run_id = ?")
        params.append(workflow_run_id)
    if workflow_attempt_id is not None:
        sets.append("workflow_attempt_id = ?")
        params.append(workflow_attempt_id)
    if workspace_dir is not None:
        sets.append("workspace_dir = ?")
        params.append(workspace_dir)
    if not sets:
        return
    params.append(assignment_id)
    conn.execute(
        f"UPDATE task_hub_assignments SET {', '.join(sets)} WHERE assignment_id = ?",
        params,
    )
    conn.commit()


def list_completed_tasks(conn: sqlite3.Connection, *, limit: int = 80) -> list[dict[str, Any]]:
    ensure_schema(conn)
    rows = conn.execute(
        """
        SELECT *
        FROM task_hub_items
        WHERE status = ?
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (TASK_STATUS_COMPLETED, max(1, min(int(limit), 500))),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        item = hydrate_item(dict(row))
        task_id = str(item.get("task_id") or "")
        assignment_row = conn.execute(
            """
            SELECT assignment_id, agent_id, workflow_run_id, workflow_attempt_id, provider_session_id, workspace_dir, state, started_at, ended_at, result_summary
            FROM task_hub_assignments
            WHERE task_id = ?
            ORDER BY COALESCE(ended_at, started_at) DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if assignment_row:
            agent_id = str(assignment_row["agent_id"] or "")
            lineage = _assignment_lineage(
                item,
                agent_id=agent_id,
                workflow_run_id=assignment_row["workflow_run_id"],
                workflow_attempt_id=assignment_row["workflow_attempt_id"],
                provider_session_id=assignment_row["provider_session_id"],
                workspace_dir=assignment_row["workspace_dir"],
            )
            item["last_assignment"] = {
                "assignment_id": str(assignment_row["assignment_id"] or ""),
                "agent_id": agent_id,
                "state": str(assignment_row["state"] or ""),
                "started_at": str(assignment_row["started_at"] or ""),
                "ended_at": str(assignment_row["ended_at"] or ""),
                "result_summary": str(assignment_row["result_summary"] or ""),
                "session_id": lineage["session_id"],
                "workflow_run_id": lineage["workflow_run_id"],
                "workflow_attempt_id": lineage["workflow_attempt_id"],
                "provider_session_id": lineage["provider_session_id"],
                "workspace_dir": lineage["workspace_dir"],
            }
        else:
            item["last_assignment"] = None

        item["completed_at"] = str(
            ((item.get("last_assignment") or {}).get("ended_at") if isinstance(item.get("last_assignment"), dict) else "")
            or item.get("updated_at")
            or ""
        )
        out.append(item)
    return out


def get_task_history(conn: sqlite3.Connection, *, task_id: str, limit: int = 80) -> dict[str, Any]:
    ensure_schema(conn)
    item = get_item(conn, task_id)
    if not item:
        raise ValueError("task not found")

    cap = max(1, min(int(limit), 500))
    assignments_rows = conn.execute(
        """
        SELECT assignment_id, task_id, agent_id, workflow_run_id, workflow_attempt_id, provider_session_id, workspace_dir, state, started_at, ended_at, result_summary
        FROM task_hub_assignments
        WHERE task_id = ?
        ORDER BY COALESCE(ended_at, started_at) DESC
        LIMIT ?
        """,
        (task_id, cap),
    ).fetchall()
    eval_rows = conn.execute(
        """
        SELECT id, task_id, evaluated_at, agent_id, decision, reason, score, score_confidence, judge_payload_json
        FROM task_hub_evaluations
        WHERE task_id = ?
        ORDER BY evaluated_at DESC
        LIMIT ?
        """,
        (task_id, cap),
    ).fetchall()

    assignments: list[dict[str, Any]] = []
    for row in assignments_rows:
        agent_id = str(row["agent_id"] or "")
        lineage = _assignment_lineage(
            item,
            agent_id=agent_id,
            workflow_run_id=row["workflow_run_id"],
            workflow_attempt_id=row["workflow_attempt_id"],
            provider_session_id=row["provider_session_id"],
            workspace_dir=row["workspace_dir"],
        )
        assignments.append(
            {
                "assignment_id": str(row["assignment_id"] or ""),
                "task_id": str(row["task_id"] or ""),
                "agent_id": agent_id,
                "session_id": lineage["session_id"],
                "workflow_run_id": lineage["workflow_run_id"],
                "workflow_attempt_id": lineage["workflow_attempt_id"],
                "provider_session_id": lineage["provider_session_id"],
                "workspace_dir": lineage["workspace_dir"],
                "state": str(row["state"] or ""),
                "started_at": str(row["started_at"] or ""),
                "ended_at": str(row["ended_at"] or ""),
                "result_summary": str(row["result_summary"] or ""),
            }
        )

    evaluations: list[dict[str, Any]] = []
    for row in eval_rows:
        evaluations.append(
            {
                "id": str(row["id"] or ""),
                "task_id": str(row["task_id"] or ""),
                "evaluated_at": str(row["evaluated_at"] or ""),
                "agent_id": str(row["agent_id"] or ""),
                "decision": str(row["decision"] or ""),
                "reason": str(row["reason"] or ""),
                "score": _safe_float(row["score"], 0.0),
                "score_confidence": _safe_float(row["score_confidence"], 0.0),
                "judge_payload": _json_loads_obj(row["judge_payload_json"], default={}),
            }
        )

    return {
        "task": item,
        "assignments": assignments,
        "evaluations": evaluations,
    }


def _email_side_effects_detected(conn: sqlite3.Connection, task_id: str) -> bool:
    item = get_item(conn, str(task_id or "").strip())
    if item:
        dispatch_meta = dict(dict(item.get("metadata") or {}).get("dispatch") or {})
        outbound = dict(dispatch_meta.get("outbound_delivery") or {})
        if any(
            str(outbound.get(field) or "").strip()
            for field in ("sent_at", "message_id", "draft_id")
        ):
            return True
    try:
        row = conn.execute(
            "SELECT * FROM email_task_mappings WHERE task_id = ? LIMIT 1",
            (str(task_id or "").strip(),),
        ).fetchone()
    except Exception:
        return False
    if not row:
        return False
    return any(
        str((row[field] if field in row.keys() else "") or "").strip()
        for field in ("email_sent_at", "final_email_sent_at", "final_message_id", "final_draft_id")
    )


def record_task_outbound_delivery(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    channel: str = "agentmail",
    message_id: str = "",
    draft_id: str = "",
    sent_at: Optional[str] = None,
) -> dict[str, Any]:
    """Persist a generic outbound-delivery marker on the Task Hub item.

    This is the cross-ingress side-effect ledger used when the task did not
    originate from an email thread and therefore has no ``email_task_mappings``
    row to prove that final delivery already happened.
    """
    ensure_schema(conn)
    current = get_item(conn, str(task_id or "").strip())
    if not current:
        raise ValueError(f"No task found with ID: {task_id}")

    metadata = dict(current.get("metadata") or {})
    dispatch_meta = dict(metadata.get("dispatch") or {})
    outbound = dict(dispatch_meta.get("outbound_delivery") or {})
    outbound.update(
        {
            "channel": str(channel or outbound.get("channel") or "agentmail").strip() or "agentmail",
            "sent_at": str(sent_at or outbound.get("sent_at") or _now_iso()).strip(),
            "message_id": str(message_id or outbound.get("message_id") or "").strip(),
            "draft_id": str(draft_id or outbound.get("draft_id") or "").strip(),
        }
    )
    dispatch_meta["outbound_delivery"] = outbound
    metadata["dispatch"] = dispatch_meta
    conn.execute(
        "UPDATE task_hub_items SET metadata_json=?, updated_at=? WHERE task_id=?",
        (_json_dumps(metadata), _now_iso(), str(task_id or "").strip()),
    )
    conn.commit()
    return get_item(conn, str(task_id or "").strip()) or current


def reconcile_task_lifecycle(
    conn: sqlite3.Connection,
    *,
    running_session_ids: Optional[set[str]] = None,
) -> dict[str, int]:
    """Repair obviously orphaned lifecycle rows at startup or on-demand.

    - Reopen or review tasks stuck in ``in_progress`` without a live assignment.
    - Flag auto-completed rows with no explicit disposition as ``needs_review``.
    """
    ensure_schema(conn)
    running_session_ids = {str(v).strip() for v in (running_session_ids or set()) if str(v).strip()}
    now_iso = _now_iso()
    reopened = 0
    reviewed = 0
    completion_flagged = 0
    delegated_backfilled = 0

    in_progress_rows = conn.execute(
        """
        SELECT task_id, metadata_json
        FROM task_hub_items
        WHERE status = ?
        """,
        (TASK_STATUS_IN_PROGRESS,),
    ).fetchall()

    for row in in_progress_rows:
        task_id = str(row["task_id"] or "").strip()
        metadata = _json_loads_obj(row["metadata_json"], default={})
        dispatch_meta = dict(metadata.get("dispatch") or {})
        active_assignment_id = str(dispatch_meta.get("active_assignment_id") or "").strip()
        active_session_id = str(dispatch_meta.get("active_provider_session_id") or "").strip()
        if active_session_id and active_session_id in running_session_ids:
            continue

        assignment_row = None
        if active_assignment_id:
            assignment_row = conn.execute(
                """
                SELECT assignment_id, state, started_at, provider_session_id
                FROM task_hub_assignments
                WHERE assignment_id = ?
                LIMIT 1
                """,
                (active_assignment_id,),
            ).fetchone()
        else:
            assignment_row = conn.execute(
                """
                SELECT assignment_id, state, started_at, provider_session_id
                FROM task_hub_assignments
                WHERE task_id = ?
                  AND state IN ('seized', 'running')
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()

        assignment_live = bool(
            assignment_row
            and str(assignment_row["state"] or "").strip().lower() in {"seized", "running"}
        )
        assignment_session_id = str(
            active_session_id or (assignment_row["provider_session_id"] if assignment_row else "") or ""
        ).strip()
        if assignment_live and assignment_session_id in running_session_ids:
            continue
        if assignment_live:
            mission_id = ""
            vp_id = ""
            assignment_started_at = str(assignment_row["started_at"] or "").strip()
            existing_delegation = metadata.get("delegation") if isinstance(metadata.get("delegation"), dict) else {}
            existing_mission_id = str(existing_delegation.get("mission_id") or "").strip()
            if not existing_mission_id:
                try:
                    tables_row = conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='vp_missions' LIMIT 1"
                    ).fetchone()
                    if tables_row and assignment_session_id:
                        candidate_rows = conn.execute(
                            """
                            SELECT mission_id, vp_id, payload_json, created_at
                            FROM vp_missions
                            WHERE created_at >= ?
                            ORDER BY created_at DESC
                            LIMIT 20
                            """,
                            (assignment_started_at or "1970-01-01T00:00:00+00:00",),
                        ).fetchall()
                        matches: list[tuple[str, str]] = []
                        for candidate_row in candidate_rows:
                            payload = _json_loads_obj(candidate_row["payload_json"], default={})
                            if str(payload.get("source_session_id") or "").strip() != assignment_session_id:
                                continue
                            matches.append(
                                (
                                    str(candidate_row["mission_id"] or "").strip(),
                                    str(candidate_row["vp_id"] or "").strip() or "vp.general.primary",
                                )
                            )
                        if len(matches) == 1:
                            mission_id, vp_id = matches[0]
                except Exception:
                    logger.exception("Failed reconciling VP mission linkage for task %s", task_id)

            if mission_id:
                perform_task_action(
                    conn,
                    task_id=task_id,
                    action="delegate",
                    reason=vp_id,
                    note=f"mission_id={mission_id} reconciled_from_vp_mission",
                    agent_id=f"todo:{assignment_session_id}" if assignment_session_id else "todo_recovery",
                )
                delegated_backfilled += 1
                continue

            conn.execute(
                """
                UPDATE task_hub_assignments
                SET state='failed', ended_at=?, result_summary=?
                WHERE assignment_id=?
                """,
                (now_iso, "reconciled_orphaned_assignment", str(assignment_row["assignment_id"] or "")),
            )

        metadata = _resolve_dispatch_metadata(
            metadata,
            assignment_state="failed" if assignment_live else "reconciled",
            now_iso=now_iso,
        )
        dispatch_meta = dict(metadata.get("dispatch") or {})
        dispatch_meta["completion_unverified"] = False
        dispatch_meta["reconciled_at"] = now_iso
        metadata["dispatch"] = dispatch_meta
        if _email_side_effects_detected(conn, task_id):
            dispatch_meta["last_disposition"] = TASK_STATUS_REVIEW
            dispatch_meta["last_disposition_reason"] = "reconciled_orphaned_in_progress_with_side_effects"
            conn.execute(
                """
                UPDATE task_hub_items
                SET status=?, seizure_state=?, metadata_json=?, updated_at=?
                WHERE task_id=?
                """,
                (TASK_STATUS_REVIEW, "unseized", _json_dumps(metadata), now_iso, task_id),
            )
            reviewed += 1
        else:
            dispatch_meta["last_disposition"] = TASK_STATUS_OPEN
            dispatch_meta["last_disposition_reason"] = "reconciled_orphaned_in_progress"
            conn.execute(
                """
                UPDATE task_hub_items
                SET status=?, seizure_state=?, metadata_json=?, updated_at=?
                WHERE task_id=?
                """,
                (TASK_STATUS_OPEN, "unseized", _json_dumps(metadata), now_iso, task_id),
            )
            reopened += 1

    completed_rows = conn.execute(
        """
        SELECT task_id, metadata_json
        FROM task_hub_items
        WHERE status = ?
        """,
        (TASK_STATUS_COMPLETED,),
    ).fetchall()
    for row in completed_rows:
        task_id = str(row["task_id"] or "").strip()
        metadata = _json_loads_obj(row["metadata_json"], default={})
        dispatch_meta = dict(metadata.get("dispatch") or {})
        last_reason = str(dispatch_meta.get("last_disposition_reason") or "").strip().lower()
        if last_reason not in {"heartbeat_auto_completed", "todo_auto_completed"}:
            continue
        dispatch_meta["completion_unverified"] = True
        dispatch_meta["reconciled_at"] = now_iso
        dispatch_meta["last_disposition"] = TASK_STATUS_REVIEW
        dispatch_meta["last_disposition_reason"] = "reconciled_completion_unverified"
        metadata["dispatch"] = dispatch_meta
        conn.execute(
            """
            UPDATE task_hub_items
            SET status=?, seizure_state=?, completion_token=NULL, metadata_json=?, updated_at=?
            WHERE task_id=?
            """,
            (TASK_STATUS_REVIEW, "unseized", _json_dumps(metadata), now_iso, task_id),
        )
        reviewed += 1
        completion_flagged += 1

    conn.commit()
    rebuild_dispatch_queue(conn)
    return {
        "reopened": reopened,
        "reviewed": reviewed,
        "completion_flagged": completion_flagged,
        "delegated_backfilled": delegated_backfilled,
    }


def finalize_assignments(
    conn: sqlite3.Connection,
    *,
    assignment_ids: list[str],
    state: str = "completed",
    result_summary: str = "",
    reopen_in_progress: bool = True,
    policy: str = "legacy",
    heartbeat_max_retries: Optional[int] = None,
) -> dict[str, int]:
    ensure_schema(conn)
    cleaned_ids = [str(v).strip() for v in assignment_ids if str(v).strip()]
    if not cleaned_ids:
        return {"finalized": 0, "reopened": 0, "reviewed": 0, "completed": 0, "retry_exhausted": 0}

    placeholders = ",".join("?" for _ in cleaned_ids)
    rows = conn.execute(
        f"""
        SELECT assignment_id, task_id, state
        FROM task_hub_assignments
        WHERE assignment_id IN ({placeholders})
        """,
        tuple(cleaned_ids),
    ).fetchall()

    now_iso = _now_iso()
    finalized = 0
    reopened = 0
    reviewed = 0
    completed = 0
    retry_exhausted = 0
    policy_norm = str(policy or "legacy").strip().lower()
    heartbeat_retry_limit = max(
        1,
        int(heartbeat_max_retries if heartbeat_max_retries is not None else _safe_int(os.getenv("UA_TASK_HUB_HEARTBEAT_MAX_RETRIES"), 3)),
    )
    todo_retry_limit = max(1, _safe_int(os.getenv("UA_TASK_HUB_TODO_MAX_RETRIES"), 3))
    run_state = str(state or "").strip().lower()
    for row in rows:
        assignment_id = str(row["assignment_id"] or "").strip()
        task_id = str(row["task_id"] or "").strip()
        current_state = str(row["state"] or "").strip().lower()
        if not assignment_id or not task_id:
            continue
        if current_state not in {"seized", "running"}:
            if policy_norm == "heartbeat":
                task_row = conn.execute(
                    "SELECT status FROM task_hub_items WHERE task_id = ? LIMIT 1",
                    (task_id,),
                ).fetchone()
                if task_row:
                    task_status = str(task_row["status"] or "").strip().lower()
                    if task_status == TASK_STATUS_COMPLETED:
                        completed += 1
                    elif task_status == TASK_STATUS_REVIEW:
                        reviewed += 1
            continue

        conn.execute(
            """
            UPDATE task_hub_assignments
            SET state=?, ended_at=?, result_summary=?
            WHERE assignment_id=?
            """,
            (state, now_iso, result_summary.strip() or None, assignment_id),
        )
        finalized += 1

        if not reopen_in_progress:
            continue

        task_row = conn.execute(
            "SELECT status, seizure_state, metadata_json FROM task_hub_items WHERE task_id = ? LIMIT 1",
            (task_id,),
        ).fetchone()
        if not task_row:
            continue

        task_status = str(task_row["status"] or "").strip().lower()
        metadata = _json_loads_obj(task_row["metadata_json"], default={})
        dispatch_meta = dict(metadata.get("dispatch") or {})
        dispatch_meta.update(
            {
                "last_assignment_state": state,
                "last_assignment_ended_at": now_iso,
                "last_provider_session_id": dispatch_meta.get("active_provider_session_id"),
                "last_workspace_dir": dispatch_meta.get("active_workspace_dir"),
                "last_workflow_run_id": dispatch_meta.get("active_workflow_run_id"),
                "last_workflow_attempt_id": dispatch_meta.get("active_workflow_attempt_id"),
            }
        )
        dispatch_meta.pop("active_assignment_id", None)
        dispatch_meta.pop("active_agent_id", None)
        dispatch_meta.pop("active_provider_session_id", None)
        dispatch_meta.pop("active_workspace_dir", None)
        dispatch_meta.pop("active_workflow_run_id", None)
        dispatch_meta.pop("active_workflow_attempt_id", None)

        if task_status == TASK_STATUS_COMPLETED:
            metadata["dispatch"] = dispatch_meta
            conn.execute(
                "UPDATE task_hub_items SET metadata_json=? WHERE task_id=?",
                (_json_dumps(metadata), task_id),
            )
            completed += 1
            continue
        if task_status == TASK_STATUS_REVIEW:
            metadata["dispatch"] = dispatch_meta
            conn.execute(
                "UPDATE task_hub_items SET metadata_json=? WHERE task_id=?",
                (_json_dumps(metadata), task_id),
            )
            reviewed += 1
            continue
        if task_status != TASK_STATUS_IN_PROGRESS:
            metadata["dispatch"] = dispatch_meta
            conn.execute(
                "UPDATE task_hub_items SET metadata_json=? WHERE task_id=?",
                (_json_dumps(metadata), task_id),
            )
            continue

        if policy_norm == "heartbeat":
            if run_state == "completed":
                dispatch_meta["last_disposition"] = "needs_review"
                dispatch_meta["last_disposition_reason"] = "heartbeat_completed_without_disposition"
                dispatch_meta["completion_unverified"] = True
                metadata["dispatch"] = dispatch_meta
                conn.execute(
                    """
                    UPDATE task_hub_items
                    SET status=?, seizure_state=?, completion_token=NULL, metadata_json=?, updated_at=?
                    WHERE task_id=?
                    """,
                    (TASK_STATUS_REVIEW, "unseized", _json_dumps(metadata), now_iso, task_id),
                )
                reviewed += 1
                logger.warning(
                    "Task Hub heartbeat finalize moved task %s to needs_review after completed run without explicit disposition",
                    task_id,
                )
                continue

            retry_count = max(0, _safe_int(dispatch_meta.get("heartbeat_retry_count"), 0)) + 1
            dispatch_meta["heartbeat_retry_count"] = retry_count
            dispatch_meta["heartbeat_retry_limit"] = heartbeat_retry_limit
            metadata["dispatch"] = dispatch_meta
            if retry_count >= heartbeat_retry_limit:
                dispatch_meta["last_disposition"] = "needs_review"
                dispatch_meta["last_disposition_reason"] = "heartbeat_retry_exhausted"
                conn.execute(
                    """
                    UPDATE task_hub_items
                    SET status=?, seizure_state=?, metadata_json=?, updated_at=?
                    WHERE task_id=?
                    """,
                    (TASK_STATUS_REVIEW, "unseized", _json_dumps(metadata), now_iso, task_id),
                )
                reviewed += 1
                retry_exhausted += 1
                logger.info(
                    "Task Hub heartbeat finalize moved task %s to needs_review after retry exhaustion (%s/%s)",
                    task_id,
                    retry_count,
                    heartbeat_retry_limit,
                )
            else:
                if _email_side_effects_detected(conn, task_id):
                    dispatch_meta["last_disposition"] = "needs_review"
                    dispatch_meta["last_disposition_reason"] = "heartbeat_retryable_with_side_effects"
                    dispatch_meta["completion_unverified"] = True
                    metadata["dispatch"] = dispatch_meta
                    conn.execute(
                        """
                        UPDATE task_hub_items
                        SET status=?, seizure_state=?, metadata_json=?, updated_at=?
                        WHERE task_id=?
                        """,
                        (TASK_STATUS_REVIEW, "unseized", _json_dumps(metadata), now_iso, task_id),
                    )
                    reviewed += 1
                    logger.warning(
                        "Task Hub heartbeat finalize moved task %s to needs_review because side effects already occurred",
                        task_id,
                    )
                else:
                    dispatch_meta["last_disposition"] = "reopened"
                    dispatch_meta["last_disposition_reason"] = f"heartbeat_{run_state or 'failed'}_retryable"
                    conn.execute(
                        """
                        UPDATE task_hub_items
                        SET status=?, seizure_state=?, metadata_json=?, updated_at=?
                        WHERE task_id=?
                        """,
                        (TASK_STATUS_OPEN, "unseized", _json_dumps(metadata), now_iso, task_id),
                    )
                    reopened += 1
                    logger.info(
                        "Task Hub heartbeat finalize reopened task %s for retry (%s/%s)",
                        task_id,
                        retry_count,
                        heartbeat_retry_limit,
                    )
            continue

        if policy_norm == "todo" and run_state == "completed":
            dispatch_meta["last_disposition"] = "needs_review"
            dispatch_meta["last_disposition_reason"] = "todo_completed_without_disposition"
            dispatch_meta["completion_unverified"] = True
            metadata["dispatch"] = dispatch_meta
            conn.execute(
                """
                UPDATE task_hub_items
                SET status=?, seizure_state=?, metadata_json=?, updated_at=?
                WHERE task_id=?
                """,
                (TASK_STATUS_REVIEW, "unseized", _json_dumps(metadata), now_iso, task_id),
            )
            reviewed += 1
            logger.warning(
                "Task Hub ToDo finalize moved task %s to needs_review after completed run without explicit disposition",
                task_id,
            )
            continue

        if policy_norm == "todo" and _email_side_effects_detected(conn, task_id):
            dispatch_meta["last_disposition"] = "needs_review"
            dispatch_meta["last_disposition_reason"] = "todo_retryable_with_side_effects"
            dispatch_meta["completion_unverified"] = True
            metadata["dispatch"] = dispatch_meta
            conn.execute(
                """
                UPDATE task_hub_items
                SET status=?, seizure_state=?, metadata_json=?, updated_at=?
                WHERE task_id=?
                """,
                (TASK_STATUS_REVIEW, "unseized", _json_dumps(metadata), now_iso, task_id),
            )
            reviewed += 1
            logger.warning(
                "Task Hub ToDo finalize moved task %s to needs_review because outbound side effects already occurred",
                task_id,
            )
        else:
            retry_count = max(0, _safe_int(dispatch_meta.get("todo_retry_count"), 0)) + 1
            dispatch_meta["todo_retry_count"] = retry_count
            dispatch_meta["todo_retry_limit"] = todo_retry_limit
            metadata["dispatch"] = dispatch_meta
            if retry_count >= todo_retry_limit:
                dispatch_meta["last_disposition"] = "completed"
                dispatch_meta["last_disposition_reason"] = "todo_retry_exhausted"
                dispatch_meta["auto_completed"] = True
                conn.execute(
                    """
                    UPDATE task_hub_items
                    SET status=?, seizure_state=?, metadata_json=?, updated_at=?
                    WHERE task_id=?
                    """,
                    (TASK_STATUS_COMPLETED, "unseized", _json_dumps(metadata), now_iso, task_id),
                )
                completed += 1
                retry_exhausted += 1
                logger.warning(
                    "Task Hub ToDo finalize auto-completed task %s after retry exhaustion (%s/%s)",
                    task_id,
                    retry_count,
                    todo_retry_limit,
                )
            else:
                dispatch_meta["last_disposition"] = "reopened"
                dispatch_meta["last_disposition_reason"] = f"todo_{run_state or 'failed'}_retryable"
                conn.execute(
                    """
                    UPDATE task_hub_items
                    SET status=?, seizure_state=?, metadata_json=?, updated_at=?
                    WHERE task_id=?
                    """,
                    (TASK_STATUS_OPEN, "unseized", _json_dumps(metadata), now_iso, task_id),
                )
                reopened += 1

    # ── Dispatch queue invalidation ─────────────────────────────────
    # Purge completed/reviewed tasks from the snapshot table so they
    # cannot be re-served by a stale dispatch queue build.
    try:
        conn.execute(
            """
            DELETE FROM task_hub_dispatch_queue
            WHERE task_id IN (
                SELECT task_id FROM task_hub_items
                WHERE status IN (?, ?, ?)
            )
            """,
            (TASK_STATUS_COMPLETED, TASK_STATUS_REVIEW, "waiting-on-reply"),
        )
    except Exception as _dq_err:
        logger.debug("Dispatch queue cleanup skipped: %s", _dq_err)

    conn.commit()
    return {
        "finalized": finalized,
        "reopened": reopened,
        "reviewed": reviewed,
        "completed": completed,
        "retry_exhausted": retry_exhausted,
    }


def prune_settled_tasks(
    conn: sqlite3.Connection,
    *,
    retention_days: int = 21,
) -> dict[str, int]:
    ensure_schema(conn)
    retention_days = max(1, int(retention_days))

    # 1. Delete evaluations associated with eligible settled tasks
    c_eval = conn.execute(
        """
        DELETE FROM task_hub_evaluations
        WHERE task_id IN (
            SELECT task_id FROM task_hub_items
            WHERE status IN (?, ?)
            AND updated_at < datetime('now', ?)
        )
        """,
        (TASK_STATUS_COMPLETED, "parked", f"-{retention_days} days"),
    )
    deleted_evaluations = c_eval.rowcount or 0

    # 2. Delete assignments associated with eligible settled tasks
    c_assign = conn.execute(
        """
        DELETE FROM task_hub_assignments
        WHERE task_id IN (
            SELECT task_id FROM task_hub_items
            WHERE status IN (?, ?)
            AND updated_at < datetime('now', ?)
        )
        """,
        (TASK_STATUS_COMPLETED, "parked", f"-{retention_days} days"),
    )
    deleted_assignments = c_assign.rowcount or 0

    # 3. Delete the tasks themselves
    c_items = conn.execute(
        """
        DELETE FROM task_hub_items
        WHERE status IN (?, ?)
        AND updated_at < datetime('now', ?)
        """,
        (TASK_STATUS_COMPLETED, "parked", f"-{retention_days} days"),
    )
    deleted_items = c_items.rowcount or 0

    conn.commit()
    
    if deleted_items > 0:
        logger.info(
            "Pruned %d tasks, %d assignments, %d evaluations older than %d days.",
            deleted_items, deleted_assignments, deleted_evaluations, retention_days
        )

    return {
        "items": deleted_items,
        "assignments": deleted_assignments,
        "evaluations": deleted_evaluations,
    }


def release_stale_assignments(
    conn: sqlite3.Connection,
    *,
    agent_id_prefix: Any = "heartbeat:",
    stale_after_seconds: int = 1800,
    limit: int = 500,
) -> dict[str, int]:
    ensure_schema(conn)
    stale_after_seconds = max(1, int(stale_after_seconds))
    raw_prefixes = agent_id_prefix
    if isinstance(raw_prefixes, (list, tuple, set)):
        prefixes = [str(v).strip() for v in raw_prefixes if str(v).strip()]
    else:
        prefixes = [str(raw_prefixes).strip()] if str(raw_prefixes).strip() else []
    if not prefixes:
        prefixes = ["heartbeat:"]

    clauses = " OR ".join("agent_id LIKE ?" for _ in prefixes)
    params = tuple(f"{prefix}%" for prefix in prefixes) + (max(1, int(limit)),)
    rows = conn.execute(
        f"""
        SELECT assignment_id, started_at
        FROM task_hub_assignments
        WHERE state IN ('seized', 'running')
          AND ({clauses})
        ORDER BY started_at ASC
        LIMIT ?
        """,
        params,
    ).fetchall()

    now_dt = datetime.now(timezone.utc)
    stale_ids: list[str] = []
    for row in rows:
        assignment_id = str(row["assignment_id"] or "").strip()
        started_at = _parse_iso(row["started_at"])
        if not assignment_id or started_at is None:
            continue
        age_seconds = (now_dt - started_at).total_seconds()
        if age_seconds >= stale_after_seconds:
            stale_ids.append(assignment_id)

    if not stale_ids:
        return {"stale_detected": 0, "finalized": 0, "reopened": 0}

    result = finalize_assignments(
        conn,
        assignment_ids=stale_ids,
        state="abandoned",
        result_summary=f"stale_assignment_timeout:{stale_after_seconds}s",
        reopen_in_progress=True,
    )
    return {
        "stale_detected": len(stale_ids),
        "finalized": int(result.get("finalized") or 0),
        "reopened": int(result.get("reopened") or 0),
    }


def _incident_key_from_text(title: str, description: str) -> str:
    text = f"{title}\n{description}".lower()
    patterns = ("event_id:", '"event_id":')
    for marker in patterns:
        idx = text.find(marker)
        if idx < 0:
            continue
        tail = text[idx + len(marker):].strip().strip('"')
        for sep in ("\n", " ", ",", '"'):
            sep_idx = tail.find(sep)
            if sep_idx > 0:
                tail = tail[:sep_idx]
                break
        if tail:
            return tail
    return ""


def list_agent_queue(
    conn: sqlite3.Connection,
    *,
    offset: int = 0,
    limit: int = 60,
    include_csi: bool = True,
    collapse_csi: bool = True,
    project_key: Optional[str] = None,
) -> dict[str, Any]:
    ensure_schema(conn)
    rebuild_dispatch_queue(conn)
    policy = current_policy()

    rows = conn.execute(
        """
        SELECT *
        FROM task_hub_items
        WHERE status IN ('open', 'in_progress', 'blocked', 'needs_review', 'delegated', 'pending_review')
          AND agent_ready = 1
        ORDER BY
          CASE
            WHEN source_kind = 'system_command'
                 AND LOWER(COALESCE(json_extract(metadata_json, '$.intent'), '')) = 'schedule_task'
            THEN 1 ELSE 0
          END DESC,
          must_complete DESC,
          score DESC,
          priority DESC,
          updated_at DESC
        """
    ).fetchall()

    items = [hydrate_item(dict(row)) for row in rows]
    items = [_decorate_csi_routing(item, threshold=policy.agent_threshold) for item in items]
    items = [
        item
        for item in items
        if str(item.get("source_kind") or "").strip().lower() != "csi"
        or str(item.get("csi_routing_state") or "") == CSI_ROUTING_AGENT_ACTIONABLE
    ]
    if project_key:
        project_norm = str(project_key).strip().lower()
        items = [i for i in items if str(i.get("project_key") or "").strip().lower() == project_norm]
    if not include_csi:
        items = [i for i in items if str(i.get("source_kind") or "") != "csi"]

    if collapse_csi:
        csi_incident_counts: dict[str, int] = {}
        for item in items:
            if str(item.get("source_kind") or "").strip().lower() != "csi":
                continue
            incident_key = str(item.get("incident_key") or "").strip()
            if not incident_key:
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            event_type = str(metadata.get("event_type") or "").strip().lower()
            collapse_key = normalize_csi_incident_key(incident_key=incident_key, event_type=event_type)
            if not collapse_key:
                continue
            csi_incident_counts[collapse_key] = int(csi_incident_counts.get(collapse_key) or 0) + 1

        collapsed: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            if str(item.get("source_kind") or "").strip().lower() != "csi":
                collapsed.append(item)
                continue
            incident_key = str(item.get("incident_key") or "").strip()
            if not incident_key:
                collapsed.append(item)
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            event_type = str(metadata.get("event_type") or "").strip().lower()
            collapse_key = normalize_csi_incident_key(incident_key=incident_key, event_type=event_type) or incident_key
            if collapse_key in seen:
                continue
            seen.add(collapse_key)
            item["collapsed_count"] = max(1, int(csi_incident_counts.get(collapse_key) or 1))
            if collapse_key != incident_key:
                item["incident_key_normalized"] = collapse_key
            collapsed.append(item)
        items = collapsed

    total = len(items)
    bounded_offset = max(0, int(offset))
    bounded_limit = max(1, min(200, int(limit)))
    page = items[bounded_offset: bounded_offset + bounded_limit]
    return {
        "items": page,
        "pagination": {
            "total": total,
            "offset": bounded_offset,
            "limit": bounded_limit,
            "count": len(page),
            "has_more": (bounded_offset + bounded_limit) < total,
        },
    }


def list_personal_queue(conn: sqlite3.Connection, *, limit: int = 120) -> list[dict[str, Any]]:
    ensure_schema(conn)
    policy = current_policy()
    rows = conn.execute(
        """
        SELECT *
        FROM task_hub_items
        WHERE status IN ('open', 'in_progress', 'blocked', 'needs_review')
        ORDER BY must_complete DESC, priority DESC, due_at ASC, updated_at DESC
        """,
    ).fetchall()
    items = [_decorate_csi_routing(hydrate_item(dict(row)), threshold=policy.agent_threshold) for row in rows]
    filtered: list[dict[str, Any]] = []
    for item in items:
        source_kind = str(item.get("source_kind") or "").strip().lower()
        if source_kind == "csi":
            if str(item.get("csi_routing_state") or "") == CSI_ROUTING_HUMAN_INTERVENTION_REQUIRED:
                filtered.append(item)
            continue
        if not bool(item.get("agent_ready")):
            filtered.append(item)
    return filtered[: max(1, int(limit))]




# ---------------------------------------------------------------------------
# Task Comments
# ---------------------------------------------------------------------------

def add_comment(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    content: str,
    author: str = "system",
) -> dict[str, Any]:
    """Add a comment/note to a task."""
    ensure_schema(conn)
    comment_id = str(uuid.uuid4())
    now = _now_iso()
    conn.execute(
        "INSERT INTO task_hub_comments (comment_id, task_id, author, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (comment_id, task_id, author, content, now),
    )
    conn.commit()
    return {"comment_id": comment_id, "task_id": task_id, "author": author, "content": content, "created_at": now}


def list_comments(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List comments for a task, newest first."""
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT * FROM task_hub_comments WHERE task_id = ? ORDER BY created_at DESC LIMIT ?",
        (task_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Question Queue (proactive heartbeat questions)
# ---------------------------------------------------------------------------

def enqueue_question(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    question_text: str,
    channel: str = "dashboard",
    expires_minutes: int = 60,
) -> dict[str, Any]:
    """Queue a proactive question for user response."""
    ensure_schema(conn)
    question_id = str(uuid.uuid4())
    now = _now_iso()
    expires_at = datetime.now(timezone.utc).isoformat() if expires_minutes <= 0 else None
    if expires_minutes > 0:
        from datetime import timedelta
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)).isoformat()
    conn.execute(
        "INSERT INTO task_hub_question_queue (question_id, task_id, question_text, asked_at, answered, channel, expires_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
        (question_id, task_id, question_text, now, channel, expires_at),
    )
    conn.commit()
    return {"question_id": question_id, "task_id": task_id, "question_text": question_text, "asked_at": now, "channel": channel, "expires_at": expires_at}


def list_pending_questions(
    conn: sqlite3.Connection,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List unanswered, non-expired questions."""
    ensure_schema(conn)
    now = _now_iso()
    rows = conn.execute(
        "SELECT * FROM task_hub_question_queue WHERE answered = 0 AND (expires_at IS NULL OR expires_at > ?) ORDER BY asked_at ASC LIMIT ?",
        (now, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def answer_question(
    conn: sqlite3.Connection,
    *,
    question_id: str,
    answer_text: str,
) -> dict[str, Any]:
    """Mark a question as answered."""
    ensure_schema(conn)
    conn.execute(
        "UPDATE task_hub_question_queue SET answered = 1, answer_text = ? WHERE question_id = ?",
        (answer_text, question_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM task_hub_question_queue WHERE question_id = ?", (question_id,)).fetchone()
    return dict(row) if row else {"question_id": question_id, "answered": True}


def list_expiring_questions(
    conn: sqlite3.Connection,
    *,
    within_minutes: int = 30,
) -> list[dict[str, Any]]:
    """List unanswered questions whose expiry is within *within_minutes* from now.

    These are candidates for a proactive re-ask before they silently expire.
    """
    ensure_schema(conn)
    now_dt = datetime.now(timezone.utc)
    from datetime import timedelta
    horizon = (now_dt + timedelta(minutes=within_minutes)).isoformat()
    now = now_dt.isoformat()
    rows = conn.execute(
        "SELECT * FROM task_hub_question_queue "
        "WHERE answered = 0 AND expires_at IS NOT NULL AND expires_at > ? AND expires_at <= ? "
        "ORDER BY expires_at ASC",
        (now, horizon),
    ).fetchall()
    return [dict(row) for row in rows]


def requeue_expired_question(
    conn: sqlite3.Connection,
    *,
    question_id: str,
    fresh_expires_minutes: int = 60,
) -> dict[str, Any] | None:
    """Re-create an expired/expiring question with a fresh TTL.

    Appends "(re-asked)" to the question text.  Uses notification dedup
    (event_key = ``reask:<original_question_id>``) to prevent infinite loops —
    returns ``None`` if this question has already been re-asked.
    """
    ensure_schema(conn)
    original = conn.execute(
        "SELECT * FROM task_hub_question_queue WHERE question_id = ?",
        (question_id,),
    ).fetchone()
    if not original:
        return None
    original = dict(original)

    # Dedup guard — only re-ask once per original question
    event_key = f"reask:{question_id}"
    task_id = str(original.get("task_id") or "")
    if has_notification(conn, task_id, event_key):
        return None

    # Mark original as answered so it's no longer pending
    conn.execute(
        "UPDATE task_hub_question_queue SET answered = 1, answer_text = '[expired — re-asked]' WHERE question_id = ?",
        (question_id,),
    )

    # Create the re-asked question
    text = str(original.get("question_text") or "")
    if not text.endswith("(re-asked)"):
        text = f"{text} (re-asked)"
    result = enqueue_question(
        conn,
        task_id=task_id,
        question_text=text,
        channel=str(original.get("channel") or "dashboard"),
        expires_minutes=fresh_expires_minutes,
    )

    # Record dedup so we don't re-ask again
    record_notification(conn, task_id=task_id, event_key=event_key, channel="system")
    return result


def list_brainstorm_tasks(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List active tasks that have a refinement_stage (i.e. brainstorm tasks)."""
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT * FROM task_hub_items "
        "WHERE refinement_stage IS NOT NULL AND status NOT IN ('done', 'parked', 'cancelled') "
        "ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Notification Dedup
# ---------------------------------------------------------------------------

def record_notification(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    event_key: str,
    channel: str = "dashboard",
) -> bool:
    """Record that a notification was sent.  Returns True if new, False if already sent."""
    ensure_schema(conn)
    now = _now_iso()
    try:
        conn.execute(
            "INSERT INTO task_hub_notifications (task_id, event_key, channel, sent_at) VALUES (?, ?, ?, ?)",
            (task_id, event_key, channel, now),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def has_notification(
    conn: sqlite3.Connection,
    task_id: str,
    event_key: str,
) -> bool:
    """Check whether a notification has already been sent."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT 1 FROM task_hub_notifications WHERE task_id = ? AND event_key = ? LIMIT 1",
        (task_id, event_key),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Sub-task / Decomposition Helpers
# ---------------------------------------------------------------------------

def list_subtasks(
    conn: sqlite3.Connection,
    parent_task_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List child tasks for a given parent."""
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT * FROM task_hub_items WHERE parent_task_id = ? ORDER BY priority DESC, created_at ASC LIMIT ?",
        (parent_task_id, limit),
    ).fetchall()
    return [hydrate_item(dict(row)) for row in rows]


def get_parent_progress(
    conn: sqlite3.Connection,
    parent_task_id: str,
) -> dict[str, Any]:
    """Return aggregate progress for a parent task's children."""
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT status, COUNT(*) AS c FROM task_hub_items WHERE parent_task_id = ? GROUP BY status",
        (parent_task_id,),
    ).fetchall()
    total = 0
    completed = 0
    by_status: dict[str, int] = {}
    for row in rows:
        count = int(row["c"] or 0)
        status = str(row["status"] or "")
        by_status[status] = count
        total += count
        if status == TASK_STATUS_COMPLETED:
            completed += count
    return {"total": total, "completed": completed, "by_status": by_status}


def get_decomposition_tree(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    max_depth: int = 3,
) -> dict[str, Any]:
    """Return a task with nested sub-task tree."""
    ensure_schema(conn)
    item = get_item(conn, task_id)
    if item is None:
        return {"task_id": task_id, "error": "not_found"}
    progress = get_parent_progress(conn, task_id)
    item["subtask_progress"] = progress
    if max_depth > 0 and progress["total"] > 0:
        children = list_subtasks(conn, task_id)
        item["subtasks"] = [
            get_decomposition_tree(conn, child["task_id"], max_depth=max_depth - 1)
            for child in children
        ]
    else:
        item["subtasks"] = []
    return item


def decompose_task(
    conn: sqlite3.Connection,
    *,
    parent_task_id: str,
    subtasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Create child tasks under a parent and mark parent as 'decomposed'.

    Each dict in *subtasks* should have at minimum ``title``.  Optional keys:
    ``description``, ``priority``, ``labels``, ``trigger_type``.
    """
    ensure_schema(conn)
    parent = get_item(conn, parent_task_id)
    if parent is None:
        raise ValueError(f"Parent task not found: {parent_task_id}")
    created = []
    for idx, sub in enumerate(subtasks):
        sub_id = sub.get("task_id") or f"{parent_task_id}:sub:{idx + 1}"
        sub_item = {
            "task_id": sub_id,
            "parent_task_id": parent_task_id,
            "source_kind": sub.get("source_kind", "decomposition"),
            "title": sub.get("title", f"Sub-task {idx + 1}"),
            "description": sub.get("description", ""),
            "project_key": sub.get("project_key", parent.get("project_key", "immediate")),
            "priority": sub.get("priority", 2),
            "labels": sub.get("labels", ["agent-ready"]),
            "status": sub.get("status", TASK_STATUS_OPEN),
            "agent_ready": sub.get("agent_ready", True),
            "trigger_type": sub.get("trigger_type", DEFAULT_TRIGGER_TYPE),
        }
        result = upsert_item(conn, sub_item)
        created.append(result)
    # Mark parent as decomposed
    now = _now_iso()
    conn.execute(
        "UPDATE task_hub_items SET refinement_stage = ?, updated_at = ? WHERE task_id = ?",
        ("decomposed", now, parent_task_id),
    )
    conn.commit()
    return created


def complete_subtask_and_check_parent(
    conn: sqlite3.Connection,
    task_id: str,
) -> dict[str, Any]:
    """Mark *task_id* as completed; auto-complete its parent when all siblings are done.

    Returns a dict with:
    - ``task``: the updated subtask
    - ``parent_completed``: ``True`` if parent was auto-completed
    - ``parent_progress``: progress snapshot of siblings
    """
    ensure_schema(conn)
    item = get_item(conn, task_id)
    if item is None:
        raise ValueError(f"Task not found: {task_id}")
    # Mark this subtask complete
    now = _now_iso()
    conn.execute(
        "UPDATE task_hub_items SET status = ?, updated_at = ? WHERE task_id = ?",
        (TASK_STATUS_COMPLETED, now, task_id),
    )
    conn.commit()
    updated_item = get_item(conn, task_id) or {}
    parent_completed = False
    parent_progress: dict[str, Any] = {}
    parent_task_id = item.get("parent_task_id")
    if parent_task_id:
        progress = get_parent_progress(conn, parent_task_id)
        parent_progress = progress
        if progress["total"] > 0 and progress["completed"] == progress["total"]:
            # All siblings done — auto-complete the parent
            conn.execute(
                "UPDATE task_hub_items SET status = ?, updated_at = ? WHERE task_id = ?",
                (TASK_STATUS_COMPLETED, now, parent_task_id),
            )
            conn.commit()
            parent_completed = True
    return {
        "task": updated_item,
        "parent_completed": parent_completed,
        "parent_progress": parent_progress,
    }


# ---------------------------------------------------------------------------
# Refinement Helpers (brainstorm progressive refinement)
# ---------------------------------------------------------------------------

def advance_refinement(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    new_stage: str,
    context_update: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Advance a brainstorm task to the next refinement stage."""
    ensure_schema(conn)
    if new_stage not in REFINEMENT_STAGES:
        raise ValueError(f"Invalid refinement stage: {new_stage}. Valid: {REFINEMENT_STAGES}")
    item = get_item(conn, task_id)
    if item is None:
        raise ValueError(f"Task not found: {task_id}")
    history = _json_loads_obj(item.get("refinement_history_json"), default={})
    now = _now_iso()
    history[now] = {
        "stage": new_stage,
        "context": context_update or {},
    }
    conn.execute(
        "UPDATE task_hub_items SET refinement_stage = ?, refinement_history_json = ?, updated_at = ? WHERE task_id = ?",
        (new_stage, _json_dumps(history), now, task_id),
    )
    conn.commit()
    return get_item(conn, task_id) or {}


def get_refinement_state(
    conn: sqlite3.Connection,
    task_id: str,
) -> dict[str, Any]:
    """Return current refinement stage and full history."""
    ensure_schema(conn)
    item = get_item(conn, task_id)
    if item is None:
        return {"task_id": task_id, "error": "not_found"}
    return {
        "task_id": task_id,
        "refinement_stage": item.get("refinement_stage"),
        "refinement_history": _json_loads_obj(item.get("refinement_history_json"), default={}),
        "trigger_type": item.get("trigger_type", DEFAULT_TRIGGER_TYPE),
    }


def perform_task_action(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    action: str,
    reason: str = "",
    note: str = "",
    agent_id: str = "dashboard_operator",
) -> dict[str, Any]:
    ensure_schema(conn)
    action_norm = str(action or "").strip().lower()
    if action_norm not in VALID_ACTIONS:
        raise ValueError(f"unsupported action: {action_norm}")

    item = get_item(conn, task_id)
    if not item:
        raise ValueError("task not found")

    reason_text = str(reason or note or "").strip()
    now_iso = _now_iso()

    if action_norm == "seize":
        assignment_id = f"asg_{uuid.uuid4().hex[:16]}"
        conn.execute(
            "INSERT INTO task_hub_assignments (assignment_id, task_id, agent_id, state, started_at) VALUES (?, ?, ?, ?, ?)",
            (assignment_id, task_id, agent_id, "seized", now_iso),
        )
        conn.execute(
            "UPDATE task_hub_items SET status=?, seizure_state=?, updated_at=? WHERE task_id=?",
            (TASK_STATUS_IN_PROGRESS, "seized", now_iso, task_id),
        )
        _record_evaluation(
            conn,
            task_id=task_id,
            agent_id=agent_id,
            decision="seize",
            reason=reason_text or "manual_seize",
            score=_safe_float(item.get("score"), 0.0),
            score_confidence=_safe_float(item.get("score_confidence"), 0.0),
            judge_payload={"source": "manual_action"},
        )
    elif action_norm == "reject":
        metadata = dict(item.get("metadata") or {})
        metadata["last_reject_reason"] = reason_text
        conn.execute(
            "UPDATE task_hub_items SET seizure_state=?, metadata_json=?, updated_at=? WHERE task_id=?",
            ("rejected", _json_dumps(metadata), now_iso, task_id),
        )
        _record_evaluation(
            conn,
            task_id=task_id,
            agent_id=agent_id,
            decision="reject",
            reason=reason_text or "manual_reject",
            score=_safe_float(item.get("score"), 0.0),
            score_confidence=_safe_float(item.get("score_confidence"), 0.0),
            judge_payload={"source": "manual_action"},
        )
    elif action_norm == "block":
        metadata = _resolve_dispatch_metadata(dict(item.get("metadata") or {}), assignment_state="completed", now_iso=now_iso)
        _complete_active_assignments_for_task(conn, task_id=task_id, result_summary=reason_text or "blocked", ended_at=now_iso)
        conn.execute(
            "UPDATE task_hub_items SET status=?, seizure_state=?, metadata_json=?, updated_at=? WHERE task_id=?",
            (TASK_STATUS_BLOCKED, "unseized", _json_dumps(metadata), now_iso, task_id),
        )
    elif action_norm == "unblock":
        conn.execute("UPDATE task_hub_items SET status=?, updated_at=? WHERE task_id=?", (TASK_STATUS_OPEN, now_iso, task_id))
    elif action_norm == "review":
        metadata = _resolve_dispatch_metadata(dict(item.get("metadata") or {}), assignment_state="completed", now_iso=now_iso)
        _complete_active_assignments_for_task(conn, task_id=task_id, result_summary=reason_text or "needs_review", ended_at=now_iso)
        conn.execute(
            "UPDATE task_hub_items SET status=?, seizure_state=?, metadata_json=?, updated_at=? WHERE task_id=?",
            (TASK_STATUS_REVIEW, "unseized", _json_dumps(metadata), now_iso, task_id),
        )
    elif action_norm == "complete":
        metadata = _resolve_dispatch_metadata(dict(item.get("metadata") or {}), assignment_state="completed", now_iso=now_iso)
        _completion_token = f"api_{uuid.uuid4().hex[:12]}_{now_iso}"
        _complete_active_assignments_for_task(conn, task_id=task_id, result_summary=reason_text or "completed", ended_at=now_iso)
        conn.execute(
            "UPDATE task_hub_items SET status=?, seizure_state=?, completion_token=?, metadata_json=?, updated_at=? WHERE task_id=?",
            (TASK_STATUS_COMPLETED, "completed", _completion_token, _json_dumps(metadata), now_iso, task_id),
        )
    elif action_norm == "park":
        metadata = _resolve_dispatch_metadata(dict(item.get("metadata") or {}), assignment_state="completed", now_iso=now_iso)
        _complete_active_assignments_for_task(conn, task_id=task_id, result_summary=reason_text or "parked", ended_at=now_iso)
        conn.execute(
            "UPDATE task_hub_items SET status=?, stale_state=?, seizure_state=?, metadata_json=?, updated_at=? WHERE task_id=?",
            (TASK_STATUS_PARKED, "parked_manual", "unseized", _json_dumps(metadata), now_iso, task_id),
        )
    elif action_norm == "snooze":
        metadata = dict(item.get("metadata") or {})
        metadata["snoozed_note"] = reason_text
        conn.execute(
            "UPDATE task_hub_items SET metadata_json=?, updated_at=? WHERE task_id=?",
            (_json_dumps(metadata), now_iso, task_id),
        )
    elif action_norm == "delegate":
        # Simone delegates this task to a VP agent.  reason_text should
        # contain the target agent slug (e.g. "vp.coder.primary").
        metadata = _resolve_dispatch_metadata(dict(item.get("metadata") or {}), assignment_state="completed", now_iso=now_iso)
        delegate_meta = dict(metadata.get("delegation") or {})
        note_text = str(note or "").strip()
        # Extract mission_id from note if present (format: "mission_id=<id>")
        _mission_id_from_note = ""
        if "mission_id=" in note_text:
            for _segment in note_text.split():
                if _segment.startswith("mission_id="):
                    _mission_id_from_note = _segment.split("=", 1)[1].strip()
                    break
        delegate_meta.update({
            "delegate_target": reason_text or "vp.general.primary",
            "delegate_reason": note_text or "simone_triage",
            "delegated_at": now_iso,
        })
        if _mission_id_from_note:
            delegate_meta["mission_id"] = _mission_id_from_note
        metadata["delegation"] = delegate_meta
        _complete_active_assignments_for_task(
            conn,
            task_id=task_id,
            result_summary=reason_text or f"delegated:{delegate_meta.get('mission_id') or 'vp'}",
            ended_at=now_iso,
        )
        conn.execute(
            "UPDATE task_hub_items SET status=?, seizure_state=?, metadata_json=?, updated_at=? WHERE task_id=?",
            (TASK_STATUS_DELEGATED, "delegated", _json_dumps(metadata), now_iso, task_id),
        )
        _record_evaluation(
            conn,
            task_id=task_id,
            agent_id=agent_id,
            decision="delegate",
            reason=reason_text or "simone_triage",
            score=_safe_float(item.get("score"), 0.0),
            score_confidence=_safe_float(item.get("score_confidence"), 0.0),
            judge_payload={"source": "simone_triage", "delegate_target": reason_text or "vp.general.primary", "mission_id": _mission_id_from_note or None},
        )

    elif action_norm == "approve":
        # Simone approves a VP-completed task after reviewing deliverables.
        # Transitions pending_review → completed with sign-off metadata.
        metadata = _resolve_dispatch_metadata(dict(item.get("metadata") or {}), assignment_state="completed", now_iso=now_iso)
        delegation = dict(metadata.get("delegation") or {})
        delegation["approved_at"] = now_iso
        delegation["approved_by"] = agent_id or "simone"
        delegation["approval_note"] = str(note or "").strip() or "approved_by_simone"
        metadata["delegation"] = delegation
        _complete_active_assignments_for_task(conn, task_id=task_id, result_summary=reason_text or "approved", ended_at=now_iso)
        conn.execute(
            "UPDATE task_hub_items SET status=?, seizure_state=?, metadata_json=?, updated_at=? WHERE task_id=?",
            (TASK_STATUS_COMPLETED, "completed", _json_dumps(metadata), now_iso, task_id),
        )
        _record_evaluation(
            conn,
            task_id=task_id,
            agent_id=agent_id,
            decision="approve",
            reason=reason_text or "vp_deliverable_approved",
            score=_safe_float(item.get("score"), 0.0),
            score_confidence=_safe_float(item.get("score_confidence"), 0.0),
            judge_payload={"source": "simone_review", "approval_note": str(note or "").strip()},
        )

    conn.commit()
    rebuild_dispatch_queue(conn)
    fresh = get_item(conn, task_id)
    if not fresh:
        raise ValueError("task not found after action")
    return fresh


# ── VP Lifecycle Helpers ─────────────────────────────────────────────────────
# Phase 4: Functions supporting the VP completion review + sign-off loop.

def transition_to_pending_review(
    conn: sqlite3.Connection,
    *,
    mission_id: str,
    vp_id: str = "",
    terminal_status: str = "completed",
    result_summary: str = "",
) -> Optional[dict[str, Any]]:
    """Transition a delegated task to pending_review when its VP mission finishes.

    Looks up the task via mission_id stored in delegation metadata.
    Returns the updated task dict, or None if no matching task was found.
    """
    ensure_schema(conn)
    task = find_delegated_task_by_mission_id(conn, mission_id=mission_id)
    if task is None:
        return None

    task_id = str(task["task_id"])
    current_status = str(task.get("status") or "")
    if current_status != TASK_STATUS_DELEGATED:
        # Already transitioned (idempotency guard)
        return task

    metadata = dict(task.get("metadata") or {})
    delegation = dict(metadata.get("delegation") or {})
    delegation["vp_terminal_status"] = terminal_status
    delegation["vp_completed_at"] = _now_iso()
    if vp_id:
        delegation["vp_id"] = vp_id
    if result_summary:
        delegation["result_summary"] = result_summary[:500]
    metadata["delegation"] = delegation

    conn.execute(
        "UPDATE task_hub_items SET status=?, seizure_state=?, metadata_json=?, updated_at=? WHERE task_id=?",
        (TASK_STATUS_PENDING_REVIEW, "pending_review", _json_dumps(metadata), _now_iso(), task_id),
    )
    conn.commit()
    rebuild_dispatch_queue(conn)

    logger.info(
        "📋→🔍 Task %s transitioned to pending_review (mission=%s vp=%s terminal=%s)",
        task_id, mission_id, vp_id, terminal_status,
    )
    return get_item(conn, task_id)


def find_delegated_task_by_mission_id(
    conn: sqlite3.Connection,
    *,
    mission_id: str,
) -> Optional[dict[str, Any]]:
    """Find a delegated task whose metadata contains the given mission_id.

    Searches delegation.mission_id in metadata_json using JSON extract.
    Falls back to a LIKE search if json_extract is unavailable.
    """
    ensure_schema(conn)
    mission_id = str(mission_id or "").strip()
    if not mission_id:
        return None

    # Primary: use json_extract (available in SQLite 3.38+)
    try:
        row = conn.execute(
            """
            SELECT * FROM task_hub_items
            WHERE status = ?
              AND json_extract(metadata_json, '$.delegation.mission_id') = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (TASK_STATUS_DELEGATED, mission_id),
        ).fetchone()
        if row:
            return _row_to_dict(row)
    except Exception:
        pass

    # Fallback: LIKE search (works on older SQLite)
    try:
        row = conn.execute(
            """
            SELECT * FROM task_hub_items
            WHERE status = ?
              AND metadata_json LIKE ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (TASK_STATUS_DELEGATED, f"%{mission_id}%"),
        ).fetchone()
        if row:
            return _row_to_dict(row)
    except Exception:
        pass

    return None


def get_pending_review_tasks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all tasks in pending_review status for Simone's sign-off prompt."""
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT * FROM task_hub_items WHERE status = ? ORDER BY updated_at DESC",
        (TASK_STATUS_PENDING_REVIEW,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def reopen_stale_delegations(
    conn: sqlite3.Connection,
    *,
    stale_hours: float = 4.0,
) -> list[dict[str, Any]]:
    """Reopen delegated tasks that have been stale for >stale_hours.

    Returns a list of tasks that were reopened.
    """
    ensure_schema(conn)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=stale_hours)).isoformat()
    rows = conn.execute(
        """
        SELECT * FROM task_hub_items
        WHERE status = ?
          AND updated_at < ?
        ORDER BY updated_at ASC
        """,
        (TASK_STATUS_DELEGATED, cutoff),
    ).fetchall()

    reopened = []
    for row in rows:
        task = _row_to_dict(row)
        task_id = str(task["task_id"])
        metadata = dict(task.get("metadata") or {})
        delegation = dict(metadata.get("delegation") or {})
        delegation["stale_reopened_at"] = _now_iso()
        delegation["stale_reason"] = f"no_vp_progress_after_{stale_hours}h"
        metadata["delegation"] = delegation

        conn.execute(
            "UPDATE task_hub_items SET status=?, seizure_state=?, metadata_json=?, updated_at=? WHERE task_id=?",
            (TASK_STATUS_OPEN, "open", _json_dumps(metadata), _now_iso(), task_id),
        )
        logger.warning(
            "📋⏰ Stale delegation reopened: task=%s delegated at %s (>%.1fh)",
            task_id, delegation.get("delegated_at", "?"), stale_hours,
        )
        reopened.append(task)

    if reopened:
        conn.commit()
        rebuild_dispatch_queue(conn)

    return reopened


def get_agent_activity(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_schema(conn)
    now = datetime.now(timezone.utc)
    window_1h = (now.timestamp() - 3600)
    window_24h = (now.timestamp() - 86400)

    active_rows = conn.execute(
        """
        SELECT a.assignment_id, a.task_id, a.agent_id, a.state, a.started_at,
               i.title, i.project_key, i.priority
        FROM task_hub_assignments a
        JOIN task_hub_items i ON i.task_id = a.task_id
        WHERE a.state IN ('seized', 'running')
        ORDER BY a.started_at DESC
        LIMIT 100
        """
    ).fetchall()

    def _count_eval(decision: str, since_ts: float) -> int:
        rows = conn.execute(
            "SELECT evaluated_at FROM task_hub_evaluations WHERE decision = ? ORDER BY evaluated_at DESC LIMIT 20000",
            (decision,),
        ).fetchall()
        count = 0
        for row in rows:
            dt = _parse_iso(row["evaluated_at"])
            if not dt:
                continue
            if dt.timestamp() >= since_ts:
                count += 1
            else:
                break
        return count

    def _count_created_tasks(since_ts: float) -> int:
        rows = conn.execute(
            "SELECT created_at FROM task_hub_items ORDER BY created_at DESC LIMIT 50000"
        ).fetchall()
        count = 0
        for row in rows:
            dt = _parse_iso(row["created_at"])
            if not dt:
                continue
            if dt.timestamp() >= since_ts:
                count += 1
            else:
                break
        return count

    def _count_created_by_source(since_ts: float) -> dict[str, int]:
        rows = conn.execute(
            """
            SELECT source_kind, created_at
            FROM task_hub_items
            ORDER BY created_at DESC
            LIMIT 50000
            """
        ).fetchall()
        counts: dict[str, int] = {}
        for row in rows:
            dt = _parse_iso(row["created_at"])
            if not dt:
                continue
            if dt.timestamp() < since_ts:
                break
            source_kind = str(row["source_kind"] or "unknown")
            counts[source_kind] = counts.get(source_kind, 0) + 1
        return counts

    def _count_completed(since_ts: float) -> int:
        rows = conn.execute(
            "SELECT ended_at FROM task_hub_assignments WHERE state = 'completed' ORDER BY ended_at DESC LIMIT 20000"
        ).fetchall()
        count = 0
        for row in rows:
            dt = _parse_iso(row["ended_at"])
            if not dt:
                continue
            if dt.timestamp() >= since_ts:
                count += 1
            else:
                break
        return count

    visible_agent_queue = list_agent_queue(conn, include_csi=True, collapse_csi=False, limit=5000)
    active_agents_row = conn.execute(
        "SELECT COUNT(DISTINCT agent_id) AS c FROM task_hub_assignments WHERE state IN ('seized', 'running')"
    ).fetchone()

    rejected_rows = conn.execute(
        """
        SELECT COALESCE(reason,'(unspecified)') AS reason, COUNT(*) AS c
        FROM task_hub_evaluations
        WHERE decision='reject'
        GROUP BY reason
        ORDER BY c DESC
        LIMIT 10
        """
    ).fetchall()

    return {
        "active_agents": int((active_agents_row["c"] if active_agents_row else 0) or 0),
        "active_assignments": [
            {
                "assignment_id": str(r["assignment_id"] or ""),
                "task_id": str(r["task_id"] or ""),
                "agent_id": str(r["agent_id"] or ""),
                "state": str(r["state"] or ""),
                "started_at": str(r["started_at"] or ""),
                "title": str(r["title"] or ""),
                "project_key": str(r["project_key"] or ""),
                "priority": _safe_int(r["priority"], 1),
            }
            for r in active_rows
        ],
        "metrics": {
            "1h": {
                # "new" should represent newly created tasks, not scorer defer events.
                "new": _count_created_tasks(window_1h),
                "new_by_source": _count_created_by_source(window_1h),
                "seized": _count_eval("seize", window_1h),
                "rejected": _count_eval("reject", window_1h),
                "completed": _count_completed(window_1h),
            },
            "24h": {
                "new": _count_created_tasks(window_24h),
                "new_by_source": _count_created_by_source(window_24h),
                "seized": _count_eval("seize", window_24h),
                "rejected": _count_eval("reject", window_24h),
                "completed": _count_completed(window_24h),
            },
            "rejection_reasons": [
                {"reason": str(r["reason"] or "(unspecified)"), "count": int(r["c"] or 0)}
                for r in rejected_rows
            ],
        },
        "backlog_open": int((visible_agent_queue.get("pagination") or {}).get("total") or 0),
    }




def upsert_csi_item(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    event_type: str,
    source: str,
    title: str,
    message: str,
    project_key: str,
    labels: list[str],
    priority: int,
    incident_key: Optional[str],
    must_complete: bool,
    mirror_status: str,
    routing_state: str = CSI_ROUTING_INCUBATING,
    routing_reason: str = "",
    confidence_target: Optional[float] = None,
    follow_up_budget_remaining: Optional[int] = None,
    human_intervention_reason: Optional[str] = None,
) -> dict[str, Any]:
    ensure_schema(conn)
    key = str(incident_key or "").strip() or str(event_id).strip() or uuid.uuid4().hex
    task_id = f"csi:{key}"
    normalized_state = str(routing_state or CSI_ROUTING_INCUBATING).strip().lower()
    if normalized_state not in CSI_ROUTING_STATES:
        normalized_state = CSI_ROUTING_INCUBATING
    csi_metadata: dict[str, Any] = {
        "routing_state": normalized_state,
        "routing_reason": str(routing_reason or "").strip() or "ingest_default",
        "last_maturation_at": _now_iso(),
    }
    if confidence_target is not None:
        csi_metadata["confidence_target"] = float(confidence_target)
    if follow_up_budget_remaining is not None:
        csi_metadata["follow_up_budget_remaining"] = int(follow_up_budget_remaining)
    if human_intervention_reason:
        csi_metadata["human_intervention_reason"] = str(human_intervention_reason).strip()
    return upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "csi",
            "source_ref": str(event_id),
            "title": title,
            "description": message,
            "project_key": project_key,
            "priority": priority,
            "labels": labels,
            "status": TASK_STATUS_OPEN,
            "incident_key": key,
            "must_complete": must_complete,
            "agent_ready": normalized_state == CSI_ROUTING_AGENT_ACTIONABLE,
            "mirror_status": mirror_status,
            "metadata": {
                "event_type": event_type,
                "source": source,
                "csi": csi_metadata,
            },
        },
    )


def park_csi_items_not_matching_event_types(
    conn: sqlite3.Connection,
    *,
    allowed_event_types: set[str],
    park_reason: str = "csi_routing_policy_filtered",
) -> dict[str, int]:
    """Park open CSI items whose event_type is outside the allowed set."""
    ensure_schema(conn)
    allowed = {str(v or "").strip().lower() for v in allowed_event_types if str(v or "").strip()}
    rows = conn.execute(
        """
        SELECT task_id, metadata_json
        FROM task_hub_items
        WHERE source_kind='csi'
          AND status IN ('open', 'blocked', 'needs_review')
        """
    ).fetchall()
    parked = 0
    examined = 0
    now_iso = _now_iso()
    for row in rows:
        examined += 1
        metadata = _json_loads_obj(row["metadata_json"], default={})
        event_type = str(metadata.get("event_type") or "").strip().lower()
        if event_type in allowed:
            continue
        metadata["auto_parked_reason"] = str(park_reason or "csi_routing_policy_filtered")
        metadata["auto_parked_at"] = now_iso
        metadata["auto_parked_event_type"] = event_type or "unknown"
        conn.execute(
            """
            UPDATE task_hub_items
            SET status=?, stale_state=?, seizure_state=?, metadata_json=?, updated_at=?
            WHERE task_id=?
            """,
            (TASK_STATUS_PARKED, "parked_policy", "unseized", _json_dumps(metadata), now_iso, str(row["task_id"] or "")),
        )
        parked += 1
    if parked:
        conn.commit()
    return {"examined": examined, "parked": parked}


def approvals_as_tasks(approvals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now = _now_iso()
    for approval in approvals:
        approval_id = str(approval.get("approval_id") or approval.get("phase_id") or "").strip()
        if not approval_id:
            continue
        title = str(approval.get("title") or approval.get("summary") or f"Approval required: {approval_id}").strip()
        due_at = str(approval.get("due_at") or approval.get("deadline") or "").strip() or None
        rows.append(
            {
                "task_id": f"approval:{approval_id}",
                "source_kind": "approval",
                "source_ref": approval_id,
                "title": title,
                "description": str(approval.get("description") or approval.get("details") or ""),
                "project_key": "approval",
                "priority": 4,
                "due_at": due_at,
                "labels": ["human", "approval", "must-complete"],
                "status": TASK_STATUS_OPEN,
                "must_complete": True,
                "agent_ready": False,
                "mirror_status": "internal",
                "metadata": {
                    "approval": approval,
                    "focus_href": "/dashboard/todolist?mode=personal&focus=approvals",
                    "generated_at": now,
                },
            }
        )
    return rows


def overview(conn: sqlite3.Connection, *, approvals_pending: int = 0) -> dict[str, Any]:
    ensure_schema(conn)
    queue = get_dispatch_queue(conn, limit=500)
    visible_agent_queue = list_agent_queue(conn, include_csi=True, collapse_csi=False, limit=500)
    activity = get_agent_activity(conn)
    policy = current_policy()

    status_rows = conn.execute("SELECT status, COUNT(*) AS c FROM task_hub_items GROUP BY status").fetchall()
    status_counts = {str(r["status"] or "unknown"): int(r["c"] or 0) for r in status_rows}

    source_rows = conn.execute(
        "SELECT source_kind, COUNT(*) AS c FROM task_hub_items WHERE status NOT IN (?, ?) GROUP BY source_kind",
        (TASK_STATUS_COMPLETED, TASK_STATUS_PARKED),
    ).fetchall()
    source_counts = {str(r["source_kind"] or "unknown"): int(r["c"] or 0) for r in source_rows}

    csi_rows = conn.execute(
        """
        SELECT incident_key, metadata_json
        FROM task_hub_items
        WHERE source_kind='csi'
          AND status NOT IN (?, ?)
        """,
        (TASK_STATUS_COMPLETED, TASK_STATUS_PARKED),
    ).fetchall()
    open_csi_incident_keys: set[str] = set()
    for row in csi_rows:
        incident_key = str(row["incident_key"] or "").strip()
        if not incident_key:
            continue
        metadata = _json_loads_obj(row["metadata_json"], default={})
        event_type = str(metadata.get("event_type") or "").strip().lower()
        normalized = normalize_csi_incident_key(incident_key=incident_key, event_type=event_type) or incident_key
        open_csi_incident_keys.add(normalized)

    csi_task_rows = conn.execute(
        """
        SELECT *
        FROM task_hub_items
        WHERE source_kind='csi'
          AND status NOT IN (?, ?)
        """,
        (TASK_STATUS_COMPLETED, TASK_STATUS_PARKED),
    ).fetchall()
    csi_agent_actionable_open = 0
    csi_human_open = 0
    csi_incubating_hidden = 0
    for row in csi_task_rows:
        item = _decorate_csi_routing(hydrate_item(dict(row)), threshold=policy.agent_threshold)
        state = str(item.get("csi_routing_state") or "")
        if state == CSI_ROUTING_AGENT_ACTIONABLE:
            csi_agent_actionable_open += 1
        elif state == CSI_ROUTING_HUMAN_INTERVENTION_REQUIRED:
            csi_human_open += 1
        else:
            csi_incubating_hidden += 1

    return {
        "default_mode": "agent",
        "approvals_pending": int(approvals_pending or 0),
        "queue_health": {
            "dispatch_queue_size": int((visible_agent_queue.get("pagination") or {}).get("total") or 0),
            "dispatch_eligible": int(queue.get("eligible_total") or 0),
            "threshold": int(policy.agent_threshold),
            "csi_agent_actionable_open": csi_agent_actionable_open,
            "csi_human_open": csi_human_open,
            "csi_incubating_hidden": csi_incubating_hidden,
            "status_counts": status_counts,
            "source_counts": source_counts,
        },
        "agent_activity": {
            "active_agents": int(activity.get("active_agents") or 0),
            "active_assignments": len(activity.get("active_assignments") or []),
            "backlog_open": int(activity.get("backlog_open") or 0),
            "metrics": activity.get("metrics") or {},
        },
        "csi_incident_summary": {
            "open_incidents": len(open_csi_incident_keys),
        },
    }
