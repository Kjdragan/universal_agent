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

