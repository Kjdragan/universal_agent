"""FastAPI router for Link payments — Phase 2b.

Endpoints:
  GET  /api/link/health                          — bridge_status + last_probe
  GET  /api/link/spend-requests                  — list from audit log
  POST /api/link/spend-requests                  — create new (UI form)
  GET  /api/link/spend-requests/{id}             — retrieve current state
  POST /api/link/spend-requests/{id}/refresh     — re-poll status
  GET  /link/card/{token}                        — UNAUTHENTICATED signed-URL page

The `/link/card/{token}` route is intentionally unauthenticated — the token IS
the credential, accessed via a one-shot email link. All other endpoints are
expected to sit behind the existing dashboard auth middleware in api/server.py.

Card data is NEVER stored on disk by this module. The card endpoint consumes
the one-shot token, calls link-cli retrieve --include=card live, renders an
HTML page in-memory, and returns. After response delivery, only the masked
last4 + valid_until remain in the audit log.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from universal_agent import feature_flags
from universal_agent.services import link_card_tokens
from universal_agent.services.link_health import last_probe, run_link_health_probe
from universal_agent.tools import link_bridge

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Models ───────────────────────────────────────────────────────────────────


class LineItem(BaseModel):
    name: str
    unit_amount: int = Field(ge=0)
    quantity: int = Field(ge=1, default=1)


class CreateSpendRequestBody(BaseModel):
    payment_method_id: Optional[str] = None  # falls back to UA_LINK_DEFAULT_PAYMENT_METHOD_ID
    merchant_name: str
    merchant_url: str
    context: str = Field(min_length=100)
    amount_cents: int = Field(ge=1, le=50000)
    currency: str = "usd"
    credential_type: str = "card"
    line_items: Optional[list[LineItem]] = None
    request_approval: bool = True
    network_id: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _resolve_default_pm() -> str:
    return os.getenv("UA_LINK_DEFAULT_PAYMENT_METHOD_ID", "").strip()


def _read_audit_recent(limit: int = 50) -> list[dict[str, Any]]:
    """Read the last `limit` create_attempt rows from the audit log."""
    path = link_bridge.resolve_audit_path()
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("event") != "create_attempt":
            continue
        rows.append(entry)
        if len(rows) >= limit:
            break
    return rows


# ── /api/link/health ─────────────────────────────────────────────────────────


@router.get("/api/link/health")
async def link_health() -> dict[str, Any]:
    """Bridge status + last health-probe snapshot. Safe to expose to ops."""
    return {
        "bridge_status": link_bridge.bridge_status(),
        "last_probe": last_probe(),
    }


@router.post("/api/link/health/probe")
async def link_health_probe_now() -> dict[str, Any]:
    """Trigger a fresh health probe. Returns the new last-probe snapshot."""
    return run_link_health_probe()


@router.post("/api/link/reconcile")
async def link_reconcile_now(
    lookback_hours: int = 48, max_per_tick: int = 10
) -> dict[str, Any]:
    """Run one reconciler pass over non-terminal spend requests.

    Designed to be called from cron / a periodic supervisor. Bounded per call
    by `max_per_tick` to avoid hammering the CLI with a backlog.
    """
    from universal_agent.services.link_reconciler import reconcile_once

    return reconcile_once(
        lookback_hours=max(1, min(lookback_hours, 168)),
        max_per_tick=max(1, min(max_per_tick, 50)),
    )


# ── Browser-automated checkout (Phase 4) ─────────────────────────────────────


@router.get("/api/link/checkout/captcha-budget")
async def link_checkout_captcha_budget() -> dict[str, Any]:
    """Snapshot of today's captcha-solver usage from purchase flows.

    Consumed by the agent-purchaser sub-agent before invoking captcha-solver,
    so research flows that share NopeCHA quota aren't starved.
    """
    from universal_agent.services.link_purchaser import captcha_budget_snapshot

    return captcha_budget_snapshot()


@router.post("/api/link/spend-requests/{spend_request_id}/checkout")
async def link_attempt_checkout(spend_request_id: str) -> JSONResponse:
    """Trigger automated checkout for an approved spend request.

    Idempotent per spend_request_id (one attempt only — the card is single-use
    and a failed-but-charged card cannot be retried). Returns the structured
    outcome envelope from link_purchaser.attempt_checkout.
    """
    from universal_agent.services.link_purchaser import attempt_checkout

    outcome = attempt_checkout(spend_request_id)
    status_code = 200 if outcome.get("ok") else 202  # accepted, fallback registered
    return JSONResponse(status_code=status_code, content=outcome)


# ── /api/link/spend-requests ─────────────────────────────────────────────────


@router.get("/api/link/spend-requests")
async def list_spend_requests(limit: int = 50) -> dict[str, Any]:
    """List recent create_attempt rows from the audit log (best-effort).

    For per-spend-request live status, callers should follow up with a GET
    on /api/link/spend-requests/{id} which calls link-cli retrieve.
    """
    rows = _read_audit_recent(limit=max(1, min(limit, 200)))
    return {"items": rows, "count": len(rows)}


@router.post("/api/link/spend-requests")
async def create_spend_request(body: CreateSpendRequestBody) -> JSONResponse:
    """Create a new spend request from the Mission Control form."""
    if not feature_flags.link_entry_ui_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Link UI entry-point disabled (UA_LINK_ENTRY_UI=0).",
        )

    pm = body.payment_method_id or _resolve_default_pm()
    if not pm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No payment_method_id provided and UA_LINK_DEFAULT_PAYMENT_METHOD_ID is unset.",
        )

    line_items = (
        [li.model_dump() for li in body.line_items] if body.line_items else None
    )

    result = link_bridge.create_spend_request(
        caller="ui",
        payment_method_id=pm,
        merchant_name=body.merchant_name,
        merchant_url=body.merchant_url,
        context=body.context,
        amount_cents=body.amount_cents,
        line_items=line_items,
        credential_type=body.credential_type,
        currency=body.currency,
        request_approval=body.request_approval,
        network_id=body.network_id,
    )

    if not result["ok"]:
        return JSONResponse(
            status_code=400,
            content={"detail": result["error"], "audit_id": result["audit_id"]},
        )
    return JSONResponse(status_code=201, content=result)


@router.get("/api/link/spend-requests/{spend_request_id}")
async def get_spend_request(spend_request_id: str) -> JSONResponse:
    result = link_bridge.retrieve_spend_request(
        caller="ui", spend_request_id=spend_request_id, include_card=False
    )
    if not result["ok"]:
        return JSONResponse(status_code=400, content={"detail": result["error"]})
    return JSONResponse(content=result)


@router.post("/api/link/spend-requests/{spend_request_id}/refresh")
async def refresh_spend_request(spend_request_id: str) -> JSONResponse:
    """Force a re-poll. Notifier hook fires if the request newly transitioned to approved."""
    result = link_bridge.retrieve_spend_request(
        caller="ui", spend_request_id=spend_request_id, include_card=False
    )
    if not result["ok"]:
        return JSONResponse(status_code=400, content={"detail": result["error"]})
    return JSONResponse(content=result)


# ── /link/card/{token} (UNAUTHENTICATED — token is the credential) ──────────


_CARD_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Link card details</title>
<meta name="robots" content="noindex,nofollow">
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 540px;
         margin: 32px auto; padding: 0 16px; color: #1a1a1a; }}
  .card {{ border: 1px solid #e1e1e8; border-radius: 12px; padding: 20px;
          background: #fafafc; margin: 16px 0; }}
  .label {{ font-size: 12px; color: #6c6c7a; text-transform: uppercase;
           letter-spacing: 0.04em; margin-bottom: 4px; }}
  .value {{ font-family: ui-monospace, "SF Mono", monospace; font-size: 18px;
           font-weight: 600; user-select: all; }}
  .row {{ display: flex; gap: 16px; margin-top: 16px; }}
  .row > div {{ flex: 1; }}
  .meta {{ font-size: 13px; color: #555; margin: 16px 0; }}
  .warn {{ background: #fff7e6; border: 1px solid #ffd591; padding: 12px;
          border-radius: 8px; font-size: 13px; margin: 16px 0; }}
  .merchant-link {{ display: inline-block; margin-top: 12px; padding: 10px 16px;
                   background: #5469d4; color: #fff; border-radius: 6px;
                   text-decoration: none; font-weight: 600; }}
  .err {{ color: #b00020; }}
  button {{ padding: 6px 10px; font-size: 12px; cursor: pointer;
           border: 1px solid #5469d4; background: #fff; color: #5469d4;
           border-radius: 4px; margin-left: 8px; }}
</style>
</head>
<body>
<h1>💳 Link card</h1>
<div class="warn">
  <strong>One-shot view.</strong> This page can only be opened once. The
  credentials shown are network-tokenized and locked to this merchant — they
  cannot be charged elsewhere.
</div>

<div class="card">
  <div class="label">Card number</div>
  <div class="value" id="pan">{pan}</div>
  <button onclick="navigator.clipboard.writeText(document.getElementById('pan').textContent.replace(/\\s/g,''))">Copy</button>

  <div class="row">
    <div>
      <div class="label">Expiration</div>
      <div class="value" id="exp">{exp}</div>
      <button onclick="navigator.clipboard.writeText(document.getElementById('exp').textContent)">Copy</button>
    </div>
    <div>
      <div class="label">CVC</div>
      <div class="value" id="cvc">{cvc}</div>
      <button onclick="navigator.clipboard.writeText(document.getElementById('cvc').textContent)">Copy</button>
    </div>
  </div>
</div>

<div class="meta">
  <div><strong>Merchant:</strong> {merchant_name}</div>
  <div><strong>Amount:</strong> {amount}</div>
  <div><strong>Valid until:</strong> {valid_until}</div>
</div>

{billing_block}

<a class="merchant-link" href="{merchant_url}" target="_blank" rel="noopener">Continue to merchant →</a>
</body>
</html>
"""

_CARD_ERROR_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Link card link</title></head>
<body style="font-family:system-ui;max-width:480px;margin:32px auto;padding:0 16px;color:#1a1a1a;">
<h1>❌ {title}</h1>
<p>{message}</p>
<p style="font-size:13px;color:#666;">If you need a new card, return to the assistant and request a fresh spend request.</p>
</body></html>
"""


def _render_billing_block(billing: dict[str, Any] | None) -> str:
    if not billing or not isinstance(billing, dict):
        return ""
    parts = []
    name = billing.get("name") or ""
    line1 = billing.get("line1") or ""
    line2 = billing.get("line2") or ""
    city = billing.get("city") or ""
    state = billing.get("state") or ""
    postal = billing.get("postal_code") or ""
    country = billing.get("country") or ""
    if name:
        parts.append(name)
    if line1:
        parts.append(line1)
    if line2:
        parts.append(line2)
    csz = " ".join(p for p in (city, state, postal) if p).strip()
    if csz:
        parts.append(csz)
    if country:
        parts.append(country)
    if not parts:
        return ""
    addr = "<br>".join(parts)
    return f'<div class="card"><div class="label">Billing address</div><div style="margin-top:6px;line-height:1.5;">{addr}</div></div>'


def _format_pan(number: str) -> str:
    digits = "".join(c for c in (number or "") if c.isdigit())
    return " ".join(digits[i : i + 4] for i in range(0, len(digits), 4))


def _format_valid_until(card: dict[str, Any]) -> str:
    vu = card.get("valid_until")
    if not vu:
        return ""
    try:
        from datetime import datetime, timezone

        # Link returns a unix timestamp per the official skill docs.
        if isinstance(vu, (int, float)):
            return datetime.fromtimestamp(vu, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return str(vu)
    except Exception:
        return str(vu)


@router.get("/link/card/{token}", response_class=HTMLResponse)
async def view_card(token: str, request: Request) -> HTMLResponse:
    consumed = link_card_tokens.consume(token)
    if not consumed["ok"]:
        title_map = {
            "not_found": "Link not found",
            "expired": "Link expired",
            "already_consumed": "Already viewed",
        }
        title = title_map.get(consumed.get("code"), "Unavailable")
        return HTMLResponse(
            _CARD_ERROR_TEMPLATE.format(title=title, message=consumed.get("message", "")),
            status_code=410,  # Gone
        )

    spend_request_id = consumed["spend_request_id"]
    retrieved = link_bridge.retrieve_spend_request(
        caller="ui", spend_request_id=spend_request_id, include_card=True
    )
    if not retrieved["ok"]:
        return HTMLResponse(
            _CARD_ERROR_TEMPLATE.format(
                title="Could not load card",
                message=retrieved.get("error", {}).get("message", "Try refreshing the request from Mission Control."),
            ),
            status_code=502,
        )

    data = retrieved["data"] or {}
    card = data.get("card") or {}
    if not card:
        return HTMLResponse(
            _CARD_ERROR_TEMPLATE.format(
                title="Card not yet available",
                message="The spend request is approved but no card has been minted. Retry in a moment.",
            ),
            status_code=409,
        )

    pan = _format_pan(card.get("number") or "")
    exp_month = str(card.get("exp_month") or "").zfill(2)
    exp_year = str(card.get("exp_year") or "")
    exp = f"{exp_month}/{exp_year[-2:]}" if exp_month and exp_year else ""
    cvc = str(card.get("cvc") or "")

    amount_cents = data.get("amount") or 0
    currency = (data.get("currency") or "usd").upper()
    try:
        money = f"${int(amount_cents)/100:,.2f} {currency}"
    except Exception:
        money = f"{amount_cents} {currency}"

    html = _CARD_PAGE_TEMPLATE.format(
        pan=pan or "(not available)",
        exp=exp or "(not available)",
        cvc=cvc or "(not available)",
        merchant_name=data.get("merchant_name") or "(merchant)",
        merchant_url=data.get("merchant_url") or "#",
        amount=money,
        valid_until=_format_valid_until(card),
        billing_block=_render_billing_block(card.get("billing_address")),
    )

    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, private",
        "Pragma": "no-cache",
        "X-Robots-Tag": "noindex, nofollow",
        "Referrer-Policy": "no-referrer",
    }
    return HTMLResponse(html, headers=headers)
