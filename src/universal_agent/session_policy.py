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
_MEMORY_MODES = {"off", "session_only", "selective", "full"}

def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        return default


def _notification_email_default() -> str:
    return (
        os.getenv("UA_NOTIFICATION_EMAIL")
        or os.getenv("UA_PRIMARY_EMAIL")
        or "kevinjdragan@gmail.com"
    ).strip()


def _default_memory_mode() -> str:
    raw = (os.getenv("UA_SESSION_MEMORY_DEFAULT_MODE") or "session_only").strip().lower()
    if raw in _MEMORY_MODES:
        return raw
    return "session_only"


def _normalize_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, list):
        raw_items = [str(item).strip() for item in value if str(item).strip()]
    else:
        raw_items = []
    deduped: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def default_memory_policy() -> dict[str, Any]:
    mode = _default_memory_mode()
    allowlist = ["retain"] if mode == "selective" else []
    return {
        "mode": mode,
        "session_memory_enabled": True,
        "tags": [],
        "long_term_tag_allowlist": allowlist,
    }


def normalize_memory_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    base = default_memory_policy()
    incoming = policy if isinstance(policy, dict) else {}
    mode = str(incoming.get("mode", base["mode"])).strip().lower()
    if mode not in _MEMORY_MODES:
        mode = str(base["mode"])
    session_memory_enabled = incoming.get("session_memory_enabled", base["session_memory_enabled"])
    if not isinstance(session_memory_enabled, bool):
        session_memory_enabled = bool(session_memory_enabled)
    tags = _normalize_tags(incoming.get("tags", base["tags"]))
    allowlist = _normalize_tags(incoming.get("long_term_tag_allowlist", base["long_term_tag_allowlist"]))
    if mode == "selective" and not allowlist:
        allowlist = ["retain"]
    if mode in {"off", "session_only", "full"}:
        allowlist = []
    return {
        "mode": mode,
        "session_memory_enabled": session_memory_enabled,
        "tags": tags,
        "long_term_tag_allowlist": allowlist,
    }


def normalize_session_policy(policy: dict[str, Any]) -> dict[str, Any]:
    payload = dict(policy)
    payload["memory"] = normalize_memory_policy(payload.get("memory"))
    return payload

def default_session_policy(session_id: str, user_id: str) -> dict[str, Any]:
    now = time.time()
    primary_email = (os.getenv("UA_PRIMARY_EMAIL") or "").strip()
    # Load whitelist from identity_registry.json if it exists
    registry_path = Path("identity_registry.json")
    registry_emails = []
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry_emails = registry.get("trusted_recipients") or []
        except Exception:
            pass

    email_whitelist = sorted(set([
        email for email in [
            primary_email, 
            _notification_email_default(),
        ] + registry_emails if email
    ]))
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
                "public_posting",
                # "outbound_email",      # Auto-allowed by user request 2026-02-07
                # "external_side_effect", # Auto-allowed by user request 2026-02-07
            ],
        },
        "limits": {
            # 0 means unlimited. Defaults are intentionally finite to prevent runaway loops.
            # These are guardrails, not guarantees of completion; degraded/partial output is preferred
            # over unbounded tool spend for most sessions.
            "max_runtime_seconds": _env_int("UA_SESSION_MAX_RUNTIME_SECONDS", 5400),  # 90 min
            "max_tool_calls": _env_int("UA_SESSION_MAX_TOOL_CALLS", 500),
            # Optional per-tool limits (enforced by local toolkit where supported).
            "max_tool_calls_by_tool": {
                "generate_image": _env_int("UA_SESSION_MAX_GENERATE_IMAGE_CALLS", 20),
            },
        },
        "hard_stops": {
            "no_payments": True,
            "outbound_email_whitelist_only": False, # Changed to False to prevent forced approval
            "block_public_posting": True,
            "block_destructive_local_ops": True,
        },
        "notifications": {
            "channels": ["dashboard", "email", "telegram"],
            "email_targets": [_notification_email_default()],
        },
        "email_whitelist": sorted(set(email_whitelist)),
        "memory": default_memory_policy(),
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
        return normalize_session_policy(merged)
    except Exception:
        return normalize_session_policy(default)


def save_session_policy(workspace_dir: str, policy: dict[str, Any]) -> Path:
    path = policy_path(workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = normalize_session_policy(policy)
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
    normalized = normalize_session_policy(updated)
    save_session_policy(workspace_dir, normalized)
    return normalized


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
