"""
SQLite catalog of all TGTG items discovered in your region.

Schema
------
items       — one row per item_id; updated on every scan
scan_runs   — one row per scan_region() call, for audit / stats

The catalog is separate from the targets (targets.py). Targets express
*intent* (auto-buy / watch) while the catalog is a factual record of what
the TGTG API has ever returned for your configured area.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_fields(raw: dict) -> dict:
    """Pull the fields we care about from a raw TGTG API item dict."""
    item = raw.get("item", {})
    store = raw.get("store", {})
    loc = store.get("store_location", {}).get("location", {})
    addr = store.get("store_location", {}).get("address", {})
    price = item.get("price_including_taxes", {})
    rating = item.get("average_overall_rating", {})

    pickup = item.get("pickup_interval", {})
    pickup_start = pickup.get("start", "")
    pickup_end = pickup.get("end", "")

    return {
        "item_id": str(item.get("item_id", "")),
        "store_name": store.get("store_name", ""),
        "item_name": item.get("name", ""),
        "display_name": raw.get("display_name", ""),
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
        "address": addr.get("address_line", ""),
        "price_minor": price.get("minor_units"),
        "price_decimals": price.get("decimals", 2),
        "price_currency": price.get("code", ""),
        "pickup_start": pickup_start,
        "pickup_end": pickup_end,
        "average_rating": rating.get("average_overall_rating"),
        "rating_count": rating.get("rating_count", 0),
        "category": item.get("item_category", ""),
        "raw_json": json.dumps(raw),
    }


# ── database class ────────────────────────────────────────────────────────────

class ItemDB:
    """Thin wrapper around a SQLite file for the TGTG item catalog."""

    def __init__(self, path: Path):
        self.path = path
        self._init()

    @contextmanager
    def _conn(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _init(self):
        with self._conn() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS items (
                    item_id            TEXT PRIMARY KEY,
                    store_name         TEXT NOT NULL DEFAULT '',
                    item_name          TEXT NOT NULL DEFAULT '',
                    display_name       TEXT NOT NULL DEFAULT '',
                    latitude           REAL,
                    longitude          REAL,
                    address            TEXT NOT NULL DEFAULT '',
                    price_minor        INTEGER,
                    price_decimals     INTEGER NOT NULL DEFAULT 2,
                    price_currency     TEXT NOT NULL DEFAULT '',
                    pickup_start       TEXT NOT NULL DEFAULT '',
                    pickup_end         TEXT NOT NULL DEFAULT '',
                    average_rating     REAL,
                    rating_count       INTEGER NOT NULL DEFAULT 0,
                    category           TEXT NOT NULL DEFAULT '',
                    first_seen_at      TEXT NOT NULL DEFAULT '',
                    last_scanned_at    TEXT NOT NULL DEFAULT '',
                    last_active_at     TEXT NOT NULL DEFAULT '',
                    is_active          INTEGER NOT NULL DEFAULT 1,
                    dead_notified      INTEGER NOT NULL DEFAULT 0,
                    raw_json           TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS scan_runs (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at        TEXT NOT NULL,
                    items_found   INTEGER NOT NULL DEFAULT 0,
                    items_new     INTEGER NOT NULL DEFAULT 0,
                    items_updated INTEGER NOT NULL DEFAULT 0,
                    items_dead    INTEGER NOT NULL DEFAULT 0,
                    latitude      REAL,
                    longitude     REAL,
                    radius        INTEGER
                );
            """)

    # ── writes ────────────────────────────────────────────────────────────────

    def upsert(self, raw: dict) -> bool:
        """
        Insert or update an item from a raw API dict.
        Returns True if this is a *new* item (first time seen).
        """
        fields = _extract_fields(raw)
        if not fields["item_id"]:
            return False

        now = _now_iso()
        with self._conn() as con:
            existing = con.execute(
                "SELECT item_id, first_seen_at FROM items WHERE item_id = ?",
                (fields["item_id"],),
            ).fetchone()

            if existing:
                con.execute(
                    """
                    UPDATE items SET
                        store_name = :store_name,
                        item_name = :item_name,
                        display_name = :display_name,
                        latitude = :latitude,
                        longitude = :longitude,
                        address = :address,
                        price_minor = :price_minor,
                        price_decimals = :price_decimals,
                        price_currency = :price_currency,
                        pickup_start = :pickup_start,
                        pickup_end = :pickup_end,
                        average_rating = :average_rating,
                        rating_count = :rating_count,
                        category = :category,
                        last_scanned_at = :now,
                        last_active_at = :now,
                        is_active = 1,
                        raw_json = :raw_json
                    WHERE item_id = :item_id
                    """,
                    {**fields, "now": now},
                )
                return False
            else:
                con.execute(
                    """
                    INSERT INTO items (
                        item_id, store_name, item_name, display_name,
                        latitude, longitude, address,
                        price_minor, price_decimals, price_currency,
                        pickup_start, pickup_end,
                        average_rating, rating_count, category,
                        first_seen_at, last_scanned_at, last_active_at,
                        is_active, dead_notified, raw_json
                    ) VALUES (
                        :item_id, :store_name, :item_name, :display_name,
                        :latitude, :longitude, :address,
                        :price_minor, :price_decimals, :price_currency,
                        :pickup_start, :pickup_end,
                        :average_rating, :rating_count, :category,
                        :now, :now, :now,
                        1, 0, :raw_json
                    )
                    """,
                    {**fields, "now": now},
                )
                return True

    def mark_dead(self, item_id: str) -> bool:
        """
        Mark an item as no longer available (not returned by the API).
        Returns True if the item *was* active and is now being marked dead
        for the first time (i.e. caller should send a notification).
        """
        with self._conn() as con:
            row = con.execute(
                "SELECT is_active, dead_notified FROM items WHERE item_id = ?",
                (str(item_id),),
            ).fetchone()
            if not row:
                return False
            was_active = bool(row["is_active"])
            already_notified = bool(row["dead_notified"])
            con.execute(
                "UPDATE items SET is_active = 0 WHERE item_id = ?",
                (str(item_id),),
            )
            return was_active and not already_notified

    def mark_dead_notified(self, item_id: str) -> None:
        with self._conn() as con:
            con.execute(
                "UPDATE items SET dead_notified = 1 WHERE item_id = ?",
                (str(item_id),),
            )

    def record_scan(
        self,
        items_found: int,
        items_new: int,
        items_updated: int,
        items_dead: int,
        latitude: float,
        longitude: float,
        radius: int,
    ) -> None:
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO scan_runs
                    (run_at, items_found, items_new, items_updated, items_dead,
                     latitude, longitude, radius)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (_now_iso(), items_found, items_new, items_updated, items_dead,
                 latitude, longitude, radius),
            )

    # ── reads ─────────────────────────────────────────────────────────────────

    def get(self, item_id: str) -> sqlite3.Row | None:
        with self._conn() as con:
            return con.execute(
                "SELECT * FROM items WHERE item_id = ?", (str(item_id),)
            ).fetchone()

    def get_active_ids(self) -> list[str]:
        """Return item_ids for all items currently marked active in the catalog."""
        with self._conn() as con:
            rows = con.execute(
                "SELECT item_id FROM items WHERE is_active = 1"
            ).fetchall()
        return [r["item_id"] for r in rows]

    def search(
        self,
        query: str = "",
        active_only: bool = True,
        limit: int = 200,
    ) -> list[sqlite3.Row]:
        """
        Search the catalog by store or item name (case-insensitive substring).
        """
        with self._conn() as con:
            q = f"%{query}%"
            active_clause = "AND is_active = 1" if active_only else ""
            rows = con.execute(
                f"""
                SELECT * FROM items
                WHERE (store_name LIKE ? OR item_name LIKE ? OR display_name LIKE ?)
                {active_clause}
                ORDER BY store_name
                LIMIT ?
                """,
                (q, q, q, limit),
            ).fetchall()
        return rows

    def all_items(self, active_only: bool = False, limit: int = 500) -> list[sqlite3.Row]:
        with self._conn() as con:
            active_clause = "WHERE is_active = 1" if active_only else ""
            return con.execute(
                f"SELECT * FROM items {active_clause} ORDER BY store_name LIMIT ?",
                (limit,),
            ).fetchall()

    def stats(self) -> dict:
        with self._conn() as con:
            total = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            active = con.execute("SELECT COUNT(*) FROM items WHERE is_active = 1").fetchone()[0]
            dead = con.execute("SELECT COUNT(*) FROM items WHERE is_active = 0").fetchone()[0]
            scans = con.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0]
            last_scan = con.execute(
                "SELECT run_at, items_found FROM scan_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return {
            "total": total,
            "active": active,
            "dead": dead,
            "scans_run": scans,
            "last_scan_at": last_scan["run_at"] if last_scan else None,
            "last_scan_found": last_scan["items_found"] if last_scan else None,
        }


# ── module-level singleton (opened lazily) ────────────────────────────────────

_db: ItemDB | None = None


def get_db(path: Path | None = None) -> ItemDB:
    """Return the module-level ItemDB, creating it if needed."""
    global _db
    if _db is None:
        from .config import TGTG_DB_FILE
        _db = ItemDB(path or TGTG_DB_FILE)
    return _db
