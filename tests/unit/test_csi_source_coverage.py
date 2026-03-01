"""Packet 20: tests for CSI source coverage expansion controls."""

import pytest

from universal_agent.csi_source_coverage import (
    SourceQuota,
    BackpressureState,
    StarvationCheck,
    is_source_enabled,
    get_source_quota,
    compute_shards,
    check_backpressure,
    check_starvation,
    enforce_quota,
    coverage_health_summary,
    DEFAULT_PER_SOURCE_QUOTA,
    DEFAULT_SHARD_SIZE,
    DEFAULT_MAX_SHARDS,
    DEFAULT_BACKPRESSURE_QUEUE_THRESHOLD,
    KNOWN_SOURCES,
)


class TestSourceEnabled:
    def test_default_sources_always_enabled(self):
        assert is_source_enabled("youtube_rss") is True
        assert is_source_enabled("reddit") is True

    def test_new_sources_disabled_by_default(self):
        assert is_source_enabled("x_twitter") is False
        assert is_source_enabled("threads") is False
        assert is_source_enabled("bluesky") is False

    def test_unknown_source_disabled(self):
        assert is_source_enabled("unknown_source_xyz") is False


class TestSourceQuota:
    def test_get_default_quota(self):
        q = get_source_quota("youtube_rss")
        assert q.source_type == "youtube_rss"
        assert q.max_items_per_cycle == DEFAULT_PER_SOURCE_QUOTA
        assert q.enabled is True

    def test_disabled_source_quota(self):
        q = get_source_quota("x_twitter")
        assert q.enabled is False

    def test_custom_quota(self):
        q = get_source_quota("reddit", max_items_per_cycle=100, shard_size=50)
        assert q.max_items_per_cycle == 100
        assert q.shard_size == 50

    def test_to_dict(self):
        q = get_source_quota("youtube_rss")
        d = q.to_dict()
        assert isinstance(d, dict)
        assert d["source_type"] == "youtube_rss"


class TestSharding:
    def test_empty_watchlist(self):
        assert compute_shards(0) == 0

    def test_small_watchlist(self):
        assert compute_shards(10) == 1

    def test_exact_shard_size(self):
        assert compute_shards(DEFAULT_SHARD_SIZE) == 1

    def test_multiple_shards(self):
        assert compute_shards(60, shard_size=25) == 3

    def test_max_shards_capped(self):
        assert compute_shards(1000, shard_size=25, max_shards=5) == 5

    def test_single_entry(self):
        assert compute_shards(1) == 1


class TestBackpressure:
    def test_no_backpressure(self):
        bp = check_backpressure(source_type="youtube_rss", queue_depth=50)
        assert bp.is_backpressured is False
        assert bp.throttle_factor == 1.0

    def test_at_threshold(self):
        bp = check_backpressure(
            source_type="youtube_rss",
            queue_depth=DEFAULT_BACKPRESSURE_QUEUE_THRESHOLD,
        )
        assert bp.is_backpressured is True
        assert bp.throttle_factor <= 1.0

    def test_above_threshold_throttles(self):
        bp = check_backpressure(
            source_type="youtube_rss",
            queue_depth=DEFAULT_BACKPRESSURE_QUEUE_THRESHOLD + 50,
        )
        assert bp.is_backpressured is True
        assert bp.throttle_factor < 1.0

    def test_above_threshold_progressive_throttle(self):
        bp_low = check_backpressure(source_type="reddit", queue_depth=250, threshold=200)
        bp_high = check_backpressure(source_type="reddit", queue_depth=500, threshold=200)
        assert bp_low.throttle_factor > bp_high.throttle_factor

    def test_to_dict(self):
        bp = check_backpressure(source_type="reddit", queue_depth=100)
        d = bp.to_dict()
        assert isinstance(d, dict)
        assert d["source_type"] == "reddit"


class TestStarvation:
    def test_not_starved(self):
        s = check_starvation(source_type="youtube_rss", items_last_cycle=10)
        assert s.is_starved is False
        assert s.cycles_starved == 0

    def test_starved_first_cycle(self):
        s = check_starvation(source_type="youtube_rss", items_last_cycle=0)
        assert s.is_starved is True
        assert s.cycles_starved == 1
        assert "Monitoring" in s.recommendation

    def test_starved_multiple_cycles_escalates(self):
        s = check_starvation(
            source_type="reddit",
            items_last_cycle=0,
            cycles_starved=2,
        )
        assert s.is_starved is True
        assert s.cycles_starved == 3
        assert "Check adapter health" in s.recommendation

    def test_recovery_resets_counter(self):
        s = check_starvation(
            source_type="reddit",
            items_last_cycle=5,
            cycles_starved=3,
        )
        assert s.is_starved is False
        assert s.cycles_starved == 0


class TestEnforceQuota:
    def test_within_quota(self):
        items = list(range(10))
        quota = get_source_quota("youtube_rss", max_items_per_cycle=50)
        accepted, record = enforce_quota(items=items, quota=quota)
        assert len(accepted) == 10
        assert record["rejected"] == 0

    def test_exceeds_quota(self):
        items = list(range(100))
        quota = get_source_quota("youtube_rss", max_items_per_cycle=30)
        accepted, record = enforce_quota(items=items, quota=quota)
        assert len(accepted) == 30
        assert record["rejected"] == 70

    def test_disabled_source_rejects_all(self):
        items = list(range(10))
        quota = get_source_quota("x_twitter")
        accepted, record = enforce_quota(items=items, quota=quota)
        assert len(accepted) == 0
        assert record["reason"] == "source_disabled"

    def test_backpressure_reduces_quota(self):
        items = list(range(100))
        quota = get_source_quota("youtube_rss", max_items_per_cycle=50)
        bp = check_backpressure(source_type="youtube_rss", queue_depth=400, threshold=200)
        accepted, record = enforce_quota(items=items, quota=quota, backpressure=bp)
        assert len(accepted) < 50
        assert record["backpressure_throttled"] is True


class TestCoverageHealthSummary:
    def test_healthy(self):
        states = {
            "youtube_rss": {"items_last_cycle": 20, "queue_depth": 10, "enabled": True},
            "reddit": {"items_last_cycle": 15, "queue_depth": 5, "enabled": True},
        }
        summary = coverage_health_summary(source_states=states)
        assert summary["status"] == "healthy"
        assert summary["total_sources"] == 2
        assert summary["enabled_sources"] == 2
        assert len(summary["starved_sources"]) == 0

    def test_degraded_with_starvation(self):
        states = {
            "youtube_rss": {"items_last_cycle": 0, "queue_depth": 0, "enabled": True},
            "reddit": {"items_last_cycle": 0, "queue_depth": 0, "enabled": True},
        }
        summary = coverage_health_summary(source_states=states)
        assert summary["status"] == "degraded"
        assert len(summary["starved_sources"]) == 2

    def test_throttled_with_backpressure(self):
        states = {
            "youtube_rss": {"items_last_cycle": 20, "queue_depth": 300, "enabled": True},
            "reddit": {"items_last_cycle": 15, "queue_depth": 5, "enabled": True},
        }
        summary = coverage_health_summary(source_states=states)
        assert summary["status"] == "throttled"
        assert "youtube_rss" in summary["backpressured_sources"]

    def test_feature_flags_included(self):
        summary = coverage_health_summary(source_states={})
        assert "feature_flags" in summary
        assert isinstance(summary["feature_flags"], dict)
