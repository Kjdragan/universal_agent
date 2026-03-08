from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional


TASK_STATUS_OPEN = "open"
TASK_STATUS_IN_PROGRESS = "in_progress"
TASK_STATUS_BLOCKED = "blocked"
TASK_STATUS_REVIEW = "needs_review"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_PARKED = "parked"

TERMINAL_STATUSES = {TASK_STATUS_COMPLETED, TASK_STATUS_PARKED}
ACTIVE_STATUSES = {TASK_STATUS_OPEN, TASK_STATUS_IN_PROGRESS, TASK_STATUS_BLOCKED, TASK_STATUS_REVIEW}
VALID_ACTIONS = {"seize", "reject", "block", "unblock", "review", "complete", "park", "snooze"}


@dataclass
class TaskHubPolicy:
    agent_threshold: int
    stale_enabled: bool
    stale_min_cycles: int
    stale_min_age_minutes: int


DEFAULT_MIRROR_POLICY = {
    "mode": "selective",
    "classes": ["personal", "approval", "must_complete", "csi_incident_high"],
    "reverse_sync_fields": ["complete", "reopen", "priority"],
    "enabled": True,
}


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
        agent_threshold=max(1, min(10, _safe_int(os.getenv("UA_TASK_AGENT_THRESHOLD"), 8))),
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

        CREATE TABLE IF NOT EXISTS task_hub_mirror_map (
            task_id TEXT PRIMARY KEY,
            todoist_task_id TEXT,
            mirror_class TEXT,
            mirror_state TEXT NOT NULL DEFAULT 'unmapped',
            last_sync_at TEXT,
            last_error TEXT
        );

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
        """
    )
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


def get_mirror_policy(conn: sqlite3.Connection) -> dict[str, Any]:
    stored = _get_setting(conn, "todoist_mirror_policy", default=DEFAULT_MIRROR_POLICY)
    merged = dict(DEFAULT_MIRROR_POLICY)
    merged.update(stored)
    classes = merged.get("classes")
    if not isinstance(classes, list):
        merged["classes"] = list(DEFAULT_MIRROR_POLICY["classes"])
    else:
        merged["classes"] = [str(c).strip() for c in classes if str(c).strip()]
    reverse = merged.get("reverse_sync_fields")
    if not isinstance(reverse, list):
        merged["reverse_sync_fields"] = list(DEFAULT_MIRROR_POLICY["reverse_sync_fields"])
    else:
        merged["reverse_sync_fields"] = [str(c).strip() for c in reverse if str(c).strip()]
    merged["enabled"] = bool(merged.get("enabled", True))
    merged["mode"] = str(merged.get("mode") or "selective").strip().lower() or "selective"
    return merged


def update_mirror_policy(conn: sqlite3.Connection, patch: dict[str, Any]) -> dict[str, Any]:
    current = get_mirror_policy(conn)
    next_policy = dict(current)
    for key in ("mode", "classes", "reverse_sync_fields", "enabled"):
        if key in patch:
            next_policy[key] = patch[key]
    if "mode" in next_policy:
        next_policy["mode"] = str(next_policy.get("mode") or "selective").strip().lower() or "selective"
    if "classes" in next_policy:
        raw = next_policy.get("classes")
        if isinstance(raw, list):
            next_policy["classes"] = [str(v).strip() for v in raw if str(v).strip()]
        else:
            next_policy["classes"] = list(DEFAULT_MIRROR_POLICY["classes"])
    if "reverse_sync_fields" in next_policy:
        raw = next_policy.get("reverse_sync_fields")
        if isinstance(raw, list):
            next_policy["reverse_sync_fields"] = [str(v).strip() for v in raw if str(v).strip()]
        else:
            next_policy["reverse_sync_fields"] = list(DEFAULT_MIRROR_POLICY["reverse_sync_fields"])
    next_policy["enabled"] = bool(next_policy.get("enabled", True))
    _set_setting(conn, "todoist_mirror_policy", next_policy)
    return get_mirror_policy(conn)


def get_mirror_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT mirror_class, mirror_state, COUNT(*) AS c FROM task_hub_mirror_map GROUP BY mirror_class, mirror_state"
    ).fetchall()
    by_class: dict[str, int] = {}
    by_state: dict[str, int] = {}
    for row in rows:
        mclass = str(row["mirror_class"] or "unknown")
        mstate = str(row["mirror_state"] or "unknown")
        count = int(row["c"] or 0)
        by_class[mclass] = by_class.get(mclass, 0) + count
        by_state[mstate] = by_state.get(mstate, 0) + count
    return {
        "total": sum(by_state.values()),
        "by_class": by_class,
        "by_state": by_state,
    }


def hydrate_item(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["labels"] = [str(v) for v in _json_loads_list(item.get("labels_json")) if str(v)]
    item["metadata"] = _json_loads_obj(item.get("metadata_json"), default={})
    item["must_complete"] = bool(item.get("must_complete"))
    item["agent_ready"] = bool(item.get("agent_ready"))
    item["score"] = _safe_float(item.get("score"), 0.0)
    item["score_confidence"] = _safe_float(item.get("score_confidence"), 0.0)
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

    status = str(item.get("status") or existing.get("status") or TASK_STATUS_OPEN).strip().lower()
    if status not in ACTIVE_STATUSES | TERMINAL_STATUSES:
        status = TASK_STATUS_OPEN

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
        "seizure_state": str(item.get("seizure_state") or existing.get("seizure_state") or "unseized"),
        "mirror_status": str(item.get("mirror_status") or existing.get("mirror_status") or "internal"),
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
            mirror_status, metadata_json, created_at, updated_at
        ) VALUES (
            :task_id, :source_kind, :source_ref, :title, :description, :project_key, :priority, :due_at,
            :labels_json, :status, :must_complete, :incident_key, :workstream_id, :subtask_role,
            :parent_task_id, :agent_ready, :score, :score_confidence, :stale_state, :seizure_state,
            :mirror_status, :metadata_json, :created_at, :updated_at
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

    final_score = max(1.0, min(10.0, round(score, 2)))

    confidence = 0.58
    if h_bonus > 0:
        confidence += 0.12
    if must_complete:
        confidence += 0.1
    if "blocked" in labels:
        confidence += 0.08
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

        conn.execute(
            "UPDATE task_hub_items SET score=?, score_confidence=?, stale_state=?, metadata_json=?, updated_at=? WHERE task_id=?",
            (score, confidence, stale_state, _json_dumps(metadata), _now_iso(), task_id),
        )

        eligible = bool(item.get("agent_ready")) and score >= float(policy.agent_threshold)
        status = str(item.get("status") or TASK_STATUS_OPEN)
        if status in {TASK_STATUS_BLOCKED, TASK_STATUS_REVIEW, TASK_STATUS_IN_PROGRESS}:
            eligible = False
        if bool(item.get("must_complete")) and status == TASK_STATUS_OPEN:
            eligible = True

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
        item["eligible"] = eligible
        item["stale_state"] = stale_state
        scored.append(item)

    def _sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        must_complete = 1 if bool(row.get("must_complete")) else 0
        approval = 1 if str(row.get("project_key") or "") == "approval" else 0
        score = _safe_float(row.get("score"), 0.0)
        priority = _safe_int(row.get("priority"), 1)
        due_sort = str(row.get("due_at") or "9999-12-31T23:59:59+00:00")
        updated_sort = str(row.get("updated_at") or "")
        return (-must_complete, -approval, -score, -priority, due_sort, updated_sort)

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
                None if eligible else "not_eligible",
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


def claim_next_dispatch_tasks(conn: sqlite3.Connection, *, limit: int = 1, agent_id: str = "heartbeat") -> list[dict[str, Any]]:
    ensure_schema(conn)
    rebuild_dispatch_queue(conn)
    queue = get_dispatch_queue(conn, limit=max(1, int(limit)) * 6)
    claimed: list[dict[str, Any]] = []

    for item in queue.get("items", []):
        if len(claimed) >= max(1, int(limit)):
            break
        if not bool(item.get("eligible")):
            continue
        task_id = str(item.get("task_id") or "")
        if not task_id:
            continue

        current = get_item(conn, task_id)
        if not current:
            continue
        if str(current.get("status") or "") != TASK_STATUS_OPEN:
            continue

        assignment_id = f"asg_{uuid.uuid4().hex[:16]}"
        conn.execute(
            "INSERT INTO task_hub_assignments (assignment_id, task_id, agent_id, state, started_at) VALUES (?, ?, ?, ?, ?)",
            (assignment_id, task_id, agent_id, "seized", _now_iso()),
        )
        conn.execute(
            "UPDATE task_hub_items SET status=?, seizure_state=?, updated_at=? WHERE task_id=?",
            (TASK_STATUS_IN_PROGRESS, "seized", _now_iso(), task_id),
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
        claimed.append(item)

    conn.commit()
    return claimed


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

    rows = conn.execute(
        """
        SELECT *
        FROM task_hub_items
        WHERE status IN ('open', 'in_progress', 'blocked', 'needs_review')
          AND agent_ready = 1
        ORDER BY must_complete DESC, score DESC, priority DESC, updated_at DESC
        """
    ).fetchall()

    items = [hydrate_item(dict(row)) for row in rows]
    if project_key:
        project_norm = str(project_key).strip().lower()
        items = [i for i in items if str(i.get("project_key") or "").strip().lower() == project_norm]
    if not include_csi:
        items = [i for i in items if str(i.get("source_kind") or "") != "csi"]

    if collapse_csi:
        collapsed: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            if str(item.get("source_kind") or "") != "csi":
                collapsed.append(item)
                continue
            incident_key = str(item.get("incident_key") or "").strip()
            if not incident_key:
                collapsed.append(item)
                continue
            if incident_key in seen:
                continue
            seen.add(incident_key)
            c_row = conn.execute(
                "SELECT COUNT(*) AS c FROM task_hub_items WHERE source_kind='csi' AND incident_key=? AND status NOT IN (?, ?)",
                (incident_key, TASK_STATUS_COMPLETED, TASK_STATUS_PARKED),
            ).fetchone()
            item["collapsed_count"] = int((c_row["c"] if c_row else 1) or 1)
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
    rows = conn.execute(
        """
        SELECT *
        FROM task_hub_items
        WHERE status IN ('open', 'in_progress', 'blocked', 'needs_review')
          AND agent_ready = 0
        ORDER BY must_complete DESC, priority DESC, due_at ASC, updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [hydrate_item(dict(row)) for row in rows]


def upsert_mirror_map(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    todoist_task_id: Optional[str],
    mirror_class: Optional[str],
    mirror_state: str,
    last_error: Optional[str] = None,
) -> None:
    ensure_schema(conn)
    conn.execute(
        """
        INSERT INTO task_hub_mirror_map (task_id, todoist_task_id, mirror_class, mirror_state, last_sync_at, last_error)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET
            todoist_task_id=excluded.todoist_task_id,
            mirror_class=excluded.mirror_class,
            mirror_state=excluded.mirror_state,
            last_sync_at=excluded.last_sync_at,
            last_error=excluded.last_error
        """,
        (task_id, todoist_task_id, mirror_class, mirror_state, _now_iso(), last_error),
    )
    conn.commit()


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

    if action_norm == "seize":
        assignment_id = f"asg_{uuid.uuid4().hex[:16]}"
        conn.execute(
            "INSERT INTO task_hub_assignments (assignment_id, task_id, agent_id, state, started_at) VALUES (?, ?, ?, ?, ?)",
            (assignment_id, task_id, agent_id, "seized", _now_iso()),
        )
        conn.execute(
            "UPDATE task_hub_items SET status=?, seizure_state=?, updated_at=? WHERE task_id=?",
            (TASK_STATUS_IN_PROGRESS, "seized", _now_iso(), task_id),
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
            ("rejected", _json_dumps(metadata), _now_iso(), task_id),
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
        conn.execute("UPDATE task_hub_items SET status=?, updated_at=? WHERE task_id=?", (TASK_STATUS_BLOCKED, _now_iso(), task_id))
    elif action_norm == "unblock":
        conn.execute("UPDATE task_hub_items SET status=?, updated_at=? WHERE task_id=?", (TASK_STATUS_OPEN, _now_iso(), task_id))
    elif action_norm == "review":
        conn.execute("UPDATE task_hub_items SET status=?, updated_at=? WHERE task_id=?", (TASK_STATUS_REVIEW, _now_iso(), task_id))
    elif action_norm == "complete":
        conn.execute(
            "UPDATE task_hub_items SET status=?, seizure_state=?, updated_at=? WHERE task_id=?",
            (TASK_STATUS_COMPLETED, "completed", _now_iso(), task_id),
        )
        conn.execute(
            """
            UPDATE task_hub_assignments
            SET state='completed', ended_at=?, result_summary=?
            WHERE task_id=? AND state IN ('seized', 'running')
            """,
            (_now_iso(), reason_text or "completed", task_id),
        )
    elif action_norm == "park":
        conn.execute(
            "UPDATE task_hub_items SET status=?, stale_state=?, seizure_state=?, updated_at=? WHERE task_id=?",
            (TASK_STATUS_PARKED, "parked_manual", "unseized", _now_iso(), task_id),
        )
    elif action_norm == "snooze":
        metadata = dict(item.get("metadata") or {})
        metadata["snoozed_note"] = reason_text
        conn.execute(
            "UPDATE task_hub_items SET metadata_json=?, updated_at=? WHERE task_id=?",
            (_json_dumps(metadata), _now_iso(), task_id),
        )

    conn.commit()
    rebuild_dispatch_queue(conn)
    fresh = get_item(conn, task_id)
    if not fresh:
        raise ValueError("task not found after action")
    return fresh


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

    backlog_row = conn.execute(
        "SELECT COUNT(*) AS c FROM task_hub_items WHERE status IN ('open', 'in_progress', 'blocked', 'needs_review')"
    ).fetchone()
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
                "new": _count_eval("defer", window_1h),
                "seized": _count_eval("seize", window_1h),
                "rejected": _count_eval("reject", window_1h),
                "completed": _count_completed(window_1h),
            },
            "24h": {
                "new": _count_eval("defer", window_24h),
                "seized": _count_eval("seize", window_24h),
                "rejected": _count_eval("reject", window_24h),
                "completed": _count_completed(window_24h),
            },
            "rejection_reasons": [
                {"reason": str(r["reason"] or "(unspecified)"), "count": int(r["c"] or 0)}
                for r in rejected_rows
            ],
        },
        "backlog_open": int((backlog_row["c"] if backlog_row else 0) or 0),
    }


def sync_todoist_task_rows(conn: sqlite3.Connection, tasks: list[dict[str, Any]], *, project_name_to_key: dict[str, str]) -> dict[str, Any]:
    ensure_schema(conn)
    seen_ids: set[str] = set()
    upserted = 0

    for task in tasks:
        source_ref = str(task.get("id") or "").strip()
        if not source_ref:
            continue
        task_id = f"todoist:{source_ref}"
        seen_ids.add(task_id)

        labels = [str(v).strip() for v in (task.get("labels") or []) if str(v).strip()]
        label_set = {v.lower() for v in labels}
        title = str(task.get("content") or "").strip() or source_ref
        description = str(task.get("description") or "")

        project_id = str(task.get("project_id") or "").strip()
        project_key = project_name_to_key.get(project_id, "immediate")

        priority_raw = task.get("priority")
        priority = _safe_int(priority_raw, 1)
        if isinstance(priority_raw, str):
            text = priority_raw.upper()
            if text.startswith("P1"):
                priority = 4
            elif text.startswith("P2"):
                priority = 3
            elif text.startswith("P3"):
                priority = 2
            else:
                priority = 1

        due_at = str(task.get("due_datetime") or task.get("due_date") or "").strip() or None
        incident_key = ""
        if "csi" in label_set or any(v.startswith("csi-project:") for v in label_set):
            incident_key = _incident_key_from_text(title, description)

        upsert_item(
            conn,
            {
                "task_id": task_id,
                "source_kind": "todoist",
                "source_ref": source_ref,
                "title": title,
                "description": description,
                "project_key": project_key,
                "priority": priority,
                "due_at": due_at,
                "labels": labels,
                "status": TASK_STATUS_OPEN,
                "incident_key": incident_key or None,
                "must_complete": bool({"must-complete", "safety-critical"} & label_set),
                "agent_ready": "agent-ready" in label_set,
                "mirror_status": "mirrored",
                "metadata": {
                    "todoist_url": str(task.get("url") or ""),
                    "section_id": str(task.get("section_id") or ""),
                    "todoist_created_at": str(task.get("created_at") or ""),
                },
            },
        )
        upserted += 1

    parked_missing = 0
    rows = conn.execute(
        "SELECT task_id FROM task_hub_items WHERE source_kind = 'todoist' AND status NOT IN (?, ?)",
        (TASK_STATUS_COMPLETED, TASK_STATUS_PARKED),
    ).fetchall()
    for row in rows:
        task_id = str(row["task_id"] or "")
        if task_id and task_id not in seen_ids:
            conn.execute(
                "UPDATE task_hub_items SET status=?, stale_state=?, updated_at=? WHERE task_id=?",
                (TASK_STATUS_PARKED, "source_missing", _now_iso(), task_id),
            )
            parked_missing += 1

    conn.commit()
    return {"upserted": upserted, "parked_missing": parked_missing}


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
) -> dict[str, Any]:
    ensure_schema(conn)
    key = str(incident_key or "").strip() or str(event_id).strip() or uuid.uuid4().hex
    task_id = f"csi:{key}"
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
            "agent_ready": True,
            "mirror_status": mirror_status,
            "metadata": {
                "event_type": event_type,
                "source": source,
            },
        },
    )


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
    activity = get_agent_activity(conn)

    status_rows = conn.execute("SELECT status, COUNT(*) AS c FROM task_hub_items GROUP BY status").fetchall()
    status_counts = {str(r["status"] or "unknown"): int(r["c"] or 0) for r in status_rows}

    source_rows = conn.execute(
        "SELECT source_kind, COUNT(*) AS c FROM task_hub_items WHERE status NOT IN (?, ?) GROUP BY source_kind",
        (TASK_STATUS_COMPLETED, TASK_STATUS_PARKED),
    ).fetchall()
    source_counts = {str(r["source_kind"] or "unknown"): int(r["c"] or 0) for r in source_rows}

    csi_row = conn.execute(
        "SELECT COUNT(DISTINCT incident_key) AS c FROM task_hub_items WHERE source_kind='csi' AND incident_key IS NOT NULL AND status NOT IN (?, ?)",
        (TASK_STATUS_COMPLETED, TASK_STATUS_PARKED),
    ).fetchone()

    return {
        "default_mode": "agent",
        "approvals_pending": int(approvals_pending or 0),
        "queue_health": {
            "dispatch_queue_size": len(queue.get("items") or []),
            "dispatch_eligible": int(queue.get("eligible_total") or 0),
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
            "open_incidents": int((csi_row["c"] if csi_row else 0) or 0),
        },
    }
