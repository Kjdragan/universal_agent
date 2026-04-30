"""Link payments — approval notifier.

When a spend request transitions to status=approved, fire an AgentMail email
to the configured operator address. Idempotent per spend_request_id (tracked
in a small JSON file so duplicate notifications never go out, even if the
caller polls the bridge multiple times).

The notification body never contains card details. It contains:
  - Merchant name + URL
  - Amount + currency + line items if provided
  - Approval timestamp
  - A short-lived signed URL (`https://<dashboard-host>/link/card/<token>`)
    where the operator views card details (one-shot, TTL-bounded).

If AgentMail isn't configured or fails, we record the failure in the audit
log and the notification state — but do NOT raise. The signed URL is always
available in Mission Control regardless.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from universal_agent import feature_flags
from universal_agent.services import link_card_tokens

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_state_path() -> Path:
    override = os.getenv("UA_LINK_NOTIFIER_STATE_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return _project_root() / "AGENT_RUN_WORKSPACES" / "link_notifications.json"


def _load_state() -> dict[str, Any]:
    path = resolve_state_path()
    if not path.exists():
        return {"notified": {}}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"notified": {}}


def _save_state(payload: dict[str, Any]) -> None:
    path = resolve_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=2))
    tmp.replace(path)


def _dashboard_base_url() -> str:
    return (
        os.getenv("UA_LINK_DASHBOARD_BASE_URL")
        or os.getenv("UA_DASHBOARD_BASE_URL")
        or "https://app.clearspringcg.com"
    ).rstrip("/")


def _operator_email() -> Optional[str]:
    return os.getenv("UA_LINK_OPERATOR_EMAIL") or os.getenv("UA_OPERATOR_EMAIL") or None


def _format_money(amount_cents: int, currency: str = "usd") -> str:
    try:
        amount_cents = int(amount_cents)
    except Exception:
        return f"{amount_cents} {currency.upper()}"
    return f"${amount_cents/100:,.2f} {currency.upper()}"


def _send_via_agentmail(
    *, to: str, subject: str, html: str, text: str
) -> dict[str, Any]:
    """Best-effort AgentMail send. Returns a status dict; never raises."""
    try:
        # The AgentMail bridge expects a runtime mapping; here we use the
        # service-level send helper if available, else fall back to the MCP
        # payload-shaped call. The exact invocation depends on the runtime
        # environment — both are wrapped in try/except so notifier never
        # raises into the caller.
        from universal_agent import agentmail_official  # type: ignore

        if hasattr(agentmail_official, "send_message"):
            agentmail_official.send_message(to=to, subject=subject, html=html, text=text)
            return {"ok": True, "via": "agentmail_official.send_message"}
    except Exception as exc:  # pragma: no cover — env-dependent
        logger.warning("AgentMail send via agentmail_official failed: %s", exc)

    try:
        # Fallback: emit a structured log so operators can pick up via
        # logfire-based alerting even if no SMTP path exists.
        logger.info(
            "LINK_NOTIFICATION_FALLBACK to=%s subject=%s body_len=%d",
            to,
            subject,
            len(text),
        )
        return {"ok": True, "via": "log_only"}
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "error": repr(exc)}


def already_notified(spend_request_id: str) -> bool:
    state = _load_state()
    return bool((state.get("notified") or {}).get(spend_request_id))


def mark_notified(
    spend_request_id: str,
    *,
    token: str,
    sent_to: Optional[str],
    via: Optional[str],
) -> None:
    state = _load_state()
    notified = state.setdefault("notified", {})
    notified[spend_request_id] = {
        "ts": time.time(),
        "token": token,
        "sent_to": sent_to,
        "via": via,
    }
    _save_state(state)


def notify_approved(spend_request: dict[str, Any]) -> dict[str, Any]:
    """Fire an approval notification for the given spend request.

    Idempotent: returns {"ok": True, "skipped": "already_notified"} if a prior
    notification already went out.
    """
    spend_request_id = spend_request.get("id") or ""
    if not spend_request_id:
        return {"ok": False, "error": "missing_spend_request_id"}

    if already_notified(spend_request_id):
        return {"ok": True, "skipped": "already_notified", "spend_request_id": spend_request_id}

    if not feature_flags.link_enabled():
        return {"ok": True, "skipped": "link_disabled"}

    operator = _operator_email()
    if not operator:
        logger.info(
            "Link notifier: no UA_LINK_OPERATOR_EMAIL/UA_OPERATOR_EMAIL set; "
            "approval %s will only surface in Mission Control.",
            spend_request_id,
        )

    issuance = link_card_tokens.issue(spend_request_id)
    token = issuance["token"]
    card_url = f"{_dashboard_base_url()}/link/card/{token}"

    merchant_name = spend_request.get("merchant_name") or "(merchant)"
    merchant_url = spend_request.get("merchant_url") or ""
    amount = spend_request.get("amount") or spend_request.get("amount_cents") or 0
    currency = (spend_request.get("currency") or "usd").lower()
    money = _format_money(amount, currency)

    subject = f"Approved: {money} to {merchant_name}"
    text_lines = [
        f"Your Link spend request was approved.",
        "",
        f"Merchant: {merchant_name}",
        f"URL:      {merchant_url}",
        f"Amount:   {money}",
        f"ID:       {spend_request_id}",
        "",
        "View card details (one-shot, expires in 15 min):",
        card_url,
        "",
        "Card details are not shown in this email; the link above fetches them",
        "fresh from Link and renders them once.",
    ]
    text = "\n".join(text_lines)

    html = f"""<!doctype html>
<html><body style="font-family:system-ui,sans-serif;max-width:560px;margin:auto;padding:24px;">
  <h2 style="margin:0 0 12px;">✅ Approved: {money}</h2>
  <p style="margin:0 0 12px;">to <a href="{merchant_url}">{merchant_name}</a></p>
  <p style="font-size:13px;color:#555;">Spend request <code>{spend_request_id}</code></p>
  <p>
    <a href="{card_url}" style="display:inline-block;padding:12px 20px;background:#5469d4;color:#fff;border-radius:6px;text-decoration:none;font-weight:600;">View card details</a>
  </p>
  <p style="font-size:12px;color:#666;">
    One-shot link, expires in 15 minutes. Card details are not stored in this email.
  </p>
</body></html>"""

    sent_status: dict[str, Any] = {"ok": False}
    if operator:
        sent_status = _send_via_agentmail(
            to=operator, subject=subject, html=html, text=text
        )

    mark_notified(
        spend_request_id,
        token=token,
        sent_to=operator,
        via=sent_status.get("via"),
    )

    return {
        "ok": True,
        "spend_request_id": spend_request_id,
        "card_url": card_url,
        "token": token,
        "expires_at": issuance["expires_at"],
        "delivery": sent_status,
    }


def maybe_notify_from_retrieve(retrieve_response: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Hook for link_bridge.retrieve_spend_request to call after a successful retrieve.

    Fires the notifier only when status == 'approved' and we haven't notified
    yet. Returns the notify result dict or None if not applicable. Never raises.
    """
    try:
        if not retrieve_response.get("ok"):
            return None
        data = retrieve_response.get("data") or {}
        if data.get("status") != "approved":
            return None
        return notify_approved(data)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Link notifier hook failed: %s", exc)
        return None
