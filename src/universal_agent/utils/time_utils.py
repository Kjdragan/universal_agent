"""Shared timestamp helpers.

Consolidates the ~40 copy-pasted ``_now_iso`` / ``_parse_iso`` helpers that
had drifted across services (2026-07-11 style pass). Only behaviorally
equivalent copies were migrated; format-drifted variants (e.g. strftime
Z-suffix producers) deliberately keep their local implementations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def now_iso() -> str:
    """Timezone-aware UTC timestamp in ``datetime.isoformat()`` format."""
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601-ish timestamp; ``None`` on falsy/unparseable input.

    Replaces a trailing ``Z`` with ``+00:00`` before parsing — redundant on
    Python >= 3.11 but kept so every call site shares one exact behavior.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
