from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_approvals_path() -> Path:
    env_path = os.getenv("UA_APPROVALS_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return _project_root() / "AGENT_RUN_WORKSPACES" / "approvals.json"


def _load_payload() -> dict[str, Any]:
    path = resolve_approvals_path()
    if not path.exists():
        return {"approvals": []}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"approvals": []}


def _write_payload(payload: dict[str, Any]) -> Path:
    path = resolve_approvals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return path


def list_approvals(status: Optional[str] = None) -> list[dict[str, Any]]:
    payload = _load_payload()
    approvals = payload.get("approvals", [])
    if status:
        return [item for item in approvals if item.get("status") == status]
    return approvals


def upsert_approval(data: dict[str, Any]) -> dict[str, Any]:
    payload = _load_payload()
    approvals = payload.get("approvals", [])
    approval_id = str(data.get("approval_id") or data.get("phase_id") or "")
    if not approval_id:
        approval_id = f"approval_{int(time.time())}"
    now = time.time()

    record = {
        **data,
        "approval_id": approval_id,
        "status": data.get("status", "pending"),
        "created_at": data.get("created_at", now),
        "updated_at": now,
    }

    updated = False
    for idx, item in enumerate(approvals):
        if item.get("approval_id") == approval_id:
            approvals[idx] = {**item, **record, "updated_at": now}
            updated = True
            record = approvals[idx]
            break
    if not updated:
        approvals.append(record)

    payload["approvals"] = approvals
    _write_payload(payload)
    return record


def update_approval(approval_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
    payload = _load_payload()
    approvals = payload.get("approvals", [])
    now = time.time()
    for idx, item in enumerate(approvals):
        if item.get("approval_id") == approval_id:
            approvals[idx] = {**item, **updates, "approval_id": approval_id, "updated_at": now}
            payload["approvals"] = approvals
            _write_payload(payload)
            return approvals[idx]
    return None
