"""Link payments — agent_purchaser orchestration.

Phase 4 entry point for browser-automated checkout. Handles:

  - Captcha-budget tracking (UA_LINK_DAILY_CAPTCHA_BUDGET, default 20/day).
    Tracked in a small JSONL file at AGENT_RUN_WORKSPACES/link_captcha_usage.jsonl
    so research flows that share NopeCHA quota can't be starved by a runaway
    purchase loop.
  - Per-spend-request idempotency: each spend_request_id can be attempted at
    most once. Subsequent calls return the cached attempt result.
  - Outcome envelope shape that callers (the API endpoint, the bridge hook)
    can switch on without depending on the sub-agent's exact prompt output.

The actual Playwright browser automation lives in the agent-purchaser
sub-agent (.claude/agents/agent-purchaser.md). This module is the
orchestration glue — it would, in a runtime where the Claude Agent SDK is
mounted, dispatch the sub-agent via the harness and wait for its result.
For Phase 4 we ship the orchestration scaffolding and a stubbed dispatch
that other code paths can mock; live invocation happens in production once
the sub-agent is loaded by the harness.

Outcome shapes returned by `attempt_checkout`:

  {"ok": True,  "status": "completed",        "spend_request_id": "...", ...}
  {"ok": False, "status": "fallback_3ds",     "spend_request_id": "...", ...}
  {"ok": False, "status": "fallback_captcha_budget", "spend_request_id": "...", ...}
  {"ok": False, "status": "fallback_ambiguous",       "spend_request_id": "...", ...}
  {"ok": False, "status": "fallback_unknown_form",    "spend_request_id": "...", ...}
  {"ok": False, "status": "fallback_error",           "spend_request_id": "...", ...}
  {"ok": False, "status": "disabled",                  "reason": "..."}
  {"ok": False, "status": "duplicate",                 "spend_request_id": "...", "first_attempt": ...}
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Callable, Optional

from universal_agent import feature_flags
from universal_agent.tools import link_bridge

logger = logging.getLogger(__name__)


# ── State paths ──────────────────────────────────────────────────────────────


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_captcha_usage_path() -> Path:
    override = os.getenv("UA_LINK_CAPTCHA_USAGE_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return _project_root() / "AGENT_RUN_WORKSPACES" / "link_captcha_usage.jsonl"


def resolve_attempts_path() -> Path:
    override = os.getenv("UA_LINK_PURCHASER_ATTEMPTS_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return _project_root() / "AGENT_RUN_WORKSPACES" / "link_purchaser_attempts.json"


# ── Captcha budget ───────────────────────────────────────────────────────────


def _today_window_seconds() -> int:
    return 24 * 60 * 60


def _read_captcha_usage_today() -> int:
    """Count captcha-solver invocations from purchases in the last 24h."""
    path = resolve_captcha_usage_path()
    if not path.exists():
        return 0
    cutoff = time.time() - _today_window_seconds()
    count = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = entry.get("ts")
                if isinstance(ts, (int, float)) and ts >= cutoff:
                    count += 1
    except OSError:
        return 0
    return count


def captcha_budget_snapshot() -> dict[str, Any]:
    cap = feature_flags.link_daily_captcha_budget()
    used = _read_captcha_usage_today()
    return {
        "cap": cap,
        "used": used,
        "remaining": max(0, cap - used),
        "window": "rolling_24h",
    }


def record_captcha_usage(spend_request_id: str, *, merchant_url: str | None = None) -> dict[str, Any]:
    """Mark one captcha-solver invocation against the daily purchase budget."""
    path = resolve_captcha_usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.time(),
        "spend_request_id": spend_request_id,
        "merchant_url": merchant_url,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return captcha_budget_snapshot()


def captcha_budget_available() -> bool:
    snap = captcha_budget_snapshot()
    return snap["remaining"] > 0


# ── Attempt idempotency ──────────────────────────────────────────────────────


def _load_attempts() -> dict[str, Any]:
    path = resolve_attempts_path()
    if not path.exists():
        return {"attempts": {}}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"attempts": {}}


def _save_attempts(payload: dict[str, Any]) -> None:
    path = resolve_attempts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=2))
    tmp.replace(path)


def get_attempt(spend_request_id: str) -> Optional[dict[str, Any]]:
    return (_load_attempts().get("attempts") or {}).get(spend_request_id)


def record_attempt(spend_request_id: str, outcome: dict[str, Any]) -> None:
    payload = _load_attempts()
    attempts = payload.setdefault("attempts", {})
    attempts[spend_request_id] = {
        "ts": time.time(),
        **{k: v for k, v in outcome.items() if k != "card"},
    }
    _save_attempts(payload)


# ── Dispatch hook (set by the harness in production) ─────────────────────────


# In production, the runtime imports this module and replaces _dispatcher
# with a function that actually invokes the agent-purchaser sub-agent via
# the Claude Agent SDK. In tests, callers monkeypatch this to inject a fake.
_dispatcher: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None


def set_dispatcher(fn: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    """Register the function that actually drives the sub-agent."""
    global _dispatcher
    _dispatcher = fn


def _default_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    """Default no-op dispatcher when the harness hasn't wired one up yet.

    Returns a structured fallback so callers always get a typed response —
    the API endpoint surfaces this clearly rather than crashing.
    """
    return {
        "ok": False,
        "status": "fallback_no_dispatcher",
        "message": (
            "agent-purchaser dispatcher not registered. The harness must call "
            "link_purchaser.set_dispatcher(...) at startup. Falling back to "
            "manual completion via the email + signed-URL flow."
        ),
    }


# ── Public entry point ───────────────────────────────────────────────────────


def attempt_checkout(spend_request_id: str) -> dict[str, Any]:
    """Drive an automated checkout for an approved spend request.

    - No-op when UA_ENABLE_LINK=0 or UA_LINK_AUTO_CHECKOUT=0.
    - Idempotent per spend_request_id (returns 'duplicate' on re-call).
    - Fetches card details fresh from the bridge (include_card=True), passes
      the fetched payload to the registered dispatcher, records the outcome.
    - Card details are NEVER persisted by this module. The attempts file
      stores only outcome status, evidence path, and a redacted last4.
    """
    if not feature_flags.link_enabled():
        return {
            "ok": False,
            "status": "disabled",
            "reason": "UA_ENABLE_LINK=0",
            "spend_request_id": spend_request_id,
        }

    if not feature_flags.link_auto_checkout_enabled():
        return {
            "ok": False,
            "status": "disabled",
            "reason": "UA_LINK_AUTO_CHECKOUT=0",
            "spend_request_id": spend_request_id,
        }

    existing = get_attempt(spend_request_id)
    if existing is not None:
        return {
            "ok": False,
            "status": "duplicate",
            "spend_request_id": spend_request_id,
            "first_attempt": existing,
        }

    retrieved = link_bridge.retrieve_spend_request(
        caller="ops", spend_request_id=spend_request_id, include_card=True
    )
    if not retrieved.get("ok"):
        outcome = {
            "ok": False,
            "status": "fallback_retrieve_failed",
            "spend_request_id": spend_request_id,
            "error": retrieved.get("error"),
        }
        record_attempt(spend_request_id, outcome)
        return outcome

    data = retrieved.get("data") or {}
    if data.get("status") != "approved":
        outcome = {
            "ok": False,
            "status": "fallback_not_approved",
            "spend_request_id": spend_request_id,
            "spend_request_status": data.get("status"),
        }
        record_attempt(spend_request_id, outcome)
        return outcome

    if (data.get("credential_type") or "card") != "card":
        outcome = {
            "ok": False,
            "status": "fallback_not_card",
            "spend_request_id": spend_request_id,
            "credential_type": data.get("credential_type"),
            "message": "Use mpp_pay for shared_payment_token credentials.",
        }
        record_attempt(spend_request_id, outcome)
        return outcome

    card = data.get("card") or {}
    if not card.get("number"):
        outcome = {
            "ok": False,
            "status": "fallback_no_card",
            "spend_request_id": spend_request_id,
            "message": "Spend request approved but card not minted yet.",
        }
        record_attempt(spend_request_id, outcome)
        return outcome

    payload = {
        "spend_request_id": spend_request_id,
        "merchant_name": data.get("merchant_name"),
        "merchant_url": data.get("merchant_url"),
        "amount_cents": data.get("amount") or data.get("amount_cents"),
        "currency": data.get("currency", "usd"),
        "card": card,
        "captcha_budget": captcha_budget_snapshot(),
    }

    dispatcher = _dispatcher or _default_dispatch
    try:
        result = dispatcher(payload)
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("Purchaser dispatcher raised for %s", spend_request_id)
        result = {
            "ok": False,
            "status": "fallback_error",
            "message": str(exc),
        }

    if not isinstance(result, dict):
        result = {"ok": False, "status": "fallback_error", "message": "non-dict result"}

    last4 = card.get("last4") or (card.get("number", "")[-4:] if card.get("number") else None)
    outcome = {
        "ok": bool(result.get("ok")),
        "status": result.get("status", "fallback_error"),
        "spend_request_id": spend_request_id,
        "evidence": result.get("evidence"),
        "merchant_url": data.get("merchant_url"),
        "card_last4": last4,
    }
    if "message" in result:
        outcome["message"] = result["message"]
    record_attempt(spend_request_id, outcome)
    return outcome
