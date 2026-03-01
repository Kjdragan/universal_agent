"""Packet 16: unit tests for CSI report quality scoring."""

import pytest

from universal_agent.csi_quality_score import (
    score_report_quality,
    _evidence_coverage_score,
    _novelty_score,
    _source_diversity_score,
    _actionability_score,
)


class TestEvidenceCoverage:
    def test_empty_subject_scores_zero(self):
        assert _evidence_coverage_score({}) == 0.0

    def test_full_subject_scores_one(self):
        subject = {
            "report_key": "rss_trend:hourly:2026-03-01T12",
            "window_start_utc": "2026-03-01T06:00:00Z",
            "window_end_utc": "2026-03-01T12:00:00Z",
            "artifact_paths": {"markdown": "/path/report.md", "json": "/path/report.json"},
            "quality_summary": {"signal_volume": 20},
        }
        assert _evidence_coverage_score(subject) == 1.0

    def test_partial_subject(self):
        subject = {
            "report_key": "rss_trend:hourly:2026-03-01T12",
            "window_start_utc": "2026-03-01T06:00:00Z",
        }
        score = _evidence_coverage_score(subject)
        assert 0.3 <= score <= 0.5


class TestNovelty:
    def test_novel_report_key(self):
        score = _novelty_score(
            {"report_key": "new_key"},
            prior_report_keys=["old_key_1", "old_key_2"],
        )
        assert score >= 0.9

    def test_repeated_report_key(self):
        score = _novelty_score(
            {"report_key": "old_key_1"},
            prior_report_keys=["old_key_1", "old_key_2"],
        )
        assert score <= 0.5

    def test_no_prior_keys_uses_baseline(self):
        score = _novelty_score({"report_key": "any"}, prior_report_keys=None)
        assert 0.4 <= score <= 0.7

    def test_volume_boost(self):
        low = _novelty_score({"total_items": 1}, prior_report_keys=None)
        high = _novelty_score({"total_items": 20}, prior_report_keys=None)
        assert high > low


class TestSourceDiversity:
    def test_no_sources(self):
        assert _source_diversity_score({}) == 0.0

    def test_single_source(self):
        score = _source_diversity_score({"youtube_rss": 10})
        assert 0.0 < score < 1.0

    def test_three_plus_sources_full_score(self):
        mix = {"youtube_rss": 5, "reddit": 3, "hackernews": 2}
        assert _source_diversity_score(mix) == 1.0

    def test_zero_count_sources_ignored(self):
        mix = {"youtube_rss": 5, "reddit": 0, "hackernews": 0}
        score = _source_diversity_score(mix)
        assert score < 1.0


class TestActionability:
    def test_no_actionable_content(self):
        assert _actionability_score({}) == 0.0

    def test_artifact_paths_boost(self):
        score = _actionability_score({"artifact_paths": {"markdown": "/path.md"}})
        assert score >= 0.4

    def test_opportunities_boost(self):
        score = _actionability_score({
            "opportunities": [{"id": "1"}, {"id": "2"}, {"id": "3"}],
        })
        assert score >= 0.4

    def test_full_actionability(self):
        score = _actionability_score({
            "artifact_paths": {"markdown": "/path.md", "json": "/path.json"},
            "opportunities": [{"id": "1"}, {"id": "2"}, {"id": "3"}],
            "quality_summary": {"signal_volume": 20},
        })
        assert score >= 0.9


class TestCompositeScore:
    """Regression tests with fixed fixtures."""

    def test_high_quality_report(self):
        """Full-featured report with all dimensions high -> grade A."""
        result = score_report_quality(
            subject={
                "report_key": "rss_trend:hourly:2026-03-01T12",
                "window_start_utc": "2026-03-01T06:00:00Z",
                "window_end_utc": "2026-03-01T12:00:00Z",
                "artifact_paths": {"markdown": "/path.md", "json": "/path.json"},
                "quality_summary": {"signal_volume": 25, "freshness_minutes": 30},
                "opportunities": [
                    {"id": "opp-1", "title": "AI momentum"},
                    {"id": "opp-2", "title": "Community pulse"},
                    {"id": "opp-3", "title": "Research gap"},
                ],
                "total_items": 25,
            },
            source_mix={"youtube_rss": 12, "reddit": 8, "hackernews": 5},
            prior_report_keys=["old_key_1"],
        )
        assert result["quality_score"] >= 0.8
        assert result["quality_grade"] == "A"
        assert all(k in result["dimensions"] for k in ["evidence_coverage", "novelty", "source_diversity", "actionability"])

    def test_minimal_report(self):
        """Bare-bones report with no evidence -> grade D."""
        result = score_report_quality(
            subject={},
            source_mix={},
        )
        assert result["quality_score"] < 0.4
        assert result["quality_grade"] == "D"
        assert result["dimensions"]["evidence_coverage"] == 0.0
        assert result["dimensions"]["source_diversity"] == 0.0
        assert result["dimensions"]["actionability"] == 0.0

    def test_medium_quality_report(self):
        """Report with some evidence but limited sources -> grade B or C."""
        result = score_report_quality(
            subject={
                "report_key": "rss_insight:daily:test",
                "window_start_utc": "2026-03-01T00:00:00Z",
                "window_end_utc": "2026-03-02T00:00:00Z",
                "artifact_paths": {"markdown": "/path.md"},
                "total_items": 8,
            },
            source_mix={"youtube_rss": 8},
            prior_report_keys=["other_key"],
        )
        assert 0.4 <= result["quality_score"] < 0.9
        assert result["quality_grade"] in ("B", "C")

    def test_score_deterministic(self):
        """Same inputs always produce same output."""
        kwargs = dict(
            subject={"report_key": "k1", "total_items": 5},
            source_mix={"youtube_rss": 5},
        )
        r1 = score_report_quality(**kwargs)
        r2 = score_report_quality(**kwargs)
        assert r1 == r2

    def test_all_dimensions_in_range(self):
        """All dimension scores are 0.0-1.0."""
        result = score_report_quality(
            subject={"report_key": "k1", "total_items": 10},
            source_mix={"a": 1, "b": 2},
        )
        for dim, val in result["dimensions"].items():
            assert 0.0 <= val <= 1.0, f"{dim}={val} out of range"
        assert 0.0 <= result["quality_score"] <= 1.0
