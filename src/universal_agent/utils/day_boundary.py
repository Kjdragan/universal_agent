"""Single source of truth for the operator's daily boundary (America/Chicago).

Every proactive demo-volume control counts "today" against the SAME day: the
normal-flow build cap (``priority_dispatcher._count_dispatched_tutorial_builds_today``,
default 3/day), the auto-route inflow ceiling
(``proactive_tutorial_builds._count_today_tutorial_builds``,
``UA_DEMO_BUILD_DAILY_CEILING``), and the end-of-day golden-nuggets ceiling
(``proactive_demo_nuggets``, 5/day). The end-of-day cron fires at 23:50
America/Chicago, so a UTC boundary would undercount the Chicago day (UTC has
already rolled over) and could let the 5/day ceiling be exceeded — hence the
boundary is the operator's local (Houston) day, not UTC.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_CHICAGO = ZoneInfo("America/Chicago")


def chicago_day_start_iso() -> str:
    """America/Chicago local midnight as a ``+00:00`` UTC ISO string.

    task_hub timestamps are ``datetime.now(timezone.utc).isoformat()`` (a
    fixed-width UTC ISO-8601 string with a ``+00:00`` offset), so the local day
    boundary is computed in Chicago time, converted to the same UTC ``+00:00``
    form, and compared lexicographically — valid because both strings are
    zero-padded ISO UTC. Always ``<= now`` (today's local midnight is in the
    past), so a row stamped "now" always falls on/after it.
    """
    local_midnight = datetime.now(_CHICAGO).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return local_midnight.astimezone(timezone.utc).isoformat()
