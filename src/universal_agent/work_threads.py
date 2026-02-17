from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional


VALID_DECISIONS = {"promote", "iterate", "archive"}
_DECISION_STATUS_MAP = {
    "promote": "promoted",
    "iterate": "active",
    "archive": "archived",
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_work_threads_path() -> Path:
    env_path = os.getenv("UA_WORK_THREADS_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return _project_root() / "AGENT_RUN_WORKSPACES" / "work_threads.json"


def _load_payload() -> dict[str, Any]:
    path = resolve_work_threads_path()
    if not path.exists():
        return {"threads": []}
    try:
        payload = json.loads(path.read_text())
        if not isinstance(payload, dict):
            return {"threads": []}
        rows = payload.get("threads")
        if not isinstance(rows, list):
            return {"threads": []}
        return payload
    except Exception:
        return {"threads": []}


def _write_payload(payload: dict[str, Any]) -> Path:
    path = resolve_work_threads_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return path


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _safe_int(value: Any, default: int = 1) -> int:
    try:
        return max(1, int(value))
    except Exception:
        return default


def list_work_threads(
    status: Optional[str] = None,
    session_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    payload = _load_payload()
    threads = payload.get("threads", [])
    out: list[dict[str, Any]] = []
    for item in threads:
        if not isinstance(item, dict):
            continue
        if status and str(item.get("status") or "").lower() != status.lower():
            continue
        if session_id and str(item.get("session_id") or "") != session_id:
            continue
        out.append(item)
    out.sort(key=lambda row: float(row.get("updated_at") or 0), reverse=True)
    return out


def get_work_thread(thread_id: str) -> Optional[dict[str, Any]]:
    payload = _load_payload()
    threads = payload.get("threads", [])
    for item in threads:
        if isinstance(item, dict) and str(item.get("thread_id") or "") == thread_id:
            return item
    return None


def upsert_work_thread(data: dict[str, Any]) -> dict[str, Any]:
    payload = _load_payload()
    threads = payload.get("threads", [])
    now = time.time()

    requested_thread_id = str(data.get("thread_id") or "").strip()
    requested_session_id = str(data.get("session_id") or "").strip()

    idx_match: Optional[int] = None
    for idx, item in enumerate(threads):
        if not isinstance(item, dict):
            continue
        if requested_thread_id and str(item.get("thread_id") or "") == requested_thread_id:
            idx_match = idx
            break
        if requested_session_id and str(item.get("session_id") or "") == requested_session_id:
            idx_match = idx
            break

    existing = threads[idx_match] if idx_match is not None else {}

    session_id = requested_session_id or str(existing.get("session_id") or "")
    if not session_id:
        raise ValueError("session_id is required")

    thread_id = requested_thread_id or str(existing.get("thread_id") or "") or f"thread_{uuid.uuid4().hex[:10]}"

    record = {
        "thread_id": thread_id,
        "session_id": session_id,
        "title": data.get("title") or existing.get("title") or f"Work Thread for {session_id}",
        "target": data.get("target") or existing.get("target") or "existing_repo",
        "branch": data.get("branch") or existing.get("branch"),
        "workspace_dir": data.get("workspace_dir") or existing.get("workspace_dir"),
        "summary": data.get("summary") or existing.get("summary"),
        "status": data.get("status") or existing.get("status") or "active",
        "acceptance_criteria": _normalize_string_list(
            data.get("acceptance_criteria")
            if "acceptance_criteria" in data
            else existing.get("acceptance_criteria")
        ),
        "open_questions": _normalize_string_list(
            data.get("open_questions")
            if "open_questions" in data
            else existing.get("open_questions")
        ),
        "patch_version": _safe_int(data.get("patch_version", existing.get("patch_version", 1))),
        "test_status": data.get("test_status") or existing.get("test_status") or "unknown",
        "risk_notes": data.get("risk_notes") or existing.get("risk_notes"),
        "decision": data.get("decision") or existing.get("decision"),
        "decision_note": data.get("decision_note") or existing.get("decision_note"),
        "metadata": {
            **(existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}),
            **(data.get("metadata") if isinstance(data.get("metadata"), dict) else {}),
        },
        "history": existing.get("history") if isinstance(existing.get("history"), list) else [],
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
    }

    if idx_match is not None:
        threads[idx_match] = record
    else:
        threads.append(record)

    payload["threads"] = threads
    _write_payload(payload)
    return record


def update_work_thread(thread_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
    payload = _load_payload()
    threads = payload.get("threads", [])
    now = time.time()

    for idx, item in enumerate(threads):
        if not isinstance(item, dict):
            continue
        if str(item.get("thread_id") or "") != thread_id:
            continue

        merged_metadata = {
            **(item.get("metadata") if isinstance(item.get("metadata"), dict) else {}),
            **(updates.get("metadata") if isinstance(updates.get("metadata"), dict) else {}),
        }
        updated = {
            **item,
            **updates,
            "thread_id": thread_id,
            "metadata": merged_metadata,
            "updated_at": now,
        }

        if "acceptance_criteria" in updates:
            updated["acceptance_criteria"] = _normalize_string_list(updates.get("acceptance_criteria"))
        if "open_questions" in updates:
            updated["open_questions"] = _normalize_string_list(updates.get("open_questions"))
        if "patch_version" in updates:
            updated["patch_version"] = _safe_int(updates.get("patch_version"), default=_safe_int(item.get("patch_version", 1)))

        threads[idx] = updated
        payload["threads"] = threads
        _write_payload(payload)
        return updated

    return None


def append_work_thread_decision(
    *,
    session_id: str,
    decision: str,
    note: Optional[str] = None,
    decided_by: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    decision_key = (decision or "").strip().lower()
    if decision_key not in VALID_DECISIONS:
        raise ValueError(f"Unsupported decision: {decision}")

    thread = upsert_work_thread({"session_id": session_id})
    now = time.time()
    history = thread.get("history") if isinstance(thread.get("history"), list) else []

    history_entry = {
        "decision": decision_key,
        "note": (note or "").strip() or None,
        "decided_by": (decided_by or "").strip() or None,
        "decided_at": now,
        "metadata": metadata or {},
    }
    history.append(history_entry)

    patch_version = _safe_int(thread.get("patch_version", 1))
    if decision_key == "iterate":
        patch_version += 1

    updated = update_work_thread(
        thread["thread_id"],
        {
            "status": _DECISION_STATUS_MAP[decision_key],
            "decision": decision_key,
            "decision_note": history_entry["note"],
            "history": history,
            "patch_version": patch_version,
            "metadata": {
                **(thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}),
                **(metadata or {}),
            },
        },
    )
    if updated is None:
        raise RuntimeError("Failed to update work thread decision")
    return updated
