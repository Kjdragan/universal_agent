"""Slow-burn pacing for the CSI enrich crons (ZAI/GLM Fair-Usage mitigation).

The RSS / threads semantic-enrich crons process up to ~40 events per run, each
making one or two ZAI/GLM classification calls. The loops are already sequential
(no parallel fan-out), but they fire those calls back-to-back, so ~40-80 calls
land in a ~90-second window — a burst that trips the ZAI Fair-Usage rate limit
(observed as the dominant source of 429 rejections).

This module inserts a fixed delay after each event that actually hit the LLM,
turning the burst into a gentle trickle (~25-30 min for a 40-event run). We do
NOT care about wall-clock time for these overnight crons; we only care that they
stop tripping the limiter and starving the shared ZAI account.

Deliberately a FIXED, manually-tuned interval — NOT adaptive back-off. We don't
model the limiter's algorithm, and auto-tuning adds complexity for little gain.
Change the default below (and redeploy) or set ``CSI_ENRICH_PACE_SECONDS``.
``0`` disables pacing entirely.
"""

from __future__ import annotations

import os
import time

# The one knob. Conservative fixed seconds-between-LLM-events. Edit here to tune.
ENRICH_PACE_SECONDS_DEFAULT = 45.0


def resolve_enrich_pace_seconds(env_value: str | None = None) -> float:
    """Resolve the per-event pacing delay.

    Reads ``CSI_ENRICH_PACE_SECONDS`` (or the explicit ``env_value`` for tests),
    falling back to ``ENRICH_PACE_SECONDS_DEFAULT`` when unset or unparseable.
    Negative values clamp to ``0`` (pacing disabled).
    """
    raw = os.getenv("CSI_ENRICH_PACE_SECONDS") if env_value is None else env_value
    raw = (raw or "").strip()
    if not raw:
        return float(ENRICH_PACE_SECONDS_DEFAULT)
    try:
        return max(0.0, float(raw))
    except ValueError:
        return float(ENRICH_PACE_SECONDS_DEFAULT)


def pace_sleep(seconds: float) -> None:
    """Sleep ``seconds`` (no-op when ``<= 0``). Isolated so callers stay simple."""
    if seconds and seconds > 0:
        time.sleep(seconds)
