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

import base64
import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from universal_agent import feature_flags

logger = logging.getLogger(__name__)

ALLOWED_CALLERS: tuple[str, ...] = (
    "chat",
    "ui",
    "skill:link-purchase",
    "ops",  # health probe, auth status checks, ops tooling
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
    """Returns 'stub', 'test', or 'live' for the audit log + caller responses.

    `UA_LINK_FORCE_STUB=1` is an ops/test escape hatch: when set, the bridge
    behaves as if disabled (returns stub responses) even with the master
    switch on. Used by tests to assert guardrail/audit logic without invoking
    real subprocess calls, and by operators who want to neuter the bridge
    without losing the rest of the runtime config.
    """
    if not feature_flags.link_enabled():
        return "stub"
    if _is_truthy(os.getenv("UA_LINK_FORCE_STUB")):
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

    mode = _bridge_mode()

    if mode == "stub":
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

    # Real CLI invocation (test or live mode).
    cli_args = [
        "spend-request",
        "create",
        "--payment-method-id",
        payment_method_id,
        "--merchant-name",
        merchant_name,
        "--merchant-url",
        merchant_url,
        "--context",
        context,
        "--amount",
        str(amount_cents),
        "--currency",
        currency,
        "--credential-type",
        credential_type,
    ]
    if line_items:
        for item in line_items:
            cli_args.extend(["--line-item", _format_line_item(item)])
    if total:
        cli_args.extend(["--total", _format_line_item(total)])
    if network_id:
        cli_args.extend(["--network-id", network_id])
    if request_approval:
        cli_args.append("--request-approval")

    cli_result = _run_link_cli(cli_args, test_flag=(mode == "test"))
    spend_request_id = None
    if cli_result["ok"] and isinstance(cli_result["data"], dict):
        spend_request_id = cli_result["data"].get("id")

    _append_audit(
        {
            "audit_id": audit_id,
            "event": "create_attempt",
            "caller": caller,
            "amount_cents": amount_cents,
            "merchant_url": merchant_url,
            "credential_type": credential_type,
            "spend_request_id": spend_request_id,
            "guardrail_blocked": None,
            "cli_exit_code": cli_result.get("exit_code"),
            "error": cli_result.get("error"),
        }
    )

    if not cli_result["ok"]:
        return _err_response(cli_result["error"], audit_id)
    return _ok_response(cli_result["data"], audit_id)


def _format_line_item(item: dict[str, Any]) -> str:
    """Encode a line-item dict as the CLI's `key:value,key:value` form."""
    return ",".join(f"{k}:{v}" for k, v in item.items())


def retrieve_spend_request(
    *,
    caller: str,
    spend_request_id: str,
    include_card: bool = False,
) -> dict[str, Any]:
    """Retrieve a spend request by id."""
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

    mode = _bridge_mode()

    if mode == "stub":
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

    cli_args = ["spend-request", "retrieve", spend_request_id]
    if include_card:
        cli_args.extend(["--include", "card"])

    cli_result = _run_link_cli(cli_args)

    _append_audit(
        {
            "audit_id": audit_id,
            "event": "retrieve_attempt",
            "caller": caller,
            "spend_request_id": spend_request_id,
            "include_card": bool(include_card),
            "guardrail_blocked": None,
            "cli_exit_code": cli_result.get("exit_code"),
            "error": cli_result.get("error"),
        }
    )

    if not cli_result["ok"]:
        return _err_response(cli_result["error"], audit_id)
    response = _ok_response(cli_result["data"], audit_id)

    # Fire the notifier hook on newly-approved spend requests. Idempotent
    # per spend_request_id (notifier maintains its own state file).
    try:
        from universal_agent.services import link_notifier

        link_notifier.maybe_notify_from_retrieve(response)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Link notifier hook failed: %s", exc)

    return response


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

    mode = _bridge_mode()

    if mode == "stub":
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

    cli_result = _run_link_cli(["payment-methods", "list"])
    _append_audit(
        {
            "audit_id": audit_id,
            "event": "payment_methods_list",
            "caller": caller,
            "guardrail_blocked": None,
            "cli_exit_code": cli_result.get("exit_code"),
            "error": cli_result.get("error"),
        }
    )
    if not cli_result["ok"]:
        return _err_response(cli_result["error"], audit_id)

    raw = cli_result["data"]
    if isinstance(raw, list):
        payload = {"payment_methods": raw}
    elif isinstance(raw, dict) and "payment_methods" in raw:
        payload = raw
    else:
        payload = {"payment_methods": [], "raw": raw}
    return _ok_response(payload, audit_id)


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

    mode = _bridge_mode()

    if mode == "stub":
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

    cli_args = [
        "mpp",
        "pay",
        url,
        "--spend-request-id",
        spend_request_id,
        "--method",
        method,
    ]
    if data is not None:
        cli_args.extend(["--data", json.dumps(data)])
    if headers:
        for name, value in headers.items():
            cli_args.extend(["--header", f"{name}: {value}"])

    cli_result = _run_link_cli(cli_args, timeout=60)

    _append_audit(
        {
            "audit_id": audit_id,
            "event": "mpp_pay_attempt",
            "caller": caller,
            "spend_request_id": spend_request_id,
            "url": url,
            "method": method,
            "guardrail_blocked": None,
            "cli_exit_code": cli_result.get("exit_code"),
            "error": cli_result.get("error"),
        }
    )

    if not cli_result["ok"]:
        return _err_response(cli_result["error"], audit_id)
    return _ok_response(cli_result["data"], audit_id)


def mpp_decode(
    *,
    caller: str,
    challenge: str,
) -> dict[str, Any]:
    """Decode a raw `WWW-Authenticate` header value into a Stripe MPP challenge.

    Used by callers that received an HTTP 402 from a merchant: the response
    header contains one or more payment challenges; `mpp decode` returns the
    `network_id` needed to mint a shared_payment_token spend request.

    Phase 3: real CLI in non-stub modes. Stub returns a synthetic decoded payload.
    """
    audit_id = _new_audit_id()
    for check in (_check_master_switch(), _check_caller(caller)):
        if check is not None:
            _append_audit(
                {
                    "audit_id": audit_id,
                    "event": "mpp_decode_attempt",
                    "caller": caller,
                    "guardrail_blocked": check["code"],
                    "error": check,
                }
            )
            return _err_response(check, audit_id)

    if not isinstance(challenge, str) or not challenge.strip():
        err = _err("validation_challenge", "challenge string required.")
        _append_audit(
            {
                "audit_id": audit_id,
                "event": "mpp_decode_attempt",
                "caller": caller,
                "guardrail_blocked": "validation_challenge",
                "error": err,
            }
        )
        return _err_response(err, audit_id)

    mode = _bridge_mode()
    if mode == "stub":
        _append_audit(
            {
                "audit_id": audit_id,
                "event": "mpp_decode_attempt",
                "caller": caller,
                "guardrail_blocked": None,
                "stub": True,
            }
        )
        return _ok_response(
            {
                "network_id": "stub_network_001",
                "method": "stripe",
                "request": {"_stub": True},
            },
            audit_id,
        )

    cli_result = _run_link_cli(["mpp", "decode", "--challenge", challenge])
    _append_audit(
        {
            "audit_id": audit_id,
            "event": "mpp_decode_attempt",
            "caller": caller,
            "guardrail_blocked": None,
            "cli_exit_code": cli_result.get("exit_code"),
            "error": cli_result.get("error"),
        }
    )
    if not cli_result["ok"]:
        return _err_response(cli_result["error"], audit_id)
    return _ok_response(cli_result["data"], audit_id)


def auth_status(*, caller: str = "ops") -> dict[str, Any]:
    """Return Link CLI auth status. Stub-mode returns a synthetic 'unauthenticated'."""
    audit_id = _new_audit_id()
    for check in (_check_master_switch(), _check_caller(caller)):
        if check is not None:
            return _err_response(check, audit_id)

    if _bridge_mode() == "stub":
        return _ok_response({"authenticated": False, "_stub": True}, audit_id)

    cli_result = _run_link_cli(["auth", "status"], timeout=15)
    if not cli_result["ok"]:
        return _err_response(cli_result["error"], audit_id)
    return _ok_response(cli_result["data"], audit_id)


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
        "cli_path": _resolve_cli_path(),
        "auth_seed_status": _last_auth_seed_status(),
    }


# ── Real CLI invocation (Phase 2a) ───────────────────────────────────────────
#
# When the master switch is on (`UA_ENABLE_LINK=1`), the bridge invokes the
# real Stripe Link CLI instead of returning stub payloads. We shell out via
# `subprocess.run` with `--format json` so output is parseable, set short
# timeouts (the bridge intentionally does NOT poll inside subprocess.run —
# polling and approval-flow handling come in Phase 2c).


_CLI_DEFAULT_TIMEOUT_SECONDS = 30
_CLI_NPX_FALLBACK = ("npx", "-y", "@stripe/link-cli")
_AUTH_SEED_STATUS: dict[str, Any] = {"applied": False, "path": None, "reason": None}


def _resolve_cli_path() -> str | None:
    """Find a usable link-cli command. Prefer global install; fall back to npx."""
    override = os.getenv("UA_LINK_CLI_PATH")
    if override:
        return override
    found = shutil.which("link-cli")
    if found:
        return found
    if shutil.which("npx"):
        return "npx"
    return None


def _link_cli_argv(args: list[str]) -> list[str]:
    """Build the argv to invoke link-cli, respecting CLI overrides."""
    override = os.getenv("UA_LINK_CLI_PATH")
    if override:
        return [override, *args]
    if shutil.which("link-cli"):
        return ["link-cli", *args]
    return [*_CLI_NPX_FALLBACK, *args]


def _last_auth_seed_status() -> dict[str, Any]:
    return dict(_AUTH_SEED_STATUS)


def _ensure_auth_seeded(*, force: bool = False) -> dict[str, Any]:
    """Restore the Link CLI auth blob from Infisical-injected env vars.

    Idempotent: only writes the file once per process unless `force=True`.
    Returns the status dict (also stored on _AUTH_SEED_STATUS for diagnostics).
    """
    if _AUTH_SEED_STATUS.get("applied") and not force:
        return _last_auth_seed_status()

    if not _is_truthy(os.getenv("UA_LINK_AUTH_SEED_ENABLED"), default=True):
        _AUTH_SEED_STATUS.update({"applied": False, "path": None, "reason": "seed_disabled"})
        return _last_auth_seed_status()

    blob = os.getenv("LINK_AUTH_BLOB")
    if not blob:
        _AUTH_SEED_STATUS.update({"applied": False, "path": None, "reason": "no_blob"})
        return _last_auth_seed_status()

    raw_path = os.getenv("UA_LINK_AUTH_BLOB_PATH")
    if not raw_path:
        _AUTH_SEED_STATUS.update(
            {"applied": False, "path": None, "reason": "no_path"}
        )
        return _last_auth_seed_status()

    try:
        target = Path(os.path.expandvars(os.path.expanduser(raw_path)))
        target.parent.mkdir(parents=True, exist_ok=True)
        decoded = base64.b64decode(blob, validate=False)
        target.write_bytes(decoded)
        try:
            target.chmod(0o600)
        except OSError:
            pass
        _AUTH_SEED_STATUS.update(
            {"applied": True, "path": str(target), "reason": None}
        )
        logger.info("Link auth blob restored to %s", target)
    except Exception as exc:  # pragma: no cover — defensive
        _AUTH_SEED_STATUS.update(
            {"applied": False, "path": raw_path, "reason": f"write_failed: {exc!r}"}
        )
        logger.warning("Failed to restore Link auth blob: %s", exc)

    return _last_auth_seed_status()


def _is_truthy(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _run_link_cli(
    args: list[str],
    *,
    timeout: int | float | None = None,
    test_flag: bool = False,
) -> dict[str, Any]:
    """Invoke the Link CLI synchronously, parse JSON stdout, normalize errors.

    Always passes `--format json`. If `test_flag=True`, appends `--test` for
    commands that support it.

    Returns:
      {"ok": True,  "data": <parsed json>, "raw_stdout": "...", "exit_code": 0}
      {"ok": False, "error": {"code": str, "message": str}, "raw_stdout": "...",
       "raw_stderr": "...", "exit_code": int}
    """
    _ensure_auth_seeded()

    argv = _link_cli_argv(args + (["--test"] if test_flag else []) + ["--format", "json"])
    env = os.environ.copy()
    env.setdefault("NO_UPDATE_NOTIFIER", "1")

    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout if timeout is not None else _CLI_DEFAULT_TIMEOUT_SECONDS,
            env=env,
            check=False,
        )
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "error": {
                "code": "cli_not_found",
                "message": f"link-cli binary not found: {exc}",
            },
            "raw_stdout": "",
            "raw_stderr": str(exc),
            "exit_code": 127,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": {
                "code": "cli_timeout",
                "message": f"link-cli timed out after {exc.timeout}s",
            },
            "raw_stdout": exc.stdout or "",
            "raw_stderr": exc.stderr or "",
            "exit_code": -1,
        }

    raw_stdout = completed.stdout or ""
    raw_stderr = completed.stderr or ""

    parsed: Any = None
    if raw_stdout.strip():
        try:
            parsed = json.loads(raw_stdout.strip().splitlines()[-1])
        except json.JSONDecodeError:
            parsed = None

    if completed.returncode == 0:
        return {
            "ok": True,
            "data": parsed if parsed is not None else {"raw": raw_stdout.strip()},
            "raw_stdout": raw_stdout,
            "exit_code": 0,
        }

    if isinstance(parsed, dict) and parsed.get("code"):
        err = {
            "code": str(parsed.get("code")),
            "message": str(parsed.get("message") or "Link CLI error."),
        }
    else:
        err = {
            "code": "cli_error",
            "message": (raw_stderr or raw_stdout or "Link CLI exited non-zero.").strip(),
        }

    return {
        "ok": False,
        "error": err,
        "raw_stdout": raw_stdout,
        "raw_stderr": raw_stderr,
        "exit_code": completed.returncode,
    }


# ── MCP server config builder ────────────────────────────────────────────────


def build_link_mcp_server_config() -> dict[str, Any] | None:
    """Return a Claude Agent SDK MCP server config for link-cli, or None.

    Mirrors the existing pattern (`build_notebooklm_mcp_server_config`,
    `build_agentmail_mcp_server_config`). When the master switch is off, we
    return None so the MCP server is never spawned.
    """
    if not feature_flags.link_enabled():
        return None
    cli = _resolve_cli_path()
    if cli is None:
        logger.warning(
            "UA_ENABLE_LINK=1 but no link-cli/npx found; skipping MCP registration."
        )
        return None
    _ensure_auth_seeded()

    # Always invoke via npx to get a known-good version regardless of global
    # install state. Operators can override via UA_LINK_CLI_PATH if they want
    # to pin to a globally-installed binary.
    override = os.getenv("UA_LINK_CLI_PATH")
    if override:
        argv = [override, "--mcp"]
    elif shutil.which("link-cli"):
        argv = ["link-cli", "--mcp"]
    else:
        argv = ["npx", "-y", "@stripe/link-cli", "--mcp"]

    return {
        "type": "stdio",
        "command": argv[0],
        "args": argv[1:],
        "env": {"NO_UPDATE_NOTIFIER": "1"},
    }
