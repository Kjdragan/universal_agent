"""
TGTG auto-purchaser.

Calls create_order() via the official (but unofficial) TGTG Python client.
Falls back to Playwright browser automation if the API call is blocked.
"""

from __future__ import annotations

import logging

from tgtg import TgtgClient
from tgtg.exceptions import TgtgAPIError

from .config import TGTG_ORDER_COUNT

log = logging.getLogger(__name__)


def purchase_item(client: TgtgClient, item: dict) -> dict | None:
    """
    Reserve a TGTG bag via the API.

    Returns the order dict on success, None on failure.
    The reservation is created immediately; TGTG charges the stored
    payment method automatically (or you pay in-app within ~15 min).
    """
    item_id = str(item.get("item", {}).get("item_id", ""))
    store = item.get("store", {}).get("store_name", item_id)
    available = item.get("items_available", 0)
    count = min(TGTG_ORDER_COUNT, available)

    if not item_id:
        log.error("Item has no item_id: %s", item)
        return None

    log.info("Attempting to purchase %d × '%s' (item_id=%s) …", count, store, item_id)
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
