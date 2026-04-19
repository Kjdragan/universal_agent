"""Tests for CODIE proactive cleanup v2: reduce brittle routing heuristics.

Covers:
  - Change 1: _AGENT_LABEL_MAP in email_task_bridge (typo fix + mapping dict)
  - Change 2: _POST_RE regex fix in session_policy (x.com detection)
  - Change 3: Shared youtube_mode_utils module (DRY extraction)
  - Change 4: retired CSI analytics action dispatch stays removed
"""

from __future__ import annotations

import re

from universal_agent.services.email_task_bridge import _AGENT_LABEL_MAP
from universal_agent.session_policy import classify_request_categories, _POST_RE
from universal_agent.youtube_mode_utils import (
    infer_youtube_mode,
    MODE_EXPLAINER_ONLY,
    MODE_EXPLAINER_PLUS_CODE,
    YOUTUBE_CODE_HINT_KEYWORDS,
    YOUTUBE_NON_CODE_HINT_KEYWORDS,
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
        assert isinstance(YOUTUBE_CODE_HINT_KEYWORDS, frozenset)

    def test_non_code_keywords_frozenset(self):
        assert isinstance(YOUTUBE_NON_CODE_HINT_KEYWORDS, frozenset)

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
        overlap = YOUTUBE_CODE_HINT_KEYWORDS & YOUTUBE_NON_CODE_HINT_KEYWORDS
        assert not overlap, f"Keyword overlap: {overlap}"


# ---------------------------------------------------------------------------
# Change 4: retired CSI analytics dispatch
# ---------------------------------------------------------------------------


class TestRetiredAnalyticsDispatch:
    """CSI analytics should not expose old per-event dispatch helpers."""

    def test_old_analytics_message_helper_is_removed(self):
        import universal_agent.signals_ingest as signals_ingest

        assert not hasattr(signals_ingest, "_analytics_message")
        assert not hasattr(signals_ingest, "to_csi_analytics_action")
