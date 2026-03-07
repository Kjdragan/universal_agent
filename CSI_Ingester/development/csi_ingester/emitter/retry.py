"""Retry policy helpers."""

from __future__ import annotations

import random


def retry_delay_seconds(attempt: int) -> float:
    if attempt <= 1:
        return 0.0
    if attempt == 2:
        base = 20.0
    else:
        base = 60.0
    jitter = random.uniform(-2.0, 2.0)
    return max(0.0, base + jitter)


def exponential_delay_seconds(
    attempt: int,
    *,
    base: float = 5.0,
    max_delay: float = 120.0,
    jitter_fraction: float = 0.2,
) -> float:
    """Exponential backoff: base * 2^(attempt-1) capped at max_delay, ±jitter."""
    if attempt <= 1:
        return 0.0
    raw = min(base * (2 ** (attempt - 2)), max_delay)
    jitter = random.uniform(-jitter_fraction, jitter_fraction) * raw
    return max(0.0, raw + jitter)

