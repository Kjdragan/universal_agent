"""Session Budget Tracker — manages Claude API session slots across consumers.

Tracks active sessions across:
- Simone (gateway interactive sessions)
- CSI Analytics (timer-driven report runs)
- VP workers (SDK mode missions)
- CLI sessions (including Agent Team teammates)

Provides acquire/release semantics and a status endpoint for the dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default maximum concurrent Claude API sessions (ZAI coding plan limit)
DEFAULT_MAX_SLOTS = int(os.getenv("UA_SESSION_BUDGET_MAX_SLOTS", "5"))


@dataclass
class SlotAllocation:
    """A single consumer's slot reservation."""
    consumer_id: str
    slots: int
    acquired_at: float = field(default_factory=time.monotonic)
    metadata: dict[str, Any] = field(default_factory=dict)


class SessionBudget:
    """Thread-safe session budget tracker.

    Enforces a global cap on concurrent Claude API sessions to prevent
    rate limit violations across all Universal Agent consumers.
    """

    _instance: Optional[SessionBudget] = None
    _lock_cls = threading.Lock()

    def __init__(self, max_slots: Optional[int] = None) -> None:
        self._max_slots = max_slots or DEFAULT_MAX_SLOTS
        self._allocations: dict[str, SlotAllocation] = {}
        self._lock = threading.Lock()
        self._heavy_mode_active = False
        self._heavy_mode_owner: Optional[str] = None
        logger.info("SessionBudget initialized: max_slots=%d", self._max_slots)

    @classmethod
    def get_instance(cls, max_slots: Optional[int] = None) -> SessionBudget:
        """Get or create the singleton instance."""
        with cls._lock_cls:
            if cls._instance is None:
                cls._instance = cls(max_slots)
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (for testing)."""
        with cls._lock_cls:
            cls._instance = None

    @property
    def max_slots(self) -> int:
        return self._max_slots

    def available(self) -> int:
        """Return number of available slots."""
        with self._lock:
            used = sum(alloc.slots for alloc in self._allocations.values())
            return max(0, self._max_slots - used)

    def used(self) -> int:
        """Return total number of slots in use."""
        with self._lock:
            return sum(alloc.slots for alloc in self._allocations.values())

    def acquire(self, consumer_id: str, slots: int = 1, metadata: Optional[dict[str, Any]] = None) -> bool:
        """Try to acquire slot(s) for a consumer.

        Args:
            consumer_id: Unique identifier for the consumer (e.g. "cli.mission-123")
            slots: Number of slots to acquire
            metadata: Optional metadata for tracking

        Returns:
            True if slots were acquired, False if budget exceeded
        """
        with self._lock:
            current_used = sum(alloc.slots for alloc in self._allocations.values())
            if current_used + slots > self._max_slots:
                logger.warning(
                    "SessionBudget: cannot acquire %d slot(s) for %s (used=%d, max=%d)",
                    slots, consumer_id, current_used, self._max_slots,
                )
                return False

            if consumer_id in self._allocations:
                # Update existing allocation
                existing = self._allocations[consumer_id]
                self._allocations[consumer_id] = SlotAllocation(
                    consumer_id=consumer_id,
                    slots=existing.slots + slots,
                    acquired_at=existing.acquired_at,
                    metadata={**(existing.metadata or {}), **(metadata or {})},
                )
            else:
                self._allocations[consumer_id] = SlotAllocation(
                    consumer_id=consumer_id,
                    slots=slots,
                    acquired_at=time.monotonic(),
                    metadata=metadata or {},
                )

            logger.info(
                "SessionBudget: acquired %d slot(s) for %s (total_used=%d/%d)",
                slots, consumer_id, current_used + slots, self._max_slots,
            )
            return True

    def release(self, consumer_id: str, slots: Optional[int] = None) -> None:
        """Release slot(s) for a consumer.

        Args:
            consumer_id: The consumer releasing slots
            slots: Number to release (None = release all for this consumer)
        """
        with self._lock:
            if consumer_id not in self._allocations:
                return

            if slots is None:
                # Release all
                released = self._allocations.pop(consumer_id).slots
            else:
                alloc = self._allocations[consumer_id]
                new_count = max(0, alloc.slots - slots)
                released = alloc.slots - new_count
                if new_count == 0:
                    del self._allocations[consumer_id]
                else:
                    self._allocations[consumer_id] = SlotAllocation(
                        consumer_id=consumer_id,
                        slots=new_count,
                        acquired_at=alloc.acquired_at,
                        metadata=alloc.metadata,
                    )

            current_used = sum(alloc.slots for alloc in self._allocations.values())
            logger.info(
                "SessionBudget: released %d slot(s) for %s (total_used=%d/%d)",
                released, consumer_id, current_used, self._max_slots,
            )

            # If this was the heavy mode owner, deactivate heavy mode
            if self._heavy_mode_owner == consumer_id:
                self._heavy_mode_active = False
                self._heavy_mode_owner = None

    def enter_heavy_mode(self, consumer_id: str) -> bool:
        """Enter heavy mission mode — signals that a resource-intensive session is running.

        This doesn't allocate extra slots but flags the system so
        lower-priority consumers (e.g. CSI analytics) can voluntarily pause.

        Returns:
            True if heavy mode was entered, False if already active
        """
        with self._lock:
            if self._heavy_mode_active:
                return False
            self._heavy_mode_active = True
            self._heavy_mode_owner = consumer_id
            logger.info("SessionBudget: heavy mode ACTIVE (owner=%s)", consumer_id)
            return True

    def exit_heavy_mode(self, consumer_id: str) -> None:
        """Exit heavy mission mode."""
        with self._lock:
            if self._heavy_mode_owner == consumer_id:
                self._heavy_mode_active = False
                self._heavy_mode_owner = None
                logger.info("SessionBudget: heavy mode DEACTIVATED")

    @property
    def heavy_mode_active(self) -> bool:
        return self._heavy_mode_active

    def status(self) -> dict[str, Any]:
        """Return current budget status for dashboard/API consumption."""
        with self._lock:
            allocations = []
            for alloc in self._allocations.values():
                allocations.append({
                    "consumer_id": alloc.consumer_id,
                    "slots": alloc.slots,
                    "age_seconds": round(time.monotonic() - alloc.acquired_at, 1),
                    "metadata": alloc.metadata,
                })

            used = sum(alloc.slots for alloc in self._allocations.values())
            return {
                "max_slots": self._max_slots,
                "used_slots": used,
                "available_slots": max(0, self._max_slots - used),
                "heavy_mode_active": self._heavy_mode_active,
                "heavy_mode_owner": self._heavy_mode_owner,
                "allocations": allocations,
            }
