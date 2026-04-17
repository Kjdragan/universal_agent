"""Tests for CODIE proactive cleanup: reduce brittle routing heuristics.

Covers:
  - _csi_source_bucket: refactored from if-chain to ordered-keyword lookup
  - _classify_subtask_role: extracted from inline keyword tuples
  - _CSI_SOURCE_BUCKET_KEYWORDS: new constant
  - _CSI_CODE/RESEARCH/WRITER_SUBTASK_KEYWORDS: new constants
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from universal_agent.gateway_server import (
    _CSI_CODE_SUBTASK_KEYWORDS,
    _CSI_RESEARCH_SUBTASK_KEYWORDS,
    _CSI_SOURCE_BUCKET_KEYWORDS,
    _CSI_WRITER_SUBTASK_KEYWORDS,
    _classify_subtask_role,
    _csi_source_bucket,
)


# ---------------------------------------------------------------------------
# _csi_source_bucket
# ---------------------------------------------------------------------------


def _make_event(event_type: str = "", source: str = "", report_type: str = "", subject: dict | None = None) -> object:
    """Build a lightweight CSI event stub."""
    return SimpleNamespace(event_type=event_type, source=source, subject=subject)


class TestCsiSourceBucket:
    """Verify that the refactored _csi_source_bucket preserves original behavior."""

    def test_threads_bucket_from_event_type(self):
        event = _make_event(event_type="threads_report")
        assert _csi_source_bucket(event) == "threads"

    def test_reddit_bucket_from_source(self):
        event = _make_event(source="reddit_trends")
        assert _csi_source_bucket(event) == "reddit"

    def test_rss_bucket_from_report_type(self):
        event = _make_event(subject={"report_type": "rss_digest"})
        assert _csi_source_bucket(event) == "rss"

    def test_youtube_bucket_from_event_type(self):
        event = _make_event(event_type="youtube_ingest")
        assert _csi_source_bucket(event) == "youtube"

    def test_analysis_task_bucket(self):
        event = _make_event(event_type="analysis_task")
        assert _csi_source_bucket(event) == "analysis_task"

    def test_fallback_to_source(self):
        event = _make_event(source="custom_source")
        assert _csi_source_bucket(event) == "custom_source"

    def test_fallback_to_csi_when_empty(self):
        event = _make_event()
        assert _csi_source_bucket(event) == "csi"

    def test_priority_order_threads_over_reddit(self):
        """When both 'threads' and 'reddit' appear, first match wins."""
        event = _make_event(event_type="threads_report", source="reddit_trends")
        assert _csi_source_bucket(event) == "threads"

    def test_subject_report_type_overrides(self):
        event = _make_event(event_type="unknown", source="csi", subject={"report_type": "youtube_summary"})
        assert _csi_source_bucket(event) == "youtube"

    def test_case_insensitive(self):
        event = _make_event(event_type="Reddit_Trends_Report")
        assert _csi_source_bucket(event) == "reddit"


# ---------------------------------------------------------------------------
# _classify_subtask_role
# ---------------------------------------------------------------------------


class TestClassifySubtaskRole:
    """Verify extracted subtask role classifier preserves original behavior."""

    def test_code_role_install(self):
        assert _classify_subtask_role("install the missing package") == "code"

    def test_code_role_fix(self):
        assert _classify_subtask_role("fix the broken hook") == "code"

    def test_code_role_patch(self):
        assert _classify_subtask_role("patch the signature") == "code"

    def test_code_role_env(self):
        assert _classify_subtask_role("update the env file") == "code"

    def test_code_role_code(self):
        assert _classify_subtask_role("add error handling to the code") == "code"

    def test_research_role_analyze(self):
        assert _classify_subtask_role("analyze the data") == "research"

    def test_research_role_investigate(self):
        assert _classify_subtask_role("investigate the root cause") == "research"

    def test_research_role_review(self):
        assert _classify_subtask_role("review the configuration") == "research"

    def test_research_role_assess(self):
        assert _classify_subtask_role("assess the impact") == "research"

    def test_writer_role_write(self):
        assert _classify_subtask_role("write a summary report") == "writer"

    def test_writer_role_draft(self):
        assert _classify_subtask_role("draft the email") == "writer"

    def test_writer_role_publish(self):
        assert _classify_subtask_role("publish the article") == "writer"

    def test_writer_role_message(self):
        assert _classify_subtask_role("send the message") == "writer"

    def test_general_role_default(self):
        assert _classify_subtask_role("schedule a meeting") == "general"

    def test_general_role_empty(self):
        assert _classify_subtask_role("") == "general"

    def test_code_priority_over_research(self):
        """If text contains both code and research keywords, code wins (first match)."""
        assert _classify_subtask_role("fix and review the code") == "code"

    def test_research_priority_over_writer(self):
        assert _classify_subtask_role("analyze and draft the report") == "research"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify new constants are well-formed."""

    def test_bucket_keywords_is_tuple(self):
        assert isinstance(_CSI_SOURCE_BUCKET_KEYWORDS, tuple)
        assert len(_CSI_SOURCE_BUCKET_KEYWORDS) == 5

    def test_bucket_keywords_unique(self):
        assert len(set(_CSI_SOURCE_BUCKET_KEYWORDS)) == len(_CSI_SOURCE_BUCKET_KEYWORDS)

    def test_code_keywords_nonempty(self):
        assert len(_CSI_CODE_SUBTASK_KEYWORDS) > 0

    def test_research_keywords_nonempty(self):
        assert len(_CSI_RESEARCH_SUBTASK_KEYWORDS) > 0

    def test_writer_keywords_nonempty(self):
        assert len(_CSI_WRITER_SUBTASK_KEYWORDS) > 0

    def test_no_overlap_between_role_sets(self):
        code = set(_CSI_CODE_SUBTASK_KEYWORDS)
        research = set(_CSI_RESEARCH_SUBTASK_KEYWORDS)
        writer = set(_CSI_WRITER_SUBTASK_KEYWORDS)
        assert not code & research, f"code/research overlap: {code & research}"
        assert not code & writer, f"code/writer overlap: {code & writer}"
        assert not research & writer, f"research/writer overlap: {research & writer}"
