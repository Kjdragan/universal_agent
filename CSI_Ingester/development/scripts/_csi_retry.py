"""Transient-failure retry helper for CSI cron scripts that POST to the
gateway through `post_json_with_failover`.

Kept standalone (no csi_ingester or universal_agent imports) so unit tests
can exercise it without the full CSI package on PYTHONPATH.

Background: on 2026-05-17 the rss-semantic-enrich timer's first fire
post-deploy hit a Webshare cold-start storm — 10 of 12 events came back
with `error=request_exception` (urllib timeout). The script recorded
those as permanent `transcript_status='failed'` rows, dragging the new
canary's ok_rate to 11% and triggering RED. The proper response to
request_exception is "try once or twice more", not "record as
permanent failure".
"""

from __future__ import annotations

import time
from typing import Any, Callable

_TRANSIENT_ERRORS = frozenset({"request_exception"})


def is_transient_failure(result: dict[str, Any]) -> bool:
    """True iff `result` represents a failure that warrants a retry.

    Auth errors (401), 4xx that aren't transient, captions_disabled,
    video_unavailable — all permanent, all skip the retry path so we
    don't burn a residential-proxy slot on a known-dead request.
    """
    if bool(result.get("ok")):
        return False
    err = str(result.get("error") or "").strip()
    if err in _TRANSIENT_ERRORS:
        return True
    if err == "http_error":
        try:
            status = int(result.get("http_status") or 0)
        except (TypeError, ValueError):
            status = 0
        # 5xx + 0 (no status — network dead) are transient. 4xx is permanent.
        if status >= 500 or status == 0:
            return True
    return False


def retry_on_transient(
    fetch: Callable[[], dict[str, Any]],
    *,
    max_retries: int = 2,
    backoff_seconds: float = 5.0,
    log_prefix: str = "CSI_RETRY",
    sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Call `fetch()` up to `max_retries + 1` times, retrying only on
    failures `is_transient_failure` agrees are retryable.

    `sleep` lets tests inject a no-op so they don't actually sleep.
    """
    sleep_fn = sleep or time.sleep
    last: dict[str, Any] = {}
    total_attempts = max(1, max_retries + 1)
    for attempt in range(total_attempts):
        last = fetch()
        if bool(last.get("ok")):
            if attempt > 0:
                print(f"{log_prefix}_RECOVERED attempt={attempt + 1}")
            return last
        if not is_transient_failure(last):
            return last
        if attempt < max_retries:
            print(
                f"{log_prefix}_TRANSIENT attempt={attempt + 1}/{total_attempts}"
                f" error={last.get('error')!r}"
            )
            sleep_fn(backoff_seconds * (attempt + 1))
    return last
