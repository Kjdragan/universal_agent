"""
Telegram notifier for TGTG stock alerts.

Sends a rich message with store info, price, pickup window, and stock count.
Includes an inline keyboard with a deep link to the TGTG app.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from .config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TGTG_WEBHOOK_URL

log = logging.getLogger(__name__)

_BASE = "https://api.telegram.org/bot{token}/{method}"


def _format_pickup(item: dict) -> str:
    try:
        window = item["item"]["pickup_interval"]
        start = datetime.fromisoformat(window["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(window["end"].replace("Z", "+00:00"))
        # Show in local time
        fmt = "%H:%M"
        return f"{start.astimezone().strftime(fmt)} – {end.astimezone().strftime(fmt)}"
    except (KeyError, TypeError, ValueError):
        return "unknown"


def _format_price(item: dict) -> str:
    try:
        p = item["item"]["price_including_taxes"]
        return f"{p['minor_units'] / 10 ** p['decimals']:.2f} {p['code']}"
    except (KeyError, TypeError):
        return "?"


def _tgtg_deep_link(item_id: str) -> str:
    """Deep link opens the TGTG app directly to the store."""
    return f"https://share.toogoodtogo.com/item/{item_id}"


def send_webhook(event_type: str, payload: dict) -> bool:
    """
    Fire an HTTP POST to TGTG_WEBHOOK_URL with a JSON body.

    Body shape:
      { "event": "<event_type>", "timestamp": "<iso>", ...payload }

    Returns True if the server responded with 2xx.
    """
    if not TGTG_WEBHOOK_URL:
        return False

    body = {
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    try:
        resp = httpx.post(TGTG_WEBHOOK_URL, json=body, timeout=10)
        resp.raise_for_status()
        log.debug("Webhook %s → %s", event_type, resp.status_code)
        return True
    except Exception as exc:
        log.warning("Webhook delivery failed (%s): %s", event_type, exc)
        return False


def send_payment_reminder(order_id: str, store: str, pickup_end: datetime) -> bool:
    """
    Send a Telegram reminder to pay for a reserved bag before pickup closes.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    closes_at = pickup_end.astimezone().strftime("%H:%M")
    text = (
        f"⏰ *Pay now!* Bag reserved at *{store}*\n\n"
        f"Pickup closes at *{closes_at}* — don't forget to pay in the app!\n"
        f"🧾 Order: `{order_id}`"
    )
    try:
        resp = httpx.post(
            _BASE.format(token=TELEGRAM_BOT_TOKEN, method="sendMessage"),
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.info("Payment reminder sent for order %s (%s)", order_id, store)
        return True
    except Exception as exc:
        log.error("Failed to send payment reminder: %s", exc)
        return False


def send_new_store_alert(new_items: list[tuple[str, str]]) -> bool:
    """
    Notify via Telegram when a scheduled region rescan finds brand-new stores.

    Args:
        new_items: list of (item_id, store_name) tuples
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    if not new_items:
        return False

    lines = "\n".join(f"  • `{iid}` — {name}" for iid, name in new_items[:20])
    overflow = f"\n  …and {len(new_items) - 20} more" if len(new_items) > 20 else ""
    text = (
        f"🆕 *New stores in your area!* ({len(new_items)} found)\n\n"
        f"{lines}{overflow}\n\n"
        f"_Use `target add <id>` to start watching any of these._"
    )
    try:
        resp = httpx.post(
            _BASE.format(token=TELEGRAM_BOT_TOKEN, method="sendMessage"),
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.info("New-store alert sent (%d item(s))", len(new_items))
        return True
    except Exception as exc:
        log.error("Failed to send new-store alert: %s", exc)
        return False


def send_dead_item_alert(item_id: str, label: str) -> bool:
    """
    Notify when a previously known item is no longer returned by the TGTG API.
    The store may have closed or removed their listing.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    text = (
        f"⚰️ *Store listing removed*\n\n"
        f"*{label}* (ID: `{item_id}`) no longer appears in the TGTG API.\n"
        f"The store may have closed or removed their listing.\n\n"
        f"_This item has been marked inactive in the catalog. "
        f"No further alerts will be sent for it._"
    )

    try:
        resp = httpx.post(
            _BASE.format(token=TELEGRAM_BOT_TOKEN, method="sendMessage"),
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.info("Dead-item alert sent for %s (%s)", label, item_id)
        return True
    except Exception as exc:
        log.error("Failed to send dead-item alert: %s", exc)
        return False


def send_stock_alert(item: dict, order: dict | None = None, target=None) -> bool:
    """
    Send a Telegram message for a newly available item.

    Args:
        item:   TGTG item dict from get_items() / get_item().
        order:  If auto-purchase succeeded, pass the order dict here.
        target: Target instance (used to show desire level in message).

    Returns True if the message was sent successfully.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing).")
        return False

    item_id = str(item.get("item", {}).get("item_id", ""))
    store = item.get("store", {}).get("store_name", "Unknown store")
    available = item.get("items_available", 0)
    price = _format_price(item)
    pickup = _format_pickup(item)
    rating = item.get("item", {}).get("average_overall_rating", {}).get("average_overall_rating")
    rating_str = f"{rating:.1f}⭐" if rating else "–"

    desire = getattr(target, "desire", None)
    desire_line = "\n🔥 *HIGH-DESIRE target* — auto-buying now" if desire == "high" else ""

    if order:
        header = f"✅ *AUTO-PURCHASED!* Bag reserved at {store}"
        order_line = f"\n🧾 Order ID: `{order.get('id', '?')}`"
    elif desire == "high":
        header = f"🔥 *HIGH-DESIRE DEAL!* {store}"
        order_line = ""
    else:
        header = f"🔔 *DEAL AVAILABLE!* {store}"
        order_line = ""

    text = (
        f"{header}\n\n"
        f"🛍 *{available} bag(s)* available\n"
        f"💰 Price: *{price}*\n"
        f"🕐 Pickup: {pickup}\n"
        f"⭐ Rating: {rating_str}"
        f"{desire_line}"
        f"{order_line}"
    )

    keyboard = {
        "inline_keyboard": [[
            {"text": "Open in TGTG app", "url": _tgtg_deep_link(item_id)},
        ]]
    }

    try:
        resp = httpx.post(
            _BASE.format(token=TELEGRAM_BOT_TOKEN, method="sendMessage"),
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": keyboard,
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.info("Telegram alert sent for %s", store)
        return True
    except Exception as exc:
        log.error("Failed to send Telegram alert: %s", exc)
        return False
