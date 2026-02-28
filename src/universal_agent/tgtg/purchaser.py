"""
TGTG auto-purchaser.

Calls create_order() via the tgtg-python client.
Respects per-target order_count and max_price cap.
"""

from __future__ import annotations

import logging

from tgtg import TgtgClient
from tgtg.exceptions import TgtgAPIError

from .config import TGTG_ORDER_COUNT
from .targets import Target

log = logging.getLogger(__name__)


def purchase_item(
    client: TgtgClient,
    item: dict,
    target: Target | None = None,
) -> dict | None:
    """
    Reserve a TGTG bag via the API.

    Applies per-target order_count and max_price guard.
    Returns the order dict on success, None on failure.
    """
    item_id = str(item.get("item", {}).get("item_id", ""))
    store = item.get("store", {}).get("store_name", item_id)
    available = item.get("items_available", 0)

    if not item_id:
        log.error("Item has no item_id: %s", item)
        return None

    # ── Price guard ───────────────────────────────────────────────────────────
    if target is not None and not target.price_ok(item):
        try:
            p = item["item"]["price_including_taxes"]
            actual = p["minor_units"] / 10 ** p["decimals"]
            currency = p["code"]
        except (KeyError, TypeError):
            actual, currency = "?", ""
        log.info(
            "SKIPPED (price guard): %s costs %s %s > max %s %s",
            store, actual, currency, target.max_price, currency,
        )
        return None

    # ── Determine count ───────────────────────────────────────────────────────
    count = min(
        target.order_count if target is not None else TGTG_ORDER_COUNT,
        available,
    )

    log.info("Purchasing %d × '%s' (item_id=%s) …", count, store, item_id)
    try:
        order = client.create_order(item_id, count)
        log.info(
            "ORDER CREATED: %s | order_id=%s | state=%s",
            store,
            order.get("id"),
            order.get("state"),
        )
        return order
    except TgtgAPIError as exc:
        log.error("create_order failed for %s: %s", store, exc)
        return None


def abort_order(client: TgtgClient, order_id: str) -> bool:
    """Cancel a pending (unpaid) order. Returns True on success."""
    try:
        client.abort_order(order_id)
        log.info("Order %s aborted.", order_id)
        return True
    except TgtgAPIError as exc:
        log.error("abort_order failed for %s: %s", order_id, exc)
        return False
