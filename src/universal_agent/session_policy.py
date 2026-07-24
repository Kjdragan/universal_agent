"""Session policy construction, persistence, and request gating.

Builds the default per-session policy (autonomy limits, approval
requirements, hard stops, notifications, memory scope), normalizes
partially-specified policy payloads into a valid shape, loads/persists
that policy to ``session_policy.json`` in a workspace, classifies an
inbound user request into risk categories, and decides whether the
gateway should allow, deny, or require operator approval for it.
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
    """Return the default memory policy used when none is supplied.

    Memory is enabled with session-scoped recall over the ``memory`` and
    ``sessions`` sources; the scope (``direct_only`` by default) is read
    from ``UA_MEMORY_SCOPE``.
    """
    return {
        "enabled": True,
        "sessionMemory": True,
        "sources": ["memory", "sessions"],
        "scope": _default_memory_scope(),
    }


def normalize_memory_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce a memory policy payload into a complete, valid shape.

    Missing fields fall back to :func:`default_memory_policy`. ``sources``
    may be a comma-separated string or a list and is filtered to the
    allowed set (``memory``, ``sessions``); an empty result reverts to the
    default sources. ``scope`` must be one of the allowed scopes
    (``direct_only``, ``all``) or it defaults. Non-bool ``enabled`` /
    ``sessionMemory`` values are coerced to bool.
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
    """Normalize the nested sub-policies of a session policy payload.

    Returns a shallow copy of ``policy`` with its ``memory`` and
    ``codebase_access`` entries replaced by their normalized forms (see
    :func:`normalize_memory_policy` and
    :func:`codebase_policy.normalize_codebase_access`). Other keys are
    passed through unchanged.
    """
    payload = dict(policy)
    payload["memory"] = normalize_memory_policy(payload.get("memory"))
    payload["codebase_access"] = normalize_codebase_access(payload.get("codebase_access"))
    return payload

def default_session_policy(session_id: str, user_id: str) -> dict[str, Any]:
    """Build a complete default session policy for the given session/user.

    Persona identity, yolo autonomy, full tool profile, with finite
    guardrail limits (runtime seconds, tool-call counts) read from the
    environment, hard stops (no payments unless Link is enabled, block
    public posting / destructive local ops), dashboard+email+telegram
    notifications, and an email whitelist assembled from
    ``UA_PRIMARY_EMAIL``, ``UA_NOTIFICATION_EMAIL``, and the
    ``trusted_recipients`` in ``identity_registry.json`` (if present).
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
    """Load the session policy for a workspace, falling back to the default.

    Reads ``session_policy.json`` under ``workspace_dir`` and merge-patches
    it onto :func:`default_session_policy`; ``session_id`` / ``user_id``
    are always forced to the supplied values regardless of file contents.
    A missing or unparseable file yields the (normalized) default policy.
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
    """Normalize and persist a session policy to ``session_policy.json``.

    The policy is normalized via :func:`normalize_session_policy`, stamped
    with an ``updated_at`` timestamp, and written as sorted, indented JSON;
    parent directories are created as needed. Returns the path written.
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
    """Apply a merge-patch to a workspace's session policy and persist it.

    Loads the current policy (via :func:`load_session_policy`), applies
    ``patch`` as a recursive merge-patch, normalizes and saves the result
    (via :func:`save_session_policy`), and returns the updated, normalized
    policy.
    """
    current = load_session_policy(workspace_dir, session_id=session_id, user_id=user_id)
    updated = apply_merge_patch(current, patch)
    normalized = normalize_session_policy(updated)
    save_session_policy(workspace_dir, normalized)
    return normalized


def classify_request_categories(user_input: str, metadata: dict[str, Any] | None = None) -> list[str]:
    """Classify a user request into policy risk categories.

    Scans ``user_input`` for money-movement, outbound-email,
    public-posting, and destructive-local-op keywords (email and posting
    also tag the umbrella ``external_side_effect`` category). An explicit
    ``risk_category`` in ``metadata`` is added verbatim (lower-cased).
    Returns the de-duplicated, sorted list of category names; empty for
    blank input.
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
    """Decide whether a request is allowed, denied, or needs approval.

    Classifies the request (:func:`classify_request_categories`) then
    applies the policy's hard stops and approval rules:

    * ``deny`` for money movement when payments are hard-stopped and Link
      is not enabled, for public posting, or for destructive local ops;
    * ``require_approval`` when the request hits an
      ``approval_required_categories`` entry (if approvals are enabled),
      or for outbound email under a whitelist-only policy (denied outright
      when the whitelist is empty);
    * ``allow`` otherwise.

    The result dict carries ``decision``, the matched ``categories``, and
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
