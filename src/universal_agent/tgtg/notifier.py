"""
Telegram notifier for TGTG stock alerts.

Sends a rich message with store info, price, pickup window, and stock count.
Includes an inline keyboard with a deep link to the TGTG app.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from .config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

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


def send_stock_alert(item: dict, order: dict | None = None) -> bool:
    """
    Send a Telegram message for a newly available item.

    Args:
        item:   TGTG item dict from get_items() / get_item().
        order:  If auto-purchase succeeded, pass the order dict here.

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

    if order:
        header = f"✅ *AUTO-PURCHASED!* Bag reserved at {store}"
        order_line = f"\n🧾 Order ID: `{order.get('id', '?')}`"
    else:
        header = f"🔔 *DEAL AVAILABLE!* {store}"
        order_line = ""

    text = (
        f"{header}\n\n"
        f"🛍 *{available} bag(s)* available\n"
        f"💰 Price: *{price}*\n"
        f"🕐 Pickup: {pickup}\n"
        f"⭐ Rating: {rating_str}"
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
