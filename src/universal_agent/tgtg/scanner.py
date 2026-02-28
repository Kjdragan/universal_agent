"""
Region scanner — discovers all TGTG items in a geographic area and
populates the local SQLite catalog (db.py).

The scan uses pagination to walk through all available items (not just
favourites).  On each run it upserts known items, detects newly appearing
stores, and flags items that have disappeared from the API as dead.

Typical usage
-------------
  from .scanner import scan_and_store
  report = scan_and_store(client, lat, lon, radius)
  print(report)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from tgtg import TgtgClient

from .db import ItemDB, get_db

log = logging.getLogger(__name__)

_PAGE_SIZE = 20   # TGTG API maximum per page


# ── result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ScanReport:
    items_found: int = 0
    items_new: int = 0
    items_updated: int = 0
    items_dead: int = 0          # previously active, not returned this scan
    new_item_ids: list[str] = field(default_factory=list)
    dead_item_ids: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [
            f"Scan complete: {self.items_found} item(s) found in area",
            f"  🆕 New:     {self.items_new}",
            f"  🔄 Updated: {self.items_updated}",
        ]
        if self.items_dead:
            lines.append(f"  ⚠️  Gone:    {self.items_dead} (marked inactive)")
        return "\n".join(lines)


# ── core functions ────────────────────────────────────────────────────────────

def fetch_all_in_region(
    client: TgtgClient,
    latitude: float,
    longitude: float,
    radius: int,
) -> list[dict]:
    """
    Paginate through the TGTG API until all items in the region are fetched.

    Returns a flat list of raw item dicts (the same format as get_items()).
    """
    all_items: list[dict] = []
    page = 1

    while True:
        log.debug("Fetching page %d (radius=%dkm) …", page, radius)
        try:
            batch = client.get_items(
                latitude=latitude,
                longitude=longitude,
                radius=radius,
                favorites_only=False,
                page_size=_PAGE_SIZE,
                page=page,
            )
        except Exception as exc:
            log.error("API error on page %d: %s", page, exc)
            break

        if not batch:
            break

        all_items.extend(batch)
        log.debug("Page %d: got %d item(s) (total so far: %d)", page, len(batch), len(all_items))

        if len(batch) < _PAGE_SIZE:
            # Last (possibly partial) page — no more results
            break

        page += 1

    return all_items


def scan_and_store(
    client: TgtgClient,
    latitude: float,
    longitude: float,
    radius: int,
    db: ItemDB | None = None,
) -> ScanReport:
    """
    Fetch all items in the region, upsert into the catalog, and detect dead items.

    Items that were previously active in the DB but not returned by this scan
    are marked as potentially dead (is_active=0).  The caller can then check
    report.dead_item_ids and send notifications if desired.

    Returns a ScanReport with counts for logging / display.
    """
    if db is None:
        db = get_db()

    raw_items = fetch_all_in_region(client, latitude, longitude, radius)
    log.info("Region scan fetched %d item(s).", len(raw_items))

    # IDs that were active before this scan
    previously_active = set(db.get_active_ids())

    report = ScanReport(items_found=len(raw_items))
    returned_ids: set[str] = set()

    for raw in raw_items:
        item_id = str(raw.get("item", {}).get("item_id", ""))
        if not item_id:
            continue

        returned_ids.add(item_id)
        is_new = db.upsert(raw)
        if is_new:
            report.items_new += 1
            report.new_item_ids.append(item_id)
        else:
            report.items_updated += 1

    # Items in DB that were active but not in this scan → potentially dead
    gone_ids = previously_active - returned_ids
    for item_id in gone_ids:
        should_notify = db.mark_dead(item_id)
        if should_notify:
            report.items_dead += 1
            report.dead_item_ids.append(item_id)

    db.record_scan(
        items_found=report.items_found,
        items_new=report.items_new,
        items_updated=report.items_updated,
        items_dead=report.items_dead,
        latitude=latitude,
        longitude=longitude,
        radius=radius,
    )

    log.info(str(report))
    return report
