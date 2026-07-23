"""Session policy: defaults, normalization, persistence, and request gating.

This module owns the per-session policy document that governs autonomy,
approvals, hard stops, notification targets, memory scope, and codebase
access for a runtime session. It also classifies user requests into risk
categories and evaluates them against the active policy to produce an
allow / require_approval / deny decision.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
from typing import Any

from universal_agent import feature_flags
from universal_agent.codebase_policy import normalize_codebase_access
from universal_agent.ops_config import apply_merge_patch
from universal_agent.utils.env_utils import env_int as _env_int

_MONEY_RE = re.compile(r"\b(pay|purchase|buy|checkout|wire|transfer|invoice|payment)\b", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b(email|gmail|send mail|send an email)\b", re.IGNORECASE)
_POST_RE = re.compile(r"\b(post|tweet|x\.com|linkedin|facebook|publish)\b", re.IGNORECASE)
_DESTRUCTIVE_RE = re.compile(
    r"\b(rm\s+-rf|shutdown|reboot|format\s+disk|wipe|sudo\s+rm)\b",
    re.IGNORECASE,
)
_MEMORY_SCOPES = {"direct_only", "all"}


def _notification_email_default() -> str:
    return (
        os.getenv("UA_NOTIFICATION_EMAIL")
        or os.getenv("UA_PRIMARY_EMAIL")
        or "kevinjdragan@gmail.com"
    ).strip()


def _default_memory_scope() -> str:
    raw = (os.getenv("UA_MEMORY_SCOPE") or "direct_only").strip().lower()
    if raw in _MEMORY_SCOPES:
        return raw
    return "direct_only"


def default_memory_policy() -> dict[str, Any]:
    """Return the default memory policy for a session.

    Enables session memory and selects both the durable ``memory`` store
    and prior ``sessions`` as retrieval sources. The ``scope`` is read
    from the ``UA_MEMORY_SCOPE`` env var (``direct_only`` by default).
    """
    return {
        "enabled": True,
        "sessionMemory": True,
        "sources": ["memory", "sessions"],
        "scope": _default_memory_scope(),
    }


def normalize_memory_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce a (possibly partial or loosely typed) memory policy into canonical form.

    Missing fields fall back to :func:`default_memory_policy`. ``enabled``
    and ``sessionMemory`` are coerced to bool; ``sources`` accepts a
    comma-separated string or a list and is filtered to the allowed set
    ``{'memory', 'sessions'}`` (defaults if none survive); an invalid
    ``scope`` falls back to the default. A ``None`` policy yields the
    full defaults.
    """
    base = default_memory_policy()
    incoming = policy if isinstance(policy, dict) else {}
    enabled = incoming.get("enabled", base["enabled"])
    if not isinstance(enabled, bool):
        enabled = bool(enabled)
    session_memory = incoming.get("sessionMemory", incoming.get("session_memory_enabled", base["sessionMemory"]))
    if not isinstance(session_memory, bool):
        session_memory = bool(session_memory)
    raw_sources = incoming.get("sources", base["sources"])
    if isinstance(raw_sources, str):
        requested = [item.strip().lower() for item in raw_sources.split(",") if item.strip()]
    elif isinstance(raw_sources, list):
        requested = [str(item).strip().lower() for item in raw_sources if str(item).strip()]
    else:
        requested = list(base["sources"])
    allowed_sources = [item for item in requested if item in {"memory", "sessions"}]
    if not allowed_sources:
        allowed_sources = list(base["sources"])
    scope = str(incoming.get("scope", base["scope"])).strip().lower()
    if scope not in _MEMORY_SCOPES:
        scope = str(base["scope"])
    return {
        "enabled": enabled,
        "sessionMemory": session_memory,
        "sources": allowed_sources,
        "scope": scope,
    }


def normalize_session_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``policy`` with its sub-objects normalized.

    Normalizes the ``memory`` block via :func:`normalize_memory_policy`
    and the ``codebase_access`` block via
    :func:`universal_agent.codebase_policy.normalize_codebase_access`.
    All other keys are passed through unchanged.
    """
    payload = dict(policy)
    payload["memory"] = normalize_memory_policy(payload.get("memory"))
    payload["codebase_access"] = normalize_codebase_access(payload.get("codebase_access"))
    return payload

def default_session_policy(session_id: str, user_id: str) -> dict[str, Any]:
    """Build the default session policy for ``session_id`` / ``user_id``.

    Defaults reflect a persona-mode, full-tool, high-autonomy ('yolo')
    session: payments, public posting, and destructive local operations
    are hard-stopped, while outbound email is auto-allowed (no whitelist
    enforcement). Runtime limits (max runtime seconds, max tool calls)
    are read from ``UA_SESSION_*`` env vars with finite defaults. The
    notification email target and email whitelist are derived from
    ``UA_NOTIFICATION_EMAIL`` / ``UA_PRIMARY_EMAIL`` plus any
    ``trusted_recipients`` found in ``identity_registry.json``.
    """
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
        "codebase_access": normalize_codebase_access({"enabled": False}),
        "created_at": now,
        "updated_at": now,
    }


def policy_path(workspace_dir: str) -> Path:
    """Return the path to ``session_policy.json`` within ``workspace_dir``."""
    return Path(workspace_dir) / "session_policy.json"


def load_session_policy(workspace_dir: str, *, session_id: str, user_id: str) -> dict[str, Any]:
    """Load the session policy for ``workspace_dir``, merged over the defaults.

    If no policy file exists (or it fails to parse), the default policy
    for ``session_id`` / ``user_id`` is returned. Otherwise the stored
    JSON is applied as an RFC 7396 JSON merge patch over the defaults
    (via :func:`universal_agent.ops_config.apply_merge_patch`), the
    runtime ``session_id`` / ``user_id`` are pinned onto the result, and
    the merged policy is normalized before returning.
    """
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
    """Normalize ``policy`` and write it to ``session_policy.json``.

    Stamps ``updated_at`` to the current time and creates the parent
    directory if needed. Returns the path written.
    """
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
    """Apply ``patch`` to the current policy and persist the result.

    Loads the current policy for ``session_id`` / ``user_id``, applies
    ``patch`` as an RFC 7396 JSON merge patch (a ``null`` value deletes
    a key), normalizes, saves, and returns the normalized policy.
    """
    current = load_session_policy(workspace_dir, session_id=session_id, user_id=user_id)
    updated = apply_merge_patch(current, patch)
    normalized = normalize_session_policy(updated)
    save_session_policy(workspace_dir, normalized)
    return normalized


def classify_request_categories(user_input: str, metadata: dict[str, Any] | None = None) -> list[str]:
    """Classify ``user_input`` into risk categories via keyword matching.

    Scans the (case-insensitive) text for money-movement, outbound-email,
    public-posting, and destructive-local-operation keywords, adding the
    matching categories plus the derived ``external_side_effect`` flag.
    An explicit ``metadata['risk_category']`` string, if present, is
    added verbatim (lower-cased). Returns a sorted, de-duplicated list
    (empty for blank input).
    """
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
    """Evaluate a request against ``policy`` and return a gating decision.

    Classifies the request, then checks the ``hard_stops`` and
    ``approvals`` blocks of the policy. Possible decisions:

    * ``deny`` -- a hard stop matched: money movement when no payment
      channel (Link) is enabled, public posting, destructive local
      operations, or outbound email under whitelist-only mode with an
      empty whitelist.
    * ``require_approval`` -- the request category is listed in
      ``approvals.approval_required_categories``, or outbound email
      under whitelist-only mode with a non-empty whitelist.
    * ``allow`` -- otherwise.

    Returns a dict with ``decision``, the matched ``categories``, and
    human-readable ``reasons``.
    """
    categories = classify_request_categories(user_input, metadata)
    hard_stops = policy.get("hard_stops") or {}
    approvals = policy.get("approvals") or {}
    decision = "allow"
    reasons: list[str] = []

    if hard_stops.get("no_payments") and "money_movement" in categories:
        # When Stripe Link is enabled, payment requests are handled by Link's
        # own multi-layer safety stack: bridge guardrails (caller allowlist,
        # per-call cap, daily budget cap, merchant allowlist) plus user
        # approval in the Link mobile app.  The gateway hard-deny only fires
        # when Link is OFF — i.e. no authorized payment channel exists.
        if not feature_flags.link_enabled():
            return {
                "decision": "deny",
                "categories": categories,
                "reasons": ["Payments or money movement are blocked by policy (no payment channel enabled)."],
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
