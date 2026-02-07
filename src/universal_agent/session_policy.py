from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from universal_agent.ops_config import apply_merge_patch


_MONEY_RE = re.compile(r"\b(pay|purchase|buy|checkout|wire|transfer|invoice|payment)\b", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b(email|gmail|send mail|send an email)\b", re.IGNORECASE)
_POST_RE = re.compile(r"\b(post|tweet|x\\.com|linkedin|facebook|publish)\b", re.IGNORECASE)
_DESTRUCTIVE_RE = re.compile(
    r"(rm\s+-rf|shutdown|reboot|format\s+disk|delete\s+all|wipe|sudo\s+rm)",
    re.IGNORECASE,
)


def _notification_email_default() -> str:
    return (
        os.getenv("UA_NOTIFICATION_EMAIL")
        or os.getenv("UA_PRIMARY_EMAIL")
        or "kevinjdragan@gmail.com"
    ).strip()


def default_session_policy(session_id: str, user_id: str) -> dict[str, Any]:
    now = time.time()
    primary_email = (os.getenv("UA_PRIMARY_EMAIL") or "").strip()
    email_whitelist = [email for email in [primary_email, _notification_email_default()] if email]
    return {
        "version": 1,
        "session_id": session_id,
        "user_id": user_id,
        "identity_mode": "persona",
        "autonomy_mode": "yolo",
        "tool_profile": "full",
        "approvals": {
            "enabled": True,
            "timeout_hours": 111,
            "reminder_count": 1,
            "approval_required_categories": [
                "outbound_email",
                "public_posting",
                "external_side_effect",
            ],
        },
        "limits": {
            "max_runtime_seconds": 0,
            "max_tool_calls": 0,
        },
        "hard_stops": {
            "no_payments": True,
            "outbound_email_whitelist_only": True,
            "block_public_posting": True,
            "block_destructive_local_ops": True,
        },
        "notifications": {
            "channels": ["dashboard", "email", "telegram"],
            "email_targets": [_notification_email_default()],
        },
        "email_whitelist": sorted(set(email_whitelist)),
        "created_at": now,
        "updated_at": now,
    }


def policy_path(workspace_dir: str) -> Path:
    return Path(workspace_dir) / "session_policy.json"


def load_session_policy(workspace_dir: str, *, session_id: str, user_id: str) -> dict[str, Any]:
    path = policy_path(workspace_dir)
    default = default_session_policy(session_id, user_id)
    if not path.exists():
        return default
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return default
        merged = apply_merge_patch(default, payload)
        merged["session_id"] = session_id
        merged["user_id"] = user_id
        return merged
    except Exception:
        return default


def save_session_policy(workspace_dir: str, policy: dict[str, Any]) -> Path:
    path = policy_path(workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(policy)
    payload["updated_at"] = time.time()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def update_session_policy(
    workspace_dir: str,
    patch: dict[str, Any],
    *,
    session_id: str,
    user_id: str,
) -> dict[str, Any]:
    current = load_session_policy(workspace_dir, session_id=session_id, user_id=user_id)
    updated = apply_merge_patch(current, patch)
    save_session_policy(workspace_dir, updated)
    return updated


def classify_request_categories(user_input: str, metadata: dict[str, Any] | None = None) -> list[str]:
    text = (user_input or "").strip()
    categories: set[str] = set()
    if not text:
        return []

    if _MONEY_RE.search(text):
        categories.add("money_movement")
    if _EMAIL_RE.search(text):
        categories.add("outbound_email")
        categories.add("external_side_effect")
    if _POST_RE.search(text):
        categories.add("public_posting")
        categories.add("external_side_effect")
    if _DESTRUCTIVE_RE.search(text):
        categories.add("destructive_local_ops")

    md = metadata or {}
    explicit_category = md.get("risk_category")
    if isinstance(explicit_category, str) and explicit_category.strip():
        categories.add(explicit_category.strip().lower())

    return sorted(categories)


def evaluate_request_against_policy(
    policy: dict[str, Any],
    *,
    user_input: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    categories = classify_request_categories(user_input, metadata)
    hard_stops = policy.get("hard_stops") or {}
    approvals = policy.get("approvals") or {}
    decision = "allow"
    reasons: list[str] = []

    if hard_stops.get("no_payments") and "money_movement" in categories:
        return {
            "decision": "deny",
            "categories": categories,
            "reasons": ["Payments or money movement are blocked by policy."],
        }

    if hard_stops.get("block_public_posting") and "public_posting" in categories:
        return {
            "decision": "deny",
            "categories": categories,
            "reasons": ["Public posting is blocked by policy."],
        }

    if hard_stops.get("block_destructive_local_ops") and "destructive_local_ops" in categories:
        return {
            "decision": "deny",
            "categories": categories,
            "reasons": ["Destructive local operations are blocked by policy."],
        }

    approval_required_categories = approvals.get("approval_required_categories") or []
    if approvals.get("enabled") and isinstance(approval_required_categories, list):
        required = {str(item).strip().lower() for item in approval_required_categories if str(item).strip()}
        if required.intersection(categories):
            decision = "require_approval"
            reasons.append("Policy requires approval for this request category.")

    if hard_stops.get("outbound_email_whitelist_only") and "outbound_email" in categories:
        whitelist = policy.get("email_whitelist") or []
        if not whitelist:
            return {
                "decision": "deny",
                "categories": categories,
                "reasons": ["Outbound email blocked because whitelist is empty."],
            }
        if decision == "allow":
            decision = "require_approval"
            reasons.append("Outbound email requires recipient whitelist confirmation.")

    return {
        "decision": decision,
        "categories": categories,
        "reasons": reasons,
    }
