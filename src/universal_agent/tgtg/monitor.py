"""
TGTG deal monitor with adaptive polling.

Adaptive strategy:
  - IDLE (5 min):    No pickup window within POLL_WINDOW_MINUTES
  - ACTIVE (30 s):   A watched item has a pickup window starting soon
  - SNIPING (15 s):  Stock > 0 was just detected on any watched item
"""

from __future__ import annotations

import itertools
import logging
import time
from datetime import datetime, timezone
from typing import Callable

from tgtg import TgtgClient
from tgtg.exceptions import TgtgAPIError

from .config import (
    POLL_ACTIVE,
    POLL_IDLE,
    POLL_SNIPING,
    POLL_WINDOW_MINUTES,
    TGTG_LATITUDE,
    TGTG_LONGITUDE,
    TGTG_PROXIES,
    TGTG_RADIUS,
    TGTG_WATCHED_ITEMS,
    load_saved_credentials,
    save_credentials,
)

log = logging.getLogger(__name__)


def _pick_proxy(proxy_cycle) -> dict | None:
    """Return the next proxy dict or None if no proxies configured."""
    url = next(proxy_cycle, None)
    if not url:
        return None
    return {"http": url, "https": url}


def _minutes_to_pickup(item: dict) -> float | None:
    """Return minutes until earliest pickup, or None if no window set."""
    try:
        window = item["item"]["pickup_interval"]["start"]
        start = datetime.fromisoformat(window.replace("Z", "+00:00"))
        delta = (start - datetime.now(timezone.utc)).total_seconds() / 60
        return delta
    except (KeyError, TypeError, ValueError):
        return None


def _determine_interval(items: list[dict]) -> int:
    """Compute next poll interval based on current item states."""
    has_stock = any(i.get("items_available", 0) > 0 for i in items)
    if has_stock:
        return POLL_SNIPING

    for item in items:
        mins = _minutes_to_pickup(item)
        if mins is not None and 0 <= mins <= POLL_WINDOW_MINUTES:
            return POLL_ACTIVE

    return POLL_IDLE


def build_client(email: str | None = None) -> TgtgClient:
    """Build a TgtgClient using saved credentials, falling back to email login."""
    creds = load_saved_credentials()

    proxy_cycle = itertools.cycle(TGTG_PROXIES) if TGTG_PROXIES else iter([])
    proxy = _pick_proxy(proxy_cycle)

    if creds.get("access_token") and creds.get("refresh_token"):
        log.info("Using saved credentials.")
        client = TgtgClient(
            access_token=creds["access_token"],
            refresh_token=creds["refresh_token"],
            cookie=creds.get("cookie"),
            proxies=proxy,
        )
    elif email:
        log.info("Logging in with email: %s", email)
        client = TgtgClient(email=email, proxies=proxy)
        new_creds = client.get_credentials()
        save_credentials(new_creds)
        log.info("Credentials saved to disk.")
    else:
        raise ValueError(
            "No saved credentials and no TGTG_EMAIL set. "
            "Run: python -m src.universal_agent.tgtg.cli login"
        )
    return client


def fetch_watched_items(client: TgtgClient) -> list[dict]:
    """Fetch item data for watched items (or all favourites if none specified)."""
    if TGTG_WATCHED_ITEMS:
        items = []
        for item_id in TGTG_WATCHED_ITEMS:
            try:
                items.append(client.get_item(item_id))
            except TgtgAPIError as exc:
                log.warning("Could not fetch item %s: %s", item_id, exc)
        return items
    else:
        return client.get_items(
            latitude=TGTG_LATITUDE,
            longitude=TGTG_LONGITUDE,
            radius=TGTG_RADIUS,
            favorites_only=True,
        )


def run_monitor(
    client: TgtgClient,
    on_stock: Callable[[dict], None],
    stop_event=None,
) -> None:
    """
    Main polling loop.

    Args:
        client:     Authenticated TgtgClient.
        on_stock:   Callback invoked with each in-stock item dict.
        stop_event: threading.Event to signal shutdown.
    """
    proxy_cycle = itertools.cycle(TGTG_PROXIES) if TGTG_PROXIES else itertools.cycle([None])
    seen_in_stock: set[str] = set()

    while True:
        if stop_event and stop_event.is_set():
            log.info("Monitor stopped.")
            break

        # Rotate proxy on each request batch
        proxy_url = next(proxy_cycle)
        if proxy_url:
            client.proxies = {"http": proxy_url, "https": proxy_url}

        try:
            items = fetch_watched_items(client)
        except TgtgAPIError as exc:
            log.error("API error during fetch: %s — backing off 60s", exc)
            time.sleep(60)
            continue
        except Exception as exc:
            log.error("Unexpected error: %s — backing off 30s", exc)
            time.sleep(30)
            continue

        for item in items:
            item_id = str(item.get("item", {}).get("item_id", ""))
            available = item.get("items_available", 0)
            store = item.get("store", {}).get("store_name", item_id)

            if available > 0:
                log.info("STOCK DETECTED: %s — %d bag(s)", store, available)
                if item_id not in seen_in_stock:
                    seen_in_stock.add(item_id)
                    on_stock(item)
            else:
                if item_id in seen_in_stock:
                    log.info("Sold out: %s", store)
                    seen_in_stock.discard(item_id)

        interval = _determine_interval(items)
        log.debug("Next poll in %ds (%s mode)", interval, {
            POLL_IDLE: "IDLE",
            POLL_ACTIVE: "ACTIVE",
            POLL_SNIPING: "SNIPING",
        }.get(interval, "?"))
        time.sleep(interval)
