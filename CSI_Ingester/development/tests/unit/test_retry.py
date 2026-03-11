"""Tests for csi_ingester.emitter.retry module."""

from __future__ import annotations

from csi_ingester.emitter.retry import exponential_delay_seconds, retry_delay_seconds


def test_legacy_retry_delay_first_attempt():
    assert retry_delay_seconds(1) == 0.0


def test_exponential_delay_first_attempt():
    assert exponential_delay_seconds(1) == 0.0


def test_exponential_delay_increases():
    delays = [exponential_delay_seconds(a, jitter_fraction=0.0) for a in range(1, 7)]
    # attempt 1 -> 0, attempt 2 -> 5, attempt 3 -> 10, attempt 4 -> 20, ...
    assert delays[0] == 0.0
    assert delays[1] == 5.0
    assert delays[2] == 10.0
    assert delays[3] == 20.0
    assert delays[4] == 40.0
    assert delays[5] == 80.0


def test_exponential_delay_capped():
    delay = exponential_delay_seconds(20, base=5.0, max_delay=120.0, jitter_fraction=0.0)
    assert delay == 120.0


def test_exponential_delay_jitter_within_bounds():
    for _ in range(50):
        delay = exponential_delay_seconds(3, base=10.0, max_delay=120.0, jitter_fraction=0.2)
        # base * 2^(3-2) = 20.0, jitter ±20% => [16.0, 24.0]
        assert 16.0 <= delay <= 24.0
