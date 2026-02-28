"""
Target management for TGTG sniping.

Each target is a store/item you've explicitly registered, with a desire level
that controls how aggressively the sniper acts when stock appears:

  "high"  — auto-purchase immediately (you are definitely fine buying this)
  "watch" — notify only, no automatic purchase

Targets are persisted in tgtg_targets.json alongside your credentials.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

TARGETS_FILE = Path(".tgtg_targets.json")

DesireLevel = Literal["high", "watch"]


@dataclass
class Target:
    item_id: str
    label: str = ""                  # Friendly name (auto-filled from store name)
    desire: DesireLevel = "watch"    # "high" = auto-buy; "watch" = notify only
    order_count: int = 1             # How many bags to reserve per trigger
    max_price: float | None = None   # Skip auto-buy if bag costs more than this (currency units)
    notes: str = ""                  # Your personal notes
    available_from: str | None = None  # HH:MM local time when you can collect (e.g. "17:00")
    available_to: str | None = None    # HH:MM local time when you stop being available

    @property
    def auto_buy(self) -> bool:
        return self.desire == "high"

    def price_ok(self, item: dict) -> bool:
        """Return True if the item price is within this target's max_price cap."""
        if self.max_price is None:
            return True
        try:
            p = item["item"]["price_including_taxes"]
            actual = p["minor_units"] / 10 ** p["decimals"]
            return actual <= self.max_price
        except (KeyError, TypeError, ZeroDivisionError):
            return True  # can't determine price — don't block

    def pickup_ok(self, item: dict) -> bool:
        """
        Return True if the item's pickup window overlaps with this target's
        available_from / available_to window.  If neither is set, always True.
        """
        if not self.available_from and not self.available_to:
            return True

        try:
            window = item["item"]["pickup_interval"]
            pu_start = datetime.fromisoformat(
                window["start"].replace("Z", "+00:00")
            ).astimezone()
            pu_end = datetime.fromisoformat(
                window["end"].replace("Z", "+00:00")
            ).astimezone()
        except (KeyError, TypeError, ValueError):
            return True  # can't determine window — don't block

        now_local = datetime.now().astimezone()
        today = now_local.date()

        def _hhmm(s: str):
            h, m = int(s[:2]), int(s[3:5])
            # Build an aware datetime for today at HH:MM in local tz
            return datetime.combine(today, time(h, m), tzinfo=now_local.tzinfo)

        avail_start = _hhmm(self.available_from) if self.available_from else None
        avail_end = _hhmm(self.available_to) if self.available_to else None

        # Reject if pickup ends before I'm available, or starts after I'm done
        if avail_start and pu_end <= avail_start:
            return False
        if avail_end and pu_start >= avail_end:
            return False
        return True


def load_targets() -> list[Target]:
    """Load all targets from disk."""
    if not TARGETS_FILE.exists():
        return []
    try:
        raw = json.loads(TARGETS_FILE.read_text())
        return [Target(**t) for t in raw]
    except Exception as exc:
        log.error("Failed to load targets: %s", exc)
        return []


def save_targets(targets: list[Target]) -> None:
    """Persist all targets to disk."""
    TARGETS_FILE.write_text(json.dumps([asdict(t) for t in targets], indent=2))


def get_target(item_id: str) -> Target | None:
    """Return the target for a given item_id, or None if not registered."""
    for t in load_targets():
        if t.item_id == str(item_id):
            return t
    return None


def upsert_target(target: Target) -> None:
    """Add or update a target (matched by item_id)."""
    targets = load_targets()
    for i, t in enumerate(targets):
        if t.item_id == target.item_id:
            targets[i] = target
            save_targets(targets)
            return
    targets.append(target)
    save_targets(targets)
    log.info("Target added: %s (%s)", target.label or target.item_id, target.desire)


def remove_target(item_id: str) -> bool:
    """Remove a target by item_id. Returns True if it was found and removed."""
    targets = load_targets()
    before = len(targets)
    targets = [t for t in targets if t.item_id != str(item_id)]
    if len(targets) == before:
        return False
    save_targets(targets)
    return True


def all_watched_ids() -> list[str]:
    """Item IDs for all registered targets (used as the fetch list)."""
    return [t.item_id for t in load_targets()]
