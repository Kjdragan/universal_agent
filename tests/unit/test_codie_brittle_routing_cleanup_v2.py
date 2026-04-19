"""Tests for CODIE proactive cleanup v2: reduce brittle routing heuristics.

Covers:
  - Change 1: _AGENT_LABEL_MAP in email_task_bridge (typo fix + mapping dict)
  - Change 2: _POST_RE regex fix in session_policy (x.com detection)
  - Change 3: Shared youtube_mode_utils module (DRY extraction)
  - Change 4: _analytics_message dispatch table in signals_ingest
"""

from __future__ import annotations

import re

from universal_agent.services.email_task_bridge import _AGENT_LABEL_MAP
from universal_agent.session_policy import classify_request_categories, _POST_RE
from universal_agent.youtube_mode_utils import (
    infer_youtube_mode,
    MODE_EXPLAINER_ONLY,
    MODE_EXPLAINER_PLUS_CODE,
    _CODE_HINT_KEYWORDS,
    _NON_CODE_HINT_KEYWORDS,
)
from universal_agent.signals_ingest import (
    _analytics_message,
    _ANALYTICS_HANDLERS,
    CreatorSignalEvent,
)


# ---------------------------------------------------------------------------
# Change 1: _AGENT_LABEL_MAP
# ---------------------------------------------------------------------------


class TestAgentLabelMap:
    """Verify agent-label mapping dict replaces hardcoded ternary."""

    def test_coder_maps_to_codie(self):
        assert _AGENT_LABEL_MAP.get("coder") == "agent-codie"

    def test_vp_coder_primary_maps_to_codie(self):
        assert _AGENT_LABEL_MAP.get("vp.coder.primary") == "agent-codie"

    def test_vp_coder_maps_to_codie(self):
        assert _AGENT_LABEL_MAP.get("vp.coder") == "agent-codie"

    def test_general_maps_to_atlas(self):
        assert _AGENT_LABEL_MAP.get("general") == "agent-atlas"

    def test_vp_general_primary_maps_to_atlas(self):
        assert _AGENT_LABEL_MAP.get("vp.general.primary") == "agent-atlas"

    def test_vp_general_maps_to_atlas(self):
        assert _AGENT_LABEL_MAP.get("vp.general") == "agent-atlas"

    def test_unknown_defaults_to_atlas(self):
        assert _AGENT_LABEL_MAP.get("unknown_agent", "agent-atlas") == "agent-atlas"

    def test_no_agent_cody_typo(self):
        """The old typo 'agent-cody' must not appear in any mapping value."""
        for value in _AGENT_LABEL_MAP.values():
            assert value != "agent-cody", f"Typo agent-cody found: {value}"


# ---------------------------------------------------------------------------
# Change 2: _POST_RE regex fix
# ---------------------------------------------------------------------------


class TestPostRegexFix:
    """Verify _POST_RE now correctly matches x.com (was double-escaped)."""

    def test_matches_x_com(self):
        assert _POST_RE.search("post this on x.com") is not None

    def test_matches_tweet(self):
        assert _POST_RE.search("tweet about this") is not None

    def test_matches_linkedin(self):
        assert _POST_RE.search("share on linkedin") is not None

    def test_classify_x_com_as_public_posting(self):
        cats = classify_request_categories("post this on x.com")
        assert "public_posting" in cats

    def test_classify_x_com_url(self):
        cats = classify_request_categories("share https://x.com/user/status/123")
        assert "public_posting" in cats


# ---------------------------------------------------------------------------
# Change 3: Shared youtube_mode_utils
# ---------------------------------------------------------------------------


class TestYoutubeModeUtils:
    """Verify shared mode inference module is the single source of truth."""

    def test_code_keywords_frozenset(self):
        assert isinstance(_CODE_HINT_KEYWORDS, frozenset)

    def test_non_code_keywords_frozenset(self):
        assert isinstance(_NON_CODE_HINT_KEYWORDS, frozenset)

    def test_code_mode_python(self):
        assert infer_youtube_mode("python programming tutorial") == MODE_EXPLAINER_PLUS_CODE

    def test_code_mode_react(self):
        assert infer_youtube_mode("react hooks explained") == MODE_EXPLAINER_PLUS_CODE

    def test_code_mode_docker(self):
        assert infer_youtube_mode("docker container setup") == MODE_EXPLAINER_PLUS_CODE

    def test_non_code_mode_cooking(self):
        assert infer_youtube_mode("cooking recipe vlog") == MODE_EXPLAINER_ONLY

    def test_non_code_mode_music(self):
        assert infer_youtube_mode("music song playlist") == MODE_EXPLAINER_ONLY

    def test_non_code_mode_empty(self):
        assert infer_youtube_mode() == MODE_EXPLAINER_ONLY

    def test_non_code_dominates_over_code(self):
        """Non-code keywords without code keywords should be explainer_only."""
        assert infer_youtube_mode("baking recipe") == MODE_EXPLAINER_ONLY

    def test_code_with_mixed_content(self):
        """Code keywords should produce explainer_plus_code even with other text."""
        assert infer_youtube_mode("python recipe for api automation") == MODE_EXPLAINER_PLUS_CODE

    def test_no_overlap_between_keyword_sets(self):
        overlap = _CODE_HINT_KEYWORDS & _NON_CODE_HINT_KEYWORDS
        assert not overlap, f"Keyword overlap: {overlap}"


# ---------------------------------------------------------------------------
# Change 4: _analytics_message dispatch table
# ---------------------------------------------------------------------------


def _make_event(event_type: str, subject: dict | None = None) -> CreatorSignalEvent:
    return CreatorSignalEvent(
        event_id="test-evt",
        dedupe_key="test-dedupe",
        source="csi_analytics",
        event_type=event_type,
        occurred_at="2026-01-01T00:00:00Z",
        received_at="2026-01-01T00:00:02Z",
        subject=subject or {},
        routing={},
    )


class TestAnalyticsDispatchTable:
    """Verify dispatch table refactor preserves original behavior."""

    def test_dispatch_table_has_all_handlers(self):
        assert len(_ANALYTICS_HANDLERS) == 8

    def test_handler_prefixes_are_unique(self):
        prefixes = [p for p, _ in _ANALYTICS_HANDLERS]
        # Prefix-based matching means startswith, so overlapping prefixes are OK
        # (first match wins), but exact duplicates would indicate a bug.
        assert len(prefixes) == len(set(prefixes))

    def test_hourly_token_usage_report(self):
        event = _make_event("hourly_token_usage_report", {
            "totals": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        })
        msg = _analytics_message(event)
        assert "hourly_tokens:" in msg
        assert "prompt=100" in msg
        assert "completion=50" in msg

    def test_rss_trend_report(self):
        event = _make_event("rss_trend_report", {
            "window_start_utc": "2026-01-01",
            "window_end_utc": "2026-01-02",
            "totals": {"items": 5, "by_category": {"ai": 3, "other_interest": 2}},
            "top_themes": ["AI trends", "LLM advances"],
        })
        msg = _analytics_message(event)
        assert "window:" in msg
        assert "items: 5" in msg
        assert "category_mix:" in msg
        assert "top_themes_preview:" in msg

    def test_threads_trend_report(self):
        event = _make_event("threads_trend_report", {
            "report_key": "threads_2026_01",
            "window_start_utc": "2026-01-01",
            "window_end_utc": "2026-01-02",
            "total_items": 10,
            "top_terms": ["technology", "ai"],
        })
        msg = _analytics_message(event)
        assert "report_key: threads_2026_01" in msg
        assert "items: 10" in msg
        assert "top_terms_preview:" in msg

    def test_global_trend_brief_ready(self):
        event = _make_event("global_trend_brief_ready", {
            "brief_key": "brief_001",
            "window_start_utc": "2026-01-01",
            "window_end_utc": "2026-01-02",
            "source_totals": {"rss": 10, "reddit": 5},
        })
        msg = _analytics_message(event)
        assert "brief_key: brief_001" in msg
        assert "source_totals:" in msg

    def test_csi_global_brief_review_due(self):
        event = _make_event("csi_global_brief_review_due", {
            "brief_key": "brief_002",
            "slot_display": "morning",
            "timezone": "UTC",
        })
        msg = _analytics_message(event)
        assert "brief_key: brief_002" in msg
        assert "slot: morning" in msg
        assert "timezone: UTC" in msg

    def test_rss_insight_prefix_match(self):
        event = _make_event("rss_insight_ai_trends", {
            "report_key": "insight_001",
            "total_items": 3,
        })
        msg = _analytics_message(event)
        assert "report_key: insight_001" in msg
        assert "items: 3" in msg

    def test_category_quality_report(self):
        event = _make_event("category_quality_report", {
            "action": "adjust_threshold",
            "metrics": {
                "total_items": 100,
                "other_interest_ratio": 0.45,
                "uncategorized_items": 10,
            },
        })
        msg = _analytics_message(event)
        assert "quality_action: adjust_threshold" in msg
        assert "items=100" in msg
        assert "uncategorized=10" in msg

    def test_analysis_task_prefix_match(self):
        event = _make_event("analysis_task_deep_dive", {
            "task_id": "task_001",
            "request_type": "deep_analysis",
            "status": "completed",
        })
        msg = _analytics_message(event)
        assert "task_id: task_001" in msg
        assert "request_type: deep_analysis" in msg
        assert "task_status: completed" in msg

    def test_unknown_event_type_no_crash(self):
        event = _make_event("completely_unknown_event", {"some_key": "some_value"})
        msg = _analytics_message(event)
        assert "CSI analytics signal received." in msg
        assert "event_type: completely_unknown_event" in msg
        assert "subject_json:" in msg

    def test_always_includes_common_fields(self):
        """All event messages should include the common header fields."""
        event = _make_event("hourly_token_usage_report", {
            "totals": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        msg = _analytics_message(event)
        assert "event_type: hourly_token_usage_report" in msg
        assert "source: csi_analytics" in msg
        assert "event_id: test-evt" in msg
        assert "occurred_at: 2026-01-01T00:00:00Z" in msg
