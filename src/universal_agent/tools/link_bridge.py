"""Link payments bridge — Phase 1 (plumbing only).

Provides a Python surface for creating/retrieving Link spend requests, listing
payment methods, and settling MPP payments. In Phase 1 the bridge runs in
stub mode (no real CLI invocation) so callers can be wired up and guardrails
exercised without any risk of contacting Stripe.

Phase 2 swaps the stubs for real Link CLI subprocess / MCP calls. The public
function signatures and return shapes are stable across the swap.

Public surface:
    create_spend_request(...)
    retrieve_spend_request(...)
    list_payment_methods(...)
    mpp_pay(...)

All entry points return a dict shaped like:
    {"ok": bool, "data": {...} | None, "error": {"code": str, "message": str} | None,
     "audit_id": str, "mode": "test"|"live"|"stub"}

Guardrails (in order, fail-closed):
    1. master_switch          — UA_ENABLE_LINK=1
    2. caller_allowlist       — caller in {"chat","ui","skill:link-purchase","test"}
    3. per_call_cap           — amount <= UA_LINK_MAX_AMOUNT_CENTS
    4. daily_cap              — sum(today's create_attempt amounts) + amount <= UA_LINK_DAILY_BUDGET_CENTS
    5. merchant_allowlist     — if UA_LINK_MERCHANT_ALLOWLIST set, hostname must match
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from universal_agent import feature_flags

ALLOWED_CALLERS: tuple[str, ...] = (
    "chat",
    "ui",
    "skill:link-purchase",
    "test",
)

CREDENTIAL_TYPE_CARD = "card"
CREDENTIAL_TYPE_SPT = "shared_payment_token"

_DAILY_WINDOW_SECONDS = 24 * 60 * 60


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_audit_path() -> Path:
    """Where the bridge appends its JSONL audit log."""
    override = os.getenv("UA_LINK_AUDIT_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return _project_root() / "AGENT_RUN_WORKSPACES" / "link_audit.jsonl"


def _now_ts() -> float:
    return time.time()


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _new_audit_id() -> str:
    return f"audit_{uuid.uuid4().hex[:16]}"


def _bridge_mode() -> str:
    """Returns 'stub', 'test', or 'live' for the audit log + caller responses."""
    if not feature_flags.link_enabled():
        return "stub"
    if feature_flags.link_live_mode_active():
        return "live"
    return "test"


def _append_audit(record: dict[str, Any]) -> str:
    """Append a single JSONL audit entry. Returns the audit_id."""
    audit_id = record.get("audit_id") or _new_audit_id()
    record = {
        "audit_id": audit_id,
        "ts": record.get("ts") or _now_ts(),
        "ts_iso": record.get("ts_iso") or _now_iso(),
        "mode": record.get("mode") or _bridge_mode(),
        **record,
    }
    path = resolve_audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n")
    return audit_id


def _iter_recent_audit_entries(window_seconds: int = _DAILY_WINDOW_SECONDS) -> Iterable[dict[str, Any]]:
    """Yield audit entries from the last `window_seconds`."""
    path = resolve_audit_path()
    if not path.exists():
        return
    cutoff = _now_ts() - window_seconds
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
                yield entry


def _spent_today_cents() -> int:
    """Sum of amount_cents on today's non-blocked create_attempts.

    Counts attempts that were not pre-blocked by guardrails. We err on the
    side of including denied/expired attempts because the guardrail's job is
    to limit how aggressively the agent *tries*, not just how much succeeds.
    """
    total = 0
    for entry in _iter_recent_audit_entries():
        if entry.get("event") != "create_attempt":
            continue
        if entry.get("guardrail_blocked"):
            continue
        amount = entry.get("amount_cents")
        if isinstance(amount, int):
            total += amount
    return total


# ── Guardrails ───────────────────────────────────────────────────────────────


def _err(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _check_master_switch() -> Optional[dict[str, str]]:
    if not feature_flags.link_enabled():
        return _err(
            "guardrail_disabled",
            "Link payments disabled (UA_ENABLE_LINK is not set). "
            "Bridge is in stub mode — no real Link calls will be made.",
        )
    return None


def _check_caller(caller: str) -> Optional[dict[str, str]]:
    if not isinstance(caller, str) or not caller.strip():
        return _err("guardrail_caller", "Missing caller identifier.")
    if caller not in ALLOWED_CALLERS:
        return _err(
            "guardrail_caller",
            f"Caller {caller!r} not in allowlist {ALLOWED_CALLERS!r}.",
        )
    return None


def _check_per_call_cap(amount_cents: int) -> Optional[dict[str, str]]:
    cap = feature_flags.link_max_amount_cents()
    if amount_cents > cap:
        return _err(
            "guardrail_per_call_cap",
            f"Amount {amount_cents}¢ exceeds per-call cap {cap}¢.",
        )
    return None


def _check_daily_cap(amount_cents: int) -> Optional[dict[str, str]]:
    cap = feature_flags.link_daily_budget_cents()
    spent = _spent_today_cents()
    if spent + amount_cents > cap:
        return _err(
            "guardrail_daily_cap",
            f"Daily cap {cap}¢ would be exceeded "
            f"(already attempted {spent}¢ in last 24h, this would add {amount_cents}¢).",
        )
    return None


def _hostname(url: str) -> str:
    try:
        parsed = urlparse(url)
        return (parsed.hostname or "").lower()
    except Exception:
        return ""


def _check_merchant_allowlist(merchant_url: str) -> Optional[dict[str, str]]:
    allowlist = feature_flags.link_merchant_allowlist()
    if not allowlist:
        return None
    host = _hostname(merchant_url)
    if not host:
        return _err(
            "guardrail_merchant_allowlist",
            f"Could not parse hostname from merchant_url={merchant_url!r}.",
        )
    normalized = {item.lower().lstrip(".") for item in allowlist}
    for entry in normalized:
        if host == entry or host.endswith("." + entry):
            return None
    return _err(
        "guardrail_merchant_allowlist",
        f"Merchant host {host!r} not in allowlist {sorted(normalized)!r}.",
    )


def _validate_context(context: str) -> Optional[dict[str, str]]:
    if not isinstance(context, str) or len(context.strip()) < 100:
        return _err(
            "validation_context",
            "context must be at least 100 characters (Link API constraint).",
        )
    return None


def _validate_amount(amount_cents: int) -> Optional[dict[str, str]]:
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        return _err("validation_amount", "amount_cents must be a positive integer.")
    if amount_cents > 50000:
        return _err(
            "validation_amount",
            "amount_cents must not exceed 50000 (Link API constraint).",
        )
    return None


def _validate_currency(currency: str) -> Optional[dict[str, str]]:
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha():
        return _err(
            "validation_currency",
            "currency must be a 3-letter ISO code (Link API constraint).",
        )
    return None


def _run_create_guardrails(
    *,
    caller: str,
    amount_cents: int,
    merchant_url: str,
    context: str,
    currency: str,
) -> Optional[dict[str, str]]:
    """Run all create-time checks in canonical order. Returns first failure or None."""
    for check in (
        _check_master_switch(),
        _check_caller(caller),
        _validate_amount(amount_cents),
        _validate_currency(currency),
        _validate_context(context),
        _check_per_call_cap(amount_cents),
        _check_merchant_allowlist(merchant_url),
        _check_daily_cap(amount_cents),
    ):
        if check is not None:
            return check
    return None


# ── Response helpers ─────────────────────────────────────────────────────────


def _ok_response(data: Any, audit_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "data": data,
        "error": None,
        "audit_id": audit_id,
        "mode": _bridge_mode(),
    }


def _err_response(error: dict[str, str], audit_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "data": None,
        "error": error,
        "audit_id": audit_id,
        "mode": _bridge_mode(),
    }


# ── Stub-mode payloads (Phase 1) ─────────────────────────────────────────────


def _stub_spend_request(
    *,
    payment_method_id: str,
    merchant_name: str,
    merchant_url: str,
    amount_cents: int,
    currency: str,
    credential_type: str,
    request_approval: bool,
) -> dict[str, Any]:
    spend_request_id = f"lsrq_stub_{uuid.uuid4().hex[:12]}"
    return {
        "id": spend_request_id,
        "status": "pending_approval" if request_approval else "created",
        "amount": amount_cents,
        "currency": currency,
        "merchant_name": merchant_name,
        "merchant_url": merchant_url,
        "payment_method_id": payment_method_id,
        "credential_type": credential_type,
        "approval_url": f"https://app.link.com/approve/{spend_request_id}",
        "_stub": True,
    }


def _stub_payment_methods() -> list[dict[str, Any]]:
    return [
        {
            "id": "csmrpd_stub_visa",
            "type": "card",
            "brand": "visa",
            "last4": "4242",
            "default": True,
            "_stub": True,
        }
    ]


# ── Public API ───────────────────────────────────────────────────────────────


def create_spend_request(
    *,
    caller: str,
    payment_method_id: str,
    merchant_name: str,
    merchant_url: str,
    context: str,
    amount_cents: int,
    line_items: Optional[list[dict[str, Any]]] = None,
    total: Optional[dict[str, Any]] = None,
    credential_type: str = CREDENTIAL_TYPE_CARD,
    currency: str = "usd",
    request_approval: bool = True,
    network_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create a Link spend request.

    Phase 1: stub-mode only. Phase 2 will invoke the real Link CLI.
    """
    audit_id = _new_audit_id()
    guardrail = _run_create_guardrails(
        caller=caller,
        amount_cents=amount_cents,
        merchant_url=merchant_url,
        context=context,
        currency=currency,
    )
    if guardrail is not None:
        _append_audit(
            {
                "audit_id": audit_id,
                "event": "create_attempt",
                "caller": caller,
                "amount_cents": amount_cents,
                "merchant_url": merchant_url,
                "credential_type": credential_type,
                "guardrail_blocked": guardrail["code"],
                "error": guardrail,
            }
        )
        return _err_response(guardrail, audit_id)

    # Stub-mode response (Phase 1).
    spend_request = _stub_spend_request(
        payment_method_id=payment_method_id,
        merchant_name=merchant_name,
        merchant_url=merchant_url,
        amount_cents=amount_cents,
        currency=currency,
        credential_type=credential_type,
        request_approval=request_approval,
    )

    _append_audit(
        {
            "audit_id": audit_id,
            "event": "create_attempt",
            "caller": caller,
            "amount_cents": amount_cents,
            "merchant_url": merchant_url,
            "credential_type": credential_type,
            "spend_request_id": spend_request["id"],
            "guardrail_blocked": None,
            "stub": True,
        }
    )

    return _ok_response(spend_request, audit_id)


def retrieve_spend_request(
    *,
    caller: str,
    spend_request_id: str,
    include_card: bool = False,
) -> dict[str, Any]:
    """Retrieve a spend request by id. Stub returns a synthetic 'approved' record."""
    audit_id = _new_audit_id()
    for check in (_check_master_switch(), _check_caller(caller)):
        if check is not None:
            _append_audit(
                {
                    "audit_id": audit_id,
                    "event": "retrieve_attempt",
                    "caller": caller,
                    "spend_request_id": spend_request_id,
                    "guardrail_blocked": check["code"],
                    "error": check,
                }
            )
            return _err_response(check, audit_id)

    data: dict[str, Any] = {
        "id": spend_request_id,
        "status": "approved",
        "_stub": True,
    }
    if include_card:
        data["card"] = {
            "last4": "4242",
            "brand": "visa",
            "exp_month": 12,
            "exp_year": 2030,
            "valid_until": int(_now_ts()) + 3600,
            "_stub": True,
            # PAN/CVC intentionally omitted in stub. Phase 2 will receive the real masked card.
        }

    _append_audit(
        {
            "audit_id": audit_id,
            "event": "retrieve_attempt",
            "caller": caller,
            "spend_request_id": spend_request_id,
            "include_card": bool(include_card),
            "guardrail_blocked": None,
            "stub": True,
        }
    )
    return _ok_response(data, audit_id)


def list_payment_methods(*, caller: str) -> dict[str, Any]:
    audit_id = _new_audit_id()
    for check in (_check_master_switch(), _check_caller(caller)):
        if check is not None:
            _append_audit(
                {
                    "audit_id": audit_id,
                    "event": "payment_methods_list",
                    "caller": caller,
                    "guardrail_blocked": check["code"],
                    "error": check,
                }
            )
            return _err_response(check, audit_id)

    _append_audit(
        {
            "audit_id": audit_id,
            "event": "payment_methods_list",
            "caller": caller,
            "guardrail_blocked": None,
            "stub": True,
        }
    )
    return _ok_response({"payment_methods": _stub_payment_methods()}, audit_id)


def mpp_pay(
    *,
    caller: str,
    spend_request_id: str,
    url: str,
    method: str = "POST",
    data: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Settle an MPP/HTTP-402 payment using an approved SPT spend request.

    Phase 1 stub returns a synthetic success response. Phase 2 invokes
    `link-cli mpp pay` for real.
    """
    audit_id = _new_audit_id()
    for check in (_check_master_switch(), _check_caller(caller)):
        if check is not None:
            _append_audit(
                {
                    "audit_id": audit_id,
                    "event": "mpp_pay_attempt",
                    "caller": caller,
                    "spend_request_id": spend_request_id,
                    "url": url,
                    "guardrail_blocked": check["code"],
                    "error": check,
                }
            )
            return _err_response(check, audit_id)

    _append_audit(
        {
            "audit_id": audit_id,
            "event": "mpp_pay_attempt",
            "caller": caller,
            "spend_request_id": spend_request_id,
            "url": url,
            "method": method,
            "guardrail_blocked": None,
            "stub": True,
        }
    )
    return _ok_response(
        {
            "spend_request_id": spend_request_id,
            "url": url,
            "method": method,
            "response": {"status": 200, "body": {"_stub": True, "ok": True}},
        },
        audit_id,
    )


# ── Diagnostics / introspection ──────────────────────────────────────────────


def bridge_status() -> dict[str, Any]:
    """Snapshot of bridge configuration for ops/diagnostics."""
    return {
        "enabled": feature_flags.link_enabled(),
        "live_mode": feature_flags.link_live_mode_active(),
        "test_mode": feature_flags.link_test_mode(),
        "mode": _bridge_mode(),
        "entry_chat": feature_flags.link_entry_chat_enabled(),
        "entry_ui": feature_flags.link_entry_ui_enabled(),
        "entry_skill": feature_flags.link_entry_skill_enabled(),
        "auto_checkout": feature_flags.link_auto_checkout_enabled(),
        "max_amount_cents": feature_flags.link_max_amount_cents(),
        "daily_budget_cents": feature_flags.link_daily_budget_cents(),
        "spent_today_cents": _spent_today_cents(),
        "merchant_allowlist": feature_flags.link_merchant_allowlist(),
        "audit_path": str(resolve_audit_path()),
    }
