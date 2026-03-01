"""CSI Source Coverage Expansion Controls (Packet 20).

Controls for scaling sources without destabilizing the pipeline:
  - Per-source quotas and sharding for large watchlists
  - Feature-flag scaffold for next source types (X/Threads)
  - Backpressure detection and enforcement
  - Source starvation prevention

All thresholds are configurable via constructor or env vars.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# Defaults
DEFAULT_PER_SOURCE_QUOTA = 50  # max items per poll cycle per source
DEFAULT_SHARD_SIZE = 25  # watchlist entries per shard
DEFAULT_MAX_SHARDS = 10
DEFAULT_BACKPRESSURE_QUEUE_THRESHOLD = 200
DEFAULT_STARVATION_MIN_ITEMS_PER_CYCLE = 1
DEFAULT_POLL_WINDOW_SECONDS = 300  # 5 minutes


@dataclass
class SourceQuota:
    """Per-source quota configuration."""
    source_type: str
    max_items_per_cycle: int = DEFAULT_PER_SOURCE_QUOTA
    shard_size: int = DEFAULT_SHARD_SIZE
    max_shards: int = DEFAULT_MAX_SHARDS
    enabled: bool = True
    feature_flag: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BackpressureState:
    """Current backpressure state for a source."""
    source_type: str
    queue_depth: int = 0
    threshold: int = DEFAULT_BACKPRESSURE_QUEUE_THRESHOLD
    is_backpressured: bool = False
    last_checked_at: float = 0.0
    throttle_factor: float = 1.0  # 1.0 = no throttle, 0.5 = half rate

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StarvationCheck:
    """Result of a source starvation check."""
    source_type: str
    items_last_cycle: int = 0
    min_threshold: int = DEFAULT_STARVATION_MIN_ITEMS_PER_CYCLE
    is_starved: bool = False
    cycles_starved: int = 0
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Feature flags for next-source scaffolding
_FEATURE_FLAGS = {
    "csi_source_x_twitter": os.environ.get("CSI_SOURCE_X_TWITTER_ENABLED", "false").lower() == "true",
    "csi_source_threads": os.environ.get("CSI_SOURCE_THREADS_ENABLED", "false").lower() == "true",
    "csi_source_hackernews": os.environ.get("CSI_SOURCE_HACKERNEWS_ENABLED", "false").lower() == "true",
    "csi_source_bluesky": os.environ.get("CSI_SOURCE_BLUESKY_ENABLED", "false").lower() == "true",
}

# Known source types
KNOWN_SOURCES = {"youtube_rss", "reddit", "hackernews", "x_twitter", "threads", "bluesky"}


def is_source_enabled(source_type: str) -> bool:
    """Check if a source type is enabled via feature flag."""
    flag_key = f"csi_source_{source_type}"
    if flag_key in _FEATURE_FLAGS:
        return _FEATURE_FLAGS[flag_key]
    # Default sources (youtube_rss, reddit) are always enabled
    if source_type in {"youtube_rss", "reddit"}:
        return True
    return False


def get_source_quota(
    source_type: str,
    *,
    max_items_per_cycle: int = DEFAULT_PER_SOURCE_QUOTA,
    shard_size: int = DEFAULT_SHARD_SIZE,
    max_shards: int = DEFAULT_MAX_SHARDS,
) -> SourceQuota:
    """Get quota configuration for a source type."""
    enabled = is_source_enabled(source_type)
    return SourceQuota(
        source_type=source_type,
        max_items_per_cycle=max(1, int(max_items_per_cycle)),
        shard_size=max(1, int(shard_size)),
        max_shards=max(1, int(max_shards)),
        enabled=enabled,
        feature_flag=f"csi_source_{source_type}",
    )


def compute_shards(watchlist_size: int, shard_size: int = DEFAULT_SHARD_SIZE, max_shards: int = DEFAULT_MAX_SHARDS) -> int:
    """Compute number of shards needed for a watchlist."""
    if watchlist_size <= 0:
        return 0
    raw_shards = (watchlist_size + shard_size - 1) // shard_size
    return min(raw_shards, max(1, max_shards))


def check_backpressure(
    *,
    source_type: str,
    queue_depth: int,
    threshold: int = DEFAULT_BACKPRESSURE_QUEUE_THRESHOLD,
) -> BackpressureState:
    """Check if a source is experiencing backpressure."""
    is_bp = int(queue_depth) >= int(threshold)
    throttle = 1.0
    if is_bp:
        # Progressive throttle: more pressure = more throttle
        ratio = min(3.0, int(queue_depth) / max(1, int(threshold)))
        throttle = max(0.1, 1.0 / ratio)
    return BackpressureState(
        source_type=source_type,
        queue_depth=int(queue_depth),
        threshold=int(threshold),
        is_backpressured=is_bp,
        last_checked_at=time.time(),
        throttle_factor=round(throttle, 3),
    )


def check_starvation(
    *,
    source_type: str,
    items_last_cycle: int,
    cycles_starved: int = 0,
    min_threshold: int = DEFAULT_STARVATION_MIN_ITEMS_PER_CYCLE,
) -> StarvationCheck:
    """Check if a source is experiencing starvation (not producing enough items)."""
    is_starved = int(items_last_cycle) < int(min_threshold)
    new_cycles = (int(cycles_starved) + 1) if is_starved else 0

    recommendation = ""
    if is_starved and new_cycles >= 3:
        recommendation = f"Source {source_type} has been starved for {new_cycles} cycles. Check adapter health and source availability."
    elif is_starved:
        recommendation = f"Source {source_type} produced {items_last_cycle} items (below {min_threshold}). Monitoring."

    return StarvationCheck(
        source_type=source_type,
        items_last_cycle=int(items_last_cycle),
        min_threshold=int(min_threshold),
        is_starved=is_starved,
        cycles_starved=new_cycles,
        recommendation=recommendation,
    )


def enforce_quota(
    *,
    items: list[Any],
    quota: SourceQuota,
    backpressure: Optional[BackpressureState] = None,
) -> tuple[list[Any], dict[str, Any]]:
    """Enforce per-source quota with optional backpressure throttling.

    Returns (accepted_items, enforcement_record).
    """
    if not quota.enabled:
        return [], {
            "source_type": quota.source_type,
            "accepted": 0,
            "rejected": len(items),
            "reason": "source_disabled",
        }

    effective_limit = quota.max_items_per_cycle
    if backpressure and backpressure.is_backpressured:
        effective_limit = max(1, int(effective_limit * backpressure.throttle_factor))

    accepted = items[:effective_limit]
    rejected_count = max(0, len(items) - effective_limit)

    return accepted, {
        "source_type": quota.source_type,
        "accepted": len(accepted),
        "rejected": rejected_count,
        "effective_limit": effective_limit,
        "quota_limit": quota.max_items_per_cycle,
        "backpressure_throttled": bool(backpressure and backpressure.is_backpressured),
        "throttle_factor": backpressure.throttle_factor if backpressure else 1.0,
    }


def coverage_health_summary(
    *,
    source_states: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build a coverage health summary from per-source states.

    source_states: dict of source_type -> {items_last_cycle, queue_depth, enabled, ...}
    """
    total_sources = len(source_states)
    enabled_count = sum(1 for s in source_states.values() if s.get("enabled", True))
    starved_sources = []
    backpressured_sources = []

    for src, state in source_states.items():
        if int(state.get("items_last_cycle", 0)) < DEFAULT_STARVATION_MIN_ITEMS_PER_CYCLE:
            starved_sources.append(src)
        if int(state.get("queue_depth", 0)) >= int(state.get("backpressure_threshold", DEFAULT_BACKPRESSURE_QUEUE_THRESHOLD)):
            backpressured_sources.append(src)

    status = "healthy"
    if len(starved_sources) > 0 and len(backpressured_sources) > 0:
        status = "degraded"
    elif len(starved_sources) > total_sources / 2:
        status = "degraded"
    elif len(backpressured_sources) > 0:
        status = "throttled"

    return {
        "status": status,
        "total_sources": total_sources,
        "enabled_sources": enabled_count,
        "starved_sources": starved_sources,
        "backpressured_sources": backpressured_sources,
        "feature_flags": {k: v for k, v in _FEATURE_FLAGS.items()},
    }
